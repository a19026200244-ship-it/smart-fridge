/* GPIO控制 - sysfs操作 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>

#define SYSFS_GPIO_EXPORT   "/sys/class/gpio/export"
#define SYSFS_GPIO_UNEXPORT "/sys/class/gpio/unexport"

static int gpio_export(int pin)
{
    int fd = open(SYSFS_GPIO_EXPORT, O_WRONLY);
    if (fd < 0) return -1;
    char buf[16];
    int len = snprintf(buf, sizeof(buf), "%d", pin);
    write(fd, buf, len);
    close(fd);
    usleep(100000);
    return 0;
}

static int gpio_unexport(int pin)
{
    int fd = open(SYSFS_GPIO_UNEXPORT, O_WRONLY);
    if (fd < 0) return -1;
    char buf[16];
    int len = snprintf(buf, sizeof(buf), "%d", pin);
    write(fd, buf, len);
    close(fd);
    return 0;
}

static int gpio_set_direction(int pin, const char *dir)
{
    char path[64];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/direction", pin);
    int fd = open(path, O_WRONLY);
    if (fd < 0) return -1;
    write(fd, dir, strlen(dir));
    close(fd);
    return 0;
}

static int gpio_read(int pin)
{
    char path[64];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/value", pin);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    char val;
    read(fd, &val, 1);
    close(fd);
    return val == '1' ? 1 : 0;
}

static int gpio_write(int pin, int value)
{
    char path[64];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/value", pin);
    int fd = open(path, O_WRONLY);
    if (fd < 0) return -1;
    write(fd, value ? "1" : "0", 1);
    close(fd);
    return 0;
}

/* Public API */
int door_init(int pin)
{
    gpio_export(pin);
    usleep(100000);
    gpio_set_direction(pin, "in");
    return 0;
}

int door_read(int pin)
{
    return gpio_read(pin);
}

void door_close(int pin)
{
    gpio_unexport(pin);
}

int relay_init(int pin)
{
    gpio_export(pin);
    usleep(100000);
    gpio_set_direction(pin, "out");
    gpio_write(pin, 0);
    return 0;
}

void relay_on(int pin)  { gpio_write(pin, 1); }
void relay_off(int pin) { gpio_write(pin, 0); }
int relay_state(int pin) { return gpio_read(pin); }
void relay_close(int pin) { gpio_unexport(pin); }
