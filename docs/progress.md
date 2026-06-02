# SmartFridge 项目进度记录

> 记录每个功能特性的计划、设计、执行、测试全过程。

---


## 2026-06-01 — 部分取出：透明瓶图像液位识别

### 1. 检查结论

本次针对截图中“部分取出”半完成项进行了详细检查。原有代码已经具备部分取出的事件结构和确认闭环：

1. `deploy/fridge_mgr.py` 已经在关门处理时把 `baseline_details` 和 `after_details` 传入 `compare_liquid_levels()`。
2. `deploy/partial_qty.py` 已经能处理 `qty_estimate`、`level`、`ratio` 这类结构化液位字段。
3. `server/app.py` 和 Web 页面已经支持 `partial_take_out` 事件的确认、驳回和人工修正。

缺口是：当 AI 检测结果只有 `bbox + frame_path`，没有直接给出 `qty_estimate/level/ratio` 时，旧逻辑会返回 `unknown`，无法从真实图像中估算液位。

同时检查 `deploy/fridge_ai` 二进制字符串后确认，当前部署版 AI 输出仍是：

```json
{"detections":[{"name":"...","confidence":0.950}]}
```

也就是说当前二进制暂时不输出 `bbox` 和 `frame_path`。本次已完成 Python 业务层的真实图像液位识别能力；要在开发板实机自动触发，还需要后续替换或重编译 `fridge_ai`，让检测 JSON 带上检测框和帧图路径。

### 2. 实现内容

改动文件：

| 文件 | 本次改动 |
| --- | --- |
| `deploy/partial_qty.py` | 新增 `estimate_liquid_level_from_image()`，支持从 `frame_path + bbox` 裁剪透明瓶 ROI，并基于行方向颜色饱和度/暗度差异估算液位比例；`detail_level()` 的优先级升级为 `qty_estimate > level > ratio > 图像估计`。 |
| `config/board.json` | 在 `liquid_level.image_detection` 下增加可调参数，如 ROI 边距、最小宽高、平滑窗口、最小对比度、阈值比例、底部连通比例，便于后续上板调参。 |
| `test/test_partial_takeout.py` | 增加两项合成图片测试：一项验证 `detail_level()` 可直接从瓶子图片和 bbox 估算 `half`；另一项验证前后两张图片可生成 `partial_take_out` 事件。 |

图像算法说明：

1. 根据 `bbox` 从 `frame_path` 中裁剪瓶身区域。
2. 去掉左右边框、瓶盖和底部少量干扰区域。
3. 对每一行计算颜色饱和度和暗度得分，液体区域通常比空气区域更有颜色或更暗。
4. 对行得分做平滑，找出底部连续液体区域的顶部边界。
5. 计算液位比例 `ratio = 液体高度 / 瓶身高度`，再映射到 `full / three_quarters / half / low / empty`。
6. 如果缺图、缺框、ROI 太小、对比度不足或边界不可靠，则返回 `unknown`，由人工确认流程处理。

### 3. 当前支持的数据格式

要触发图像液位识别，检测 JSON 至少需要包含：

```json
{
  "frame_path": "/tmp/fridge_frame.jpg",
  "detections": [
    {
      "name": "bottle",
      "confidence": 0.91,
      "bbox": [120, 80, 220, 360]
    }
  ]
}
```

也兼容每个检测项单独携带 `frame_path`。`bbox` 支持像素坐标 `[x1, y1, x2, y2]`，也支持 0-1 归一化坐标。

### 4. 验证结果

已执行的验证命令和结果：

| 命令 | 结果 |
| --- | --- |
| `python3 -m py_compile deploy/partial_qty.py test/test_partial_takeout.py` | 通过 |
| `python3 test/test_partial_takeout.py` | 10/10 通过 |
| `python3 test/test_config.py` | 11/11 通过 |
| `python3 test/test_event_enhanced.py` | 10/10 通过 |
| `python3 test/test_partial_remove.py` | 9/9 场景通过，人工修正流程通过 |
| `python3 test/test_regression.py` | 70/70 通过 |

新增测试覆盖：

1. `test_detail_level_estimates_level_from_image_bbox`：合成一张半瓶透明瓶图片，验证可识别为 `half`。
2. `test_compare_liquid_levels_uses_image_bbox_when_no_level_field`：合成满瓶到半瓶的前后图片，验证能生成 `partial_take_out` 且自动确认为 `confirmed`。

