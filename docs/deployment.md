# 部署与运维

克隆或下载项目到自行选择的目录。运行路径、配置与私有数据由部署者管理；项目不依赖某台特定服务器的目录。

## 调度

完成学校登录、微信取票验证和配置核对后，将 CHECKIN_ENABLED 设置为 true，再启动定时任务：

```bash
docker compose --profile scheduled up -d scheduler
docker compose logs --tail 30 scheduler
docker compose --profile scheduled stop scheduler
```

使用 NapCat 外部网络时，上述命令均加载 compose.napcat.yaml。默认 Asia/Shanghai 每日 21:05，在配置小时内可补执行一次；过了该小时不补签。调度日期持久化，重启不会重新安排同一天的任务。不确定提交会保留防重状态。

## 远程学校登录

先停止 scheduler，再开启 debug 容器。debug 只绑定服务器回环地址，通过 SSH 隧道访问：

```bash
docker compose --profile scheduled stop scheduler
docker compose --profile debug up -d debug
# 在自己的电脑建立隧道，替换服务器地址
ssh -N -L 6080:127.0.0.1:6080 user@server
```

在电脑打开 `http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale`，完成学校登录后运行 cli check。关闭 debug 与隧道，再恢复调度。学校挑战或密码拒绝时先人工核对，不连续自动重试。

## 云端扫码

无桌面服务器可以直接使用授权命令，在另一终端获取二维码：

```bash
docker compose run --name goodnightuestc-authorize cli wechat-authorize
# 等待输出 WECHAT_QR_READY，在另一终端执行：
docker cp goodnightuestc-authorize:/app/data/wechat-authorization.png /安全的临时目录/qr.png
```

通过 SSH 将图片下载到受信任桌面，用本人微信扫码确认。二维码仅在等待期间存在，确认后等待命令返回 `wechat_ticket_verified_no_submission`，删除临时图片和已退出的授权容器，再运行 cli wechat-ticket 验证复用。

该命令只授权取票，不签到、不发 QQ 消息。与其他任务共用锁，授权期间先暂停 scheduler。使用外部 NapCat 网络的环境，可给命令加对应 Compose 覆盖文件，但取票本身不依赖 NapCat。

## 数据与备份

保留 Compose 项目名 `uestc-checkin`，默认命名卷为 `uestc-checkin_checkin-data`。项目改名为 GoodNightUESTC 不会自动迁移既有卷。

| 数据 | 内容 |
| --- | --- |
| browser-profile/ | 学校浏览器会话 |
| wechat/wechat.enc、wechat/wechat.key | 加密微信凭据及本地密钥 |
| state.json | 每日业务结果、调度日期和通知队列 |
| auth-paused、task.lock、scheduler.lock | 认证暂停与任务锁 |

备份前停止 scheduler、debug 与正在运行的一次性容器，确保浏览器关闭。分别备份 .env 和数据卷，备份目录 0700、文件 0600。备份包含授权，不上传公开仓库。

`docker compose down` 保留数据；`down -v` 删除本地默认卷，不作为重启方式。恢复卷时使用相同镜像架构和浏览器版本，并核对 pwuser 对数据目录的访问权限。不要只复制 state.json 或只复制微信密文而丢失对应密钥。

可选 compose.cloud.yaml 将现有兼容卷声明为 external，适合明确管理生产数据生命周期的部署。首次使用需先创建卷；`scripts/cloud.sh` 固定合并基础、NapCat 与 external 卷配置，因此要求 BOT_NETWORK 已配置。单独运行、不接 NapCat 的部署直接使用基础 Compose。

## 升级和失败处理

停止调度 → 备份配置、卷与旧镜像 → 更新源码、构建和测试 → check、wechat-ticket → 核对后恢复调度。回滚程序时不盲目覆盖每日状态，防止丢失升级后已产生的记录。

遇到 UNKNOWN 或 SUBMITTING，先在官方页面或 status 中回查；不要清空状态强行再提交。微信授权失效时停止调度，在无任务运行时移除本项目 wechat 目录中的授权缓存，再重新扫码；保留浏览器会话和每日状态。

任务日志中的 `operation_error` 包含阶段、异常类型和调用文件/行号，不包含异常原文、页面内容或凭据。`checkin_ticket_unroute`、`browser_unroute_all`、`browser_context_close`、`playwright_stop` 分别表示移除票据拦截、清理剩余拦截、关闭浏览器及停止驱动；清理各自最多等待 5 秒，异常只记录诊断，不覆盖已核验的业务结果。`browser_task` 表示业务执行异常，仍按原规则返回失败。移除拦截时等待正在执行的处理函数结束，再关闭浏览器。
