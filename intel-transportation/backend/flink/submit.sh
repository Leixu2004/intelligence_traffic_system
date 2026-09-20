#!/usr/bin/env bash
# 9/21 实时计算 · Flink SQL 作业提交与监控脚本
#
# 用法：
#   bash backend/flink/submit.sh start            # 起集群（docker compose）
#   bash backend/flink/submit.sh submit           # 依次执行 source/sink/job 三份 SQL
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

FLINK_WEB_UI_URL="${FLINK_WEB_UI_URL:-http://localhost:8081}"
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
render_sql() {
  require_cmd sed
  mkdir -p "${WORK_DIR}"
  local out="${WORK_DIR}/rendered.sql"
  cat "${SQL_DIR}/source_ddl.sql" "${SQL_DIR}/sink_ddl.sql" "${SQL_DIR}/speed_stats_job.sql" >"${out}"
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
    ;;

  submit)
    rendered="$(render_sql)"
    if [[ -n "${FLINK_HOME:-}" && -x "${FLINK_HOME}/bin/sql-client.sh" ]]; then
      echo "[提交] ${FLINK_HOME}/bin/sql-client.sh -f ${rendered}"
      "${FLINK_HOME}/bin/sql-client.sh" -f "${rendered}"
    else
      require_cmd docker
      echo "[提交] docker compose exec flink-jobmanager ./bin/sql-client.sh -f /tmp/rendered.sql"
      docker compose -f "${COMPOSE_FILE}" cp "${rendered}" flink-jobmanager:/tmp/rendered.sql
      docker compose -f "${COMPOSE_FILE}" exec -T flink-jobmanager \
        ./bin/sql-client.sh -f /tmp/rendered.sql
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
