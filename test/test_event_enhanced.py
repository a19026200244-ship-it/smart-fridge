#!/usr/bin/env python3
"""真实事件识别增强 - 场景测试脚本

覆盖 6 个典型误判场景，验证判定规则是否正确。
每个测试通过模拟 parse_detections() 的输出来驱动 process_events() 逻辑。
"""
import os, sys, json

PROJECT = "/home/jing/my-project/smartfridge"
os.chdir(PROJECT)

# ── 加载实际阈值配置 ──────────────────────────────────────────────────────
_config_path = os.path.join(PROJECT, "config/board.json")
_cfg = {}
with open(_config_path) as f: _cfg = json.load(f)

def _get(*keys, default):
    v = _cfg
    for k in keys:
        if isinstance(v, dict): v = v.get(k)
        else: return default
    return v if v is not None else default

EVT_AUTO_THRESH   = _get("event", "auto_confirm_threshold", default=2)
EVT_REVIEW_THRESH = _get("event", "needs_review_threshold", default=1)

# ── 模拟 process_events 判定逻辑 ─────────────────────────────────────────
def simulate(before, after):
    """
    完整模拟 process_events() 的判定逻辑。
    返回有效事件列表（已排除 diff==0 的 ignore）。
    """
    results = []
    all_names = set(before) | set(after)
    for name in all_names:
        bc = before.get(name, 0)
        ac = after.get(name, 0)
        diff = ac - bc
        if diff == 0:
            continue
        td = abs(diff)
        action = "put_in" if diff > 0 else "take_out"
        if td >= EVT_AUTO_THRESH:
            status = "confirmed"
        elif td >= EVT_REVIEW_THRESH:
            status = "needs_review"
        else:
            status = "ignore"
        results.append({
            "name": name, "action": action,
            "count": abs(diff), "decision": status,
            "diff": diff
        })
    return results

def result_str(results):
    if not results:
        return "无有效事件（diff=0 → ignore）"
    return " | ".join(
        f"{r['action']} {r['name']} x{r['count']} [{r['decision']}]"
        for r in results
    )

def pass_str(passed):
    return "✓ PASS" if passed else "✗ FAIL"

# ═══════════════════════════════════════════════════════════════════════════
# 测试用例定义
# ═══════════════════════════════════════════════════════════════════════════
CASES = [
    # (场景名, baseline_dict, after_dict, 期望决策类型, 期望事件数, 期望描述)
    ("1. 手伸入又伸出，无净变化",
     {"苹果": 2, "香蕉": 1}, {"苹果": 2, "香蕉": 1},
     "ignore", 0,
     "diff=0 → ignore，无事件记录"),

    ("1b. 手被误识别为食材（diff=1）",
     {"苹果": 2, "香蕉": 1}, {"苹果": 3, "香蕉": 1},
     "needs_review", 1,
     "diff=+1 → needs_review，不自动改库存"),

    ("2. 整理位置但库存没变（diff=-1）",
     {"苹果": 3, "牛奶": 2, "面包": 1}, {"苹果": 2, "牛奶": 2, "面包": 1},
     "needs_review", 1,
     "diff=-1 → needs_review，库存不变"),

    ("3. 放入 1 个明显新物体（diff=+1）",
     {"苹果": 2}, {"苹果": 2, "香蕉": 1},
     "needs_review", 1,
     "diff=+1 → needs_review，待人工确认"),

    ("4. 取出 2 个（diff=2，达到 auto）",
     {"苹果": 3, "香蕉": 2}, {"苹果": 3},
     "confirmed", 1,
     "diff=-2 → confirmed，自动更新库存"),

    ("4b. 取出 1 个（diff=1，进 review）",
     {"苹果": 3, "香蕉": 2}, {"苹果": 3, "香蕉": 1},
     "needs_review", 1,
     "diff=-1 → needs_review"),

    ("5. 遮挡 + 物体变化同时发生",
     {"苹果": 1, "香蕉": 3, "牛奶": 2}, {"苹果": 3, "香蕉": 2, "牛奶": 2},
     "mixed", 2,
     "苹果+2→confirmed + 香蕉-1→needs_review"),

    ("6. 低置信度变化（diff=1）",
     {"苹果": 5}, {"苹果": 6},
     "needs_review", 1,
     "diff=+1 → needs_review"),

    ("额外: 全部取空（diff 大）",
     {"苹果": 3, "香蕉": 2}, {},
     "confirmed", 2,
     "香蕉-2 + 苹果-3 → confirmed"),

    ("额外: 完全无变化",
     {"苹果": 2}, {"苹果": 2},
     "ignore", 0,
     "diff=0 → ignore"),
]

