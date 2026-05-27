/**
 * 冰箱食材识别系统 - 开发板端 (自包含, 纯RKMPI+NPU)
 *
 * 状态机:
 *   门关(IDLE)  -> 休眠省电, LCD显示库存UI, CPU低频轮询
 *   门开(ACTIVE) -> 开灯, 启动摄像头+YOLOv5, LCD显示视频流
 *   门关回来     -> 对比画面变化, 生成put_in/take_out事件, 更新DB, 同步服务器
 *
 * 颜色修复: VI输出RK_FMT_YUV420SP=NV12, cvtColor用COLOR_YUV2BGR_NV12
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <math.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>

/* ===== RKMPI 头文件 ===== */
#include "rk_mpi_sys.h"
#include "rk_mpi_vi.h"
#include "rk_mpi_mb.h"
#include "rk_mpi_venc.h"
#include "rk_comm_vi.h"
#include "rk_comm_video.h"

/* ===== rkaiq ISP头文件 ===== */
#include "rk_aiq_user_api2_sysctl.h"
#include "rk_aiq_user_api2_imgproc.h"

/* ===== RKNN YOLOv5 ===== */
#include "yolov5.h"

/* ===== OpenCV (颜色转换+绘图) ===== */
#include "opencv2/core/core.hpp"
#include "opencv2/imgproc/imgproc.hpp"

/* ===== SQLite ===== */
#include "sqlite3.h"

/* ===== 配置 ===== */
#define DOOR_PIN        32
#define RELAY_PIN       40
#define DB_PATH         "/root/smartfridge/fridge.db"
#define SERVER_URL      "http://192.168.2.100:5000"
#define CAM_W           720
#define CAM_H           480
#define MODEL_W         640
#define MODEL_H         640
#define LCD_W           480
#define LCD_H           480
#define FB_DEV          "/dev/fb0"
#define IQ_DIR          "/etc/iqfiles"
#define SYNC_INTERVAL   2

/* ===== 全局 ===== */
static volatile int running = 1;
static void sig_handler(int s) { running = 0; }

/* ===== GPIO sysfs ===== */
static int gpio_export(int p) { int f=open("/sys/class/gpio/export",O_WRONLY); if(f<0)return-1; char b[16]; int n=snprintf(b,sizeof(b),"%d",p); write(f,b,n); close(f); usleep(100000); return 0; }
static int gpio_read(int p) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/value",p); int f=open(path,O_RDONLY); if(f<0)return 1; char v; read(f,&v,1); close(f); return v=='1'; }
static void gpio_write(int p, int v) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/value",p); int f=open(path,O_WRONLY); if(f>=0){write(f,v?"1":"0",1);close(f);} }
static void gpio_dir(int p, const char *d) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/direction",p); int f=open(path,O_WRONLY); if(f>=0){write(f,d,strlen(d));close(f);} }

