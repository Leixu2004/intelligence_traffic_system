#!/usr/bin/env bash
# 9/21 实时计算 · Flink SQL 作业提交与监控脚本
#
# 用法：
#   bash backend/flink/submit.sh start            # 起集群（docker compose）
#   bash backend/flink/submit.sh submit           # 依次执行 9/21 车速统计 + 9/21 CEP 预警两份作业
#   bash backend/flink/submit.sh status           # curl Web UI 作业列表
#   bash backend/flink/submit.sh cancel <jobId>   # 取消作业
#   bash backend/flink/submit.sh stop             # 停集群
#
# 覆盖默认地址/口令（不要把真实口令写进 git 跟踪的文件）：
#   FLINK_WEB_UI_URL=http://localhost:8081 \
#   KAFKA_BROKERS=kafka:29092 TIMESCALEDB_URL=jdbc:postgresql://timescaledb:5432/traffic \
#   TIMESCALEDB_USER=postgres TIMESCALEDB_PASSWORD=*** bash backend/flink/submit.sh submit
#
# 裸机部署（非容器）时设 FLINK_HOME=$FLINK_HOME 并跳过 start：
#   ./bin/start-cluster.sh && ./bin/sql-client.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SQL_DIR="${SCRIPT_DIR}/sql"
WORK_DIR="${PROJECT_ROOT}/data/flink"

# 默认 8181 = docker-compose.yml 里 FLINK_WEB_UI_HOST_PORT 的默认值。
# 别填 8081：本机 8081 常驻着另一套 Flink，指错会读到别人集群的槽位与作业数。
FLINK_WEB_UI_URL="${FLINK_WEB_UI_URL:-http://localhost:8181}"
KAFKA_BROKERS="${KAFKA_BROKERS:-kafka:29092}"
TIMESCALEDB_URL="${TIMESCALEDB_URL:-jdbc:postgresql://timescaledb:5432/traffic}"
TIMESCALEDB_USER="${TIMESCALEDB_USER:-postgres}"
TIMESCALEDB_PASSWORD="${TIMESCALEDB_PASSWORD:-}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
# Windows 上一般指向 .venv/Scripts/python.exe
PYTHON="${PYTHON:-python}"

action="${1:-submit}"
shift || true

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[失败] 缺少命令 $1" >&2
    exit 127
  }
}

# sql-client 不支持环境变量插值，所以渲染一份带真实口令的临时副本；
# 副本落在 data/flink/（已被 git 忽略），提交物本身保持默认值。
# 顺序不能改：Source → 两张 Sink → 车速作业 → CEP 视图 → 预警分级与写出。
SQL_FILES=(
  source_ddl.sql
  sink_ddl.sql
  alert_sink.sql
  speed_stats_job.sql
  congestion_cep.sql
  alert_level.sql
)

render_sql() {
  require_cmd sed
  mkdir -p "${WORK_DIR}"
  local out="${WORK_DIR}/rendered.sql"
  : >"${out}"
  local file
  for file in "${SQL_FILES[@]}"; do
    [[ -f "${SQL_DIR}/${file}" ]] || { echo "[失败] 缺少 ${SQL_DIR}/${file}" >&2; exit 1; }
    cat "${SQL_DIR}/${file}" >>"${out}"
    printf '\n' >>"${out}"
  done
  sed -i.bak \
    -e "s#'properties.bootstrap.servers' = 'kafka:29092'#'properties.bootstrap.servers' = '${KAFKA_BROKERS}'#" \
    -e "s#'url' = 'jdbc:postgresql://timescaledb:5432/traffic'#'url' = '${TIMESCALEDB_URL}'#" \
    -e "s#'username' = 'postgres'#'username' = '${TIMESCALEDB_USER}'#" \
    "${out}"
  if [[ -n "${TIMESCALEDB_PASSWORD}" ]]; then
    sed -i.bak "s#'password' = 'postgres'#'password' = '${TIMESCALEDB_PASSWORD}'#" "${out}"
  fi
  rm -f "${out}.bak"
  echo "${out}"
}

