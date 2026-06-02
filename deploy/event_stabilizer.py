"""
SmartFridge 事件稳定模块 — 多帧稳定 + 门关后冷却期。

解决赛题明确点出的两个真实事件识别问题：

1. 单帧误检 / 手部短暂遮挡 / AI 抽风导致假事件
   -> FrameStabilizer 多帧稳定器

2. 门关瞬间手还在、物品没放稳导致的误判
   -> CooldownController 冷却期控制器

两个类都可独立使用，也可在 fridge_mgr.py 主循环里组合。
本模块不依赖 GPIO / 摄像头 / Flask，纯逻辑便于单元测试。
"""
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple


# 默认要排除的物品名。'person' 是手/人被误识别时的兜底，
# 任何 person 都不会进入库存（即使多帧稳定也不采纳）。
DEFAULT_EXCLUDE_NAMES: Tuple[str, ...] = ("person",)


class FrameStabilizer:
    """
    多帧稳定器：一个物品连续 K 帧都被检出才进入稳定集合。

    行为：
    - 跳过 ``exclude_names``（默认 'person'）。
    - 计数器连续递增，达到 ``stability_frames`` 才确认。
    - 物品从画面消失立即清零（不留惯性）。
    - 输出：当前帧的稳定物品及数量 {name: count}。

    使用场景：
        stab = FrameStabilizer(stability_frames=3)
        for det in frame_stream:
            stable = stab.update(det)
            # stable 只包含"连续 >= 3 帧出现"的物品
        stab.reset()
    """

    def __init__(
        self,
        stability_frames: int = 3,
        exclude_names: Tuple[str, ...] = DEFAULT_EXCLUDE_NAMES,
    ):
        if stability_frames < 1:
            stability_frames = 1
        self.stability_frames = stability_frames
        self.exclude_names = set(exclude_names or ())
        # name -> 连续出现帧数
        self._counter: Dict[str, int] = {}
        # 最近一次确认时的稳定集合（用于 snapshot 和冷却期稳定度比较）
        self._last_stable: Dict[str, int] = {}

    def update(self, detections: Optional[List[dict]]) -> Dict[str, int]:
        """
        输入当前帧的检测列表，返回经过多帧稳定过滤的 {name: count}。

        同一物品若在多帧里都出现，取该帧观测到的最大数量。
        """
        current: Dict[str, int] = defaultdict(int)
        for d in detections or []:
            name = d.get("name")
            if not name or name in self.exclude_names:
                continue
            current[name] += 1
        present = set(current.keys())

        # 递增 / 清零
        for name in present:
            self._counter[name] = self._counter.get(name, 0) + 1
        for name in list(self._counter.keys()):
            if name not in present:
                del self._counter[name]

        # 输出：连续 K 帧以上才确认
        result: Dict[str, int] = {}
        for name in present:
            if self._counter[name] >= self.stability_frames:
                cnt = current[name]
                if cnt > result.get(name, 0):
                    result[name] = cnt
        self._last_stable = dict(result)
        return result

    def reset(self) -> None:
        """重置状态（开门 / 关门 / 阶段切换时调用）。"""
        self._counter = {}
        self._last_stable = {}

    def snapshot(self) -> Dict[str, int]:
        """当前已稳定的画面（只读拷贝），用于冷却期 / 外部轮询。"""
        return dict(self._last_stable)