### 5. 剩余边界和后续任务

1. 当前 Python 层已经支持真实图片液位识别，但开发板部署版 `fridge_ai` 仍未输出 `bbox/frame_path`，因此实机自动液位识别还需要更新 AI 输出格式。
2. 该算法适合透明/半透明且液体与空气区域有明显颜色或明暗差异的瓶子；透明清水、强反光、白色牛奶对浅色背景、遮挡严重时会返回 `unknown`，这是有意设计，避免误改库存。
3. 下一步上板时建议先只用透明蓝色/橙色饮料瓶做样例，记录不同光照下的 `min_contrast` 和 `threshold_ratio`，再决定是否调参。
4. 如果后续要做比赛演示，建议准备一个“满瓶 → 半瓶”的透明饮料瓶场景，最容易稳定展示部分取出能力。

---

## 2026-06-01 — 阶段一：基线修复

### 1. 修复背景

本次修复对应后续开发计划中的“阶段一：基线修复”。目标不是新增展示功能，而是先把后续继续开发容易踩坑的基础问题处理掉，避免后面继续叠加真实事件识别、分层分类、部分取出和测试报告时出现隐性不一致。

发现的问题：

1. `config/server.json` 已经采用 `database/video/server/tunnel` 的嵌套结构，但 `server/app.py` 仍有部分字段按旧的扁平结构读取，例如 `rtsp_url`、`host`、`port`、`ffmpeg_path`。这会导致修改配置文件后服务端不一定真正生效。
2. 旧数据库或测试环境中的 `inventory` 表可能缺少 `category`、`category_l2`、`qty_type`、`qty_estimate` 字段，导致部分取出/人工修正流程在旧库上报错。
3. `/api/edit` 的 `confirm_event` 在确认 `put_in` 待审核事件时，只对已有库存执行 `UPDATE`。如果库存中还没有该物品，确认事件后不会自动创建库存记录。
4. `test/test_partial_remove.py` 的人工修正流程直接使用真实数据库，但测试开始前没有主动补齐旧库字段，因此之前出现过 `table inventory has no column named qty_type`。

### 2. 实现内容

改动文件：

| 文件 | 本次改动 |
| --- | --- |
| `server/app.py` | 新增 `_sg_any()`，支持优先读取嵌套配置，同时兼容旧扁平配置；修正 `RTSP_URL`、`SERVER_HOST`、`SERVER_PORT`、`ffmpeg_path` 的读取路径；统一 `events` 和 `inventory` 的字段迁移；确认 `put_in` 待审核事件时支持库存不存在则自动插入。 |
| `test/test_config.py` | 新增“服务端-嵌套配置生效”测试，临时修改 `config/server.json` 后通过子进程导入 `server.app`，验证嵌套配置确实被实际读取。 |
| `test/test_partial_remove.py` | 在人工修正流程验证前补齐测试数据库字段，使测试初始化与 `server.init_db()` 的迁移逻辑保持一致。 |

关键行为说明：

1. 服务端配置读取现在支持两种格式：优先读取 `video.rtsp_url`、`video.ffmpeg_path`、`server.host`、`server.port` 等新格式；如果旧配置仍使用 `rtsp_url`、`ffmpeg_path`、`host`、`port`，也会继续兼容。
2. 数据库初始化现在会对 `events` 和 `inventory` 两张表都执行幂等迁移，旧库缺字段时自动补列，已有字段时忽略。
3. 用户在 Web 端确认待审核 `put_in` 事件时，如果库存已有该物品，则增加数量；如果库存没有该物品，则新建库存记录。
4. 如果事件没有携带 `qty_type`，确认已有库存时不会强行把原库存类型覆盖为 `count`，尽量保留已有库存语义。

### 3. 验证结果

已执行的验证命令和结果：

| 命令 | 结果 |
| --- | --- |
| `python3 -m py_compile server/app.py test/test_config.py test/test_partial_remove.py` | 通过 |
| `python3 test/test_config.py` | 11/11 通过 |
| `python3 test/test_partial_remove.py` | 9/9 场景通过，人工修正流程通过 |
| `python3 test/test_partial_takeout.py` | 8/8 通过 |
| `python3 test/test_event_enhanced.py` | 10/10 通过 |
| `python3 test/test_regression.py` | 70/70 通过 |

