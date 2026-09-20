# flink-lib

Flink 官方镜像不带连接器，SQL Client 建 Kafka / JDBC 表前需要把下面三个 jar 放进本目录，
然后 `docker compose --profile flink build flink-jobmanager flink-taskmanager`（或裸机
`cp *.jar $FLINK_HOME/lib/`）。

| jar | 用途 | 版本要求 |
| --- | --- | --- |
| `flink-connector-kafka-3.2.0-1.19.jar` | `'connector' = 'kafka'` | 尾号必须匹配 Flink 主版本 1.19 |
| `flink-connector-jdbc-3.2.0-1.19.jar` | `'connector' = 'jdbc'` | 同上 |
| `postgresql-42.7.3.jar` | JDBC 驱动 `org.postgresql.Driver` | 与 Flink 版本无关 |

下载地址（Maven Central，浏览器直接打开即可）：

- https://repo1.maven.org/maven2/org/apache/flink/flink-connector-kafka/3.2.0-1.19/
- https://repo1.maven.org/maven2/org/apache/flink/flink-connector-jdbc/3.2.0-1.19/
- https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.3/

jar 不进 git（体积大且与镜像版本绑定），只保留这份说明。