/* ===== SQLite ===== */
static sqlite3 *g_db = NULL;
static void db_open(void) {
    sqlite3_open(DB_PATH, &g_db);
    sqlite3_exec(g_db,
        "CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,count INTEGER DEFAULT 1,category TEXT DEFAULT '',first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,action TEXT,food_name TEXT,count INTEGER DEFAULT 1);"
        "CREATE TABLE IF NOT EXISTS status(id INTEGER PRIMARY KEY CHECK(id=1),door_state TEXT,light_state TEXT,cpu_temp REAL,updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "INSERT OR IGNORE INTO status(id,door_state,light_state) VALUES(1,'closed','off');",
        NULL,NULL,NULL);
}
static void db_item_update(const char *name, int delta) {
    sqlite3_stmt *s;
    sqlite3_prepare_v2(g_db,"SELECT id,count FROM inventory WHERE name=?",-1,&s,NULL);
    sqlite3_bind_text(s,1,name,-1,SQLITE_STATIC);
    if(sqlite3_step(s)==SQLITE_ROW){int id=sqlite3_column_int(s,0),c=sqlite3_column_int(s,1)+delta;sqlite3_finalize(s);
        if(c<=0){char sql[64];snprintf(sql,sizeof(sql),"DELETE FROM inventory WHERE id=%d",id);sqlite3_exec(g_db,sql,NULL,NULL,NULL);}
        else{char sql[128];snprintf(sql,sizeof(sql),"UPDATE inventory SET count=%d,last_updated=CURRENT_TIMESTAMP WHERE id=%d",c,id);sqlite3_exec(g_db,sql,NULL,NULL,NULL);}}
    else{sqlite3_finalize(s);if(delta>0){sqlite3_prepare_v2(g_db,"INSERT INTO inventory(name,count) VALUES(?,?)",-1,&s,NULL);sqlite3_bind_text(s,1,name,-1,SQLITE_STATIC);sqlite3_bind_int(s,2,delta);sqlite3_step(s);sqlite3_finalize(s);}}
}
static void db_event_add(const char *action, const char *food, int cnt) {
    sqlite3_stmt *s;sqlite3_prepare_v2(g_db,"INSERT INTO events(action,food_name,count) VALUES(?,?,?)",-1,&s,NULL);
    sqlite3_bind_text(s,1,action,-1,SQLITE_STATIC);sqlite3_bind_text(s,2,food,-1,SQLITE_STATIC);sqlite3_bind_int(s,3,cnt);
    sqlite3_step(s);sqlite3_finalize(s);
}
static void db_status_update(const char *door, const char *light, float temp) {
    char sql[256];snprintf(sql,sizeof(sql),"UPDATE status SET door_state='%s',light_state='%s',cpu_temp=%.1f,updated=CURRENT_TIMESTAMP WHERE id=1",door,light,temp);
    sqlite3_exec(g_db,sql,NULL,NULL,NULL);
}
static char *db_build_json(void) {
    char *j=malloc(8192);int l=0,first=1;sqlite3_stmt *s;
    l+=snprintf(j+l,8192-l,"{\"inventory\":[");
    sqlite3_prepare_v2(g_db,"SELECT name,category,count,first_seen,last_updated FROM inventory ORDER BY last_updated DESC",-1,&s,NULL);
    while(sqlite3_step(s)==SQLITE_ROW){if(!first)l+=snprintf(j+l,8192-l,",");l+=snprintf(j+l,8192-l,"{\"name\":\"%s\",\"category\":\"%s\",\"count\":%d,\"first_seen\":\"%s\",\"last_updated\":\"%s\"}",sqlite3_column_text(s,0),sqlite3_column_text(s,1)?(char*)sqlite3_column_text(s,1):"",sqlite3_column_int(s,2),sqlite3_column_text(s,3)?(char*)sqlite3_column_text(s,3):"",sqlite3_column_text(s,4)?(char*)sqlite3_column_text(s,4):"");first=0;}
    sqlite3_finalize(s);l+=snprintf(j+l,8192-l,"],\"events\":[");
    sqlite3_prepare_v2(g_db,"SELECT id,timestamp,action,food_name,count FROM events ORDER BY id ASC LIMIT 100",-1,&s,NULL);first=1;
    while(sqlite3_step(s)==SQLITE_ROW){if(!first)l+=snprintf(j+l,8192-l,",");l+=snprintf(j+l,8192-l,"{\"id\":%d,\"timestamp\":\"%s\",\"action\":\"%s\",\"food_name\":\"%s\",\"count\":%d}",sqlite3_column_int(s,0),sqlite3_column_text(s,1),sqlite3_column_text(s,2),sqlite3_column_text(s,3),sqlite3_column_int(s,4));first=0;}
    sqlite3_finalize(s);l+=snprintf(j+l,8192-l,"],\"hardware_status\":{");
    sqlite3_prepare_v2(g_db,"SELECT door_state,light_state,cpu_temp,updated FROM status WHERE id=1",-1,&s,NULL);
    if(sqlite3_step(s)==SQLITE_ROW)l+=snprintf(j+l,8192-l,"\"door_state\":\"%s\",\"light_state\":\"%s\",\"cpu_temp\":%.1f,\"updated_at\":\"%s\"",sqlite3_column_text(s,0),sqlite3_column_text(s,1),sqlite3_column_double(s,2),sqlite3_column_text(s,3)?(char*)sqlite3_column_text(s,3):"");
    sqlite3_finalize(s);l+=snprintf(j+l,8192-l,"}}");return j;
}