本次修复后，之前 `test/test_partial_remove.py` 末尾人工修正流程中的 `inventory has no column named qty_type` 问题已消失。

### 4. 后续注意事项

1. 本次验证主要是 PC/WSL 逻辑测试，还不能替代开发板上的实机全链路测试。下一阶段仍需要上板验证：开门、补光、AI 启停、识别结果写入、关门生成事件、同步到 Web、LCD 刷新。
2. `server/app.py` 被测试导入时会启动视频代理线程；目前测试能通过，但后续如果要做更标准的单元测试，可以考虑把 ffmpeg 线程启动改成可配置或只在主程序运行时启动。
3. 数据库迁移目前采用轻量 `ALTER TABLE ADD COLUMN` 方式，适合当前小项目；如果后续表结构继续复杂化，建议新增专门的迁移脚本或版本字段。

---

## 2026-05-31 — 真实事件识别增强

### 1. 需求分析 / 计划

**目标**: 增强冰箱事件检测的可靠性，解决以下问题:
1. 手伸入又伸出（无净变化）被误判为事件
2. 放入/取出 1 个物体时，置信度低，不应直接强行改库存
3. 需要区分真实事件与检测噪声
4. 比赛要求：分层识别（先判断是否有真实增删 → 粗粒度分类 → 具体食材）

**实现方案**:
- 引入 `review_status` + `confidence` 双字段
- 阈值策略：`diff==0`→忽略，`diff==1`→needs_review，`diff>=2`→confirmed
- 事件结构扩展：增加 `category`、`category_l2`、`item_key`
- 向后兼容旧数据（无字段时默认识别为 confirmed）

### 2. 设计决策

**判定矩阵**（由 `config/board.json` 的 `event.auto_confirm_threshold` 和 `event.needs_review_threshold` 配置）:

```
diff == 0         → IGNORE    → 无事件，无库存变动
diff == 1         → needs_review → 有事件，待审核，库存不变
diff >= 2         → confirmed  → 有事件，已确认，库存更新
```

**粗分类体系**（已定义于 `config/board.json`）:

| C1（粗分类） | C2（细分类） | 代表食材 |
|---|---|---|
| 果蔬 | 水果/蔬菜 | 苹果、香蕉、橙子、西兰花、胡萝卜 |
| 即食 | 熟食/甜点/切片 | 热狗、甜甜圈、披萨、蛋糕、三明治 |
| 乳品饮品 | 乳制品/蛋制品/烘焙/包装饮品/容器 | 牛奶、鸡蛋、面包、奶酪、瓶装饮品、杯子 |

### 3. 改动文件清单

| 文件 | 改动内容 |
|---|---|
| `deploy/fridge_mgr.py` | 新增配置加载、`_get_category()`、`_display_name()`、`evt_add()` 扩展字段、`process_events()` 阈值逻辑 |
| `server/app.py` | `events` 表新增 `review_status/confidence/category/category_l2/item_key` 列、`/api/edit` 新增 `confirm_event`/`reject_event` action、同步逻辑更新 |
| `server/server_fridge.db` | 通过 `ALTER TABLE` 迁移旧库，新增 5 个列 |
| `server/templates/index.html` | 事件列表显示审核状态标签（待审核/已确认/已驳回）+ 分类标签 + 确认/驳回按钮 |
| `config/board.json` | 已有 `category_map`/`display_name_map`/`category_icons`/`event` 配置，无需改动 |
| `test/test_event_enhanced.py` | 新增，10 个场景测试 |
| `test/test_regression.py` | 新增，56 项回归检查 |
| `test/test_config.py` | 新增，10 项配置容错测试 |

### 4. 事件结构

```json
{
  "id": 1,
  "timestamp": "2026-05-31 12:00:00",
  "action": "put_in",
  "food_name": "苹果",
  "count": 1,
  "review_status": "needs_review",   // confirmed / needs_review / rejected
  "confidence": 0.5,                // 0.0~1.0
  "category": "果蔬",               // C1 粗分类
  "category_l2": "水果",            // C2 细分类
  "item_key": "apple"               // COCO原名，透传字段
}
```

### 5. 测试结果

