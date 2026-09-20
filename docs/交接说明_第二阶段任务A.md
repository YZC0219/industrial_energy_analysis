# 交接说明：第二阶段任务 A（增量装载 / upsert）—— 进行中

> 写入时间：2026-09-20
> 用途：换聊天窗口后继续。**先读完本文再动手。**

## 一句话状态

改动**做了一半，仓库处于不能装载的中间状态**。两个文件已改（未提交），
建表和装载层未改，因此 `clean_energy.csv` 比事实表多一列，**现在跑装载会失败**。

## 当前 git 状态

```
分支 main, tag v0.1 (=96e3974), 最新提交 f6aef72
工作区: M src/clean_data.py    ← 未提交
        M src/generate_data.py ← 未提交
```

## 已定的决策

**方案 B**（已与用户确认）：`updated_at` 只作元数据，**不注入覆盖行**。
即同一业务键在数据里仍只出现一次，upsert 路径靠测试构造数据验证。
理由：不破坏 23 份基线快照。

**修法 1**（已与用户确认，尚未实施）：让 `updated_at` **不消耗随机数**。

## 关键教训：为什么需要"修法 1"

给 `generate_data.py` 的 `build_clean_rows()` 加 `updated_at` 时，我用了
`rng.integers(0, 720)` 生成时间戳。这**消耗了随机数**，使后续所有随机数序列
整体偏移，后果：

| 指标 | 改前 | 改后 |
|---|---|---|
| 清洗后行数 | 18,972 | 18,977 |
| 离群剔除 | 34 | 29 |
| 综合能耗 | 63,653.91 tce | 63,668.16 tce（+0.022%） |

**23 份基线全部失效。** 本次改动的目的只是加一个元数据字段，不该顺带改变
业务数据 —— 数据变化应当来自有意的口径调整，而不是"加字段碰巧动了随机数"。

### 怎么改（修法 1）

把时间戳改成**由行内容确定性派生**，不占用 `rng`：

```python
# 用业务键决定"当天第几分钟上报", 确定性、不消耗 rng
h = hash((d.isoformat(), ws["code"], ecode)) % 720
updated_at = (datetime.combine(d, time(hour=8))
              + timedelta(minutes=h)).strftime("%Y-%m-%d %H:%M:%S")
```

注意：Python 的 `hash()` 对 str 有随机化种子（PYTHONHASHSEED），**跨进程不稳定**。
要可复现应该用 `hashlib.md5(...).hexdigest()` 取模，不要用内置 `hash()`。

改完必须验证：重跑 generate+clean，六份产物（raw_energy_data.csv /
clean_energy.csv / clean_production.csv / dim_calendar.csv /
clean_rejects.csv / clean_fixed.csv）应与 v0.1 时**逐字节一致**（除新增列外）。

## 已完成的两个文件改动

### `src/generate_data.py`

1. import 行加了 `datetime, time`：
   `from datetime import date, datetime, time, timedelta`
2. `build_clean_rows()` 的 row dict 末尾加了 `updated_at` 字段
   （当前用 `rng.integers(0,720)`，**需改成上述确定性派生**）

### `src/clean_data.py`

1. 第 4 步后新增 `updated_at` 解析：
   `df["updated_at"] = pd.to_datetime(norm_text(df.get("updated_at")), errors="coerce")`
   解析失败不剔除（它只是元数据）
2. 第 7 步去重逻辑从 `keep="first"` 改为**按 `updated_at` 保留最新**：
   先 `dup_mask = duplicated(keep=False)`，再 `sort_values("updated_at",
   na_position="first")`，保留每组 `keep="last"`。
   已确认：纯重复行（updated_at 相同）时行为与原来一致，重复数仍是 228。
3. 第 13 步事实表选列加入 `"updated_at"`，并
   `.dt.strftime("%Y-%m-%d %H:%M:%S")` 落盘（NaT → 空串）

## 还没做（按顺序）

1. **`src/generate_data.py`**：把 `updated_at` 改成确定性派生（修法 1）
2. **`src/create_table.sql`**：`fact_energy_consumption` 加列
   `updated_at DATETIME NULL COMMENT '源系统最后修改时刻'`。
   位置：放在 `is_production_day` 之后、`PRIMARY KEY` 之前。
   **唯一键 `uk_date_ws_energy` 不变。**
