# SmartFridge AI二次开发文档

> 本文档写给AI/开发者，用于快速理解项目当前状态、已做决策、遗留问题，便于接手继续开发。

---

## 1. 项目快照 (Snapshot: 2024-05-24)

### 1.1 当前状态

| 组件 | 状态 | 位置 |
|------|------|------|
| AI推理程序 (C++) | ✅ 正常工作 | `deploy/fridge_ai` |
| 管理程序 (Python) | ✅ 正常工作 | `deploy/fridge_mgr.py` |
| LCD界面 (Python) | ✅ 正常工作 | `deploy/lcd_ui.py` |
| Flask服务器 | ✅ 正常工作 | `server/app.py` |
| Web管理页面 | ✅ 正常工作 | `server/templates/index.html` |
| YOLOv5模型 | ✅ 已部署 | `deploy/yolov5.rknn` (4MB) |
| 触摸屏 | ✅ 正常工作 | MT Protocol B, GT911 |
| 视频流 | ✅ 正常（开门时） | RTSP 554端口 |
| 门磁传感器 | ✅ 正常工作 | GPIO32, 0=开门 |
| 继电器/灯 | ✅ 正常工作 | GPIO40, 1=亮 |
| 公网访问 | ✅ localtunnel可用 | 随机子域名 |

### 1.2 部署位置

- **开发板IP**: 192.168.2.77 (root / luckfox)
- **部署根目录**: `/root/smartfridge/`
- **Web页面**: `http://127.0.0.1:5000` (本地) / `http://192.168.2.75:5000` (局域网) / `https://xxxxxx.loca.lt` (公网)

---

## 2. 架构决策记录 (Architecture Decision Records)

### ADR-1: 为什么AI程序用C++而不是Python？

**决策**: C++编写AI推理部分（摄像头+NPU+RTSP），Python编写管理逻辑。

**原因**:
- RKMPI和RKNN的C API更稳定、资源占用更小
- NPU推理需要在进程内使用rknn_context，Python封装可能有性能损失
- 256MB内存紧张，C程序约30MB，Python约20MB，各司其职刚好够
- OpenCV C++版支持NV12颜色转换（cv::COLOR_YUV2RGB_NV12），Python版不一定有

### ADR-2: 为什么门关时停止AI？

**决策**: 门关闭后kill掉fridge_ai进程。

**原因**:
- 省电：NPU持续推理耗电约1-2W
- 省内存：释放NPU和摄像头占用的DMA内存
- 避免LCD冲突：关门时Python接管屏幕显示UI
- 用户明确要求：门关着不需要视频流

**代价**: 门关着时浏览器看不到视频（可通过显示"门关闭"占位解决）

### ADR-3: LCD互斥锁机制 (/tmp/fb_lock)

**决策**: 用文件锁 `/tmp/fb_lock` 协调AI程序和Python程序的LCD写入。

**原因**:
- 两个进程都写 `/dev/fb0` 会导致画面撕裂
- 进程间信号量/共享内存太复杂
- 文件锁简单可靠，Python和C都能轻松检测

**规则**:
- Python画UI前检查 `/tmp/fb_lock` 不存在 → 创建锁 → 画完
- 门打开时Python删掉锁 → AI可以写摄像头画面
- AI每帧检查锁是否存在 → 存在就跳过写屏

### ADR-4: 为什么选localtunnel而不是ngrok做公网隧道？

**决策**: 优先本地localtunnel，备选ngrok。

**原因**:
- localtunnel免费、无需注册、npm一键安装
- ngrok需要注册token（设置了NGROK_AUTHTOKEN环境变量就会用）
- localtunnel缺点：每次重启地址会变

**代码逻辑** (`server/app.py:209-236`):
```python
if ngrok_token:
    try ngrok
if not public_url:
    try localtunnel
```

### ADR-5: 视频代理用MJPEG快照轮询而非MJPEG流

**决策**: 前端用 `<img>` 标签每150ms轮询 `/api/snapshot`，而非MJPEG流。

**原因**:
- MJPEG流在buildroot系统中兼容性差
- 快照方案可以加 `Cache-Control: no-cache` 确保不缓存旧帧
- ffmpeg转RTSP→MJPEG管道输出，逐帧解析，已经有逐帧数据