**测试套件 1: `test_event_enhanced.py` — 场景测试（10/10 通过）**

| # | 场景 | baseline→after | 预期 | 实际 | 通过 |
|---|---|---|---|---|---|
| 1 | 手伸入又伸出，无净变化 | `{苹果:2,香蕉:1}`→`{苹果:2,香蕉:1}` | diff=0→ignore | 无有效事件 | ✓ |
| 1b | 手被误识别（diff=1） | `{苹果:2,香蕉:1}`→`{苹果:3,香蕉:1}` | diff=+1→needs_review | put_in 苹果 x1 [needs_review] | ✓ |
| 2 | 整理位置但库存没变 | `{苹果:3,牛奶:2,面包:1}`→`{苹果:2,牛奶:2,面包:1}` | diff=-1→needs_review | take_out 苹果 x1 [needs_review] | ✓ |
| 3 | 放入 1 个明显新物体 | `{苹果:2}`→`{苹果:2,香蕉:1}` | diff=+1→needs_review | put_in 香蕉 x1 [needs_review] | ✓ |
| 4 | 取出 2 个（达自动阈值） | `{苹果:3,香蕉:2}`→`{苹果:3}` | diff=-2→confirmed | take_out 香蕉 x2 [confirmed] | ✓ |
| 4b | 取出 1 个 | `{苹果:3,香蕉:2}`→`{苹果:3,香蕉:1}` | diff=-1→needs_review | take_out 香蕉 x1 [needs_review] | ✓ |
| 5 | 遮挡+物体变化同时发生 | `{苹果:1,香蕉:3,牛奶:2}`→`{苹果:3,香蕉:2,牛奶:2}` | 苹果+2→confirmed + 香蕉-1→needs_review | confirmed=1, needs_review=1 | ✓ |
| 6 | 低置信度变化（diff=1） | `{苹果:5}`→`{苹果:6}` | diff=+1→needs_review | put_in 苹果 x1 [needs_review] | ✓ |
| 额外 | 全部取空 | `{苹果:3,香蕉:2}`→`{}` | 苹果-3+香蕉-2→confirmed | take_out 香蕉 x2 [confirmed] \| take_out 苹果 x3 [confirmed] | ✓ |
| 额外 | 完全无变化 | `{苹果:2}`→`{苹果:2}` | diff=0→ignore | 无有效事件 | ✓ |

**测试套件 2: `test_regression.py` — 回归检查（56/56 通过）**

验证原有函数名（`DOOR_PIN`, `ai_start`, `ai_stop`, `http_sync`, `process_events`, `dashboard`, `sync`, `edit` 等）全部完好。

**测试套件 3: `test_config.py` — 配置容错（10/10 通过）**

验证配置加载、覆盖值生效、缺失回退默认值、非法JSON不崩溃。

### 6. 验证命令

```bash
# 场景测试
python3 test/test_event_enhanced.py

# 回归检查
python3 test/test_regression.py

# 配置容错测试
python3 test/test_config.py

# 数据库结构验证
python3 -c "
import sqlite3
DB = 'server/server_fridge.db'
conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute('PRAGMA table_info(events)').fetchall()]
for f in ['category','category_l2','item_key','review_status','confidence']:
    assert f in cols
print('events schema: OK —', cols)
"
```

---

## 更早记录

（暂无 — 本条为首次记录，后续在此文件追加。）
---

## 2026-06-02 端侧 AI 输出 bbox/frame_path 编译完成

### 本次处理

- 位置确认：main.cc 位于 /home/jing/my-project/luckfox_pico_rkmpi_example/example/luckfox_pico_rtsp_yolov5/src/main.cc。
- 已修复 Luckfox 端侧源码，使检测结果写入 /tmp/fridge_detections.json，并保存当前帧到 /tmp/fridge_frame.jpg。
- 检测 JSON 现在包含 frame_path 字段，detections 中每个目标包含 name、confidence、bbox。
- bbox 会从 letterbox 推理坐标映射回原始画面坐标，并过滤 person，避免手/人误识别进入库存。
- 端侧程序支持通过启动参数传入模型路径；不传参数时仍使用 ./model/yolov5.rknn，兼容原启动方式。
- 编译时发现 Luckfox SDK 不提供 opencv2/imgcodecs.hpp，已删除该 include；cv::imwrite 由现有 highgui 头文件提供。

