# 9/21 实时计算 · 在官方 Flink 镜像上并入连接器 jar
#
# 官方 flink 镜像不含 Kafka / JDBC / PostgreSQL 连接器，SQL Client 建表会在
# "Could not find any factory for identifier 'kafka'" 处失败。
# 把下面三个 jar 下载进 ./flink-lib/ 后重新构建即可：
#   flink-sql-connector-kafka-3.2.0-1.19.jar   ← 必须是这个 shaded 包
#   flink-connector-jdbc-3.2.0-1.19.jar
#   postgresql-42.7.3.jar
# （版本必须与 Flink 主版本 1.19 对应，来源 Maven Central / jdbc 驱动来自 PostgreSQL 官方）
#
# Kafka 连接器的两个 jar 名只差一个 -sql-，踩错后现象很隐蔽：非 shaded 的
# flink-connector-kafka-*.jar 不带 kafka-clients，建表能过、提交也过，
# 运行时才在 TM 上炸 ClassNotFoundException:
# org.apache.kafka.clients.consumer.OffsetResetStrategy（本机集群实测踩过）。
# 只有 flink-sql-connector-kafka-* 这个 fat jar 是自足的。
#
# 构建：docker compose --profile flink build flink-jobmanager
# 运行：bash backend/flink/submit.sh start

ARG FLINK_IMAGE=flink:1.19.1-scala_2.12-java11
FROM ${FLINK_IMAGE}

COPY flink-lib/ /tmp/flink-lib/
# 目录里只有 README 时不阻断构建，让 submit.sh 在提交阶段给出可读错误
RUN cp /tmp/flink-lib/*.jar "${FLINK_HOME}/lib/" 2>/dev/null \
    || echo "[warn] ./flink-lib 内没有连接器 jar，Kafka/JDBC 表将无法创建"
