/* GPIO控制 */
#ifndef GPIO_H
#define GPIO_H

int door_init(int pin);
int door_read(int pin);    /* 返回1=门关闭(高电平), 0=门打开 */
void door_close(int pin);

int relay_init(int pin);
void relay_on(int pin);
void relay_off(int pin);
int relay_state(int pin);
void relay_close(int pin);

#endif /* GPIO_H */
