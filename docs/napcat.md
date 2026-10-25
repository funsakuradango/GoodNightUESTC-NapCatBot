# NapCat OneBot HTTP 接入

GoodNightUESTC 作为独立服务调用已有 NapCat。AstrBot 可继续使用原有反向 WebSocket；本项目的私聊通知通过 OneBot HTTP 服务端发送，两者独立配置。

## NapCat 端准备

在 NapCat WebUI 的网络配置中新增或启用 HTTP 服务端，设置访问 Token 和容器内监听地址。下文以容器内 3000 端口为例，实际端口以你的配置为准。

- WebUI Token 与 OneBot HTTP Token 分别管理。
- 6099 通常是管理页面端口，不是消息 API。
- 跨容器访问时，HTTP 服务需监听容器内可达地址；仅监听容器 127.0.0.1 无法被其他容器访问。
- 同一 Docker 网络内不需要发布宿主机 HTTP 端口。

## 方式一：共用已有 Docker 网络

确认 NapCat 所在网络名和服务别名后，填写 `.env`：

```dotenv
BOT_NETWORK=<已有网络名>
NAPCAT_HTTP_URL=http://napcat:3000
NAPCAT_TOKEN=<OneBot HTTP Token>
QQ_USER_ID=<接收私聊的 QQ 号，仅数字>
```

`napcat` 必须是该网络中的实际可解析别名。覆盖文件仅将 cli、scheduler 接入外部网络，不创建或重启 NapCat：

```bash
docker compose -f compose.yaml -f compose.napcat.yaml config --quiet
docker compose -f compose.yaml -f compose.napcat.yaml run --rm cli status
docker compose -f compose.yaml -f compose.napcat.yaml --profile scheduled up -d scheduler
```

如果复用 qq-bot 部署，默认网络可能是 `qq-bot_bot`；不要把这个名称当作所有部署的固定值。

## 方式二：访问宿主机或远程 API

在 Docker Desktop 中，宿主机可达的 API 可使用 `http://host.docker.internal:<端口>`。Linux 需按实际路由或 host-gateway 配置处理，该名称不保证默认存在。

远程 API 使用 HTTPS、受限网络或 SSH 隧道，并确认容器能访问隧道端口。不要把不带访问控制的 OneBot HTTP 服务直接暴露到公网。只使用远程地址时无需加载 compose.napcat.yaml。

## 请求和结果

程序调用：

```http
POST /send_private_msg
Authorization: Bearer <OneBot HTTP Token>
Content-Type: application/json

{"user_id":"<QQ 号>","message":[{"type":"text","data":{"text":"<任务结果>"}}]}
```

成功要求 HTTP 请求正常、`status == "ok"`、`retcode == 0` 且返回 `data.message_id`。这代表接口接收，不代表收件人已读。客户端不跟随重定向，也不将 Token 交给系统代理。

## 通知补发

通知状态与业务结果分别写入 state.json。失败通知进入队列，每轮最多补发三条；scheduler 每 30 秒处理队列。手动补发：

```bash
docker compose -f compose.yaml -f compose.napcat.yaml run --rm cli flush-notifications
```

这条命令不重新签到。若消息已发出但响应丢失，补发可能重复，不能保证恰好一次送达。空置 NAPCAT_HTTP_URL 和 QQ_USER_ID 可关闭通知。

本项目当前只调用 HTTP 动作，没有接收 QQ 事件的入口；填写 API 地址不会自动接收私聊或群消息。