### ADR-6: 事件检测用开门前后对比而非连续帧追踪

**决策**: 记录baseline(开门时识别结果) → 关门时对比after → 计算diff。

**原因**:
- RV1106单核CPU无法承受实时帧追踪
- 冰箱场景物体是静态的（放进去就不会动）
- 简单对比已经足够区分放入/取出

**局限**: 如果用户开门→放苹果→拿香蕉→关门，会被识别为 "放入苹果, 取出香蕉"，而非两次独立事件。这是可接受的折衷。

---

## 3. 完整文件清单

### 3.1 部署到开发板的文件 (deploy/)

| 文件 | 大小 | 作用 | 修改频率 |
|------|------|------|----------|
| `fridge_ai` | 3.9MB | AI推理二进制(C++编译) | 改C代码后重新编译 |
| `yolov5.rknn` | ~4MB | YOLOv5n INT8量化模型 | 换模型时更新 |
| `fridge_mgr.py` | ~7KB | 管理主程序 | 高频修改 |
| `lcd_ui.py` | ~11KB | LCD界面+触摸 | 高频修改 |
| `start.sh` | ~900B | 开发板启动脚本 | 低频 |
| `stop.sh` | ~500B | 停止脚本 | 低频 |
| `test_door.sh` | ~200B | 门磁测试 | 仅调试用 |

### 3.2 源码文件 (WSL中, 不需要部署到开发板)

| 路径 | 作用 |
|------|------|
| `\\wsl$\Ubuntu-22.04\home\taki\luckfox_pico_rkmpi_example\example\luckfox_pico_rtsp_yolov5\src\main.cc` | C++主源文件 |
| `\\wsl$\Ubuntu-22.04\home\taki\luckfox-pico\` | Luckfox SDK |

### 3.3 服务器文件 (server/)

| 文件 | 作用 |
|------|------|
| `app.py` | Flask应用主文件 |
| `templates/index.html` | Web管理页面(单文件,含CSS+JS) |
| `server_fridge.db` | SQLite数据库(自动生成) |

### 3.4 早期设计文件 (board/, 仅供参考)

`board/` 目录下的文件是项目早期设计阶段的原型代码，**不是当前部署版本**。当前实际使用的是：
- `deploy/fridge_mgr.py`（而非 `board/fridge_manager/main.py`）
- `deploy/lcd_ui.py`（而非 `board/fridge_manager/display_ui.py`）
- `deploy/fridge_ai`（基于luckfox官方示例的main.cc，而非 `board/fridge_ai/main.c`）

---

## 4. 关键文件详解

### 4.1 main.cc (C++ AI程序)

**源码位置**: `\\wsl$\Ubuntu-22.04\home\taki\luckfox_pico_rkmpi_example\example\luckfox_pico_rtsp_yolov5\src\main.cc`

**关键常量**:
```cpp
#define CAM_WIDTH   720    // 摄像头采集宽度
#define CAM_HEIGHT  480    // 摄像头采集高度
#define LCD_WIDTH   480    // 屏幕宽度
#define LCD_HEIGHT  480    // 屏幕高度
#define DET_FILE    "/tmp/fridge_detections.json"
int model_width = 640;     // YOLO输入尺寸
int model_height = 640;
```

**主要流程** (main函数, 179-267行):
```
while(1):
    1. VI取帧 (NV12格式, 720x480)
    2. NV12 → RGB (cv::COLOR_YUV2RGB_NV12)
    3. letterbox缩放 (720x480 → 640x640, 等比例+黑边)
    4. NPU推理 (YOLOv5)
    5. 结果画框标注 (cv::rectangle + cv::putText)
    6. 写检测JSON (/tmp/fridge_detections.json)
    7. VENC编码送帧 (RGB → H264)
    8. 获取H264码流 → RTSP推流
    9. 如果 /tmp/fb_lock 不存在: RGB→BGR→BGRA → 缩放480x480 → 写/dev/fb0
   10. 释放VI帧 + VENC码流
