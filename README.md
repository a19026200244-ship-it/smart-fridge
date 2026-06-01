# SmartFridge 冰箱食材识别与管理系统

SmartFridge 是一个基于 Luckfox Pico Ultra W 的智能冰箱原型项目：开发板负责门磁检测、补光控制、摄像头采集、YOLOv5/RKNN 食材识别、LCD 触摸屏展示和数据同步；PC/服务器端运行 Flask Web 后台，用于查看库存、事件记录、硬件状态和实时视频画面。

本仓库适合继续做二次开发、部署复现和研电赛材料整理。更完整的接手说明见 `docs/AI二次开发文档.md`，从零部署教程见 `docs/小白开发技术文档.md`。

## 当前进度


| 模块        | 状态                                           | 主要位置                                    |
| --------- | -------------------------------------------- | --------------------------------------- |
| AI 推理程序   | 已可工作，使用 RKNN YOLOv5 模型                       | `deploy/fridge_ai`、`deploy/yolov5.rknn` |
| 板端管理程序    | 已可工作，负责门磁、补光、AI 进程、库存事件和同步                   | `deploy/fridge_mgr.py`                  |
| LCD 触摸界面  | 已可工作，支持库存编辑、事件查看和触摸操作                        | `deploy/lcd_ui.py`                      |
| Flask 服务端 | 已可工作，提供 REST API、Web 面板和视频代理                 | `server/app.py`                         |
| Web 管理页面  | 已可工作，原生 HTML/CSS/JS 实现                       | `server/templates/index.html`           |
| 视频流       | 开门时可用，开发板 RTSP 经服务端 ffmpeg 转为浏览器可显示的快照/MJPEG | `rtsp://192.168.2.77/live/0`            |
| 硬件控制      | 门磁 GPIO32、继电器/灯 GPIO40 已打通                   | `deploy/fridge_mgr.py`                  |


当前主线方案是：板端运行预编译/交叉编译后的 `fridge_ai` 做 AI 与视频输出，`fridge_mgr.py` 做业务管理和同步，PC 端运行 Flask 提供 Web 页面。`board/` 下保留了源码、早期 Python 拆分实现和 C/C++ 交叉编译工程，便于继续优化。

## 工作流程

```text
打开冰箱门
  -> 门磁触发，继电器打开补光灯
  -> 启动 AI 推理，摄像头开始识别食材
  -> 记录开门时的检测结果 baseline
关闭冰箱门
  -> 读取关门后的检测结果 after
  -> 对比 before/after，生成 put_in 或 take_out 事件
  -> 更新本地库存和事件数据
  -> 同步到 Flask 服务端
  -> LCD 与 Web 页面刷新显示
```

## 项目结构

```text
smartfridge/
├── board/
│   ├── fridge_ai/          # AI 推理源码/交叉编译工程，依赖 Luckfox SDK、RKMPI、RKNN、OpenCV
│   ├── fridge_manager/     # 早期板端 Python 模块化实现，供参考和复用
│   └── smartfridge/        # 早期 C 管理程序实验代码
├── deploy/                 # 实际部署到开发板的运行文件
│   ├── fridge_ai           # 板端 AI 推理二进制
│   ├── fridge_mgr.py       # 板端主控程序
│   ├── lcd_ui.py           # LCD/触摸屏 UI
│   ├── start.sh            # 板端启动脚本
│   ├── stop.sh             # 板端停止脚本
│   └── yolov5.rknn         # RKNN 模型
├── docs/                   # 项目说明、二次开发文档、比赛资料
├── models/                 # 模型相关文件
├── server/                 # PC/服务器端 Flask 应用
│   ├── app.py
│   ├── server_fridge.db
│   ├── templates/index.html
│   └── static/style.css
├── README.md
└── requirements.txt
```

## 环境依赖

PC/服务器端：

- Python 3.10+
- `pip install -r requirements.txt`
- ffmpeg，用于把开发板 RTSP 视频转换给浏览器显示
- 可选：Node.js + `localtunnel`，或 `NGROK_AUTHTOKEN` + `pyngrok`，用于公网访问

