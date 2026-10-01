"""Create a fresh isolated warehouse schema; dimensions must come from DataX."""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from pyspark.sql import SparkSession
    p = argparse.ArgumentParser()
    p.add_argument('--database', required=True)
    args = p.parse_args()
    db = args.database
    if not re.fullmatch(r'energy_cdc_business_probe_[0-9]{14}', db):
        raise ValueError('Only fresh CDC probe databases can be initialized')
    spark = SparkSession.builder.appName('cdc-scheduler-schema').enableHiveSupport().getOrCreate()
    try:
        location = f'hdfs://localhost:9000/warehouse/{db}'
        fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration())
        if spark.catalog.databaseExists(db) or fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
            raise ValueError('Refusing to reuse existing schema or storage')
        spark.sql(f"CREATE DATABASE {db} LOCATION '{location}'")
        for path in ('hive/ddl/01_ods.sql', 'hive/ddl/02_dwd.sql', 'hive/ddl/03_dws_ads.sql'):
            sql = (ROOT / path).read_text(encoding='utf-8')
            for source in ('energy_ods', 'energy_dwd', 'energy_dws', 'energy_ads'):
                sql = sql.replace(source + '.', db + '.')
            sql = re.sub(r"LOCATION '/warehouse/energy_(?:ods|dwd|dws|ads)/([^']+)'",
                         lambda m: f"LOCATION '{location}/{m[1]}'", sql)
            sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
            for statement in sql.split(';'):
                if statement.strip():
                    spark.sql(statement.strip())
        print('CDC_PROBE_SCHEMA_READY', db)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
