# 事件检测模块 - 判断食材放入/取出事件
import json
import time
from collections import defaultdict


class EventDetector:
    """基于AI检测结果和门磁状态的变化检测器"""

    def __init__(self, stability_frames=3):
        self.stability_frames = stability_frames  # 画面稳定需要连续多少帧
        self.prev_objects = {}     # 上一次稳定画面中的物体 {name: count}
        self.current_objects = {}  # 当前累积的物体
        self.stable_counter = {}   # 物体稳定计数 {name: consecutive_frames}
        self.door_was_open = False
        self.event_pending = False
        self.baseline_frame_objects = {}  # 门开前的基准画面

    def update_detections(self, detections, door_is_closed):
        """
        输入当前帧的检测结果和门状态
        detections: [{"name": "苹果", "confidence": 0.9, "box": [...]}, ...]
        返回: 事件列表 [{"action": "put_in", "food_name": "苹果", "count": 1}, ...]
        """
        events = []

        # 汇总当前帧检测到的物体
        current = defaultdict(int)
        for d in detections:
            name = d.get("name", "unknown")
            current[name] += 1

        # 门状态变化检测
        if not self.door_was_open and not door_is_closed:
            # 门刚打开，记录基准画面
            self.door_was_open = True
            self.baseline_frame_objects = dict(self.prev_objects)
            self.current_objects = {}
            self.stable_counter = {}

        elif self.door_was_open and door_is_closed:
            # 门刚关闭，触发事件判断
            self.door_was_open = False
            self.event_pending = True

        # 如果门开着，累积稳定检测结果
        if not door_is_closed:
            # 更新物体稳定计数
            for name in current:
                if name not in self.stable_counter:
                    self.stable_counter[name] = 0
                self.stable_counter[name] += 1

            # 移除不再出现的物体
            gone = [n for n in self.stable_counter if n not in current]
            for n in gone:
                del self.stable_counter[n]

            # 稳定出现超过阈值的物体加入current_objects
            for name, cnt in self.stable_counter.items():
                if cnt >= self.stability_frames:
                    self.current_objects[name] = max(
                        self.current_objects.get(name, 0),
                        current[name]
                    )

        # 门关后产生事件
        if self.event_pending:
            self.event_pending = False
            events = self._compare_and_generate_events()
            self.current_objects = {}

        # 持续更新prev_objects（门关着时也更新，作为稳定基准）
        if door_is_closed and not self.door_was_open:
            for name, cnt in current.items():
                if name not in self.stable_counter:
                    self.stable_counter[name] = 0
                self.stable_counter[name] += 1
            # 缓慢更新基准
            for name, cnt in list(self.stable_counter.items()):
                if name not in current:
                    del self.stable_counter[name]
                elif self.stable_counter[name] >= self.stability_frames * 2:
                    self.prev_objects[name] = current[name]

        return events

    def _compare_and_generate_events(self):
        """对比开关门期间的画面变化，生成事件"""
        events = []

        # 新增的物体 → 放入
        for name, count in self.current_objects.items():
            prev_count = self.baseline_frame_objects.get(name, 0)
            if count > prev_count:
                events.append({
                    "action": "put_in",
                    "food_name": name,
                    "count": count - prev_count
                })
                self.prev_objects[name] = self.prev_objects.get(name, 0) + (count - prev_count)

        # 消失的物体 → 取出
        for name, count in self.baseline_frame_objects.items():
            cur_count = self.current_objects.get(name, 0)
            if count > cur_count:
                events.append({
                    "action": "take_out",
                    "food_name": name,
                    "count": count - cur_count
                })
                new_count = self.prev_objects.get(name, 0) - (count - cur_count)
                if new_count <= 0:
                    self.prev_objects.pop(name, None)
                else:
                    self.prev_objects[name] = new_count

        return events

    def get_current_summary(self):
        """获取当前画面中食材的汇总"""
        return dict(self.prev_objects)
