/* 事件检测逻辑 */
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#define MAX_OBJECTS 64
#define STABILITY_FRAMES 3

typedef struct {
    char name[64];
    int count;
} food_item_t;

typedef struct {
    char action[16];      /* "put_in" 或 "take_out" */
    char food_name[64];
    int count;
} event_t;

static food_item_t baseline_objects[MAX_OBJECTS];
static int baseline_count = 0;
static food_item_t current_objects[MAX_OBJECTS];
static int current_count = 0;
static int stable_counter[MAX_OBJECTS];
static int door_was_open = 0;
static int event_pending = 0;

/* 从检测结果更新current_objects */
void event_update_detections(const char **names, int *boxes, float *confs, int count)
{
    current_count = 0;
    for (int i = 0; i < count && current_count < MAX_OBJECTS; i++) {
        /* 查找是否已存在 */
        int found = -1;
        for (int j = 0; j < current_count; j++) {
            if (strcmp(current_objects[j].name, names[i]) == 0) {
                found = j;
                break;
            }
        }
        if (found >= 0) {
            current_objects[found].count++;
        } else {
            strncpy(current_objects[current_count].name, names[i], 63);
            current_objects[current_count].name[63] = '\0';
            current_objects[current_count].count = 1;
            current_count++;
        }
    }
}

/* 门状态变化通知 */
void event_door_changed(int door_is_closed)
{
    if (!door_was_open && !door_is_closed) {
        /* 门刚打开 - 记录基准 */
        door_was_open = 1;
        memcpy(baseline_objects, current_objects, sizeof(current_objects));
        baseline_count = current_count;
    } else if (door_was_open && door_is_closed) {
        /* 门刚关闭 - 触发检测 */
        door_was_open = 0;
        event_pending = 1;
    }
}

/* 生成事件, 返回事件数量 */
int event_generate(event_t *events, int max_events)
{
    if (!event_pending) return 0;
    event_pending = 0;

    int n = 0;

    /* 新增的 -> put_in */
    for (int i = 0; i < current_count && n < max_events; i++) {
        int found = -1;
        for (int j = 0; j < baseline_count; j++) {
            if (strcmp(current_objects[i].name, baseline_objects[j].name) == 0) {
                found = j;
                break;
            }
        }
        int prev_cnt = found >= 0 ? baseline_objects[found].count : 0;
        if (current_objects[i].count > prev_cnt) {
            strcpy(events[n].action, "put_in");
            strncpy(events[n].food_name, current_objects[i].name, 63);
            events[n].count = current_objects[i].count - prev_cnt;
            n++;
        }
    }

    /* 消失的 -> take_out */
    for (int i = 0; i < baseline_count && n < max_events; i++) {
        int found = -1;
        for (int j = 0; j < current_count; j++) {
            if (strcmp(baseline_objects[i].name, current_objects[j].name) == 0) {
                found = j;
                break;
            }
        }
        int cur_cnt = found >= 0 ? current_objects[found].count : 0;
        if (baseline_objects[i].count > cur_cnt) {
            strcpy(events[n].action, "take_out");
            strncpy(events[n].food_name, baseline_objects[i].name, 63);
            events[n].count = baseline_objects[i].count - cur_cnt;
            n++;
        }
    }

    return n;
}
