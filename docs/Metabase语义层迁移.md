# Metabase 费用卡片接入语义层

2026-10-03 已更新本机既有看板的两张费用卡片（ID 40/41），分别按车间编码、按日汇总。实际卡片使用查询构建器，并非原生 SQL。新增 `governance/metabase.py` 从 YAML 的 `total_cost_yuan` 定义生成费用聚合表达式，并在卡片说明记录指标定义、指标摘要和 Q28 SQL 摘要。

该适配器只支持审核过的费用求和。保持 `v_energy_enriched` 数据源、原维度、筛选映射、卡片 ID 和下钻能力；没有扩大 BI 数据库权限。能耗/碳排的逐日舍入、产品单耗等不能简单替换原始字段，因此尚未提供这些指标的 BI 编译。

## 复现与恢复

在项目根目录运行（本地 `.env` 已有管理员、普通用户和 MySQL 凭据）：

```powershell
python -m tools.migrate_metabase_semantics
python -m tools.migrate_metabase_semantics --apply
```

默认仅准备方案并核验；`--apply` 在写入前保存原卡片查询与说明，再检查写后结果，失败时尝试恢复已触及的卡片。每次备份保存在 D 盘项目 `output/extensions/metabase_semantic_<批次>/backup.json`，不包含密码或会话令牌。恢复本次迁移前的原卡片：

```powershell
python -m tools.migrate_metabase_semantics --restore D:\industrial_energy_analysis\output\extensions\metabase_semantic_4bec720a7d3c\backup.json
```

迁移前会重新读取卡片，检测准备期间的编辑冲突；Metabase 更新接口仍不提供跨两张卡片的原子事务。备份和恢复只覆盖查询及说明，不恢复其他用户后续修改的看板布局。

## 已验证范围

- MySQL 只读一致快照：Q28 全部 5,848 个车间日费用，与明细视图费用求和逐组一致。
- 两张已保存卡片实际执行成功；全量、2025 年 1 月 W04、同月 W01，共 6 组图表结果逐组一致。
- 普通用户仍只能查看既有数据库/视图/看板；原生 SQL、看板编辑、事实表读取与数据库写入仍被拒绝。
- 浏览器普通用户筛选 W04 / 2025 年 1 月，两图可见，费用 ¥375,635.34，下钻 93 行。
- 新编译器测试验证拒绝其他数据源、原始字段、平均值和多阶段查询，并保持输入、维度和过滤条件。

证据：[迁移运行报告](../output/extensions/metabase_semantic_runtime.json)、[浏览器报告](../output/extensions/metabase_semantic_4bec720a7d3c/browser.json)。截图、备份及方案保留本地 D 盘，未加入版本控制。

此模块是在生成/迁移时读取语义注册表，Metabase 运行时仍执行保存的查询构建器表达式；修改 YAML 后需要重新核验、生成和应用，不能声称 Metabase 每次查询会调用 FastAPI。默认核验生成待应用方案，不能代替确认当前卡片已受管。现有前端的全部衍生公式及更多 BI 指标仍待逐项迁移。
