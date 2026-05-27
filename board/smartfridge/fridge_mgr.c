/**
 * fridge_mgr - 冰箱管理程序
 * 状态机: IDLE(关门休眠) <-> DETECTING(开门检测)
 * 编译: 在WSL中用Luckfox SDK交叉编译 (无RKMPI/OpenCV依赖)
 *   export LUCKFOX_SDK_PATH=~/luckfox-pico
 *   arm-rockchip830-linux-uclibcgnueabihf-gcc -Os -o fridge_mgr fridge_mgr.c sqlite3.c -lpthread -ldl -lm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include "sqlite3.h"

/* ===== 配置 ===== */
#define SERVER_URL       "http://192.168.2.100:5000"
#define DOOR_PIN         32
#define RELAY_PIN        40
#define DB_PATH          "/root/smartfridge/fridge.db"
#define AI_BIN           "/root/smartfridge/bin/fridge_ai"
#define AI_MODEL         "/root/smartfridge/model/yolov5.rknn"
#define DET_FILE         "/tmp/fridge_detections.json"
#define FB_DEV           "/dev/fb0"
#define SYNC_INTERVAL    2

/* ===== GPIO sysfs ===== */
static int g_export(int p) { int f=open("/sys/class/gpio/export",O_WRONLY); if(f<0)return -1; char b[16]; int n=snprintf(b,sizeof(b),"%d",p); write(f,b,n); close(f); usleep(100000); return 0; }
static void g_unexport(int p) { int f=open("/sys/class/gpio/unexport",O_WRONLY); if(f<0)return; char b[16]; int n=snprintf(b,sizeof(b),"%d",p); write(f,b,n); close(f); }
static void g_dir(int p, const char *d) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/direction",p); int f=open(path,O_WRONLY); if(f>=0){write(f,d,strlen(d));close(f);} }
static int g_read(int p) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/value",p); int f=open(path,O_RDONLY); if(f<0)return 1; char v; read(f,&v,1); close(f); return v=='1'; }
static void g_write(int p, int v) { char path[64]; snprintf(path,sizeof(path),"/sys/class/gpio/gpio%d/value",p); int f=open(path,O_WRONLY); if(f>=0){write(f,v?"1":"0",1);close(f);} }

