# 开发与测试

仓库根目录包含 app、tests、Compose、Dockerfile 和文档。配置与运行数据独立于源码；修改页面适配和授权链路时先使用离线测试，不用真实签到或 QQ 发送替代测试。

## 运行时

主程序沿用 Playwright 镜像中的 Python 与 Chromium；固定版本协议 SDK 要求 Python 3.13+，通过单独运行时执行授权 worker。凭据通过标准输入传递，ticket 经管道返回主程序，不放在命令行或公开日志。

```bash
docker compose build cli
docker compose run --rm test
docker compose run --rm cli demo
# 独立协议运行时测试
docker compose run --rm --entrypoint /opt/wechat/bin/python3 \
  -e PYTHONHOME=/opt/wechat -e LD_LIBRARY_PATH=/opt/wechat/lib \
  cli -m pytest -q tests/test_wechat.py
```

主运行时没有安装协议 SDK 时，相关测试会跳过；需同时运行独立 Python 3.13 测试才能覆盖协议适配。反过来，独立运行时不含 Playwright，其浏览器测试由主运行时覆盖。

测试覆盖学校认证与暂停、页面状态、今日业务记录解释、防重、通知队列、双 code 与两类 SESSION 隔离、身份不一致时停止、授权凭据刷新与加密保存、精确请求头限制及重定向处理。浏览器集成测试只访问回环模拟服务。

## 检查与修改范围

```bash
git diff --check
bash -n scripts/wechat-authorize.sh
sh -n scripts/cloud.sh
sh -n scripts/debug-browser.sh
docker compose config --quiet
```

编写日志与异常处理时，不序列化原始学校页面、URL 查询参数、Cookie、微信凭据、code 或 ticket。SDK 异常只输出必要阶段和类型。修改业务写操作必须保留提交前落盘、单次请求和独立回查。

新增依赖需记录来源与许可，协议依赖使用 requirements-wechat.txt 固定提交。本项目原创代码使用 MIT；第三方代码按 [第三方声明](../THIRD_PARTY_NOTICES.md) 处理。

公开分发仅使用仓库源码；不复制现用 .env、整个浏览器 profile、数据卷、备份或本地研究导出。原始个人部署笔记不属于公开项目文档。
