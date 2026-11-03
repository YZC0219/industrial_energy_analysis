# 基于 Spark 的多城市气象与空气质量数据采集及预处理系统

期末作业主路径：**真实 API 采集 → ODS → Spark 清洗/汇总 → DWD → 看板/对账/PPT**。  
`pandas` 仅作 Spark 不可用时的**演示备用**。

## 数据性质（答辩必讲）

Open-Meteo Forecast / Air-Quality 接口返回的是**数值模式预报及归档预报产品**，不是监测站原始观测。  
说明见 [Open-Meteo 文档](https://open-meteo.com/en/docs)。ODS 与报告中字段 `data_nature` 已标注。

## 两种运行方式

| 模式 | 命令 | 用途 |
|---|---|---|
| 本机 Spark | `python data_collection/tools/run_course.py --engine spark-local --ppt --serve` | 日常演示，`local[1]` 控内存 |
| 两节点集群 | `python data_collection/tools/run_course.py --engine spark-cluster` | 大数据验收（复用现有 VM） |
| pandas 备用 | `--engine pandas` | Spark 故障时兜底 |

集群：`192.168.21.131` Master/NN + `192.168.21.132` Worker/DN。内存紧张时先关多余软件再开两台 VM。

## 清洗政策（字段级）

见 `cleaning/rules.py`：

- 短缺口 ≤3h：连续量插值；降水填 0  
- 长缺口：保留空并 `flag_long_gap_*`  
- 污染峰值：IQR **只标记不盖帽**  
- 湿度/云量：物理界裁剪并标记  

## 证据位置

- `output/data_collection/ods/<batch>/manifest.json`、`collect_log.json`  
- `output/data_collection/reports/spark_local_*.json` 或 `spark_cluster_*.json`  
- `output/data_collection/dwd/<batch>/`  
- PPT：`output/data_collection/基于Spark的多城市气象空气质量采集预处理.pptx`  
- 看板：http://127.0.0.1:8020/  

## 依赖

```powershell
python -m pip install -r data_collection/requirements.txt --cache-dir D:/industrial_energy_analysis/.pip-cache
```

本机 Spark 需要可用的 `pyspark`（或系统 `spark-submit`）。集群模式通过已有 SSH 密钥上传作业并 `spark-submit`。