/* ===== 数据库 (SQLite 已编译进二进制) ===== */
static sqlite3 *db = NULL;
static void db_init(void) {
    sqlite3_open(DB_PATH, &db);
    sqlite3_exec(db,
        "CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, count INTEGER DEFAULT 1, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, action TEXT, food_name TEXT, count INTEGER DEFAULT 1);"
        "CREATE TABLE IF NOT EXISTS status(id INTEGER PRIMARY KEY CHECK(id=1), door_state TEXT, light_state TEXT, cpu_temp REAL, updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "INSERT OR IGNORE INTO status(id,door_state,light_state) VALUES(1,'closed','off');",
        NULL,NULL,NULL);
}
static void db_inventory_update(const char *name, int delta) {
    sqlite3_stmt *s;
    sqlite3_prepare_v2(db,"SELECT id,count FROM inventory WHERE name=?",-1,&s,NULL);
    sqlite3_bind_text(s,1,name,-1,SQLITE_STATIC);
    if(sqlite3_step(s)==SQLITE_ROW){int id=sqlite3_column_int(s,0),c=sqlite3_column_int(s,1)+delta; sqlite3_finalize(s);
        if(c<=0){char sql[64];snprintf(sql,sizeof(sql),"DELETE FROM inventory WHERE id=%d",id);sqlite3_exec(db,sql,NULL,NULL,NULL);}
        else{char sql[128];snprintf(sql,sizeof(sql),"UPDATE inventory SET count=%d,last_updated=CURRENT_TIMESTAMP WHERE id=%d",c,id);sqlite3_exec(db,sql,NULL,NULL,NULL);}}
    else{sqlite3_finalize(s);if(delta>0){sqlite3_prepare_v2(db,"INSERT INTO inventory(name,count) VALUES(?,?)",-1,&s,NULL);sqlite3_bind_text(s,1,name,-1,SQLITE_STATIC);sqlite3_bind_int(s,2,delta);sqlite3_step(s);sqlite3_finalize(s);}}
}
static void db_event_add(const char *action, const char *food, int count) {
    sqlite3_stmt *s;
    sqlite3_prepare_v2(db,"INSERT INTO events(action,food_name,count) VALUES(?,?,?)",-1,&s,NULL);
    sqlite3_bind_text(s,1,action,-1,SQLITE_STATIC);sqlite3_bind_text(s,2,food,-1,SQLITE_STATIC);sqlite3_bind_int(s,3,count);
    sqlite3_step(s);sqlite3_finalize(s);
}
static void db_status_update(const char *door, const char *light, float temp) {
    char sql[256];snprintf(sql,sizeof(sql),"UPDATE status SET door_state='%s',light_state='%s',cpu_temp=%.1f,updated=CURRENT_TIMESTAMP WHERE id=1",door,light,temp);
    sqlite3_exec(db,sql,NULL,NULL,NULL);
}
static char *db_build_sync_json(void) {
    char *j=malloc(8192);int l=0;l+=snprintf(j+l,8192-l,"{\"inventory\":[");
    sqlite3_stmt *s;int first=1;
    sqlite3_prepare_v2(db,"SELECT name,category,count,first_seen,last_updated FROM inventory ORDER BY last_updated DESC",-1,&s,NULL);
    while(sqlite3_step(s)==SQLITE_ROW){if(!first)l+=snprintf(j+l,8192-l,",");l+=snprintf(j+l,8192-l,"{\"name\":\"%s\",\"category\":\"%s\",\"count\":%d,\"first_seen\":\"%s\",\"last_updated\":\"%s\"}",sqlite3_column_text(s,0),sqlite3_column_text(s,1)?(char*)sqlite3_column_text(s,1):"",sqlite3_column_int(s,2),sqlite3_column_text(s,3)?(char*)sqlite3_column_text(s,3):"",sqlite3_column_text(s,4)?(char*)sqlite3_column_text(s,4):"");first=0;}
    sqlite3_finalize(s);
    l+=snprintf(j+l,8192-l,"],\"events\":[");
    sqlite3_prepare_v2(db,"SELECT id,timestamp,action,food_name,count FROM events ORDER BY id ASC LIMIT 100",-1,&s,NULL);first=1;
    while(sqlite3_step(s)==SQLITE_ROW){if(!first)l+=snprintf(j+l,8192-l,",");l+=snprintf(j+l,8192-l,"{\"id\":%d,\"timestamp\":\"%s\",\"action\":\"%s\",\"food_name\":\"%s\",\"count\":%d}",sqlite3_column_int(s,0),sqlite3_column_text(s,1),sqlite3_column_text(s,2),sqlite3_column_text(s,3),sqlite3_column_int(s,4));first=0;}
    sqlite3_finalize(s);
    l+=snprintf(j+l,8192-l,"],\"hardware_status\":{");
    sqlite3_prepare_v2(db,"SELECT door_state,light_state,cpu_temp,updated FROM status WHERE id=1",-1,&s,NULL);
    if(sqlite3_step(s)==SQLITE_ROW)l+=snprintf(j+l,8192-l,"\"door_state\":\"%s\",\"light_state\":\"%s\",\"cpu_temp\":%.1f,\"updated_at\":\"%s\"",sqlite3_column_text(s,0),sqlite3_column_text(s,1),sqlite3_column_double(s,2),sqlite3_column_text(s,3)?(char*)sqlite3_column_text(s,3):"");
    sqlite3_finalize(s);l+=snprintf(j+l,8192-l,"}}");return j;
}

/* ===== HTTP POST ===== */
static int http_post(const char *url, const char *path, const char *json) {
    char host[128]={0},api[256]={0};int port=80;
    const char *p=url+7,*s=strchr(p,'/'),*c=strchr(p,':');
    if(c&&(!s||c<s)){memcpy(host,p,c-p);port=atoi(c+1);}else if(s){memcpy(host,p,s-p);}else{strcpy(host,p);}
    strcpy(api,path);
    int sock=socket(AF_INET,SOCK_STREAM,0);if(sock<0)return -1;
    struct timeval tv={2,0};setsockopt(sock,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));setsockopt(sock,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
    struct sockaddr_in addr={0};addr.sin_family=AF_INET;addr.sin_port=htons(port);
    if(inet_pton(AF_INET,host,&addr.sin_addr)<=0){struct hostent *he=gethostbyname(host);if(!he){close(sock);return -1;}memcpy(&addr.sin_addr,he->h_addr_list[0],he->h_length);}
    if(connect(sock,(struct sockaddr*)&addr,sizeof(addr))<0){close(sock);return -1;}
    char req[16384];int rlen=snprintf(req,sizeof(req),"POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",api,host,(int)strlen(json),json);
    send(sock,req,rlen,0);char resp[512];recv(sock,resp,sizeof(resp)-1,0);close(sock);return 0;
}