开发板端：

- Luckfox Pico Ultra W / RV1106 系列运行环境
- Python 3
- `python-periphery`：GPIO 控制
- `Pillow`：LCD framebuffer 绘图
- `/oem/usr/lib` 中的板端运行库
- `/dev/fb0` LCD framebuffer、`/dev/input/event*` 触摸设备

AI/交叉编译环境：

- WSL2 Ubuntu 22.04
- Luckfox SDK
- `luckfox_pico_rkmpi_example`
- `luckfox_pico_rknn_example`
- RKNN/RKMPI/OpenCV-mobile 相关库和头文件

## 快速启动服务端

```bash
cd /home/jing/my-project/smartfridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server/app.py
```

启动后访问：

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://<PC_IP>:5000`

如果需要显示实时视频，请确保 PC/服务器能访问开发板的 RTSP 地址，并已安装 `ffmpeg`。当前默认 RTSP 地址在 `server/app.py` 中为 `rtsp://192.168.2.77/live/0`。

## 部署到开发板

默认开发板信息：

- IP：`192.168.2.77`
- 用户：`root`
- 密码：`luckfox`
- 部署目录：`/root/smartfridge/`

首次部署可参考：

```bash
ssh root@192.168.2.77
mkdir -p /root/smartfridge/bin /root/smartfridge/model /root/smartfridge/logs
exit

scp deploy/fridge_ai root@192.168.2.77:/root/smartfridge/bin/
scp deploy/yolov5.rknn root@192.168.2.77:/root/smartfridge/model/
scp deploy/fridge_mgr.py deploy/lcd_ui.py deploy/start.sh deploy/stop.sh root@192.168.2.77:/root/smartfridge/

ssh root@192.168.2.77
chmod +x /root/smartfridge/bin/fridge_ai /root/smartfridge/start.sh /root/smartfridge/stop.sh
sh /root/smartfridge/start.sh
tail -f /root/smartfridge/logs/mgr.log
```

部署前请根据实际 PC 地址修改 `deploy/fridge_mgr.py` 中的：

```python
SERVER_URL = "http://192.168.2.73:5000"
```

## 常用调试命令

```bash
# 服务端健康检查
curl http://127.0.0.1:5000/api/ping
curl http://127.0.0.1:5000/api/dashboard

# 开发板进程和日志
ssh root@192.168.2.77
ps | grep -E "fridge|python"
tail -f /root/smartfridge/logs/mgr.log
tail -f /root/smartfridge/logs/ai.log

# 门磁和温度
cat /sys/class/gpio/gpio32/value
cat /sys/class/thermal/thermal_zone0/temp

# 清理 LCD framebuffer 锁
rm -f /tmp/fb_lock
```

## 关键开发入口


| 目标            | 建议入口                                                    |
| ------------- | ------------------------------------------------------- |
| 改 Web 页面      | `server/templates/index.html`、`server/static/style.css` |
| 改服务端 API/同步逻辑 | `server/app.py`                                         |
| 改板端主流程        | `deploy/fridge_mgr.py`                                  |
| 改 LCD 触摸 UI   | `deploy/lcd_ui.py`                                      |
| 改 AI 推理和视频链路  | `board/fridge_ai/`                                      |
| 改模型           | `models/`、`deploy/yolov5.rknn`                          |
| 查看接手背景        | `docs/AI二次开发文档.md`                                      |
| 查看完整部署教程      | `docs/小白开发技术文档.md`                                      |

## 配置文件说明

项目支持通过 JSON 配置文件管理所有硬编码参数，兼容向后加载（无配置文件时使用内置默认值）。

### 配置文件位置

```
smartfridge/
├── config/
│   ├── board.json    # 开发板端配置（GPIO/路径/同步/UI）
│   └── server.json   # 服务端配置（RTSP/数据库/监听）
```