/* ===== HTTP POST (socket) ===== */
static int http_post(const char *url, const char *json) {
    char host[128]={0};int port=80;
    const char *p=url+7,*s=strchr(p,'/'),*c=strchr(p,':');
    if(c&&(!s||c<s)){memcpy(host,p,c-p);port=atoi(c+1);}else if(s){memcpy(host,p,s-p);}else{strcpy(host,p);}
    char path[256]="/api/sync";if(s)strcpy(path,s);
    int sock=socket(AF_INET,SOCK_STREAM,0);if(sock<0)return-1;
    struct timeval tv={2,0};setsockopt(sock,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));setsockopt(sock,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
    struct sockaddr_in addr={0};addr.sin_family=AF_INET;addr.sin_port=htons(port);
    if(inet_pton(AF_INET,host,&addr.sin_addr)<=0){struct hostent *he=gethostbyname(host);if(!he){close(sock);return-1;}memcpy(&addr.sin_addr,he->h_addr_list[0],he->h_length);}
    if(connect(sock,(struct sockaddr*)&addr,sizeof(addr))<0){close(sock);return-1;}
    char req[16384];int rlen=snprintf(req,sizeof(req),"POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",path,host,(int)strlen(json),json);
    send(sock,req,rlen,0);char resp[512];recv(sock,resp,sizeof(resp)-1,0);close(sock);return 0;
}

/* ===== CPU温度 ===== */
static float cpu_temp(void){FILE *f=fopen("/sys/class/thermal/thermal_zone0/temp","r");if(!f)return 0;float t;fscanf(f,"%f",&t);fclose(f);return t/1000.f;}

/* ===== 食材中文名 ===== */
static const char *food_name(int id){switch(id){case 44:return"瓶装饮品";case 46:return"香蕉";case 47:return"苹果";case 48:return"三明治";case 49:return"橙子";case 50:return"西兰花";case 51:return"胡萝卜";case 52:return"热狗";case 53:return"披萨";case 54:return"甜甜圈";case 55:return"蛋糕";case 67:return"手机";case 69:return"烤箱";case 70:return"烤面包机";case 72:return"冰箱";case 73:return"书本";case 76:return"剪刀";case 0:return"人";default:return coco_cls_to_name(id);}}

/* ===== Letterbox ===== */
static float lb_s;static int lb_l,lb_t;
static void letterbox(cv::Mat &in, cv::Mat &out){float sx=(float)MODEL_W/CAM_W,sy=(float)MODEL_H/CAM_H;lb_s=sx<sy?sx:sy;int iw=(int)(CAM_W*lb_s),ih=(int)(CAM_H*lb_s);lb_l=(MODEL_W-iw)/2;lb_t=(MODEL_H-ih)/2;cv::Mat s;cv::resize(in,s,cv::Size(iw,ih),0,0,cv::INTER_LINEAR);cv::Mat bg(MODEL_H,MODEL_W,CV_8UC3,cv::Scalar(0,0,0));cv::Rect r(lb_l,lb_t,iw,ih);s.copyTo(bg(r));out=bg.clone();}
static void unletterbox(int *x,int *y){*x=(int)((*x-lb_l)/lb_s);*y=(int)((*y-lb_t)/lb_s);}