### 编译与产物

- 构建目录：/home/jing/my-project/luckfox_pico_rkmpi_example/build。
- 构建命令：cmake -S . -B build -DEXAMPLE_DIR=example/luckfox_pico_rtsp_yolov5 -DEXAMPLE_NAME=luckfox_pico_rtsp_yolov5 -DLIBC_TYPE=uclibc，然后 cmake --build build --target install。
- 编译结果：通过，生成 /home/jing/my-project/luckfox_pico_rkmpi_example/install/uclibc/luckfox_pico_rtsp_yolov5_demo/luckfox_pico_rtsp_yolov5。
- 已复制到项目：/home/jing/my-project/smartfridge/deploy/fridge_ai。
- 二进制验证：file 显示 ARM 32-bit uClibc 可执行文件；strings 可见 fridge_detections、fridge_frame、frame_path、bbox。

### 后续上板验证

- 上传新版 /home/jing/my-project/smartfridge/deploy/fridge_ai 到开发板 /root/smartfridge/bin/fridge_ai。
- 启动端侧后开关冰箱门触发识别。
- 在开发板检查 /tmp/fridge_detections.json，应包含 frame_path 和 detections[].bbox。
- 检查 /tmp/fridge_frame.jpg 是否存在；云端 partial_qty.py 会依赖该图片进行液体余量估计。

---

## 2026-06-02 — 真实事件识别：多帧稳定 + 门关后冷却期

### 1. 需求与目标

赛题《冰箱食材识别与管理系统》明确指出："系统应能够区分真实物品放入、取出、部分取出与仅有手部经过、短时遮挡、整理冰箱但无物品变化等情况，不得仅凭手部出现即判定发生物品进出。"

旧实现 `deploy/fridge_mgr.py` 的判定逻辑只读 **开门一帧** 和 **关门一帧** 做差，存在两个具体问题：

1. **单帧误检**：开门瞬间手还在画面里就被识别为新物品 / 门关瞬间手刚离开就被识别为物品消失。
2. **没有冷却期**：门关后立刻 `process_events`，此时物品可能还没放稳（手刚从冰箱内抽出来），AI 检测框还套在手或半个物品上。

本次新增独立模块 `event_stabilizer.py`，提供两个核心能力：

- **FrameStabilizer**：一个物品连续 K 帧被检出才进入稳定集合；消失立即清零。
- **CooldownController**：门关后等 T 秒，冷却期内持续读帧、监控门重开，满足"冷却期满"或"画面稳定"任一条件才触发 process_events。

### 2. 设计决策

#### 2.1 FrameStabilizer（多帧稳定器）

```
输入：当前帧的 detections 列表
行为：
  1. 过滤 exclude_names（默认 'person'，不让人/手进入库存）
  2. 每个物品的连续出现帧数 +1
  3. 物品从画面消失时计数器清零（不留惯性）
  4. 连续出现 ≥ stability_frames 帧才进入稳定集合
输出：当前帧的稳定物品数量 {name: count}
```

- **为什么不用滑动窗口**：滑动窗口实现复杂、对内存要求高；连续计数 + 立即清零已能满足"过滤单帧误检"目标。
- **为什么 `person` 默认排除**：COCO 模型的 `person` 类经常把手部误识别为其它物品（COCO 80 类里没 'hand'），但 person 出现不一定是放东西 / 取东西，必须过滤。
- **数量取帧内最大**：同帧内同一物品出现 2 次（如两个 apple），输出 `count=2`，与 `build_count_map` 协同。

#### 2.2 CooldownController（冷却期控制器）

```
状态机：
  ARMED → on_door_close() → COOLING
  COOLING → tick() → READY | CANCELED
  READY/CANCELED → reset() → ARMED

进入 READY 的条件（任一满足即触发）：
  - elapsed ≥ cooldown_seconds（默认 3 秒，时间到）
  - 画面连续 N 帧完全一致（默认 3 帧，提前结束）

进入 CANCELED 的条件：
  - 冷却期内门被重新打开（取消本轮处理）
```

- **门重开就取消**：避免"开门 → 关门 → 又开门 → 再关门"的多轮操作被错误合并。
- **画面稳定提前结束**：常见场景下（关门后没再操作），第 4 tick 就 READY，节省等待时间。
- **两种触发条件并存**：时间触发兜底（最坏 3 秒），画面稳定触发加速（理想 < 1 秒）。