class CooldownController:
    """
    门关后冷却期控制器。

    状态机：
        ARMED     等待门关闭触发
        COOLING   冷却中（主循环每个 tick 调用 ``tick()``）
        READY     冷却完成，可以 ``process_events``
        CANCELED  冷却期内门重新打开，本轮取消

    满足下列任一条件时进入 READY：
    - ``cooldown_seconds`` 时间已到
    - 画面已连续 ``stable_frames_required`` 帧完全一致

    用法：
        cd = CooldownController(
            cooldown_seconds=3.0,
            door_is_open_callback=lambda: not door.read(),
            take_snapshot_callback=lambda: build_count_map(parse_detection_details()),
        )
        # 门刚关
        cd.on_door_close()
        # 主循环
        while True:
            state = cd.tick()
            if state == "READY":
                process_events(...)
                cd.reset()
            elif state == "CANCELED":
                cd.reset()
    """

    STATE_ARMED = "ARMED"
    STATE_COOLING = "COOLING"
    STATE_READY = "READY"
    STATE_CANCELED = "CANCELED"

    def __init__(
        self,
        cooldown_seconds: float = 3.0,
        stable_frames_required: int = 3,
        door_is_open_callback: Optional[Callable[[], bool]] = None,
        take_snapshot_callback: Optional[Callable[[], Dict[str, int]]] = None,
    ):
        if cooldown_seconds < 0:
            cooldown_seconds = 0.0
        if stable_frames_required < 1:
            stable_frames_required = 1
        self.cooldown_seconds = float(cooldown_seconds)
        self.stable_frames_required = int(stable_frames_required)
        self.door_is_open = door_is_open_callback
        self.take_snapshot = take_snapshot_callback
        self._state = self.STATE_ARMED
        self._cooldown_start: Optional[float] = None
        self._stable_counter = 0
        self._last_snapshot: Optional[Tuple[Tuple[str, int], ...]] = None
        self._cancel_reason: str = ""
        self._last_now: Optional[float] = None  # 最近一次 tick 的时间基准

    # ----- 状态查询 -----
    @property
    def state(self) -> str:
        return self._state

    def is_cooling(self) -> bool:
        return self._state == self.STATE_COOLING

    def is_ready(self) -> bool:
        return self._state == self.STATE_READY

    def is_canceled(self) -> bool:
        return self._state == self.STATE_CANCELED

    @property
    def cancel_reason(self) -> str:
        return self._cancel_reason

    @property
    def elapsed(self) -> float:
        """冷却已用时间（秒），未启动时为 0。

        优先使用最近一次 tick 传入的时间（便于测试），
        未 tick 时 fallback 到 time.time()。"""
        if self._cooldown_start is None:
            return 0.0
        if self._last_now is not None:
            return self._last_now - self._cooldown_start
        return time.time() - self._cooldown_start

    # ----- 控制流 -----
    def on_door_close(self, t: Optional[float] = None) -> None:
        """门刚关闭时调用，进入冷却期。"""
        self._state = self.STATE_COOLING
        self._cooldown_start = t if t is not None else time.time()
        self._stable_counter = 0
        self._last_snapshot = None
        self._cancel_reason = ""

    def tick(self, t: Optional[float] = None) -> str:
        """
        每个 tick（主循环一次）调用一次。
        监控门重开 + 画面稳定度，更新状态。
        返回当前状态字符串。
        """
        if self._state != self.STATE_COOLING:
            return self._state

        now = t if t is not None else time.time()
        self._last_now = now  # 记录 tick 基准,供 elapsed 查询

        # 门重新打开 -> 取消本轮
        if self.door_is_open and self.door_is_open():
            self._state = self.STATE_CANCELED
            self._cancel_reason = "door_reopened"
            return self._state

        # 画面稳定度：取一帧 count_map，连续 N 帧完全一致算稳定
        if self.take_snapshot:
            snap = self.take_snapshot() or {}
            key = tuple(sorted(snap.items()))
            if key == self._last_snapshot:
                self._stable_counter += 1
            else:
                self._stable_counter = 0
            self._last_snapshot = key

        elapsed = now - (self._cooldown_start if self._cooldown_start is not None else now)
        cooldown_done = elapsed >= self.cooldown_seconds
        stable_done = self._stable_counter >= self.stable_frames_required

        if cooldown_done or stable_done:
            self._state = self.STATE_READY
        return self._state

    def reset(self) -> None:
        """处理完一轮后调用，回到 ARMED 等待下一轮。"""
        self._state = self.STATE_ARMED
        self._cooldown_start = None
        self._stable_counter = 0
        self._last_snapshot = None
        self._cancel_reason = ""
        self._last_now = None

    def force_cancel(self, reason: str = "manual") -> None:
        """外部主动取消（保留 cancel 后可手动 reset 复用）。"""
        if self._state == self.STATE_COOLING:
            self._state = self.STATE_CANCELED
            self._cancel_reason = reason


__all__ = [
    "FrameStabilizer",
    "CooldownController",
    "DEFAULT_EXCLUDE_NAMES",
]