/* ===== 对比检测结果, 生成事件 ===== */
typedef struct {char name[64];int cnt;} item_t;
static int parse_dets(item_t *items,int max,object_detect_result_list *od){int n=0;for(int i=0;i<od->count;i++){const char *nm=food_name(od->results[i].cls_id);if(strcmp(nm,"人")==0)continue;int f=-1;for(int j=0;j<n;j++)if(strcmp(items[j].name,nm)==0){f=j;break;}if(f>=0)items[f].cnt++;else if(n<max){strncpy(items[n].name,nm,63);items[n].cnt=1;n++;}}return n;}
static void process_events(item_t *before,int nb,item_t *after,int na){
    for(int i=0;i<na;i++){int f=-1;for(int j=0;j<nb;j++)if(strcmp(after[i].name,before[j].name)==0){f=j;break;}int prev=f>=0?before[f].cnt:0;if(after[i].cnt>prev){int d=after[i].cnt-prev;printf("  -> 放入 %s x%d\n",after[i].name,d);db_item_update(after[i].name,d);db_event_add("put_in",after[i].name,d);}}
    for(int i=0;i<nb;i++){int f=-1;for(int j=0;j<na;j++)if(strcmp(before[i].name,after[j].name)==0){f=j;break;}int cur=f>=0?after[f].cnt:0;if(before[i].cnt>cur){int d=before[i].cnt-cur;printf("  -> 取出 %s x%d\n",before[i].name,d);db_item_update(before[i].name,-d);db_event_add("take_out",before[i].name,d);}}
}

/* ===== ISP/VI/VPSS 初始化 (内联) ===== */
static int vi_init(int w,int h){
    VI_DEV_ATTR_S da;memset(&da,0,sizeof(da));RK_MPI_VI_SetDevAttr(0,&da);RK_MPI_VI_EnableDev(0);
    VI_DEV_BIND_PIPE_S bp;bp.u32Num=1;bp.PipeId[0]=0;RK_MPI_VI_SetDevBindPipe(0,&bp);
    VI_CHN_ATTR_S ca;memset(&ca,0,sizeof(ca));ca.stIspOpt.u32BufCount=2;ca.stIspOpt.enMemoryType=VI_V4L2_MEMORY_TYPE_DMABUF;
    ca.stSize.u32Width=w;ca.stSize.u32Height=h;ca.enPixelFormat=RK_FMT_YUV420SP;ca.enCompressMode=COMPRESS_MODE_NONE;ca.u32Depth=2;
    RK_MPI_VI_SetChnAttr(0,0,&ca);RK_MPI_VI_EnableChn(0,0);return 0;
}