#### 2.3 主循环状态机变化

```
旧：IDLE → DETECTING → PROCESSING → IDLE
新：IDLE → DETECTING → COOLING → IDLE
              ↑          │
              └──────────┘  （门在 COOLING 期重开则回到 DETECTING）
```

- baseline 和 after 都用同一个 FrameStabilizer 累积
- DETECTING 期每帧 `stab.update(dets)` 累积 baseline
- 门关 → 暂存 stab.snapshot() 作为 baseline → 启动冷却
- COOLING 期每帧继续 `stab.update(dets)` 累积 after
- 满足条件 → process_events(baseline, after) → reset → IDLE

### 3. 改动文件清单

| 文件 | 本次改动 |
| --- | --- |
| `deploy/event_stabilizer.py` | **新增**，提供 `FrameStabilizer` 与 `CooldownController` 两个独立类，纯逻辑无硬件依赖 |
| `deploy/fridge_mgr.py` | 导入新模块；新增 `STAB_FRAMES`/`COOLDOWN_SECONDS`/`COOLDOWN_STABLE_FRAMES` 配置项；主循环从 IDLE/DETECTING/PROCESSING 三态改为 IDLE/DETECTING/COOLING 三态；baseline 与 after 改用 stab.update() 累积；门关后进入 COOLING 阶段 |
| `config/board.json` | 新增 `stabilization` 段：`stability_frames=3`、`cooldown_seconds=3.0`、`cooldown_stable_frames=3` |
| `test/test_stabilizer.py` | **新增**，21 个测试场景（见下表） |

### 4. 测试结果

**新增测试 `test_stabilizer.py` — 21/21 通过**

| # | 类别 | 场景 | 通过 |
| --- | --- | --- | --- |
| 1 | FrameStabilizer | 初始为空 | ✓ |
| 2 | FrameStabilizer | 单帧不通过 | ✓ |
| 3 | FrameStabilizer | 连续 3 帧通过 | ✓ |
| 4 | FrameStabilizer | 中途消失清零 | ✓ |
| 5 | FrameStabilizer | person 永远过滤 | ✓ |
| 6 | FrameStabilizer | 数量取帧内观测最大 | ✓ |
| 7 | FrameStabilizer | reset 清空 | ✓ |
| 8 | FrameStabilizer | snapshot 返回稳定集 | ✓ |
| 9 | FrameStabilizer | stability_frames=1 时单帧即采纳 | ✓ |
| 10 | CooldownController | 初始 ARMED | ✓ |
| 11 | CooldownController | on_door_close 进入 COOLING | ✓ |
| 12 | CooldownController | 门重开触发 CANCELED | ✓ |
| 13 | CooldownController | 冷却期满变 READY | ✓ |
| 14 | CooldownController | 画面稳定提前结束 | ✓ |
| 15 | CooldownController | 画面变化重置稳定计数 | ✓ |
| 16 | CooldownController | reset 回到 ARMED | ✓ |
| 17 | CooldownController | elapsed 计时准确 | ✓ |
| 18 | CooldownController | force_cancel 手动取消 | ✓ |
| 19 | 集成 | 关门后画面稳定 → 4 tick READY | ✓ |
| 20 | 集成 | 关门后画面变化 → 时间触发 READY | ✓ |
| 21 | 集成 | 开门累积→关门→门重开 → 本轮取消 | ✓ |

**回归测试（其他 5 个测试文件，无 regression）**

| 命令 | 结果 |
| --- | --- |
| `python3 test/test_config.py` | 11/11 通过 |
| `python3 test/test_event_enhanced.py` | 10/10 通过 |
| `python3 test/test_partial_remove.py` | 9/9 通过 |
| `python3 test/test_partial_takeout.py` | 10/10 通过 |
| `python3 test/test_regression.py` | 70/70 通过 |

### 5. 验证命令

```bash
# 编译检查
python3 -m py_compile deploy/event_stabilizer.py
python3 -m py_compile deploy/fridge_mgr.py
python3 -m py_compile test/test_stabilizer.py

# 跑新测试
python3 test/test_stabilizer.py

# 跑全套测试（确认无 regression）
python3 test/test_config.py
python3 test/test_event_enhanced.py
python3 test/test_partial_remove.py
python3 test/test_partial_takeout.py
python3 test/test_regression.py
```

