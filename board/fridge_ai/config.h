/* 冰箱食材识别系统 - 配置 */
#ifndef CONFIG_H
#define CONFIG_H

/* 网络 */
#define SERVER_URL      "http://192.168.2.100:5000"
#define SERVER_SYNC_API "/api/sync"
#define SYNC_INTERVAL   2       /* 秒 */

/* GPIO */
#define DOOR_PIN        32      /* 门磁传感器 */
#define RELAY_PIN       40      /* 继电器 */

/* AI */
#define MODEL_WIDTH     640
#define MODEL_HEIGHT    640
#define DETECTION_FILE  "/tmp/fridge_detections.json"
#define JSON_INTERVAL   1.0     /* 秒 */

/* SQLite */
#define DB_PATH         "/userdata/fridge.db"

/* LCD */
#define FB_DEVICE       "/dev/fb0"
#define UI_REFRESH_MS   2000

#endif /* CONFIG_H */
