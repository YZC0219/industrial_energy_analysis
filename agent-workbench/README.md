# Agent 工作台

一个在 Windows 本机运行的多模型协作网页，适用于代码、数据分析、写作等不同项目。Node.js 22+，网页和服务端不需要第三方依赖。

## 启动

仓库仅保存工作台源码与安装配置，不包含下载的 `tools/agy.exe`、第三方安装脚本、
`node_modules`、缓存或本机 `data/` 运行记录。新机器上的 Google 客户端须另行安装并授权；
本机已有客户端不受上传筛选影响。

双击 `启动工作台.cmd`，打开 http://127.0.0.1:3210 。也可以在本目录运行 `npm start`。启动脚本会在后台运行服务，日志保存在 `data/server.log` 和 `data/server-error.log`。关闭页面不会取消任务；重启电脑后重新双击启动。

## 接入

2026-10-13 更新：真实调用 Gemini CLI 0.63.0 返回 Google 服务端 `UNSUPPORTED_CLIENT`，因此已接入官方 Antigravity CLI 1.3.4（tools/agy.exe，核验 SHA512）。网页仍以 Gemini 表示 Google 模型角色，但实际优先使用 Antigravity。双击原「登录Gemini.cmd」会启动新客户端。Antigravity 会话与配置默认放在 C:\Users\32074\.gemini\antigravity-cli；用户已明确允许该客户端运行数据保存在 C 盘。Google 官方 OAuth 仍需完成新客户端授权，旧 Gemini 登录不保证复用。

- **Codex**：自动检测 PATH 中的 codex.exe 或 npm 的 codex.cmd；后者解析为包声明的 JavaScript 入口，由 Node.js 直接启动。自定义批处理包装器不能自动解析时，请在设置中指定实际 .exe 或 .js 入口。工作台复用 CLI 登录状态。需要时双击 `登录Codex.cmd`。默认只读，勾选「允许 Codex 修改项目文件」才使用 workspace-write。不会使用不受限模式。具体账号额度和模型由 Codex 配置决定。
- **Google Agent（网页仍标为 Gemini）**：按下节先准备 Antigravity，再双击 `登录Gemini.cmd` 完成该客户端的官方授权。运行使用只读 plan 模式。Antigravity 会话目录与 Gemini CLI 不同，具体账号权限和额度在实际调用时验证。
- **DeepSeek**：在「模型与连接」填写密钥和模型名称。密钥保存于本机 `data/settings.json`，未加密，不返回到网页、不写入日志、且 Git 忽略 data。也支持 DEEPSEEK_API_KEY 环境变量。固定调用官方 api.deepseek.com，发生实际请求会消耗 API 额度。

## Google 客户端安装（新机器）

1. 从 Google Antigravity 官方发布渠道获取 Windows CLI，按官方说明核验发布摘要。不要使用来路不明的同名安装包。本仓库不分发第三方程序，也不会自动下载它。
2. 将核验过的 Windows 客户端保存为本目录 `tools/agy.exe`（本项目位于 D 盘），或在网页「Google Agent 路径」中填写现有 `agy.exe` 的绝对路径。自定义路径须先直接运行对应客户端完成登录；`登录Gemini.cmd` 只检测本目录客户端。
3. 本目录安装后双击 `登录Gemini.cmd`。新机器若客户端必须将大量会话数据写到 C 盘，先取得使用者同意，再授权登录；既有本机许可不适用于其他机器。
4. 双击 `安装Gemini.cmd` 只检查优先客户端并提示安装步骤；没有 Antigravity 时不会悄悄安装另一客户端。仅在明确选择旧版时，运行 `安装Gemini.cmd --legacy` 安装 npm Gemini CLI 到本目录 `tools/`，npm 缓存、临时文件及 Gemini CLI 登录数据也保存在本目录。旧版曾返回 `UNSUPPORTED_CLIENT`，安装成功不代表当前可用。若已有 `tools/agy.exe`，检测和登录始终优先使用它；要使用旧版，在网页中明确配置包的 `bin.gemini` 指向的 JS 入口并直接登录对应客户端。

## 使用

1. 新建项目，填写现有本地目录的绝对路径（优先 D 盘）、说明和目标。
2. 选择流程或自定义角色与模型：需求、研究、架构、实现、测试、独立审查、文档。
3. 启动协作。每个角色接收目标、背景和前序成果；查看执行状态、日志、各步骤结果。
4. 可取消任务、复制目标重新执行、导出 Markdown。浏览器下载位置由浏览器设置决定，建议选择 D 盘。

## 第一版边界

- 同一时间执行一个任务；任务按提交顺序排队，角色按顺序执行。尚未实现跨任务并行、自动建工作树、审查驱动的自动修复、文件附件和预算统计。
- Gemini/Codex CLI 可以读取项目目录；DeepSeek 只分析任务背景和前序返回内容，尚未接入文件读取/终端工具。让 Codex 测试后提供证据，再交给 DeepSeek 审查。
- 页面「已检测」仅表示找到 CLI，不表示账号已登录或可调用。实际登录、网络、额度和模型可用性在任务中验证。
- 「已完成」表示所有选定角色已返回结果，不等于成果自动通过验收；请查看测试和审查报告。
- 取消会终止本次 CLI 进程树或 HTTP 请求，不回滚已完成的文件修改。服务重启后，未完成任务标为中断，不会偷偷重跑收费请求。
- 仅绑定 127.0.0.1，检查 Host/Origin 和 JSON 请求类型。请不要把服务暴露到公网。开发任务应选独立项目目录；模型提示不能代替权限隔离。
- 首次联网安装 Gemini 若失败，保留已运行的工作台，在有网络的本机环境重新运行安装脚本。

## 开发与验证

`npm test`：无外部模型费用的集成测试，覆盖顺序交接、队列取消、失败停止、持久化、密钥隐藏与跨站请求拒绝。使用注入的测试适配器，不代表三个模型已真实联网调用成功。

`server.mjs`：本地 HTTP 接口、任务队列、进程/API 适配器、JSON 持久化。

`public/`：无构建步骤的中文网页。`data/`：项目/任务、设置、Gemini 登录状态及运行日志。`tools/`：可选 Gemini CLI 本地安装。
