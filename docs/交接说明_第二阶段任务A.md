# 交接说明：第二阶段任务 A（增量装载 / upsert）—— 已完成

> 完成时间：2026-09-20
> 提交：`ec508bb`
> 状态：**已完成并验证**。本文保留决策记录与两条踩过的坑，供后续参考。

## 一句话状态

事实表装载已从 `INSERT IGNORE` 改为**按 `updated_at` 判新旧的 upsert**，
历史修正能正确落地，过期数据重放不会把新值改回去。
23 份基线逐字节未变，71 条测试全绿。

## 改动范围

| 文件 | 改动 |
|---|---|
| `src/generate_data.py` | 新增 `updated_at` 字段，由业务键 md5 确定性派生 |
| `src/clean_data.py` | 解析 `updated_at`；去重改为按时间戳取最新 |
| `sql/create_table.sql` | 事实表加 `updated_at DATETIME NULL`，唯一键不变 |
| `src/import_mysql.py` | 临时表 + `INSERT ... SELECT ... ODKU` 实现 upsert |
| `tests/test_import_mysql.py` | 摘掉 xfail，补一条"过期版本不回退"测试 |

## 已定的决策

**方案 B**：`updated_at` 只作元数据，**不注入覆盖行**。
即同一业务键在数据里仍只出现一次，upsert 路径靠测试构造数据验证。
理由：不破坏 23 份基线快照。

**upsert 语义**：只有严格更新的版本才覆盖。判新旧的条件用
`COALESCE(new.updated_at, '1970-01-01') > COALESCE(t.updated_at, '1970-01-01')`，
把 NULL 显式当作"最旧"。若业务上"空时间戳应当覆盖"，改这个兜底日期即可。
`updated_at` 列本身用 `GREATEST` 保留最大值，避免被旧值拉回去。

## 关键教训 1：元数据字段不该消耗随机数（"修法 1"）

给 `build_clean_rows()` 加 `updated_at` 时，第一版用了 `rng.integers(0, 720)`。
`rng` 是共享的全局序列，多消耗一个随机数会让它后面所有取值**整体偏移**：

| 指标 | 改前 | 改后 |
|---|---|---|
| 清洗后行数 | 18,972 | 18,977 |
| 离群剔除 | 34 | 29 |
| 综合能耗 | 63,653.91 tce | 63,668.16 tce（+0.022%） |

**23 份基线全部失效。** 本次改动只是加一个元数据字段，不该顺带改变业务数据 ——
数据变化应当来自有意的口径调整，而不是"加字段碰巧动了随机数"。

**最终做法**：由业务键确定性派生，一个随机数都不消耗。

```python
datetime.combine(d, time(hour=8))
+ timedelta(minutes=int(hashlib.md5(
    f"{d.isoformat()}|{ws['code']}|{ecode}".encode()
).hexdigest(), 16) % 720)
```

两个注意点：

- **不能用内置 `hash()`**：Python 对 str 的 hash 带随机化种子（PYTHONHASHSEED），
  跨进程不稳定，换个进程重跑就会得到不同时间戳。必须用 `hashlib.md5`。
- **`minute` 不能直接接 `% 720`**：`% 720` 的结果会超过 `minute` 的 0..59 范围，
  `time(hour=8, minute=h)` 会抛 `ValueError`。要用 `timedelta(minutes=h)` 加。

## 关键教训 2：去重顺序会波及基线

去重从 `keep="first"` 改成"按 `updated_at` 取最新"时，有两个连带影响：

1. **行顺序会变**。原实现是 `df.loc[~dup_mask]`，新实现走 `sort_values` 后重建，
   顺序不同。而 `output/clean_energy.csv` 的行序会一直传到 23 份查询结果里。
2. **打平时保留哪条不能是随机的**。`inject_dirt` 注入的"完全重复行"会被后续
   步骤各自独立改写（如第 10 步的 cost 重算），于是同一业务键可能有两行
   **时间戳相同、内容却不同**。此时 `sort_values` 默认的 quicksort **不稳定**，
   保留哪条就取决于内部顺序，基线会随机变红。

**最终做法**：`sort_values(..., kind="stable")`，并在打平时回落到原版语义
（保留 `df` 顺序里的首条，等价于原 `keep="first"`）。

当前数据集里这样的歧义组**恰好只有 1 个**（`2025-04-18 / W03 / E03`），
但它的存在足以让基线随机失败 —— 排查时要意识到这一点。

## 关键教训 3：测试通过 ≠ 验证过

第一次跑基线测试时**显示全绿，那是假的** —— `output/Q*.csv` 还是旧的，
拿旧输出比旧基线，当然一致。重跑 `--run-analysis` 才暴露 23 份全红。

**改完管道后，必须先重跑 `--run-analysis` 刷新 `output/Q*.csv`，再去比基线。**
判断方法：`ls -la output/Q01*.csv output/clean_energy.csv` 看时间戳，
Q*.csv 必须比 clean_energy.csv 新。

同理，比对产物时要拿**真正的 v0.1 参考**，不要拿磁盘上现成的输出当基准 ——
那份输出本身可能就是带着 bug 的中间态。可靠做法是用 worktree 拉出 tag：

```bash
git worktree add /tmp/v01_check 96e3974
cd /tmp/v01_check && python src/generate_data.py && python src/clean_data.py
```

## 验证结果

对照真正的 v0.1 worktree 逐列比对：

| 产物 | 结果 |
|---|---|
| `dim_calendar.csv` / `clean_production.csv` | **逐字节一致** |
| `raw_energy_data.csv` / `clean_energy.csv` | 业务列与行顺序一致，仅新增 `updated_at` |
| `clean_rejects.csv` / `clean_fixed.csv` | 内容一致，仅新增 `updated_at` |
| `Q01`~`Q23` | `python tests/update_baseline.py --check` 报 23 份**全部未变** |

测试：`pytest`（离线 40 条）+ `pytest -m db`（31 条）= **71 passed**。
其中 `test_historical_correction_is_applied` 已从 xfail 转为 passed。

装载幂等性实测：连续重装三次，行数恒为 18,972、消耗量合计恒为
166,580,008.378，`updated_at` 无 NULL。

## 视图和 23 条查询不用改（已确认）

`v_energy_enriched` 是显式列名列举，不是 `SELECT *`；`sql/analysis.sql` 里也没有
`SELECT *`。加一列不会波及它们。这是改动范围比预想小的原因。

## 环境信息

- MySQL 容器在 **3307** 端口（不是 3306），口令 `energy_root_pwd`
- 跑数据库测试：`pytest -m db`（默认 `pytest` 只跑离线测试）
- 装载命令：
  `MYSQL_PORT=3307 MYSQL_PWD=energy_root_pwd python src/import_mysql.py --init --run-analysis`

## 遗留 / 未做

- 事实表之外的 `fact_production` 与 `dim_calendar` 仍是 `INSERT IGNORE`。
  它们没有历史修正需求（产量按 日期×车间 主键覆盖即可，日期维是静态的），
  暂不需要 upsert。若将来产量也要修正，照 `load_csv(..., upsert=True)` 加一个
  `updated_at` 即可复用。
- `updated_at` 的判新旧依赖源系统时间戳的单调性。若上游存在时钟回拨，
  覆盖判断会失真 —— 当前模拟数据不涉及，真实接入时需要留意。
