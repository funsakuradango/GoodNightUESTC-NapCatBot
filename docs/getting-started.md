# 首次配置与登录

所有命令从仓库根目录执行。先按 README 创建权限为 0600 的 `.env`，构建 cli 与 debug 镜像。学校账号和密码可以留空，在专用浏览器中人工登录。

## 学校登录

```bash
docker compose --profile debug up debug
```

打开 [容器浏览器](http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale)，在学校官方页面输入本人账号并处理验证码。识别登录成功后调试任务自动退出，最长等待 600 秒。

```bash
docker compose run --rm cli check
```

`AUTHENTICATED` 只说明学校会话有效，不代表签到成功。遇到密码拒绝、验证码或无法确认的状态时会保留 `auth-paused`，避免循环提交密码。人工核对后可以运行 `cli resume` 解除暂停，再执行一次登录检查。

调试浏览器与任务共享同一个 profile 和任务锁，不要同时打开。debug 端口只绑定回环地址；远程部署通过 SSH 隧道访问，见 [部署说明](deployment.md)。

## 微信授权

确认已在官方智慧学工小程序绑定当前学校账号，然后运行：

```bash
bash scripts/wechat-authorize.sh
```

脚本生成短期二维码；macOS 会自动打开图片，其他桌面环境可打开终端输出的图片路径。用绑定该学校账号的微信摄像头扫码，确认“应用宝”授权，等待 `wechat_ticket_verified_no_submission`。二维码在任务退出时删除。

程序会核对微信绑定学校身份与浏览器身份；未绑定或身份不一致就停止，不自动绑定、解绑或更换学校账号。初次授权仍需手机确认，之后有效凭据可免扫码取票：

```bash
docker compose run --rm cli wechat-ticket
```

两条授权命令不签到、不发 QQ 消息。无桌面的云服务器按 [云端扫码步骤](deployment.md#云端扫码) 操作。

## 查询与启用业务

先运行 `cli status` 查询今日记录。默认未配置 NapCat，不发送通知；配置后该命令会发送相应状态。需要只查询时见 [命令与副作用](configuration.md#命令与副作用)。

核对 `.env` 中页面入口、位置与提交选择器。示例里的页面选择器仅代表已观察版本，不保证永久适用。正式执行要求 `CHECKIN_ENABLED=true` 和完整位置配置；默认值为 false。配置完成后再运行 `cli run` 或启用每日调度。