```

**颜色管线 (重要!)**:
```
NV12 → COLOR_YUV2RGB_NV12 → RGB (pool_data)
  → YOLO推理 (直接用RGB)
  → VENC (RK_FMT_RGB888, 即RGB)
  → LCD: RGB → BGR (cvtColor RGB2BGR) → BGRA (cvtColor BGR2BGRA) → fb0
```

**letterbox函数** (37-56行):
- 等比缩放摄像头画面到640×640
- 不够的部分填黑色
- 记录scale, leftPadding, topPadding用于坐标反算
- `mapCoordinates()` 函数(58-63行): 将640×640空间坐标映射回720×480

**write_detections_json** (65-82行):
- 写入 `/tmp/fridge_detections.json.tmp` 然后原子rename
- 过滤掉 "person" 类别（不把人的手识别为食材）
- 格式: `{"detections":[{"name":"apple","confidence":0.950}, ...]}`

### 4.2 fridge_mgr.py (Python管理程序)

**核心状态机**:

```
IDLE (门关, AI停, UI显示)
  │
  ├─ 检测到 door_open → 状态切换到 DETECTING
  │    ├─ 继电器通电(灯亮)
  │    ├─ ai_start() → fork fridge_ai进程
  │    ├─ baseline = parse_detections() (记录开门时有哪些食材)
  │    └─ 持续loop检测door状态, 每2秒http_sync
  │
  ├─ 检测到 door_close (从DETECTING切换) → 状态切换到 PROCESSING
  │    ├─ after = parse_detections() (关门时有哪些食材)
  │    ├─ process_events(baseline, after) (对比得出放入/取出)
  │    ├─ ai_stop() → kill fridge_ai进程
  │    ├─ 继电器断电(灯灭)
  │    ├─ save_data() + draw_lcd() + http_sync()
  │    └─ 回到 IDLE
  │
  └─ IDLE中的例行工作:
       ├─ handle_touch_events() (触摸事件处理)
       ├─ 每1.5秒 draw_lcd() (刷新UI)
       └─ 每2秒 http_sync() (同步数据到服务器)
```

**关键函数**:

| 函数 | 行号 | 作用 |
|------|------|------|
| `load_data()` | 22-27 | 从JSON文件加载本地数据 |
| `save_data()` | 29-31 | 保存数据到JSON（原子写） |
| `inv_update()` | 33-42 | 更新库存（增减数量/增删物品） |
| `evt_add()` | 44-46 | 添加事件记录 |
| `http_sync()` | 48-56 | HTTP POST全量同步到服务器 |
| `parse_detections()` | 59-69 | 读取AI写的JSON, 按名称聚合计次 |
| `ai_start()` | 71-77 | 启动AI子进程 |
| `ai_stop()` | 79-88 | 停止AI子进程 |
| `process_events()` | 96-106 | 对比开门前后, 生成放入/取出事件 |
| `cpu_temp()` | 108-111 | 读取CPU温度 |

**GPIO接线约定**:
```python
DOOR_PIN = 32   # 门磁: 0=开门(磁铁远离), 1=关门(磁铁靠近)
RELAY_PIN = 40  # 继电器: True=通电=灯亮, False=断电=灯灭
```

**HTTP同步目标**:
```python
SERVER_URL = "http://192.168.2.75:5000"  # Flask服务器IP
SYNC_INTERVAL = 2  # 每2秒同步一次
```

### 4.3 lcd_ui.py (LCD界面)

**配色方案** (第11-19行):
```python
BLACK  = (0, 0, 0)        # 纯黑背景
WHITE  = (255, 255, 255)  # 白色文字
GREEN  = (0, 255, 150)    # 荧光绿强调色
BLUE   = (60, 160, 255)   # 蓝色标签
RED    = (255, 60, 80)    # 红色警告/删除
YELLOW = (255, 210, 40)   # 黄色状态显示
GRAY   = (140, 140, 150)  # 灰色辅助文字
DGRAY  = (40, 40, 50)     # 深灰面板背景
LGRAY  = (25, 25, 35)     # 浅灰顶栏背景
```

**界面布局**:
```
┌──────────────────────────────────┐ y=0
│ SMART FRIDGE    OPEN/CLOSED 65°C │ h=58 (LGRAY背景+绿线)
├──────────────────────────────────┤
│ INVENTORY               [数量]   │ h=28 (DGRAY标题栏)
│ [1] 苹果          x3  +1 -1 X   │ h=48 (交替黑白行)
│ [2] 牛奶          x1  +1 -1 X   │
│ [3] 香蕉          x2  +1 -1 X   │
│ ...                              │
├──────────────────────────────────┤ y=分隔线
│ ACTIVITY LOG           [数量]    │
│ IN  苹果 x3                 X    │ h=38 (交替)
│ OUT 牛奶 x1                 X    │
├──────────────────────────────────┤
│ >> DETECTING <<     Luckfox...   │ h=26 (底部状态栏)
└──────────────────────────────────┘
```

**触摸处理** (handle_touch_events, 192-222行):

使用Linux MT协议B解析触摸事件:
```
事件结构体 (16字节):
  tv_sec(4) + tv_usec(4) + type(2) + code(2) + value(4)