/* ===== 简易JSON解析: 提取name字段 ===== */
typedef struct { char name[64]; int count; } det_item_t;
static int parse_detections(det_item_t *items, int max_items) {
    FILE *fp=fopen(DET_FILE,"r");if(!fp)return 0;
    char buf[8192]={0};fread(buf,1,sizeof(buf)-1,fp);fclose(fp);
    int n=0;char *p=buf;
    while((p=strstr(p,"\"name\":\""))&&n<max_items){
        p+=8;char nm[64]={0};int i=0;while(*p&&*p!='"'&&i<63)nm[i++]=*p++;
        if(!nm[0]||strcmp(nm,"人")==0)continue;
        int found=-1;for(int j=0;j<n;j++)if(strcmp(items[j].name,nm)==0){found=j;break;}
        if(found>=0)items[found].count++;else{strncpy(items[n].name,nm,63);items[n].count=1;n++;}
    }
    return n;
}

/* ===== 获取CPU温度 ===== */
static float cpu_temp(void){FILE *f=fopen("/sys/class/thermal/thermal_zone0/temp","r");if(!f)return 0;float t;fscanf(f,"%f",&t);fclose(f);return t/1000.0f;}

/* ===== 信号处理 ===== */
static volatile int running=1;
static void sig_handler(int s){running=0;}

/* ===== 启动AI子进程 ===== */
static pid_t ai_pid=0;
static void ai_start(void){
    if(ai_pid>0)return;
    pid_t p=fork();
    if(p==0){/* 子进程 */
        char *argv[]={"fridge_ai",AI_MODEL,NULL};
        setenv("LD_LIBRARY_PATH","/oem/usr/lib:/usr/lib",1);
        freopen("/root/smartfridge/logs/ai.log","w",stdout);
        freopen("/root/smartfridge/logs/ai.log","a",stderr);
        execv(AI_BIN,argv);exit(1);
    }else if(p>0){ai_pid=p;printf("[Mgr] AI进程启动 PID=%d\n",p);}
}
static void ai_stop(void){
    if(ai_pid<=0)return;
    kill(ai_pid,SIGTERM);int st;waitpid(ai_pid,&st,0);ai_pid=0;
    /* 额外确认杀掉 */
    killall("fridge_ai",SIGTERM);
    printf("[Mgr] AI进程已停止\n");
}
static void killall(const char *name, int sig){
    /* 简化版killall */
    char cmd[128];snprintf(cmd,sizeof(cmd),"killall -%d %s 2>/dev/null",sig,name);
    system(cmd);
}

/* ===== 检测画面变化 -> 生成事件 ===== */
static void process_events(det_item_t *before, int n_before, det_item_t *after, int n_after) {
    /* 新增的 -> put_in */
    for(int i=0;i<n_after;i++){
        int found=-1;for(int j=0;j<n_before;j++)if(strcmp(after[i].name,before[j].name)==0){found=j;break;}
        int prev_cnt=found>=0?before[found].count:0;
        if(after[i].count>prev_cnt){
            int delta=after[i].count-prev_cnt;
            printf("  -> 放入 %s x%d\n",after[i].name,delta);
            db_inventory_update(after[i].name,delta);
            db_event_add("put_in",after[i].name,delta);
        }
    }
    /* 消失的 -> take_out */
    for(int i=0;i<n_before;i++){
        int found=-1;for(int j=0;j<n_after;j++)if(strcmp(before[i].name,after[j].name)==0){found=j;break;}
        int cur_cnt=found>=0?after[found].count:0;
        if(before[i].count>cur_cnt){
            int delta=before[i].count-cur_cnt;
            printf("  -> 取出 %s x%d\n",before[i].name,delta);
            db_inventory_update(before[i].name,-delta);
            db_event_add("take_out",before[i].name,delta);
        }
    }
}

