---
name: energy-change-check
description: "为能迹项目的代码改动选择相关回归测试与运行验收，区分离线、数据库、浏览器和副作用检查。用于修改完成后的验证或明确的回归请求；文案小改和无行为变化的文件整理不默认跑全套。"
---

# 能迹改动验证

根据实际改动及风险选择验证，避免每次读取所有测试或启动全部服务。

## 选择检查

1. 从当前任务和 `git diff` 确认实际改动。未提交差异可能包含其他窗口工作，不把全部差异归到本次任务，也不覆盖它们。
2. 读取 [验证地图](references/check-map.md) 中匹配的行，检查选定测试的 fixture、marker 和产物影响；运行已有的最小有效组合。
3. 失败时区分行为回归、缺依赖、服务不可达与输入证据过期。只在新改动或未解决的问题需要时扩大测试。

## D 盘测试环境

以下在 PowerShell 当前进程设置，实际工作树不在默认目录时替换根目录：

```powershell
$taskTmp = 'D:/industrial_energy_analysis/tmp/skill-check'
New-Item -ItemType Directory -Path $taskTmp -Force | Out-Null
$env:TEMP = $taskTmp
$env:TMP = $taskTmp
$env:TMPDIR = $taskTmp
$env:PYTHONPYCACHEPREFIX = "$taskTmp/pycache"
```

`preserve_outputs` fixture 会整体备份/还原 `data/` 与 `output/`，备份使用 Python tempfile。因此只指定 pytest `--basetemp` 不够，需要在启动 Python 前设置上述临时目录；使用唯一的测试子目录，避免与其他窗口测试共享 `--basetemp`。

## 解释验收结果

- `pytest.ini` 默认 `-m "not db"`。数据库测试必须显式指定 `-m db`，并先确认库及端口；会全量重建的测试只能针对明确的测试库或已获授权的可重建环境。
- `tests/conftest.py` 会在数据库不可达时 skip。命令退出 0 但关键测试全跳过，不算数据库验收成功；报告 passed/failed/skipped 及跳过原因。
- `tools/verify_*.py` 名称不代表只读：执行前确认参数、服务要求及副作用。浏览器核验可能需要 Playwright 和本机 Chrome，不自动下载浏览器或启动外部模型收费请求。
- SQL 快照差异先逐项解释。`tests/update_baseline.py` 及语义摘要刷新是更改预期行为，不是修复测试的方法。
- 离线归因、浏览器展示、实时管道和在线模型分别验收。mock 模型测试不能证明真实联网调用成功，旧 JSON 不能替代新运行。

结果简要说明改动、实际运行的检查、关键计数、证据路径和未覆盖范围。常规相关检查通过后就完成交付；不为了显得全面重复跑全套。
