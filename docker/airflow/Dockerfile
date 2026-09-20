# Airflow 镜像: 官方镜像 + 本项目的 Python 依赖
#
# 为什么不用 AIRFLOW__CORE__... 的 _PIP_ADDITIONAL_REQUIREMENTS?
# 那个开关会在**每次容器启动时**现装依赖, 启动慢、不可复现, 官方明确不推荐用于
# 生产。把依赖固化进镜像, 换来的是"同一个 tag 永远是同一个环境"。
FROM apache/airflow:2.10.5-python3.11

USER root
COPY requirements.txt /tmp/requirements.txt
RUN chown airflow:0 /tmp/requirements.txt

# 官方镜像的约定: pip install 以 airflow 用户执行, 避免 site-packages 权限错乱
USER airflow
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt
