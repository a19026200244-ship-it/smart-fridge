#!/usr/bin/env python3
"""
事件稳定模块测试 — 多帧稳定 + 门关后冷却期。

覆盖场景:
- FrameStabilizer: 单帧抖动 / 持续出现 / 中途消失 / person 过滤 / 数量取最大
- CooldownController: 门重开取消 / 冷却期满 / 画面提前稳定 / 多次循环
- 集成: 与 build_count_map 协同,模拟一次完整开门-关门-冷却
"""
import os, sys, time

PROJECT = "/home/jing/my-project/smartfridge"
os.chdir(PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "deploy"))

from event_stabilizer import FrameStabilizer, CooldownController

# ══════════════════════════════════════════════════
# 测试框架
# ══════════════════════════════════════════════════
TESTS = []


def test(name, desc):
    def decorator(fn):
        TESTS.append((name, desc, fn))
        return fn
    return decorator


def run():
    results = []
    for name, desc, fn in TESTS:
        try:
            passed, detail = fn()
        except Exception as e:
            import traceback
            passed, detail = False, f"异常: {e}\n{traceback.format_exc()}"
        results.append((name, desc, passed, detail))
        print(f"  {'✓' if passed else '✗'} {name}: {detail}")
    print("\n" + "=" * 60)
    print(f"汇总: {sum(1 for _, _, p, _ in results if p)}/{len(results)} 通过")
    for name, desc, passed, detail in results:
        print(f"  {'✓' if passed else '✗'} {name} — {detail}")
    return all(p for _, _, p, _ in results)


# ══════════════════════════════════════════════════
# FrameStabilizer 测试
# ══════════════════════════════════════════════════

@test("stab-初始为空", "新建的 stabilizer 不应包含任何物品")
def _():
    s = FrameStabilizer(stability_frames=3)
    out = s.update([{"name": "apple", "confidence": 0.9}])
    return (out == {}, f"第一帧返回 {out}")


@test("stab-单帧不通过", "物品只出现 1 帧, 不应被采纳")
def _():
    s = FrameStabilizer(stability_frames=3)
    s.update([{"name": "apple", "confidence": 0.9}])  # 1
    out = s.update([])                                 # 2 (消失)
    return (out == {}, f"2 帧后仍应为空, 实际 {out}")


@test("stab-连续 3 帧通过", "物品连续 3 帧被检出, 应进入稳定集合")
def _():
    s = FrameStabilizer(stability_frames=3)
    s.update([{"name": "apple"}])
    s.update([{"name": "apple"}])
    out = s.update([{"name": "apple"}])
    return (out == {"apple": 1}, f"第 3 帧应返回 apple=1, 实际 {out}")


@test("stab-中途消失清零", "物品连续 2 帧, 然后消失 1 帧, 再连续 3 帧, 计数应从 1 重新开始")
def _():
    s = FrameStabilizer(stability_frames=3)
    s.update([{"name": "apple"}])  # cnt=1
    s.update([{"name": "apple"}])  # cnt=2
    s.update([])                    # 消失
    s.update([{"name": "apple"}])  # cnt=1 (重置)
    s.update([{"name": "apple"}])  # cnt=2
    out = s.update([{"name": "apple"}])  # cnt=3 → 通过
    return (out == {"apple": 1}, f"重新计数后第 3 帧应通过, 实际 {out}")


@test("stab-person 永远过滤", "person 出现多少帧都不进入稳定集合")
def _():
    s = FrameStabilizer(stability_frames=3)
    for _ in range(10):
        s.update([{"name": "person", "confidence": 0.99}])
    out = s.update([{"name": "person"}])
    return (out == {}, f"person 不应被采纳, 实际 {out}")


@test("stab-数量取帧内观测最大", "同一物品在帧内出现 2 次, 输出 count=2")
def _():
    s = FrameStabilizer(stability_frames=2)
    s.update([
        {"name": "apple"},
        {"name": "apple"},
        {"name": "banana"},
    ])
    out = s.update([
        {"name": "apple"},
        {"name": "apple"},
        {"name": "banana"},
        {"name": "banana"},
    ])
    return (
        out.get("apple") == 2 and out.get("banana") == 2,
        f"apple={out.get('apple')} banana={out.get('banana')}",
    )


@test("stab-reset 清空", "reset 后计数应清零")
def _():
    s = FrameStabilizer(stability_frames=2)
    s.update([{"name": "apple"}])
    s.update([{"name": "apple"}])  # 已稳定
    s.reset()
    out = s.update([{"name": "apple"}])  # 从 cnt=1 重新开始
    return (out == {}, f"reset 后第 1 帧应为空, 实际 {out}")


@test("stab-snapshot 返回稳定集", "snapshot 返回的是已稳定的物品")
def _():
    s = FrameStabilizer(stability_frames=3)
    s.update([{"name": "apple"}])
    s.update([{"name": "apple"}])
    s.update([{"name": "apple"}])  # 稳定
    snap = s.snapshot()
    return (
        snap == {"apple": 1},
        f"snapshot={snap}",
    )


@test("stab-稳定度 1 帧也能通过", "stability_frames=1 时, 单帧即采纳")
def _():
    s = FrameStabilizer(stability_frames=1)
    out = s.update([{"name": "apple"}])
    return (out == {"apple": 1}, f"k=1 时应立即通过, 实际 {out}")


# ══════════════════════════════════════════════════
# CooldownController 测试
# ══════════════════════════════════════════════════

@test("cd-初始 ARMED", "新建的 cooldown 状态为 ARMED")
def _():
    cd = CooldownController(cooldown_seconds=2.0)
    return (
        cd.state == "ARMED" and not cd.is_cooling(),
        f"state={cd.state}",
    )


@test("cd-on_door_close 进入 COOLING", "门关后状态切到 COOLING")
def _():
    cd = CooldownController(cooldown_seconds=2.0)
    cd.on_door_close(t=0.0)
    return (
        cd.is_cooling() and cd.state == "COOLING",
        f"state={cd.state}",
    )


@test("cd-门重开触发 CANCELED", "冷却期内门重开, 状态变 CANCELED")
def _():
    door_open = [False]
    cd = CooldownController(
        cooldown_seconds=10.0,  # 故意设长, 让取消路径先触发
        door_is_open_callback=lambda: door_open[0],
    )
    cd.on_door_close(t=0.0)
    door_open[0] = True
    state = cd.tick(t=0.5)
    return (
        state == "CANCELED" and cd.is_canceled() and cd.cancel_reason == "door_reopened",
        f"state={state} reason={cd.cancel_reason}",
    )


@test("cd-冷却期满变 READY", "cooldown_seconds 后自动变 READY")
def _():
    cd = CooldownController(cooldown_seconds=2.0)
    cd.on_door_close(t=0.0)
    state1 = cd.tick(t=0.5)
    state2 = cd.tick(t=1.5)
    state3 = cd.tick(t=2.0)
    return (
        state1 == "COOLING" and state2 == "COOLING" and state3 == "READY",
        f"state1={state1} state2={state2} state3={state3}",
    )


@test("cd-画面稳定提前结束", "连续 3 帧快照相同即可提前结束冷却")
def _():
    # 关键: 第 1 次 tick 时 _last_snapshot=None 不算稳定,需 4 次 tick 才 READY
    snap = [{"apple": 2}, {"apple": 2}, {"apple": 2}, {"apple": 2}, {"apple": 2}]
    idx = [0]
    cd = CooldownController(
        cooldown_seconds=10.0,  # 故意设长, 走稳定路径
        stable_frames_required=3,
        take_snapshot_callback=lambda: snap[idx[0]],
    )
    cd.on_door_close(t=0.0)
    states = []
    for t, i in [(0.1, 1), (0.2, 2), (0.3, 3), (0.4, 4)]:
        idx[0] = i
        states.append(cd.tick(t=t))
    # 前 2 tick COOLING(stable_counter=0,1), 第 3 tick 仍 COOLING(counter=2),
    # 第 4 tick(counter=3) -> READY
    return (
        states == ["COOLING", "COOLING", "COOLING", "READY"],
        f"期望 [COOLING,COOLING,COOLING,READY], 实际 {states}",
    )


@test("cd-画面变化重置稳定计数", "画面变化时稳定计数应清零, 不能提前结束")
def _():
    # 模拟: 画面在中间变化, 计数器应重置, 需再稳定 3 帧才结束
    # 顺序: {a:2} → {a:3} → {a:2} → {a:2} → {a:2} → {a:2} → {a:2}
    # idx 0..6
    snap = [{"apple": 2}, {"apple": 3}, {"apple": 2}, {"apple": 2}, {"apple": 2}, {"apple": 2}, {"apple": 2}]
    idx = [0]
    cd = CooldownController(
        cooldown_seconds=10.0,  # 长, 走稳定路径
        stable_frames_required=3,
        take_snapshot_callback=lambda: snap[idx[0]],
    )
    cd.on_door_close(t=0.0)
    states = []
    for t, i in [(0.1, 1), (0.2, 2), (0.3, 3), (0.4, 4), (0.5, 5), (0.6, 6)]:
        idx[0] = i
        states.append(cd.tick(t=t))
    # 解析:
    #   tick1 idx=1 {a:3}  -> 与 None 不等 -> counter=0
    #   tick2 idx=2 {a:2}  -> 与 {a:3} 不等 -> counter=0
    #   tick3 idx=3 {a:2}  -> counter=1
    #   tick4 idx=4 {a:2}  -> counter=2
    #   tick5 idx=5 {a:2}  -> counter=3 -> READY
    #   tick6              -> 已 READY 不变
    expected = ["COOLING", "COOLING", "COOLING", "COOLING", "READY", "READY"]
    return (
        states == expected,
        f"期望 {expected}, 实际 {states}",
    )


