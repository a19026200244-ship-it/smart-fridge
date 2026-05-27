# SQLite数据库管理模块
import sqlite3
import os
from datetime import datetime


class FridgeDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT '',
                    count INTEGER DEFAULT 1,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action TEXT NOT NULL,
                    food_name TEXT NOT NULL,
                    count INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hardware_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    door_state TEXT DEFAULT 'closed',
                    light_state TEXT DEFAULT 'off',
                    cpu_temp REAL DEFAULT 0.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO hardware_status (id, door_state, light_state)
                VALUES (1, 'closed', 'off')
            """)

    # ========== 食材库存操作 ==========

    def get_inventory(self):
        """获取所有食材库存"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, category, count, first_seen, last_updated FROM inventory ORDER BY last_updated DESC"
            ).fetchall()
        return [
            {"id": r[0], "name": r[1], "category": r[2], "count": r[3],
             "first_seen": r[4], "last_updated": r[5]}
            for r in rows
        ]

    def add_or_update_item(self, name, category="", delta=1):
        """添加或更新食材: 存在则更新count, 不存在则插入"""
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, count FROM inventory WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                new_count = existing[1] + delta
                if new_count <= 0:
                    conn.execute("DELETE FROM inventory WHERE id = ?", (existing[0],))
                else:
                    conn.execute(
                        "UPDATE inventory SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_count, existing[0])
                    )
            elif delta > 0:
                conn.execute(
                    "INSERT INTO inventory (name, category, count) VALUES (?, ?, ?)",
                    (name, category, delta)
                )

    def remove_item(self, name, count=1):
        """减少食材数量"""
        return self.add_or_update_item(name, delta=-count)

    def get_item_count(self, name):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT count FROM inventory WHERE name = ?", (name,)).fetchone()
            return row[0] if row else 0

    # ========== 事件记录操作 ==========

    def add_event(self, action, food_name, count=1):
        """记录事件: action = 'put_in' 或 'take_out'"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (action, food_name, count) VALUES (?, ?, ?)",
                (action, food_name, count)
            )

    def get_events(self, limit=50):
        """获取最近的事件记录"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, action, food_name, count FROM events ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "action": r[2], "food_name": r[3], "count": r[4]}
            for r in reversed(rows)
        ]

    # ========== 硬件状态操作 ==========

    def update_hardware_status(self, door_state=None, light_state=None, cpu_temp=None):
        fields = []
        values = []
        if door_state is not None:
            fields.append("door_state = ?")
            values.append(door_state)
        if light_state is not None:
            fields.append("light_state = ?")
            values.append(light_state)
        if cpu_temp is not None:
            fields.append("cpu_temp = ?")
            values.append(cpu_temp)
        if not fields:
            return
        fields.append("updated_at = CURRENT_TIMESTAMP")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE hardware_status SET {', '.join(fields)} WHERE id = 1",
                values
            )

    def get_hardware_status(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT door_state, light_state, cpu_temp, updated_at FROM hardware_status WHERE id = 1").fetchone()
            if row:
                return {"door_state": row[0], "light_state": row[1], "cpu_temp": row[2], "updated_at": row[3]}
            return {"door_state": "unknown", "light_state": "unknown", "cpu_temp": 0.0, "updated_at": ""}

    # ========== 同步用 ==========

    def get_all_data(self):
        """获取全部数据用于同步到服务器"""
        return {
            "inventory": self.get_inventory(),
            "events": self.get_events(limit=100),
            "hardware_status": self.get_hardware_status(),
        }

    def get_pending_events(self, last_sync_id=0):
        """获取上次同步之后的新事件"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, action, food_name, count FROM events WHERE id > ? ORDER BY id ASC",
                (last_sync_id,)
            ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "action": r[2], "food_name": r[3], "count": r[4]}
            for r in rows
        ]
