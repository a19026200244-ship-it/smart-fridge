# SmartFridge 项目进度记录

> 记录每个功能特性的计划、设计、执行、测试全过程。

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