### 6. 修复过程中发现的真 bug

在测试过程中暴露并修复了 `event_stabilizer.py` 的两个 bug：

1. **`_cooldown_start or now` 短路 bug**：当 `on_door_close(t=0.0)` 时 `_cooldown_start=0.0`，`0.0 or now` 触发 Python `or` 短路返回 `now`，导致 `elapsed` 永远算成 0。改为显式 `is not None` 判断。
2. **`elapsed` 不响应测试传入的时间**：`@property elapsed` 直接用 `time.time()`，未使用 tick 传入的 `t`，导致测试无法验证时间逻辑。新增 `_last_now` 字段，tick 时记录，`elapsed` 优先使用 `_last_now`。

### 7. 后续注意事项

1. **本次未做端到端实机验证**：所有测试都是 PC/WSL 上的单元 + 集成测试。需要在开发板上验证：
   - 实际开门时 `FrameStabilizer` 能否在 3 帧内累积到稳定画面（取决于 fridge_ai 输出频率）
   - 冷却期内的 LCD 显示不应卡顿（每次 tick 0.2 秒 sleep）
   - 门重开取消本轮的判断是否在 0.2 秒内及时响应
2. **`stability_frames=3` 是经验值**：如果 fridge_ai 输出频率 ~5 FPS，3 帧约 0.6 秒；如果 FPS 较低，可能需要降到 2。可通过 `config/board.json` 的 `stabilization.stability_frames` 调整。
3. **`cooldown_seconds=3.0` 是经验值**：太短则手还没完全抽出会误判；太长则用户感知卡顿。3 秒是基于"用户关门后 1-2 秒手完全抽出"的常识估算，可视实际调优。
4. **`person` 过滤依赖 `exclude_names` 配置**：如果后续增加自定义模型并支持手部检测（`hand` 类），需要在 `event_stabilizer.py` 默认 `exclude_names` 中追加，或者改成在 `build_count_map` 里集中过滤。
5. **冷却期与 AI 启停的协同**：本次实现是门关后**立即**停 AI，冷却期内只读 `fridge_detections.json` 的最后残留值。如果该文件被 fridge_ai 退出时清空，冷却期内的 `take_snapshot_callback` 只会读到空 dict，导致画面变化、冷却期满。建议在 `ai_stop()` 前先把当前帧的稳定值保存到 `after_details`，作为冷却期内的"基线参考"。
---

## 2026-06-02 实时识别框 null 名称修复

### 问题现象

- Web 实时画面中的识别框显示 null 80.9%，没有显示具体物品名称。
- 原因：Luckfox YOLO 后处理代码通过相对路径 ./model/coco_80_labels_list.txt 加载标签；fridge_mgr.py 从 /root/smartfridge 启动 /root/smartfridge/bin/fridge_ai 时，如果当前目录下没有对应标签文件，coco_cls_to_name() 会返回 null。

### 本次修复

- 修改 /home/jing/my-project/luckfox_pico_rkmpi_example/example/luckfox_pico_rtsp_yolov5/src/postprocess.cc。
- 在 postprocess.cc 内增加 COCO 80 类英文标签 fallback_labels。
- coco_cls_to_name() 在外部标签文件未加载或某项为空时，返回内置 fallback 标签，避免实时框和检测 JSON 出现 null。
- 端侧 OpenCV cv::putText 默认字体不支持中文，因此实时框先显示英文 COCO 类名，例如 mouse、bottle、apple；云端/库存页面仍通过配置映射为中文。

### 编译与验证

- 已重新编译 luckfox_pico_rtsp_yolov5，编译通过。
- 已复制新二进制到 /home/jing/my-project/smartfridge/deploy/fridge_ai。
- 二进制验证：strings/grep 可见 mouse、bottle、apple，说明内置标签已进入新版 fridge_ai。

### 上板验证

- 上传 deploy/fridge_ai 到开发板 /root/smartfridge/bin/fridge_ai 后，执行 ./stop.sh && ./start.sh。
- 打开实时画面，原来的 null 应显示为对应英文物品名称，如 mouse 80.9%。
