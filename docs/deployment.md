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

已启用 [管理页面](management.md) 时，优先使用页面的学校验证入口；如需手动运行旧 debug 服务，先停止 management 释放 6080 端口，完成后关闭 debug 再恢复 management。

先停止 scheduler，再开启 debug 容器。debug 只绑定服务器回环地址，通过 SSH 隧道访问：

```bash
docker compose --profile scheduled stop scheduler
docker compose --profile debug up -d debug
# 在自己的电脑建立隧道，替换服务器地址
ssh -N -L 6080:127.0.0.1:6080 user@server
```

在电脑打开 `http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale`，操作的是服务器上的专用浏览器。学校首次登录时输入本人账号密码，完成短信验证码并信任当前浏览器环境；不能用本机普通浏览器的信任状态代替。识别成功后调试任务自动退出，关闭 debug，再运行 `cli check`。学校挑战或密码拒绝时先人工核对，不连续自动重试。

## 保存远端账号密码与更新配置

首次短信验证与信任完成后，在**服务器实际运行目录**的私有 `.env` 中保存：

```dotenv
UESTC_USERNAME=<本人学校账号>
UESTC_PASSWORD='<本人学校密码>'
```

文件保持 0600，密码中的特殊字符按 dotenv 规则引用；不保存短信验证码。只修改本机源码目录的 `.env` 不会更新服务器。配置与同一个 `browser-profile` 数据卷配合使用：优先恢复有效会话，失效后复用受信任环境尝试一次密码登录。再次出现短信验证或其他学校挑战时仍需人工处理，不保证永久免验证。

在服务器运行目录按顺序操作。已有调度器先停止，再备份和编辑 `.env`；备份同样包含凭据，保持 0600：

```bash
docker compose --profile scheduled stop scheduler
# 先将现有 .env 备份到权限受控的位置，再编辑服务器上的 .env
chmod 600 .env
# 确认 debug 已退出，避免争用浏览器 profile
docker compose --profile debug stop debug
# 会话有效则恢复，否则尝试一次密码登录；不签到、不发 QQ 消息
docker compose run --rm cli login
# 登录成功后验证微信授权复用；不签到、不发 QQ 消息
docker compose run --rm cli wechat-ticket
# 仅在上述验证成功后恢复调度，并加载修改后的环境变量
docker compose --profile scheduled up -d --no-build --force-recreate scheduler
```

`login` 返回 `AUTHENTICATED / login_completed` 表示本次密码登录成功；`session_restored` 表示已有会话仍有效，本次没有验证密码。已有认证暂停标记时，先人工检查原因，再按需 `cli resume` 后重试一次 `cli login`，或使用 debug 完成验证。

`docker compose restart scheduler` 不会重新加载 `.env`，必须重新创建容器。重新创建时保留原有数据卷，不删除 profile、微信授权或 `state.json`。使用外部 NapCat 网络时，以上命令均加 `-f compose.yaml -f compose.napcat.yaml`；已有 cloud 部署使用 `./scripts/cloud.sh` 代替 `docker compose`，按服务器权限需要加 `sudo`。

调度日期在首次触发时已落盘，登录失败后也不会因重启或重新创建容器而自动补跑同一天。恢复登录后先用不发通知的 `status` 查询今日官方记录；确需补签时显式执行一次 `run`，不清空原有防重状态。账号密码配置和登录验证本身不执行签到。

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
| settings.json（启用管理页时） | 网页保存的学校账号密码与调度开关，优先于 .env |
| admin-last-job.json（启用管理页时） | 最近管理操作的已过滤结果 |
| auth-paused、task.lock、scheduler.lock | 认证暂停与任务锁 |

备份前停止 scheduler、debug、已启用的 management 与正在运行的一次性容器，确保浏览器关闭。分别备份 .env 和数据卷，备份目录 0700、文件 0600。备份包含授权，不上传公开仓库。

`docker compose down` 保留数据；`down -v` 删除本地默认卷，不作为重启方式。恢复卷时使用相同镜像架构和浏览器版本，并核对 pwuser 对数据目录的访问权限。不要只复制 state.json 或只复制微信密文而丢失对应密钥。

可选 compose.cloud.yaml 将现有兼容卷声明为 external，适合明确管理生产数据生命周期的部署。首次使用需先创建卷；`scripts/cloud.sh` 固定合并基础、NapCat 与 external 卷配置，因此要求 BOT_NETWORK 已配置。单独运行、不接 NapCat 的部署直接使用基础 Compose。

## 升级和失败处理

停止调度 → 备份配置、卷与旧镜像 → 更新源码、构建和测试 → check、wechat-ticket → 核对后恢复调度。回滚程序时不盲目覆盖每日状态，防止丢失升级后已产生的记录。

遇到 UNKNOWN 或 SUBMITTING，先在官方页面或 status 中回查；不要清空状态强行再提交。微信授权失效时停止调度，在无任务运行时移除本项目 wechat 目录中的授权缓存，再重新扫码；保留浏览器会话和每日状态。

任务日志中的 `operation_error` 包含阶段、异常类型和调用文件/行号，不包含异常原文、页面内容或凭据。`checkin_ticket_unroute`、`browser_unroute_all`、`browser_context_close`、`playwright_stop` 分别表示移除票据拦截、清理剩余拦截、关闭浏览器及停止驱动；清理各自最多等待 5 秒，异常只记录诊断，不覆盖已核验的业务结果。`browser_task` 表示业务执行异常，仍按原规则返回失败。移除拦截时等待正在执行的处理函数结束，再关闭浏览器。
