# 服务器数据同步模块
import json
import time
import urllib.request
import urllib.error
from config import SERVER_URL


class ServerSync:
    def __init__(self, server_url=None):
        self.server_url = server_url or SERVER_URL
        self.last_sync_event_id = 0

    def _post(self, endpoint, data):
        """发送POST请求到服务器"""
        url = f"{self.server_url}{endpoint}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            print(f"[Sync] 连接服务器失败: {e}")
            return None
        except Exception as e:
            print(f"[Sync] 同步异常: {e}")
            return None

    def sync_all(self, data):
        """全量同步"""
        result = self._post("/api/sync", data)
        if result and result.get("ok"):
            print(f"[Sync] 全量同步成功")
        return result

    def sync_events(self, events):
        """增量同步事件"""
        if not events:
            return True
        result = self._post("/api/sync/events", {"events": events})
        if result and result.get("ok"):
            self.last_sync_event_id = max(e["id"] for e in events)
            print(f"[Sync] 事件同步成功: {len(events)}条")
            return True
        return False

    def sync_inventory(self, inventory):
        """同步库存"""
        result = self._post("/api/sync/inventory", {"inventory": inventory})
        if result and result.get("ok"):
            print(f"[Sync] 库存同步成功: {len(inventory)}种")
        return result

    def sync_hardware_status(self, status):
        """同步硬件状态"""
        result = self._post("/api/sync/status", status)
        return result

    def check_connection(self):
        """检查与服务器的连接"""
        try:
            req = urllib.request.Request(f"{self.server_url}/api/ping")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.read().decode("utf-8") == "pong"
        except Exception:
            return False
