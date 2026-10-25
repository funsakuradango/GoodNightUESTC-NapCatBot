# 微信授权与票据链路

协议代码来自固定版本的 moon-uestc SDK。本项目不导入其会直接执行业务的 main.py，而是在隔离 worker 中调用授权方法。学校 transport 只允许身份查询、check-bind 和 checkin-ticket，不能访问签到或填报接口。

## 首次授权与后续复用

1. 容器浏览器完成学校登录，取得 H5 SESSION；worker 查询学校用户作为身份基准。
2. SDK 生成腾讯应用宝 PC OAuth 二维码。手机微信确认后得到 OAuth code，再由腾讯回调交换 openid、access_token、refresh_token、expires_at。
3. 凭据使用 Fernet 加密保存。距离到期不超过 60 秒时尝试应用宝 refresh，并先保存返回凭据。调度过程中不自行发起扫码。
4. 应用宝凭据换取 login_buffer，SDK 与微信建立 MMTLS 连接、执行 ManualAuth，再针对学校小程序 AppID `wxaa78bf2905f01cee` 请求 js-login。
5. 微信返回 code A，用于学校 check-bind，取得小程序 SESSION。随后查询小程序用户，与 H5 的学校 ID、学号比较；不一致或未绑定立即停止。
6. 再次向微信获取新的 code B，携带小程序 SESSION 请求 checkin-ticket，得到学校 ticket。
7. H5 使用自己的会话提交；ticket 只随指定签到 POST 发出一次，然后回查今日记录。

OAuth code 与小程序 code 不同，code A、code B 也不能复用。有效 code 来自微信响应，ticket 来自学校响应，不能通过修改 UA 或随机生成字符串获得。

## 请求材料对照

| 请求 | 主要请求头 | 请求体或结果 |
| --- | --- | --- |
| 应用宝 login_buffer | 应用宝凭据及 SDK 构造的 Ual-Access-* 头 | 返回 login_buffer，不向学校发送这些凭据 |
| 微信 js-login | SDK 的 MMTLS 加密协议报文 | 返回目标小程序的新 code |
| 学校 H5 身份 GET | `Cookie: SESSION=<H5 SESSION>`，`platform: h5` | 身份基准，无 ticket |
| 学校 check-bind POST | `platform: mp-weixin`，`Content-Type: application/json`；初始客户端无学校 Cookie | `{"code":"<code A>","sn":""}`，返回 sessionId |
| 学校小程序身份 GET | `Cookie: SESSION=<小程序 SESSION>`，`platform: mp-weixin` | 核对学校身份，无 ticket |
| 学校 checkin-ticket POST | 小程序 SESSION、mp-weixin、JSON | `{"code":"<code B>"}`，返回 ticket |
| 学校签到 POST | H5 SESSION、正常 H5 业务头、`X-Checkin-Ticket: <ticket>` | 页面生成的业务 JSON，不含微信 code |
| 今日记录 / 时段 GET | 正常学校 Cookie | 不发送 X-Checkin-Ticket |

换票示例：

```http
POST /platform-app/api/auth/checkin-ticket
platform: mp-weixin
Cookie: SESSION=<小程序 SESSION>
Content-Type: application/json

{"code":"<新的 code B>"}
```

签到请求头示例：

```http
POST /daily-checkin/api/student-checkin-record/checkin
Cookie: SESSION=<H5 SESSION>
Content-Type: application/json
X-Checkin-Ticket: <学校签发的 ticket>
```

## 本项目的处理边界

ticket 必须符合学校已观察到的 43 字符 base64url 格式，但格式不能证明有效期、消费状态或绑定规则。本项目每次提交前重新取票，不把 ticket 存入持久凭据文件。

H5 从 `sessionStorage['daily-checkin-operation-ticket']` 读取票据。Playwright 路由进一步限制精确 HTTPS 地址和 POST 方法，只发送一次，不自动重试、不跟随该 POST 的重定向；其他请求移除票据头。

提交前持久化 SUBMITTING，响应不明后保留 UNKNOWN，通过记录回查处理，不能因为获得新 ticket 就再次提交。

凭据密文与密钥位于同一个私有数据卷，完整卷的持有者仍可能解密授权。协议是第三方实现，其可用性、刷新和平台兼容性需要持续验证；MMTLS 阶段使用直连 TCP，普通 HTTP 代理不会自动覆盖该连接。
