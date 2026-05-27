/* 数据库管理 - SQLite操作 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sqlite3.h>

static sqlite3 *g_db = NULL;

int db_init(const char *db_path)
{
    int rc = sqlite3_open(db_path, &g_db);
    if (rc) {
        printf("[DB] 无法打开数据库: %s\n", sqlite3_errmsg(g_db));
        return -1;
    }

    const char *sql =
        "CREATE TABLE IF NOT EXISTS inventory ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  name TEXT NOT NULL,"
        "  category TEXT DEFAULT '',"
        "  count INTEGER DEFAULT 1,"
        "  first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ");"
        "CREATE TABLE IF NOT EXISTS events ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  action TEXT NOT NULL,"
        "  food_name TEXT NOT NULL,"
        "  count INTEGER DEFAULT 1"
        ");"
        "CREATE TABLE IF NOT EXISTS hardware_status ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  door_state TEXT DEFAULT 'closed',"
        "  light_state TEXT DEFAULT 'off',"
        "  cpu_temp REAL DEFAULT 0.0,"
        "  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ");"
        "INSERT OR IGNORE INTO hardware_status (id, door_state, light_state) VALUES (1, 'closed', 'off');";

    char *err = NULL;
    rc = sqlite3_exec(g_db, sql, NULL, NULL, &err);
    if (rc) {
        printf("[DB] 建表失败: %s\n", err);
        sqlite3_free(err);
        return -1;
    }
    printf("[DB] 数据库就绪: %s\n", db_path);
    return 0;
}

void db_close(void)
{
    if (g_db) {
        sqlite3_close(g_db);
        g_db = NULL;
    }
}

/* 库存操作 */
int db_add_or_update_item(const char *name, const char *category, int delta)
{
    /* 先查询是否存在 */
    char sql[512];
    snprintf(sql, sizeof(sql),
             "SELECT id, count FROM inventory WHERE name = ?");
    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(g_db, sql, -1, &stmt, NULL);
    sqlite3_bind_text(stmt, 1, name, -1, SQLITE_STATIC);

    int rc = sqlite3_step(stmt);
    if (rc == SQLITE_ROW) {
        int id = sqlite3_column_int(stmt, 0);
        int old_count = sqlite3_column_int(stmt, 1);
        int new_count = old_count + delta;
        sqlite3_finalize(stmt);

        if (new_count <= 0) {
            snprintf(sql, sizeof(sql), "DELETE FROM inventory WHERE id = %d", id);
            sqlite3_exec(g_db, sql, NULL, NULL, NULL);
        } else {
            snprintf(sql, sizeof(sql),
                     "UPDATE inventory SET count = %d, last_updated = CURRENT_TIMESTAMP WHERE id = %d",
                     new_count, id);
            sqlite3_exec(g_db, sql, NULL, NULL, NULL);
        }
    } else if (delta > 0) {
        sqlite3_finalize(stmt);
        snprintf(sql, sizeof(sql),
                 "INSERT INTO inventory (name, category, count) VALUES (?, ?, ?)");
        sqlite3_prepare_v2(g_db, sql, -1, &stmt, NULL);
        sqlite3_bind_text(stmt, 1, name, -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 2, category ? category : "", -1, SQLITE_STATIC);
        sqlite3_bind_int(stmt, 3, delta);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    } else {
        sqlite3_finalize(stmt);
    }
    return 0;
}

/* 记录事件 */
int db_add_event(const char *action, const char *food_name, int count)
{
    char sql[256];
    snprintf(sql, sizeof(sql),
             "INSERT INTO events (action, food_name, count) VALUES (?, ?, ?)");
    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(g_db, sql, -1, &stmt, NULL);
    sqlite3_bind_text(stmt, 1, action, -1, SQLITE_STATIC);
    sqlite3_bind_text(stmt, 2, food_name, -1, SQLITE_STATIC);
    sqlite3_bind_int(stmt, 3, count);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    return 0;
}

/* 更新硬件状态 */
int db_update_status(const char *door, const char *light, float cpu_temp)
{
    char sql[256];
    snprintf(sql, sizeof(sql),
             "UPDATE hardware_status SET door_state='%s', light_state='%s', cpu_temp=%.1f, updated_at=CURRENT_TIMESTAMP WHERE id=1",
             door, light, cpu_temp);
    sqlite3_exec(g_db, sql, NULL, NULL, NULL);
    return 0;
}

/* 获取全部数据用于同步到服务器 */
/* 回调辅助 */
struct sync_data {
    char *json;
    int len;
    int cap;
};

static void sync_append(struct sync_data *sd, const char *str)
{
    int slen = strlen(str);
    if (sd->len + slen + 1 > sd->cap) {
        sd->cap = (sd->len + slen) * 2 + 256;
        sd->json = (char *)realloc(sd->json, sd->cap);
    }
    memcpy(sd->json + sd->len, str, slen);
    sd->len += slen;
    sd->json[sd->len] = '\0';
}

static int inv_callback(void *data, int cols, char **vals, char **names)
{
    struct sync_data *sd = (struct sync_data *)data;
    char buf[512];
    if (sd->len > 2) sync_append(sd, ",");
    snprintf(buf, sizeof(buf),
             "{\"name\":\"%s\",\"category\":\"%s\",\"count\":%s}",
             vals[1] ? vals[1] : "", vals[2] ? vals[2] : "", vals[3] ? vals[3] : "0");
    sync_append(sd, buf);
    return 0;
}

static int evt_callback(void *data, int cols, char **vals, char **names)
{
    struct sync_data *sd = (struct sync_data *)data;
    char buf[512];
    if (sd->len > 2) sync_append(sd, ",");
    snprintf(buf, sizeof(buf),
             "{\"id\":%s,\"timestamp\":\"%s\",\"action\":\"%s\",\"food_name\":\"%s\",\"count\":%s}",
             vals[0] ? vals[0] : "0",
             vals[1] ? vals[1] : "",
             vals[2] ? vals[2] : "",
             vals[3] ? vals[3] : "",
             vals[4] ? vals[4] : "0");
    sync_append(sd, buf);
    return 0;
}

char *db_build_sync_json(void)
{
    struct sync_data sd = {0};
    sd.cap = 2048;
    sd.json = (char *)malloc(sd.cap);
    sd.len = 0;
    sd.json[0] = '\0';

    sync_append(&sd, "{");

    /* inventory */
    sync_append(&sd, "\"inventory\":[");
    sd.len = strlen(sd.json);
    sqlite3_exec(g_db, "SELECT * FROM inventory ORDER BY last_updated DESC", inv_callback, &sd, NULL);
    sync_append(&sd, "],");

    /* events */
    sync_append(&sd, "\"events\":[");
    sd.len = strlen(sd.json);
    sqlite3_exec(g_db, "SELECT * FROM events ORDER BY id ASC LIMIT 100", evt_callback, &sd, NULL);
    sync_append(&sd, "],");

    /* hardware_status */
    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(g_db, "SELECT door_state, light_state, cpu_temp, updated_at FROM hardware_status WHERE id=1", -1, &stmt, NULL);
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "\"hardware_status\":{\"door_state\":\"%s\",\"light_state\":\"%s\",\"cpu_temp\":%.1f}",
                 sqlite3_column_text(stmt, 0) ? (char*)sqlite3_column_text(stmt, 0) : "closed",
                 sqlite3_column_text(stmt, 1) ? (char*)sqlite3_column_text(stmt, 1) : "off",
                 sqlite3_column_double(stmt, 2));
        sync_append(&sd, buf);
    }
    sqlite3_finalize(stmt);

    sync_append(&sd, "}");

    return sd.json;
}