### 开发板端配置 (config/board.json)

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `hardware.door_pin` | 门磁GPIO编号 | `32` |
| `hardware.relay_pin` | 继电器GPIO编号 | `40` |
| `paths.data_file` | 本地数据文件路径 | `/root/smartfridge/fridge_data.json` |
| `paths.ai_binary` | AI二进制文件路径 | `/root/smartfridge/bin/fridge_ai` |
| `paths.ai_model` | RKNN模型文件路径 | `/root/smartfridge/model/yolov5.rknn` |
| `paths.det_file` | AI检测结果临时文件 | `/tmp/fridge_detections.json` |
| `paths.fb_lock` | LCD互斥锁文件 | `/tmp/fb_lock` |
| `paths.log_dir` | 日志目录 | `/root/smartfridge/logs` |
| `sync.server_url` | 服务端同步地址 | `http://192.168.2.73:5000` |
| `sync.interval_seconds` | 同步间隔（秒） | `2` |
| `ui.*` | LCD配色和食材名称 | 见下文 |

### 服务端配置 (config/server.json)

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `database.path` | SQLite数据库路径 | `server/server_fridge.db` |
| `video.rtsp_url` | 开发板RTSP流地址 | `rtsp://192.168.2.77/live/0` |
| `video.ffmpeg_path` | ffmpeg路径（空则自动探测） | `""` |
| `server.host` | 监听地址 | `0.0.0.0` |
| `server.port` | 监听端口 | `5000` |
| `tunnel.ngrok_token` | ngrok认证令牌 | `""` |

### LCD UI 配色配置 (board.json → ui.*)

颜色值为 RGB 三元组列表，例如 `"color_green": [0, 255, 150]`。

| 字段 | 说明 |
|------|------|
| `ui.color_black / white / green / blue / red / yellow / gray / dgray / lgray` | UI各区域颜色 |
| `ui.screen_size` | 屏幕分辨率 `[宽, 高]` |
| `ui.fb_device` | framebuffer设备路径 |
| `ui.font_path` | 字体文件路径 |
| `ui.food_names` | 食材选择列表 |

### 启动日志示例

配置加载成功时程序会输出关键配置项：

```
===== SmartFridge v6.0 =====
[Config] Loaded from /root/smartfridge/../config/board.json
  hardware: door=32 relay=40
  paths: data=/root/smartfridge/fridge_data.json
  sync: server=http://192.168.2.73:5000 interval=2s
[Init] GPIO OK
```

```
SmartFridge Server started!
[Config] Loaded from .../smartfridge/config/server.json
  database: .../smartfridge/server/server_fridge.db
  video: rtsp=rtsp://192.168.2.77/live/0 ffmpeg=(auto)
  server: 0.0.0.0:5000
  Local:   http://127.0.0.1:5000
  Network: http://192.168.2.75:5000
```

### 已知限制

- 当前 YOLOv5 使用 COCO 预训练类别，对冰箱真实食材、包装袋、调味瓶等场景识别精度有限，后续建议采集自定义数据集重新训练。
- 事件检测基于“开门前后检测结果差异”，一次开门过程中的多次中间操作会被合并为净变化。
- 开门时 AI 推理、视频输出和 Python 管理程序会同时占用板端资源，复杂场景下可能卡顿。
- `fb_lock` 是 LCD 互斥机制，AI 与 Python UI 同时写 `/dev/fb0` 前需要正确加锁/解锁。
- `localtunnel` 公网地址每次启动可能变化；需要稳定地址时可改用 ngrok 或自建反向代理。
- Flask 中的 ffmpeg 子进程在异常退出或手动重启时可能残留，必要时需要手动清理。

## 后续优化方向 -

- 训练面向冰箱场景的食材识别模型，并重新转换为 RKNN。
- 把 IP、路径、GPIO、RTSP 地址等硬编码配置抽成统一配置文件。
- 增加服务端认证、数据导出和库存有效期提醒。
- 优化板端异常恢复，例如断电后自动清理 `fb_lock`、自动重启 AI/同步进程。
- 将部署流程整理为一键脚本，减少手动 SCP 和 SSH 操作。