wait_for_web_ui() {
  local deadline=$((SECONDS + 90))
  while ((SECONDS < deadline)); do
    if curl -sf "${FLINK_WEB_UI_URL}/overview" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

case "${action}" in
  start)
    require_cmd docker
    docker compose -f "${COMPOSE_FILE}" up -d flink-jobmanager flink-taskmanager
    echo "[等待] Flink Web UI ${FLINK_WEB_UI_URL} ..."
    if wait_for_web_ui; then
      echo "[就绪] ${FLINK_WEB_UI_URL}"
    else
      echo "[失败] 90 秒内 Web UI 未就绪，检查 docker compose logs flink-jobmanager" >&2
      exit 1
    fi
    # 两件必做预备（漏掉任一件，第一次提交必失败；均为本机集群实测踩点）：
    #   1) checkpoint 卷属主：docker exec 以 root 进容器，而 Flink 进程以 flink 用户运行，
    #      否则 checkpoint 报 Failed to create directory for shared state；
    #   2) 建 topic：Kafka 侧关了 auto-create，生产端先报 UnknownTopicOrPartitionException。
    KAFKA_TOPIC="${KAFKA_TOPIC:-traffic_stream}"
    ( cd "${PROJECT_ROOT}" && \
      MSYS_NO_PATHCONV=1 docker compose exec -T flink-jobmanager \
        chown -R flink:flink /opt/flink/checkpoints && \
      MSYS_NO_PATHCONV=1 docker compose exec -T flink-taskmanager \
        chown -R flink:flink /opt/flink/checkpoints && \
      MSYS_NO_PATHCONV=1 docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server localhost:9092 --create --if-not-exists \
        --topic "${KAFKA_TOPIC}" --partitions 4 --replication-factor 1 ) \
      || echo "[警告] 预备步骤没全成，手工命令见 README「CEP 运行步骤」第 0 步" >&2
    ;;

  submit)
    rendered="$(render_sql)"
    if [[ -n "${FLINK_HOME:-}" && -x "${FLINK_HOME}/bin/sql-client.sh" ]]; then
      echo "[提交] ${FLINK_HOME}/bin/sql-client.sh -f ${rendered}"
      "${FLINK_HOME}/bin/sql-client.sh" -f "${rendered}"
    else
      require_cmd docker
      echo "[提交] docker compose exec flink-jobmanager ./bin/sql-client.sh -f /tmp/rendered.sql"
      # Git Bash 的 MSYS 会把参数里的 /tmp/... 和 /d/... 都改写成 Windows 路径：
      # 这里 cd 到项目根（compose 自动找 ./docker-compose.yml，不再传绝对 -f 路径），
      # 并对 cp/exec 两条命令关掉路径转换，让容器内 /tmp/rendered.sql 保持原样。
      # 部署目录是 Windows 盘符路径，相对路径一律交给 bash 的 ${var##prefix} 求值，
      # 不要用 python os.path.relpath —— 它收到的 /d/... 会被 MSYS 改写成 C:/...。
      relative_rendered="${rendered#${PROJECT_ROOT}/}"
      relative_rendered="${relative_rendered//\\//}"
      ( cd "${PROJECT_ROOT}" && \
        MSYS_NO_PATHCONV=1 docker compose cp "${relative_rendered}" flink-jobmanager:/tmp/rendered.sql && \
        MSYS_NO_PATHCONV=1 docker compose exec -T flink-jobmanager \
          ./bin/sql-client.sh -f /tmp/rendered.sql )
    fi
    ;;

  status)
    curl -sf "${FLINK_WEB_UI_URL}/jobs" | "${PYTHON}" -c '
import json, sys
jobs = json.load(sys.stdin).get("jobs") or []
if not jobs:
    print("(当前没有作业)")
for job in jobs:
    print("%s  %-9s  %s" % (job.get("id"), job.get("state"), job.get("name")))
'
    ;;

  cancel)
    job_id="${1:-}"
    [[ -n "${job_id}" ]] || { echo "[失败] cancel 需要 jobId" >&2; exit 2; }
    curl -sf -X PATCH "${FLINK_WEB_UI_URL}/jobs/${job_id}?mode=cancel"
    echo
    echo "[已请求取消] ${job_id}"
    ;;

  stop)
    require_cmd docker
    docker compose -f "${COMPOSE_FILE}" stop flink-jobmanager flink-taskmanager
    ;;

  check)
    # 不依赖集群的自检：交付物齐不齐 + 窗口语义复算
    python3 "${PROJECT_ROOT}/backend/scripts/flink_smoke.py"
    ;;

  *)
    echo "用法: bash backend/flink/submit.sh {start|submit|status|cancel|stop|check}" >&2
    exit 2
    ;;
esac
