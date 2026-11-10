# 验证记录

## 2026-10-13 更新

- Google Gemini CLI 实际调用被服务端以 UNSUPPORTED_CLIENT 拒绝；安装和用户登录完成不代表可用。
- 官方替代 Antigravity CLI 1.3.4 下载到 tools/agy.exe，SHA512 与官方 manifest 一致；已适配 --mode plan 和 JSON status/response。用户明确允许该客户端运行数据写入默认 C 盘目录。尚待 Google 新客户端登录与模型响应验证。

- 网络权限恢复后 npm 安装已成功；Gemini CLI 实测 `--version` 输出 `0.63.0`。
- 新版启动路径为包中 bin.gemini 指向的 `bundle/gemini.js`。工作台与登录脚本改为读取 package.json 的 bin 声明，避免固定旧版路径产生误判。
- 10 项程序测试全部通过。Google OAuth 登录仍须在官方流程完成，CLI 启动成功不等于已登录或模型调用成功。

## 当前环境限制

2026-10-12，Windows 执行沙箱：

- `codex login status` 返回 `Logged in using ChatGPT`。
- Gemini CLI 本地安装请求 npm registry 时返回 `ENOTFOUND registry.npmjs.org`，尚未完成安装和 Google 登录。安装缓存路径已设置到 D 盘。
- HTTP 集成测试通过套接字访问本机时返回 `connect EACCES 127.0.0.1`。测试改为在进程内调用同一个生产 HTTP handler，验证路由、请求检查和任务生命周期，不把它称为真实浏览器或套接字验证。
- 没有填写 DeepSeek 密钥，也没有发起收费模型任务。

## 交付检查

- 服务端和网页 JavaScript 使用 `node --check` 检查语法。
- `npm test` 测试实际路由和调度逻辑，模型适配器使用测试注入，覆盖顺序交接、FIFO、取消、失败停止、持久化、密钥隐藏、请求拒绝、CLI 输出解析和 stdin 传递。
- HTML/CSS/JS 静态资源由生产 HTTP handler 返回。

## 用户本机待验证

双击启动工作台，在浏览器打开页面；按 README 的 Google 客户端安装流程准备 Antigravity，运行登录脚本并确认 Google OAuth；在网页提交一个只读 Codex 单角色任务，再添加 Gemini 和 DeepSeek 的真实调用。实际网络、账号额度与权限由该验证确认。
