/* HTTP同步 - 使用原生Socket */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <time.h>

/* 解析URL中的host和port */
static int parse_url(const char *url, char *host, int *port, char *path)
{
    /* url格式: http://host:port/path */
    const char *p = url + 7; /* 跳过 http:// */
    const char *slash = strchr(p, '/');
    const char *colon = strchr(p, ':');

    if (colon && colon < slash) {
        /* host:port/path */
        int hlen = colon - p;
        memcpy(host, p, hlen);
        host[hlen] = '\0';
        *port = atoi(colon + 1);
    } else if (slash) {
        int hlen = slash - p;
        memcpy(host, p, hlen);
        host[hlen] = '\0';
        *port = 80;
    } else {
        strcpy(host, p);
        *port = 80;
    }

    if (slash)
        strcpy(path, slash);
    else
        strcpy(path, "/");

    return 0;
}

/* HTTP POST JSON数据到服务器 */
int sync_post_json(const char *server_url, const char *api_path, const char *json_data)
{
    char host[128];
    int port;
    char path[256];

    parse_url(server_url, host, &port, path);
    strcat(path, api_path);

    /* 创建socket */
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        printf("[Sync] socket创建失败\n");
        return -1;
    }

    /* 设置超时 */
    struct timeval tv = {3, 0}; /* 3秒超时 */
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    /* 解析host */
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);

    /* 尝试直接IP */
    if (inet_pton(AF_INET, host, &addr.sin_addr) <= 0) {
        struct hostent *he = gethostbyname(host);
        if (!he) {
            printf("[Sync] DNS解析失败: %s\n", host);
            close(sock);
            return -1;
        }
        memcpy(&addr.sin_addr, he->h_addr_list[0], he->h_length);
    }

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        printf("[Sync] 连接服务器失败 %s:%d\n", host, port);
        close(sock);
        return -1;
    }

    /* 构造HTTP请求 */
    char request[8192];
    int req_len = snprintf(request, sizeof(request),
        "POST %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s",
        path, host, (int)strlen(json_data), json_data);

    /* 发送 */
    send(sock, request, req_len, 0);

    /* 接收响应 (简单读取,忽略body) */
    char response[1024];
    recv(sock, response, sizeof(response) - 1, 0);

    close(sock);
    return 0;
}
