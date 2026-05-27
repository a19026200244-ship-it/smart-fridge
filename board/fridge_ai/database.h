/* 数据库管理 */
#ifndef DATABASE_H
#define DATABASE_H

int db_init(const char *db_path);
void db_close(void);
int db_add_or_update_item(const char *name, const char *category, int delta);
int db_add_event(const char *action, const char *food_name, int count);
int db_update_status(const char *door, const char *light, float cpu_temp);
char *db_build_sync_json(void);
void sync_json_free(char *json);

#endif /* DATABASE_H */
