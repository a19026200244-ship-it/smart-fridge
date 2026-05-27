/* 事件检测 */
#ifndef EVENT_H
#define EVENT_H

#define MAX_OBJECTS 64

typedef struct {
    char action[16];
    char food_name[64];
    int count;
} event_t;

void event_update_detections(const char **names, int *boxes, float *confs, int count);
void event_door_changed(int door_is_closed);
int event_generate(event_t *events, int max_events);

#endif /* EVENT_H */