/* ===== 主函数 ===== */
int main(int argc,char **argv){
    if(argc<2){printf("Usage:%s <model.rknn> [server_url]\n",argv[0]);return-1;}
    const char *model_path=argv[1],*server_url=argc>2?argv[2]:SERVER_URL;

    printf("===============================================\n  冰箱食材识别系统 (v2.0 RKMPI)\n===============================================\n");
    signal(SIGINT,sig_handler);signal(SIGTERM,sig_handler);

    /* 停止rkIPC */
    printf("[Init] 停止rkIPC...\n");system("/oem/usr/bin/RkLunch-stop.sh");usleep(500000);

    /* GPIO */
    printf("[Init] GPIO...\n");gpio_export(DOOR_PIN);gpio_dir(DOOR_PIN,"in");gpio_export(RELAY_PIN);gpio_dir(RELAY_PIN,"out");gpio_write(RELAY_PIN,0);

    /* 数据库 */
    printf("[Init] 数据库...\n");db_open();

    /* RKNN */
    printf("[Init] RKNN模型...\n");
    rknn_app_context_t rknn_ctx;memset(&rknn_ctx,0,sizeof(rknn_ctx));
    if(init_yolov5_model(model_path,&rknn_ctx)<0){printf("模型加载失败!\n");return-1;}init_post_process();

    /* ISP - 直接调用rkaiq API (不依赖sample_comm) */
    printf("[Init] ISP...\n");
    rk_aiq_sys_ctx_t *aiq_ctx = NULL;
    rk_aiq_sys_ctx_t *ctx = rk_aiq_uapi2_sysctl_init("m00_b_mis5001 4-0031", IQ_DIR, NULL, NULL);
    if(ctx){aiq_ctx=ctx;rk_aiq_uapi2_sysctl_prepare(ctx,0,0,0);rk_aiq_uapi2_sysctl_start(ctx);printf("  ISP就绪\n");}
    else printf("  ISP init failed, 尝试继续...\n");

    /* RKMPI */
    printf("[Init] RKMPI...\n");RK_MPI_SYS_Init();

    /* VI */
    printf("[Init] VI...\n");vi_init(CAM_W,CAM_H);printf("  VI: %dx%d YUV420SP\n",CAM_W,CAM_H);

    /* 内存池 */
    MB_POOL_CONFIG_S pc;memset(&pc,0,sizeof(pc));pc.u64MBSize=CAM_W*CAM_H*3;pc.u32MBCnt=1;pc.enAllocType=MB_ALLOC_TYPE_DMA;
    MB_POOL pool=RK_MPI_MB_CreatePool(&pc);MB_BLK blk=RK_MPI_MB_GetMB(pool,CAM_W*CAM_H*3,RK_TRUE);
    unsigned char *pdata=(unsigned char*)RK_MPI_MB_Handle2VirAddr(blk);
    cv::Mat bgr(CAM_H,CAM_W,CV_8UC3,pdata);

    /* LCD */
    printf("[Init] LCD...\n");int fb=open(FB_DEV,O_RDWR),lcd_w=480,lcd_h=480;uint32_t *fbp=NULL;size_t fbs=0;
    if(fb>=0){struct fb_var_screeninfo vi;ioctl(fb,FBIOGET_VSCREENINFO,&vi);lcd_w=vi.xres;lcd_h=vi.yres;fbs=lcd_w*lcd_h*4;fbp=(uint32_t*)mmap(NULL,fbs,PROT_READ|PROT_WRITE,MAP_SHARED,fb,0);printf("  LCD:%dx%d\n",lcd_w,lcd_h);}

    /* 状态变量 */
    VIDEO_FRAME_INFO_S vf;object_detect_result_list od;item_t baseline[64],current[64];
    int baseline_n=0,door_was_open=0,light_on=0,door_closed=1,frame_cnt=0;
    time_t last_sync=0;char text[64];

    printf("\n[Main] 就绪 - 关门休眠模式\n");

    /* ===== 主循环 ===== */
    while(running){
        /* 读门磁: 高电平=门关 */
        door_closed=gpio_read(DOOR_PIN);

        /* === 门打开 → 启动检测 === */
        if(!door_closed && !door_was_open){
            printf("\n>>> 冰箱门打开! 开灯+启动AI...\n");door_was_open=1;
            gpio_write(RELAY_PIN,1);light_on=1;
        }

        /* === 门开着 → 持续AI检测 === */
        if(!door_closed){
            /* 取帧 (阻塞等ISP出帧) */
            if(RK_MPI_VI_GetChnFrame(0,0,&vf,-1)!=RK_SUCCESS){usleep(5000);continue;}

            /* YUV420SP(NV12) -> BGR (修复颜色: 使用COLOR_YUV2BGR_NV12) */
            void *vd=RK_MPI_MB_Handle2VirAddr(vf.stVFrame.pMbBlk);
            cv::Mat yuv(CAM_H+CAM_H/2,CAM_W,CV_8UC1,vd);
            cv::cvtColor(yuv,bgr,cv::COLOR_YUV2BGR_NV12);

            /* Letterbox + 推理 */
            cv::Mat lb;letterbox(bgr,lb);
            memcpy(rknn_ctx.input_mems[0]->virt_addr,lb.data,MODEL_W*MODEL_H*3);
            inference_yolov5_model(&rknn_ctx,&od);

            /* 绘制检测框 */
            for(int i=0;i<od.count;i++){object_detect_result *d=&od.results[i];int x1=d->box.left,y1=d->box.top,x2=d->box.right,y2=d->box.bottom;unletterbox(&x1,&y1);unletterbox(&x2,&y2);printf("%s @(%d %d %d %d)%.3f\n",food_name(d->cls_id),x1,y1,x2,y2,d->prop);cv::rectangle(bgr,cv::Point(x1,y1),cv::Point(x2,y2),cv::Scalar(0,255,0),2);snprintf(text,sizeof(text),"%s %.1f%%",food_name(d->cls_id),d->prop*100);cv::putText(bgr,text,cv::Point(x1,y1-5),cv::FONT_HERSHEY_SIMPLEX,0.4,cv::Scalar(0,255,0),1);}

            /* LCD显示视频 */
            if(fb>=0){cv::Mat bgra;cv::cvtColor(bgr,bgra,cv::COLOR_BGR2BGRA);cv::Mat lcd;cv::resize(bgra,lcd,cv::Size(lcd_w,lcd_h),0,0,cv::INTER_LINEAR);memcpy(fbp,lcd.data,fbs);}

            /* 记录开门基准 */
            if(baseline_n==0)baseline_n=parse_dets(baseline,64,&od);

            RK_MPI_VI_ReleaseChnFrame(0,0,&vf);frame_cnt++;
            if(frame_cnt%30==0)printf("[Frame %d]\n",frame_cnt);

            /* 状态更新 */
            db_status_update("open","on",cpu_temp());
            if(time(NULL)-last_sync>=SYNC_INTERVAL){char *j=db_build_json();if(j){http_post(server_url,j);free(j);}last_sync=time(NULL);}
        }

        /* === 门关了(from open) → 处理事件 → 休眠 === */
        if(door_closed && door_was_open){
            printf(">>> 冰箱门关闭! 关灯+对比画面...\n");door_was_open=0;
            gpio_write(RELAY_PIN,0);light_on=0;

            /* 最后取一帧作为关门后画面 */
            int after_n=0;item_t after[64];
            if(RK_MPI_VI_GetChnFrame(0,0,&vf,100)==RK_SUCCESS){
                void *vd=RK_MPI_MB_Handle2VirAddr(vf.stVFrame.pMbBlk);
                cv::Mat yuv(CAM_H+CAM_H/2,CAM_W,CV_8UC1,vd);
                cv::cvtColor(yuv,bgr,cv::COLOR_YUV2BGR_NV12);
                cv::Mat lb;letterbox(bgr,lb);
                memcpy(rknn_ctx.input_mems[0]->virt_addr,lb.data,MODEL_W*MODEL_H*3);
                inference_yolov5_model(&rknn_ctx,&od);
                after_n=parse_dets(after,64,&od);
                RK_MPI_VI_ReleaseChnFrame(0,0,&vf);
            }

            printf("[对比] 开门前:%d种, 关门后:%d种\n",baseline_n,after_n);
            process_events(baseline,baseline_n,after,after_n);
            baseline_n=0;

            char *j=db_build_json();if(j){http_post(server_url,j);free(j);}
            last_sync=time(NULL);
            printf("[Main] 回到休眠省电模式\n\n");
        }

        /* === 门关着 → 低功耗休眠 === */
        if(door_closed && !door_was_open){
            db_status_update("closed","off",cpu_temp());
            if(time(NULL)-last_sync>=SYNC_INTERVAL){char *j=db_build_json();if(j){http_post(server_url,j);free(j);}last_sync=time(NULL);}
            sleep(1);
        }
    }

    /* 清理 */
    printf("[Exit] 清理...\n");gpio_write(RELAY_PIN,0);
    if(fb>=0){munmap(fbp,fbs);close(fb);}
    RK_MPI_MB_ReleaseMB(blk);RK_MPI_MB_DestroyPool(pool);
    RK_MPI_VI_DisableChn(0,0);RK_MPI_VI_DisableDev(0);
    if(aiq_ctx)rk_aiq_uapi2_sysctl_stop(aiq_ctx);
    RK_MPI_SYS_Exit();deinit_post_process();release_yolov5_model(&rknn_ctx);
    sqlite3_close(g_db);printf("[Exit] 完成\n");return 0;
}
