# 配置说明

配置放在仓库根目录 `.env`，文件权限必须为 0600；模板为 `.env.example`。密码包含 `$` 等插值字符时使用单引号。实际凭据通过容器环境注入，不写入镜像或文档。

## 主要字段

| 字段 | 说明 |
| --- | --- |
| `UESTC_USERNAME` / `UESTC_PASSWORD` | 学校账号；可留空后人工登录，学号非空时用于身份核对 |
| `CHECKIN_ADAPTER` | 示例为 uestc；generic 使用通用状态选择器 |
| `CHECKIN_ENTRY_SELECTOR` / `CHECKIN_URL` | 学校首页入口或直接业务地址，任选一种 |
| `CHECKIN_FRAME_SELECTOR` | 可选，业务位于 iframe 时填写 |
| `CHECKIN_SUBMIT_SELECTOR` / `CHECKIN_CONFIRM_SELECTOR` | 提交与确认按钮，须核对当前页面 |
| `WECHAT_TICKET_ENABLED` | 示例为 true，UESTC 提交前通过协议申请新票据 |
| `CHECKIN_ENABLED` | 默认 false，必须显式启用才允许提交 |
| `GEO_LATITUDE` / `GEO_LONGITUDE` | WGS84 坐标，同时填写或同时留空；正式 UESTC 提交要求配置完整 |
| `GEO_ACCURACY_METERS` | 浏览器定位配置的精度值 |
| `CHECKIN_HOUR` / `CHECKIN_MINUTE` | 北京时间每日调度时间，默认 21:05 |
| `TASK_TIMEOUT_SECONDS` | 示例 300 秒，整个任务的时间上限 |
| `PAGE_TIMEOUT_SECONDS` / `AUTH_WAIT_SECONDS` | 页面与认证状态等待时间 |
| `NAPCAT_HTTP_URL` / `NAPCAT_TOKEN` / `QQ_USER_ID` | 可选 QQ 私聊通知配置 |
| `BOT_NETWORK` | 使用网络覆盖配置时，填写已有 Docker 网络名 |

`CHECKIN_PENDING_SELECTOR`、`CHECKIN_COMPLETED_SELECTOR`、`CHECKIN_TIME_SELECTOR` 用于 generic 适配器，UESTC 适配器通过学校返回的今日记录与有效时段判断状态。固定 generic 选择器不能替代对真实学校页面的核对。

Docker 镜像默认将 `WECHAT_PYTHON` 设置为独立 Python 3.13 路径，通常不需要在 `.env` 覆盖；设置为空会失去这个默认值。

位置配置会传给容器浏览器的 Geolocation；学校页面负责后续坐标处理。不要混用 WGS84 和 GCJ-02。配置成功不等于服务端接受，也不等于设备实时定位或人员在场。

## 命令与副作用

| 命令 | 学校操作 | QQ 消息 |
| --- | --- | --- |
| `check` | 检查已有会话 | 无 |
| `login` | 必要时尝试一次密码登录 | 无 |
| `manual-login` | 交互登录，由 debug 容器使用 | 无 |
| `resume` | 清除本地认证暂停标记，不登录 | 无 |
| `wechat-authorize` | 扫码、核对身份、申请票据 | 无 |
| `wechat-ticket` | 使用缓存凭据申请票据 | 无 |
| `status` | 查询今日记录 | 配置后发送状态 |
| `run` | 条件满足时执行一次签到并回查 | 配置后发送结果 |
| `flush-notifications` | 不访问学校、不签到 | 补发待发通知 |
| `schedule` | 在配置时间启动任务 | 按结果及队列发送 |
| `demo` | 模拟学校与通知服务 | 无真实消息 |

只查询且不发送 QQ 消息：

```bash
docker compose run --rm -e NAPCAT_HTTP_URL= -e QQ_USER_ID= -e NAPCAT_TOKEN= cli status
```

退出码 0 表示成功识别的状态或认证成功；2 表示需人工处理、业务失败或未确认；3 表示超时或运行错误；4 表示任务锁被占用。`COMPLETED` 要求今日 `CHECKED_IN` 和有效签到时间，免签到与已填报情况分别返回，不混作成功。