/* ===== 主函数 ===== */
int main(int argc, char **argv){
    const char *server_url=argc>1?argv[1]:SERVER_URL;
    printf("===== 冰箱管理系统启动 =====\n");
    signal(SIGINT,sig_handler);signal(SIGTERM,sig_handler);
    signal(SIGCHLD,SIG_IGN);/* 防止僵尸进程 */

    /* GPIO初始化 */
    g_export(DOOR_PIN);g_dir(DOOR_PIN,"in");
    g_export(RELAY_PIN);g_dir(RELAY_PIN,"out");g_write(RELAY_PIN,0);
    printf("[Init] GPIO OK (门磁=%d, 继电器=%d)\n",DOOR_PIN,RELAY_PIN);

    /* 数据库 */
    db_init();printf("[Init] 数据库 OK\n");

    /* 状态初始化 */
    int door_was_open=0, light_on=0;
    det_item_t baseline[64];int baseline_n=0;
    time_t last_sync=0;

    /* 先杀干净旧AI进程 */
    killall("fridge_ai",SIGKILL);
    system("/oem/usr/bin/RkLunch-stop.sh 2>/dev/null");usleep(500000);

    printf("[Main] 进入主循环 (当前: 休眠省电模式)\n");

    while(running){
        int door_open=!g_read(DOOR_PIN);/* 高电平=门关 */

        /* ===== IDLE -> DETECTING: 门打开 ===== */
        if(door_open && !door_was_open){
            printf("\n>>> 冰箱门打开! 开灯 + 启动检测...\n");
            door_was_open=1;

            /* 开灯 */
            g_write(RELAY_PIN,1);light_on=1;

            /* 记录开门前基准画面 */
            baseline_n=parse_detections(baseline,64);
            printf("[Baseline] 开门前检测到 %d 种食材\n",baseline_n);

            /* 启动AI进程 */
            ai_start();
            sleep(1);/* 等AI启动 */
        }

        /* ===== DETECTING: 门开着, 持续检测中 ===== */
        if(door_open){
            /* 定期同步状态到服务器 */
            if(time(NULL)-last_sync>=SYNC_INTERVAL){
                char *j=db_build_sync_json();
                if(j){http_post(server_url,"/api/sync",j);free(j);}
                last_sync=time(NULL);
            }
            db_status_update("open","on",cpu_temp());
            usleep(200000);/* 200ms检查一次 */
            continue;
        }

        /* ===== DETECTING -> IDLE: 门关了 ===== */
        if(!door_open && door_was_open){
            printf(">>> 冰箱门关闭! 关灯 + 对比画面变化...\n");
            door_was_open=0;

            /* 关灯 */
            g_write(RELAY_PIN,0);light_on=0;

            /* 停止AI */
            ai_stop();usleep(500000);

            /* 读取关门后画面 */
            det_item_t after[64];int after_n=parse_detections(after,64);
            printf("[Result] 关门后检测到 %d 种食材\n",after_n);

            /* 对比变化 -> 更新数据库 */
            process_events(baseline,baseline_n,after,after_n);

            /* 立即同步 */
            char *j=db_build_sync_json();
            if(j){http_post(server_url,"/api/sync",j);free(j);}
            last_sync=time(NULL);

            printf("[Main] 回到休眠省电模式\n\n");
        }

        /* ===== IDLE: 门关着, 低功耗运行 ===== */
        if(!door_open){
            db_status_update("closed","off",cpu_temp());
            if(time(NULL)-last_sync>=SYNC_INTERVAL){
                char *j=db_build_sync_json();
                if(j){http_post(server_url,"/api/sync",j);free(j);}
                last_sync=time(NULL);
            }
            sleep(1);/* 休眠时1秒检查一次 */
        }
    }

    /* 清理 */
    g_write(RELAY_PIN,0);ai_stop();
    g_unexport(DOOR_PIN);g_unexport(RELAY_PIN);
    sqlite3_close(db);
    printf("===== 冰箱管理系统已停止 =====\n");
    return 0;
}
