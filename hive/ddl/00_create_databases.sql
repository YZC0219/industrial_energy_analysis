CREATE DATABASE IF NOT EXISTS energy_ods COMMENT '原始贴源层'
LOCATION '/warehouse/energy_ods';
CREATE DATABASE IF NOT EXISTS energy_dwd COMMENT '清洗明细层'
LOCATION '/warehouse/energy_dwd';
CREATE DATABASE IF NOT EXISTS energy_dws COMMENT '主题汇总层'
LOCATION '/warehouse/energy_dws';
CREATE DATABASE IF NOT EXISTS energy_ads COMMENT '应用数据层'
LOCATION '/warehouse/energy_ads';
