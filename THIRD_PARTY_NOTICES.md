# 第三方声明

本文件区分直接依赖、实现参考和思路来源。本仓库的 MIT License 适用于原创代码，不替第三方授予许可，也不覆盖它们的名称、商标或服务。

## moon-uestc：直接依赖

- 项目：https://github.com/MioYiSama/moon-uestc
- 固定提交：f8a93c3bc4d62cea879b76560c49c9646387b386
- 使用范围：应用宝 OAuth、凭据刷新、微信 MMTLS/js-login 取码及学校身份、绑定、操作票据 API。
- 接入方式：构建时按 requirements-wechat.txt 下载，运行在独立 Python 3.13 环境中；本仓库没有复制或内置其源码，也不运行其 main.py。
- 授权信息：核对该固定提交的仓库树与 pyproject.toml，未找到明确许可证声明。该事实不表示上游已授予任意使用或再分发权。依赖的使用和镜像再分发许可需向上游确认；本项目 MIT 不替代该授权。

## LCX-luo/GoodnightUESTC：实现参考

- 项目：https://github.com/LCX-luo/GoodnightUESTC
- 使用范围：隔离授权 worker、学校身份比较、两个新 code 分别用于登录与换票、加密保存授权等接入思路。
- 相关文件：app/wechat_worker.py、app/wechat.py。
- 核对日期：2026-10-25。上游采用 MIT，许可及版权声明保留如下。

```text
MIT License

Copyright (c) 2026 GoodNightUESTC contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 其他鸣谢

`seccomp_profile.json` 来源为 Microsoft Playwright v1.56.0 的 `utils/docker/seccomp_profile.json`，其 Apache 2.0 许可文本随项目保存在 [licenses/Playwright-Apache-2.0.txt](licenses/Playwright-Apache-2.0.txt)。该文件按上游许可处理，不归入本项目原创 MIT 代码。

- beichenO2/GoodNight：https://github.com/beichenO2/GoodNight —— 早期浏览器签到方案参考，未作为运行依赖打包。
- 525815266/YYB-Go-Enhanced：https://github.com/525815266/YYB-Go-Enhanced —— moon-uestc 标注的协议参考；本仓库未复制其源码或调用外部 YYB 服务。
- Microsoft Playwright：https://github.com/microsoft/playwright —— 浏览器自动化与容器基础镜像。
- NapNeko/NapCatQQ：https://github.com/NapNeko/NapCatQQ —— 提供 OneBot QQ 接入；本项目通过 HTTP 与用户已有实例交互，不内置 NapCat。

Python 和容器依赖保留各自许可。分发包含依赖的镜像时，应同时处理实际安装组件的许可和声明，不把本仓库的 MIT 视为所有依赖的统一许可。