关键事件:
  type=3, code=53 (ABS_MT_POSITION_X) → X坐标(0-480)
  type=3, code=54 (ABS_MT_POSITION_Y) → Y坐标(0-480)
  type=3, code=57 (ABS_MT_TRACKING_ID) → -1=抬起, >=0=按下
  type=0, code=0  (SYN_REPORT) → 一帧报文的结束标志
  type=1, code=330 (BTN_TOUCH) → 备用判断
```

触摸文件描述符**持久化**：全局 `_touch_fd` 变量，避免每次事件循环重新打开设备（会导致事件丢失）。

**触摸区域定义** (_touch_zones):
```
每个zone: (x, y, width, height, action_string, *args)

例: (W-118, y+6, 30, 32, "inv_inc", idx)
    → 区域坐标+大小, 动作类型"inv_inc", 参数idx(物品索引)
```

支持的触摸动作 (_do_action, 224-246行):
| 动作 | 参数 | 说明 |
|------|------|------|
| `add_dialog` | - | 弹出食材选择面板 |
| `close_dialog` | - | 关闭弹窗 |
| `add_item_name` | name | 从弹窗添加指定食材 |
| `inv_inc` | idx | 食材数量+1 |
| `inv_dec` | idx | 食材数量-1 |
| `inv_del` | idx | 删除该食材 |
| `evt_del` | idx | 删除该事件 |

**已知的12种可选食材** (FOOD_NAMES):
```python
["苹果","香蕉","橙子","西兰花","胡萝卜","三明治","披萨","蛋糕",
 "瓶装饮品","热狗","甜甜圈","杯子","牛奶","鸡蛋","面包","奶酪"]
```

### 4.4 app.py (Flask服务器)

**API路由表**:

| 路由 | 方法 | 作用 |
|------|------|------|
| `/` | GET | Web管理页面 |
| `/api/dashboard` | GET | 返回库存+事件+状态全量数据 |
| `/api/edit` | POST | 编辑数据库(增/删/改) |
| `/api/sync` | POST | 开发板全量同步数据 |
| `/api/video` | GET | MJPEG视频流(仅供参考,主要用snapshot) |
| `/api/snapshot` | GET | 最新一帧JPEG快照 |
| `/api/ping` | GET | 健康检查 |

**数据库表结构** (`server_fridge.db`):

```sql
-- 食材库存
CREATE TABLE inventory(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    category TEXT DEFAULT '',
    count INTEGER DEFAULT 1,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 操作事件
CREATE TABLE events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action TEXT,           -- 'put_in' / 'take_out' / 'manual_add' / 'manual_del'
    food_name TEXT,
    count INTEGER DEFAULT 1
);

