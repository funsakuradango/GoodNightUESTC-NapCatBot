# 首次配置与登录

所有命令从仓库根目录执行。先按 README 创建权限为 0600 的 `.env`，构建 cli 与 debug 镜像。无人值守部署需要在实际运行端的私有 `.env` 中保存学校账号密码；只人工维护会话时也可留空，但会话失效后程序无法自行重新登录。

## 学校登录

学校首次登录需要短信验证码，并在学校页面完成信任当前浏览器环境。必须在后续任务实际使用的专用容器浏览器中完成；在自己电脑的普通浏览器登录，不等于服务器浏览器已受信任。远端部署先按 [远程学校登录](deployment.md#远程学校登录) 建立 SSH 隧道。

```bash
docker compose --profile debug up debug
```

打开 [容器浏览器](http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale)，在学校官方页面输入本人账号密码，完成短信验证码及信任环境步骤。识别登录成功后调试任务自动退出，最长等待 600 秒。关闭 debug 后再运行其他任务。

在实际运行端的 `.env` 中保存 `UESTC_USERNAME`、`UESTC_PASSWORD`，并保持权限为 0600。后续沿用同一个 `browser-profile` 数据卷：已有会话有效时直接恢复，会话失效后才尝试一次账号密码登录。仅填写账号密码不能代替首次短信验证与环境信任。不要把短信验证码保存在 `.env` 中。

```bash
docker compose --profile debug stop debug
# 验证已保存的学校会话；不会提交密码
docker compose run --rm cli check
# 允许在会话失效时尝试一次账号密码登录；不签到、不发通知
docker compose run --rm cli login
```

`AUTHENTICATED` 只说明学校认证成功，不代表签到成功；已有会话有效时 `login` 返回 `session_restored`，不能据此证明密码已被实际使用。遇到密码拒绝、验证码或无法确认的状态时会保留 `auth-paused`，避免循环提交密码。再次要求短信验证时，回到同一个 debug 浏览器人工完成；成功识别登录后暂停标记自动清除。仅在人工核对后需要再尝试一次密码登录时使用 `cli resume`，再执行 `cli login`；`check` 本身不会尝试密码登录。

受信任环境不保证永久免验证。迁移、清空浏览器 profile 或学校风控变化后可能需要重新验证。修改 `.env` 后，已存在的 scheduler 容器需重新创建才能加载新凭据，见 [部署说明](deployment.md#保存远端账号密码与更新配置)。

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