3. **`src/import_mysql.py`**：
   - `ENERGY_COLS` 末尾加 `"updated_at"`
   - `LOAD DATA ... IGNORE` 改成 upsert

### 装载 upsert 的写法（已在 MySQL 8.0.46 上实测确认）

`LOAD DATA` **不能**直接写 `ON DUPLICATE KEY UPDATE`（MySQL 不支持这个组合）。
可行方案有两条：

**方案 甲：先灌临时表，再 INSERT ... SELECT ... ODKU（推荐）**

```sql
-- 1) 灌进临时表（结构与事实表一致）
CREATE TEMPORARY TABLE _stage LIKE fact_energy_consumption;
LOAD DATA LOCAL INFILE '...' INTO TABLE _stage ... ;

-- 2) 从临时表 upsert 进事实表，只让更新的版本胜出
INSERT INTO fact_energy_consumption
  (record_date, workshop_code, energy_code, ..., updated_at)
SELECT record_date, workshop_code, energy_code, ..., updated_at FROM _stage
AS new
ON DUPLICATE KEY UPDATE
  consumption = IF(new.updated_at > fact_energy_consumption.updated_at,
                   new.consumption, fact_energy_consumption.consumption),
  ...
  updated_at  = GREATEST(fact_energy_consumption.updated_at, new.updated_at);
```

**已实测验证**（MySQL 8.0.46）：
- 推入**更新**的记录 → 正确覆盖（v 由 1 → 99）
- 推入**更旧**的记录 → 正确忽略（仍为 99），即过期数据不会回退已有值

要点：
- 必须用 `AS new` 行别名（MySQL 8.0.19+ 支持）。旧的 `VALUES(col)` 写法在
  8.0.20 起已废弃。
- `IF(new.updated_at > t.updated_at, ...)` 这个条件是**关键** —— 只靠
  ODKU 会无条件覆盖，那样旧数据重放会把新值改回去。
- `updated_at` 用 `GREATEST` 保留最大值，避免被旧值拉回去
- 事实表用 `AUTO_INCREMENT` 主键 `id`，ODKU 命中唯一键时不会消耗 id
- 注意 `NULL` 比较：若某行 `updated_at` 为空，`new.updated_at > t.updated_at`
  恒为 NULL（falsy），该行不会被覆盖。若业务上"空时间戳应当覆盖"，需
  显式处理（如 `IFNULL`）。当前数据 100% 非空，暂不涉及。

**方案 乙：放弃 LOAD DATA，改用批量 `INSERT ... ODKU`**（executemany）。
慢 1~2 个数量级，但代码简单。现有 `load_csv` 的降级分支已经是这个形态
（`INSERT IGNORE`），改 `IGNORE` 为 ODKU 即可复用。
4. **验证 23 份基线**：重新 `--run-analysis` 后 `pytest -m db`，
   23 份应全部通过且逐字节一致
5. **`tests/test_import_mysql.py`**：把 `test_historical_correction_is_applied`
   的 `@pytest.mark.xfail(strict=True)` 摘掉，它应从 xfail 转为 passed

## 视图和 23 条查询不用改（已确认）

`v_energy_enriched` 是显式列名列举，不是 `SELECT *`；
`sql/analysis.sql` 里也没有 `SELECT *`。加一列不会波及它们。
这是改动范围比预想小的原因。

## 环境信息

- MySQL 容器在 **3307** 端口（不是 3306），口令 `energy_root_pwd`
- 跑数据库测试：`pytest -m db`（默认 `pytest` 只跑离线测试）
- 装载命令：
  `MYSQL_PORT=3307 MYSQL_PWD=energy_root_pwd python src/import_mysql.py --init --run-analysis`
- 重置磁盘上的脏数据：`git checkout -- src/clean_data.py src/generate_data.py`

## 一个流程教训（重要）

我第一次跑基线测试时**显示全绿，那是假的** —— `output/Q*.csv` 还是旧的
（14:45 生成），我拿旧输出比旧基线，当然一致。重跑 `--run-analysis` 才暴露
23 份全红。

**测试通过 ≠ 验证过。** 改完管道后，必须先重跑 `--run-analysis` 刷新
`output/Q*.csv`，再去比基线。否则测的是陈旧的产物。
判断方法：`ls -la output/Q01*.csv output/clean_energy.csv` 看时间戳，
Q*.csv 必须比 clean_energy.csv 新。