@test("cd-reset 回到 ARMED", "reset 后可以开始新一轮")
def _():
    cd = CooldownController(cooldown_seconds=0.5)
    cd.on_door_close(t=0.0)
    cd.tick(t=0.6)  # READY
    cd.reset()
    return (
        cd.state == "ARMED" and not cd.is_cooling() and not cd.is_ready(),
        f"reset 后 state={cd.state}",
    )


@test("cd-elapsed 计时", "elapsed 应随时间递增")
def _():
    cd = CooldownController(cooldown_seconds=5.0)
    cd.on_door_close(t=0.0)
    cd.tick(t=1.0)
    e1 = cd.elapsed
    cd.tick(t=3.0)
    e2 = cd.elapsed
    return (
        abs(e1 - 1.0) < 0.01 and abs(e2 - 3.0) < 0.01,
        f"elapsed1={e1:.2f} elapsed2={e2:.2f}",
    )


@test("cd-force_cancel 手动取消", "force_cancel 可以主动取消冷却")
def _():
    cd = CooldownController(cooldown_seconds=10.0)
    cd.on_door_close(t=0.0)
    cd.force_cancel(reason="test")
    return (
        cd.is_canceled() and cd.cancel_reason == "test",
        f"state={cd.state} reason={cd.cancel_reason}",
    )


# ══════════════════════════════════════════════════
# 集成测试 — 模拟一次完整的 开门-关门-冷却
# ══════════════════════════════════════════════════

@test("集成-开门放1个苹果,关门后稳定提前结束", "画面稳定时 cooldown 提前结束, 节省等待时间")
def _():
    stab = FrameStabilizer(stability_frames=3)
    door_open = [True]
    snap = [None]
    cd = CooldownController(
        cooldown_seconds=10.0,  # 长, 走稳定路径
        stable_frames_required=3,
        door_is_open_callback=lambda: door_open[0],
        take_snapshot_callback=lambda: snap[0] or {},
    )

    # 开门 → DETECTING 累积 3 帧
    for _ in range(3):
        stab.update([{"name": "apple"}])

    # 关门 → COOLING
    door_open[0] = False
    snap[0] = {"apple": 1}
    cd.on_door_close(t=0.0)

    # 冷却期内画面稳定 -> 4 次 tick 后 READY
    states = []
    for t in [0.5, 1.0, 1.5, 2.0]:
        states.append(cd.tick(t=t))

    return (
        states == ["COOLING", "COOLING", "COOLING", "READY"],
        f"画面稳定后第 4 tick READY, 实际 {states}",
    )


@test("集成-开门放1个苹果,关门3秒后时间触发", "画面变化时走纯时间路径")
def _():
    stab = FrameStabilizer(stability_frames=3)
    door_open = [True]
    # snap 每次返回新的 dict 但内容变化, 走纯时间路径
    counter = [0]
    def snapshot_fn():
        counter[0] += 1
        return {"apple": counter[0] % 2}  # 一直变化

    cd = CooldownController(
        cooldown_seconds=2.0,  # 短, 走时间路径
        stable_frames_required=3,
        door_is_open_callback=lambda: door_open[0],
        take_snapshot_callback=snapshot_fn,
    )

    for _ in range(3):
        stab.update([{"name": "apple"}])

    door_open[0] = False
    cd.on_door_close(t=0.0)

    states = []
    for t in [0.5, 1.5, 2.0, 2.5]:
        states.append(cd.tick(t=t))

    return (
        states == ["COOLING", "COOLING", "READY", "READY"],
        f"时间到后 READY, 实际 {states}",
    )


@test("集成-开门又关门但中间开门,本轮取消", "模拟多次开关门")
def _():
    stab = FrameStabilizer(stability_frames=3)
    door_open = [True]
    snap = [None]
    cd = CooldownController(
        cooldown_seconds=10.0,  # 长冷却, 强制走取消路径
        stable_frames_required=3,
        door_is_open_callback=lambda: door_open[0],
        take_snapshot_callback=lambda: snap[0] or {},
    )

    # 第一次开门累积 3 帧 apple
    for _ in range(3):
        stab.update([{"name": "apple"}])

    # 关门进入冷却
    door_open[0] = False
    snap[0] = {"apple": 1}
    cd.on_door_close(t=0.0)
    cd.tick(t=0.1)

    # 1 秒后门又开了
    door_open[0] = True
    state = cd.tick(t=1.0)
    assert state == "CANCELED", f"门重开应取消, 实际 {state}"

    # reset, 重新开门累积 orange
    cd.reset()
    stab.reset()
    door_open[0] = True
    for _ in range(3):
        stab.update([{"name": "orange"}])
    baseline = stab.snapshot()

    return (
        baseline == {"orange": 1} and cd.state == "ARMED",
        f"reset 后 baseline={baseline}, cd.state={cd.state}",
    )


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)