-- 硬件状态
CREATE TABLE status(
    id INTEGER PRIMARY KEY CHECK(id=1),
    door_state TEXT DEFAULT 'closed',   -- 'open' / 'closed'
    light_state TEXT DEFAULT 'off',     -- 'on' / 'off'
    cpu_temp REAL DEFAULT 0,
    updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**/api/sync 逻辑** (128-149行):
- 接收开发板POST的全量数据
- 先 `DELETE FROM inventory` 再逐条INSERT（全量替换）
- 同样 `DELETE FROM events` 再逐条INSERT
- 更新status表
- 这意味着：**服务器的数据是开发板数据的镜像，不是合并**

**ffmpeg视频代理** (44-76行):
```
RTSP(rtsp://192.168.2.77/live/0)
  → ffmpeg (tcp传输, 8fps, 缩放到640宽, mjpeg编码)
  → pipe:1 stdout
  → Python逐帧解析JPEG (找FF D8 ... FF D9)
  → 存到全局变量 mjpeg_frame (线程安全用锁保护)
  → /api/snapshot 返回最新帧
```

ffmpeg进程自动重启：如果ffmpeg退出（如RTSP断连），3秒后自动重连。

**公网隧道** (209-236行):
1. 如果有 `NGROK_AUTHTOKEN` 环境变量 → 用pyngrok
2. 否则尝试 localtunnel (需 `npm i -g localtunnel`)
3. 都不行就只提供局域网访问

### 4.5 index.html (Web管理页面)

**技术方案**:
- 纯原生HTML+CSS+JS, 无框架依赖
- 响应式布局(CSS Grid, 移动端单列/桌面端双列)
- 视频用 `<img>` 标签每150ms轮询 `/api/snapshot?_=时间戳`

**数据刷新**:
- JS `setInterval(refresh, 1200)` 每1.2秒拉取 `/api/dashboard`
- 视频独立于数据刷新, 有自己的150ms轮询
- 编辑操作后会立即调用 `refresh()`

**交互功能**:
| 功能 | JS函数 | API调用 |
|------|--------|---------|
| 添加食材 | `addItem()` | `/api/edit {action:"add"}` |
| 数量增减 | `editItem(idx, delta)` | `/api/edit {action:"adjust"}` |
| 删除食材 | `deleteItem(idx)` | `/api/edit {action:"delete"}` |
| 删除事件 | `deleteEvent(idx)` | `/api/edit {action:"delete_event"}` |

**食材图标映射** (icons对象):
```javascript
{'苹果':'🍎','香蕉':'🍌','橙子':'🍊','西兰花':'🥦','胡萝卜':'🥕',
 '三明治':'🥪','披萨':'🍕','蛋糕':'🎂','瓶装饮品':'🧃','热狗':'🌭',
 '甜甜圈':'🍩','杯子':'🥤','牛奶':'🥛','鸡蛋':'🥚','面包':'🍞','奶酪':'🧀'}
```

---

## 5. 构建与部署流水线

### 5.1 完整部署流程（代码→开发板）

```
[1. 改代码]
  │
  ├─ 改C++代码: 编辑 main.cc (WSL中)
  │   └─ WSL中编译: cmake + make → fridge_ai
  │
  ├─ 改Python代码: 编辑 fridge_mgr.py / lcd_ui.py (Windows中)
  │
  └─ 改Web代码: 编辑 app.py / index.html (Windows中)
  │
[2. 复制到deploy/]
  │   copy WSL编译结果 → c:\claude_code_demo\deploy\
  │
[3. 部署到开发板]
  │   scp deploy/* → root@192.168.2.77:/root/smartfridge/
  │
[4. 重启开发板服务]
  │   SSH: killall fridge_ai; killall python3
  │   SSH: /root/smartfridge/start.sh
  │
[5. 重启Flask服务器 (如果改了server/文件)]
  │   Windows: cd server; python app.py
```

### 5.2 交叉编译命令速查

```bash
# 在WSL Ubuntu中执行
export LUCKFOX_SDK_PATH=/home/taki/luckfox-pico
cd /home/taki/luckfox_pico_rkmpi_example
mkdir -p build_merged && cd build_merged

cmake .. \
  -DLIBC_TYPE=uclibc \
  -DCMAKE_BUILD_TYPE=Release \
  -DEXAMPLE_DIR=example/luckfox_pico_rtsp_yolov5 \
  -DEXAMPLE_NAME=fridge_ai

make -j8

# 输出: build_merged/fridge_ai (约3.9MB)
# 复制到Windows:
# copy \\wsl$\Ubuntu-22.04\home\taki\luckfox_pico_rkmpi_example\build_merged\fridge_ai c:\claude_code_demo\deploy\
```

### 5.3 SCP部署命令速查

```bash
# 从Windows PowerShell或WSL执行

# 停掉开发板服务
ssh root@192.168.2.77 "killall fridge_ai 2>/dev/null; killall python3 2>/dev/null"

# 上传所有文件
scp c:\claude_code_demo\deploy\fridge_ai root@192.168.2.77:/root/smartfridge/bin/
scp c:\claude_code_demo\deploy\fridge_mgr.py root@192.168.2.77:/root/smartfridge/
scp c:\claude_code_demo\deploy\lcd_ui.py root@192.168.2.77:/root/smartfridge/

# 启动
ssh root@192.168.2.77 "/root/smartfridge/start.sh"
```

---

## 6. 已知问题与注意事项

### 6.1 已修复的问题（历史记录）

| 问题 | 根因 | 修复方式 | 涉及文件 |
|------|------|----------|----------|
| 视频颜色蓝黄互换 | `COLOR_YUV2BGR_NV12` → VENC `RK_FMT_RGB888` 不匹配 | 改 `COLOR_YUV2RGB_NV12` + VENC维持 `RK_FMT_RGB888` | main.cc:192,126 |
| 视频卡在同一帧 | 多个孤儿ffmpeg堆积 | taskkill所有ffmpeg+atexit清理 | app.py:242-243 |
| 删除事件不同步 | `/api/sync`用INSERT OR IGNORE, 不删除旧数据 | 改为DELETE+INSERT全量替换 | app.py:139 |
| LCD触摸不响应 | 使用BTN_TOUCH协议而非MT协议 | 改为MT协议B: ABS_MT_POSITION_X/Y + SYN_REPORT | lcd_ui.py:192-222 |
| LCD UI崩溃(Pillow) | `d.rectangle([(坐标)])` 错误语法 | 改为 `d.rectangle((坐标))` | lcd_ui.py:175 |
| Python heredoc在SSH中断 | bash转义问题 | 改用base64编码传输 | 部署脚本 |

### 6.2 当前存在的局限性

1. **模型精度**: YOLOv5n使用COCO预训练权重，对冰箱特定食材（如保鲜袋、调料瓶）识别不准。需要收集冰箱食材数据集重新训练。

2. **光照影响**: 弱光环境下识别率下降。当前用LED灯带补光缓解，但灯带亮度不可调。

3. **单核CPU瓶颈**: 如果AI推理和Python管理同时高负载，可能出现卡顿。通过门关停AI缓解。

4. **事件检测粒度**: 开门→关门之间的多次操作会被合并为净变化。无法追踪"先放后取"的中间状态。

5. **断电丢失**: 如果开发板在门开状态下断电，AI不会正常退出，可能导致fb_lock残留。

6. **公网地址不固定**: localtunnel每次重启换地址。可用ngrok固定。

7. **单人假设**: 当前逻辑假设一次只有一个人操作冰箱。多人同时开冰箱会导致数据混乱。

### 6.3 坑点提醒

1. **C++ main.cc中的颜色顺序绝不能乱动**。整个颜色管线已经调通，改任何一个转换函数都可能导致全链路颜色错误。

2. **fb_lock是隐式依赖**。如果手动删除了/tmp/fb_lock但没有停AI，AI会覆盖Python的UI。反过来如果不删锁就开AI，屏幕不会显示摄像头画面。

3. **Bash heredoc在远程SSH中不可靠**。如果要通过SSH执行多行Python代码，用base64: `echo 'base64字符串' | base64 -d | python3`

4. **开发板上的killall不可靠**。有时候进程改过名字找不到。先尝试fridge_mgr的terminate()，超时再kill()。

5. **ffmpeg孤儿进程**。Flask重启（如修改app.py后的自动重载）不会杀掉旧的ffmpeg子进程，必须手动 `taskkill /f /im ffmpeg.exe`。

6. **触屏持久化fd很重要**。每次 `handle_touch_events()` 调用不要重新 `os.open()`，否则会丢失上一次调用到本次调用之间产生的触摸事件。

---

## 7. 扩展方向建议

### 7.1 短期改进（直接影响功能）

1. **重新训练YOLOv5模型**: 收集20-50种常见冰箱食材，标注数据集，训练专用模型
2. **过期提醒**: 记录食材放入时间，设定每种食材的保质期，自动提醒
3. **食谱推荐**: 根据库存食材推荐能做的菜
4. **语音播报**: 接入TTS，识别食材时语音播报

### 7.2 中期改进（提升体验）

5. **多人协同**: 绑定多个用户的手机，各自管理自己的食材
6. **购物清单**: 自动生成"需要购买"的食材清单
7. **营养分析**: 统计冰箱食材的营养成分

### 7.3 架构改进

8. **MQTT替代HTTP**: 用MQTT协议做开发板↔服务器通信，更省电更实时
9. **WebRTC替代MJPEG**: 视频流体验更好，延迟更低
10. **OTA固件更新**: 支持远程更新开发板程序

---

## 8. 快速调试命令

```bash
# === 开发板端 (SSH root@192.168.2.77) ===

# 检查系统状态
ps aux | grep -E "fridge|python3"          # 进程
cat /sys/class/gpio/gpio32/value           # 门磁(0=开)
free -m                                     # 内存
df -h /                                     # 磁盘
cat /sys/class/thermal/thermal_zone0/temp  # CPU温度(需/1000)

# 手动操作AI
/root/smartfridge/bin/fridge_ai /root/smartfridge/model/yolov5.rknn &  # 手动启动
killall fridge_ai                                                       # 手动停止

# 查看数据
cat /tmp/fridge_detections.json              # AI检测结果
cat /root/smartfridge/fridge_data.json       # 本地数据库
tail -f /root/smartfridge/logs/mgr.log       # 管理程序日志
tail -f /root/smartfridge/logs/ai.log        # AI程序日志

# 重启管理程序
killall python3 && /root/smartfridge/start.sh

# 清理fb_lock（如果残留）
rm -f /tmp/fb_lock

# === Windows端 ===

# 重启Flask服务器
taskkill /f /im python.exe
taskkill /f /im ffmpeg.exe
cd c:\claude_code_demo\server && python app.py

# 检查服务器状态
curl http://127.0.0.1:5000/api/ping
curl http://127.0.0.1:5000/api/dashboard | python -m json.tool

# 手动触发同步
curl -X POST http://192.168.2.75:5000/api/sync \
  -H "Content-Type: application/json" \
  -d '{"inventory":[],"events":[],"status":{"door_state":"closed"}}'
```

---

## 9. 依赖关系图

```
开发板端:
  fridge_mgr.py
  ├── Python3.11
  ├── python-periphery (GPIO)
  ├── Pillow (LCD绘图, 通过lcd_ui.py)
  ├── urllib (HTTP同步)
  └── fridge_ai (子进程)
      ├── RKMPI库 (VI/VENC/SYS)
      ├── RKNN库 (NPU推理)
      ├── OpenCV-mobile (颜色转换, 画框)
      └── yolov5.rknn (模型文件)

服务器端:
  app.py
  ├── Python3.10+
  ├── Flask
  ├── ffmpeg (RTSP→MJPEG, 外部进程)
  ├── localtunnel/ngrok (公网隧道, 外部进程)
  └── SQLite3 (Python内置)

浏览器端:
  index.html
  └── 无依赖 (纯HTML/CSS/原生JS)
```

---

## 10. 文件版本对照表

| 组件 | 当前版本 | 文件 | 行数 |
|------|---------|------|------|
| AI程序 | v2.0 (颜色修复版) | main.cc | 287 |
| 管理程序 | v6.0 | fridge_mgr.py | 199 |
| LCD界面 | v4.0 (MT协议+高对比度) | lcd_ui.py | 267 |
| Web页面 | v3.0 (快照轮询+编辑) | index.html | 239 |
| Flask服务器 | v3.0 (localtunnel) | app.py | 247 |

---

> 文档版本：v1.0 | 最后更新：2024-05-24 | 写给AI/开发者的二次开发参考
