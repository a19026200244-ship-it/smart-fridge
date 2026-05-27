# GPIO控制模块 - 门磁传感器 + 继电器
import time
from periphery import GPIO


class DoorSensor:
    """门磁传感器 (GPIO32, 吸合=高电平=门关闭)"""

    def __init__(self, pin=32):
        self.pin = pin
        self.gpio = GPIO(pin, "in")
        self._last_state = self.read()

    def read(self):
        """读取门磁状态: True=门关闭, False=门打开"""
        return self.gpio.read()

    @property
    def is_closed(self):
        return self.read()

    @property
    def is_open(self):
        return not self.read()

    def wait_for_edge(self, edge="both", timeout=None):
        """等待电平变化, edge: 'rising'/'falling'/'both'"""
        self.gpio.edge = edge
        start = time.time()
        while True:
            if self.gpio.poll(timeout if timeout else 0.1):
                new_state = self.read()
                self._last_state = new_state
                return new_state
            if timeout and time.time() - start > timeout:
                return None

    def get_event(self):
        """检测门状态变化, 返回事件类型或None"""
        current = self.read()
        if current != self._last_state:
            prev = self._last_state
            self._last_state = current
            if not prev and current:
                return "door_closed"  # 门关上了
            elif prev and not current:
                return "door_opened"  # 门打开了
        return None

    def close(self):
        self.gpio.close()


class Relay:
    """继电器控制 (GPIO40, 高电平=闭合=灯带亮)"""

    def __init__(self, pin=40):
        self.pin = pin
        self.gpio = GPIO(pin, "out")
        self.gpio.write(False)

    @property
    def is_on(self):
        return self.gpio.read()

    def turn_on(self):
        """开灯"""
        self.gpio.write(True)

    def turn_off(self):
        """关灯"""
        self.gpio.write(False)

    def toggle(self):
        """切换灯状态"""
        self.gpio.write(not self.gpio.read())

    def close(self):
        self.gpio.write(False)
        self.gpio.close()
