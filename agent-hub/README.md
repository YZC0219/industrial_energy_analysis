# 神经中枢 · 项目起始包（全局）

> 全局铁律：`~/.dsh/AGENTS.md`。本包是「每个新项目怎么落地」的最小件，纯文档契约，不绑壳、不绑工具。

## 装进任意项目

把本目录**整个**拷到项目根（或 `agent/` 子目录），然后：

1. 把 `AGENTS.fragment.md` 的内容并进项目 `AGENTS.md`（只保留项目差异，别重复全局七条）
2. `scenes/_模板/` 整夹拷成 `scenes/<场景名>/`，改定位卡
3. `shared/TASKBOARD.md` 开张（空着是健康的）
4. 用 `notes/换会话协议.md` 跑一次「写交接 → 换 → 接上」

## 最小验证

拷 `_模板` → `scenes/demo/` → 填 §〇 的「下一步」→ 假换一次：新开对话只读 `HANDOVER.md` + §〇，
**30 秒内能说出下一步** 才算成立。

## 文件

| 文件 | 作用 |
|---|---|
| `AGENTS.fragment.md` | 项目层入口（并入项目 AGENTS.md） |
| `shared/GLOBAL.md` | 项目全局须知（极短） |
| `shared/TASKBOARD.md` | 任务看板（含「未回执」档） |
| `notes/换会话协议.md` | 换会话四步 + 减肥 |
| `scenes/_模板/` | 定位卡 / §〇 记忆 / HANDOVER / 项目记忆 |
