# 真实工业数据

此目录与项目原有的模拟 ERP/MES 数据分开存放，避免混用。

## UCI 钢铁行业能耗数据

- 数据集：Steel Industry Energy Consumption（UCI id 851）
- 场景：韩国光阳一家小型钢铁企业的 15 分钟级用电记录
- 规模：35,040 行，2018 全年，包含用电量、无功功率、功率因数、CO₂、负荷类型等字段
- 许可：CC BY 4.0
- 引用：Sathishkumar V E, Changsun Shin, Yongyun Cho (2021), DOI `10.24432/C52G8C`

抓取命令：

```powershell
python tools/fetch_real_industrial_data.py
```

脚本会在下载前请求站点根目录的 `robots.txt`。若规则禁止下载会立即停止；若允许，则只请求一次官方压缩包，并在 `uci_steel_energy/provenance.json` 中记录抓取时间、来源、robots 判定和 SHA-256 校验值。

数据版权归原作者所有。使用或再发布时须按 CC BY 4.0 署名。