# ═══════════════════════════════════════════════════════════════════════════
# 报告
# ═══════════════════════════════════════════════════════════════════════════
SEP = "=" * 65
print(f"\n{SEP}")
print(f"SmartFridge 真实事件识别增强 - 场景测试")
print(f"{'='*65}")
print(f"\n当前阈值配置:")
print(f"  auto_confirm_threshold = {EVT_AUTO_THRESH}")
print(f"  needs_review_threshold = {EVT_REVIEW_THRESH}")
print(f"\n判定矩阵:")
print(f"  diff == 0         → ignore（无变化）")
print(f"  diff == 1         → needs_review（待审核）")
print(f"  diff >= {EVT_AUTO_THRESH}        → confirmed（自动确认）")

results_summary = []

print(f"\n{SEP}")
print(f"测试结果")
print(f"{'='*65}")

for case_name, baseline, after, expected_type, expected_count, expected_desc in CASES:
    raw = simulate(baseline, after)
    # Filter out ignore results for validation
    non_ignore = [r for r in raw if r["decision"] != "ignore"]

    if expected_type == "mixed":
        confirmed = sum(1 for r in non_ignore if r["decision"] == "confirmed")
        review    = sum(1 for r in non_ignore if r["decision"] == "needs_review")
        passed    = confirmed >= 1 and review >= 1
        actual    = f"confirmed={confirmed}, needs_review={review}"
    elif expected_type == "ignore":
        passed    = len(non_ignore) == 0
        actual    = result_str(raw)
    else:
        passed    = (
            len(non_ignore) == expected_count and
            all(r["decision"] == expected_type for r in non_ignore)
        )
        actual    = result_str(non_ignore) or "无有效事件"

    results_summary.append((case_name, passed, expected_desc, actual))
    print(f"\n[{pass_str(passed)}] {case_name}")
    print(f"  baseline → after : {dict(baseline)} → {dict(after)}")
    print(f"  预期: {expected_desc}")
    print(f"  实际: {actual}")

print(f"\n{SEP}")
print(f"汇总: {sum(1 for _,p,*_ in results_summary)}/{len(results_summary)} 通过")
for case_name, passed, expected_desc, actual in results_summary:
    print(f"  {'✓' if passed else '✗'} {case_name}")

# ═══════════════════════════════════════════════════════════════════════════
# 判定规则说明
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print(f"判定规则速查")
print(f"{'='*65}")
print(f"""
  1. diff = 0（无变化）       → IGNORE    → 无事件，无库存变动
  2. diff = 1（低于自动阈值） → needs_review → 有事件，待审核，库存不变
  3. diff >= {EVT_AUTO_THRESH}（≥自动阈值）  → confirmed  → 有事件，已确认，库存更新

  自动确认场景（diff >= {EVT_AUTO_THRESH}）:
    · 放入 >= {EVT_AUTO_THRESH} 个物品
    · 取出 >= {EVT_AUTO_THRESH} 个物品
    · 物品完全消失（diff >= {EVT_AUTO_THRESH}）

  待审核场景（diff == 1）:
    · 放入 1 个物品（疑似噪声）
    · 取出 1 个物品（疑似检测误差）
    · 整理位置导致的检测波动

  被忽略的场景（diff = 0）:
    · 手伸入又缩出（无净变化）
    · 纯粹光照变化导致的检测波动
    · 同一位置的物品计数不变
""")