# -*- coding: utf-8 -*-
"""车路云一体化集成演示脚本（9/22 课件交付）。

四段输出：
  [1/4] 交付物清单        —— 课件要求的文件是否齐备（集成模块 + 部署/监控清单 + README）
  [2/4] 五段链路自检      —— Mock vLLM 端点 + 固定窗口夹具跑通「全绿」；再用关断端口展示降级矩阵
  [3/4] 真机连通性        —— TimescaleDB / vLLM / Flink Web UI 三个外部依赖的探测（不可达只报待办）
  [4/4] 大屏推送回读      —— screen_push.jsonl 的最后一条记录（大屏轮询 /api/v1/integration/latest 的口径）

第 2、4 段是接线自检（simulation=true / verified=false 语义如实标注），
不是模型效果结论；真机性能数字（1.2s / QPS62 / 320ms / 680ms）需要
GPU + 权重压测，本脚本不代造，见 backend/integration/README.md「性能数字的边界」。

用法：
    python backend/scripts/integration_smoke.py
    python backend/scripts/integration_smoke.py --json data/integration/smoke.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    """把 git 忽略的 .env 读进当前进程（已存在的环境变量优先）。"""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

from backend.integration.config import load_integration_settings  # noqa: E402
from backend.integration.e2e_test_suite import (  # noqa: E402
    GREEN_ROW,
    PURPLE_ROW,
    MockVllmServer,
    closed_port_base_url,
    make_service,
)
from backend.integration.service import read_pushes  # noqa: E402
from backend.integration.vllm_client import VllmClient  # noqa: E402

MODULE_ROOT = ROOT / "backend" / "integration"
ARTIFACTS = (
    MODULE_ROOT / "config.py",
    MODULE_ROOT / "contracts.py",
    MODULE_ROOT / "flink_source.py",
    MODULE_ROOT / "agent_pipeline.py",
    MODULE_ROOT / "vllm_client.py",
    MODULE_ROOT / "service.py",
    MODULE_ROOT / "router.py",
    MODULE_ROOT / "system_integration.py",
    MODULE_ROOT / "e2e_test_suite.py",
    MODULE_ROOT / "deploy" / "vllm_deploy.yaml",
    MODULE_ROOT / "deploy" / "monitor_config.yaml",
    MODULE_ROOT / "README.md",
)


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def show_artifacts() -> list[dict[str, object]]:
    _section("[1/4] 交付物清单（课件《模型服务化_vLLM与车路云一体化集成》）")
    rows = []
    for path in ARTIFACTS:
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        rows.append({"path": str(path.relative_to(ROOT)), "exists": exists, "bytes": size})
        print(f"  {'[有]' if exists else '[缺]'} {rows[-1]['path']:<46} {size:>7} B")
    return rows


def show_chain_self_check() -> dict[str, object]:
    _section("[2/4] 五段链路自检（Mock vLLM + 固定窗口夹具，接线自证）")
    with MockVllmServer() as mock:
        service, _, push_path = make_service(rows=(PURPLE_ROW, GREEN_ROW), vllm_base_url=mock.base_url)
        green_run = service.run()
    print("  A) 依赖全注入（Flink 窗口→预测→vLLM→Agent→大屏）:")
    for stage in green_run.stages:
        print(f"     {stage.name:<14} {stage.status:<8} {stage.detail}")
    print(f"     verified={green_run.verified}（预期 True），建议 origin={green_run.agent.recommendation.origin}")

    degraded_service, _, _ = make_service(rows=(PURPLE_ROW,), predict=None, vllm_base_url=closed_port_base_url())
    degraded_run = degraded_service.run()
    print("  B) vLLM 不可达 + 预测缺失（降级矩阵）:")
    for stage in degraded_run.stages:
        print(f"     {stage.name:<14} {stage.status:<8} {stage.detail}")
    print(f"     verified={degraded_run.verified}（预期 False），建议 origin={degraded_run.agent.recommendation.origin}")
    print("     降级条目:")
    for item in degraded_run.degradations:
        print(f"       - {item}")
    return {
        "green": {"verified": green_run.verified, "stages": [stage.as_dict() for stage in green_run.stages]},
        "degraded": {"verified": degraded_run.verified, "degradations": degraded_run.degradations},
        "push_path": str(push_path),
        "simulation": True,
    }


def probe_dependencies(settings) -> dict[str, object]:
    _section("[3/4] 真机连通性（探测外部依赖，不可达只报待办不算失败）")
    result: dict[str, object] = {}

    dsn = settings.timescaledb_dsn
    if not dsn:
        print("  [待办] TIMESCALEDB_DSN 未配置 → 窗口段将回落 SimulationWindowReader（simulation=true）")
        result["timescaledb"] = {"configured": False}
    else:
        try:
            import psycopg2

            with psycopg2.connect(dsn, connect_timeout=3) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM speed_stats")
                    count = cursor.fetchone()[0]
            print(f"  [OK] TimescaleDB 可达，speed_stats 行数 {count}")
            result["timescaledb"] = {"configured": True, "reachable": True, "speed_stats_rows": count}
        except Exception as exc:  # 库侧任何异常都不该让脚本崩
            print(f"  [待办] TimescaleDB 不可达: {type(exc).__name__}（docker compose up -d timescaledb）")
            result["timescaledb"] = {"configured": True, "reachable": False, "reason": type(exc).__name__}

    client = VllmClient(settings)
    health = client.health()
    if health.get("reachable"):
        print(f"  [OK] vLLM 可达: {settings.vllm_base_url}，服务模型 {health.get('models')}")
        result["vllm"] = {"reachable": True, "models": health.get("models")}
    else:
        print(
            "  [待办] vLLM 不可达: {}（docker compose --profile vllm up -d vllm，".format(health.get("reason"))
            + "或把 TRAFFIC_VLLM_BASE_URL 指向已有端点）"
        )
        result["vllm"] = {"reachable": False, "reason": health.get("reason")}
    client.close()

    web_ui = os.getenv("FLINK_WEB_UI_URL", "http://localhost:8081")
    try:
        with urllib.request.urlopen(f"{web_ui}/overview", timeout=3) as response:
            json.loads(response.read().decode("utf-8"))
        print(f"  [OK] Flink Web UI 可达: {web_ui}")
        result["flink_web_ui"] = {"reachable": True}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"  [待办] Flink Web UI 不可达: {type(exc).__name__}（bash backend/flink/submit.sh start）")
        result["flink_web_ui"] = {"reachable": False, "reason": type(exc).__name__}
    return result


def show_push_log(settings) -> dict[str, object]:
    _section("[4/4] 大屏推送回读（screen_push.jsonl 最后一条 = /api/v1/integration/latest 的口径）")
    records = read_pushes(settings.push_path, limit=5)
    if not records:
        print(f"  [空] {settings.push_path} 还没有记录（跑一次 python -m backend.integration.system_integration）")
        return {"records": 0, "path": str(settings.push_path)}
    latest = records[0]
    print(f"  {settings.push_path} 共 {len(records)} 条（本批回读 5 条内）")
    for key in ("time", "checkpoint_id", "alert_level", "avg_speed", "recommendation_origin", "verified"):
        print(f"    {key:<22} {latest.get(key)}")
    degradations = latest.get("degradations") or []
    if degradations:
        print(f"    降级 {len(degradations)} 条，例如: {degradations[0]}")
    return {"records": len(records), "latest": latest, "path": str(settings.push_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="车路云一体化集成演示与自检")
    parser.add_argument("--json", dest="json_path", default=None, help="把结果写入 JSON 文件")
    args = parser.parse_args()

    settings = load_integration_settings()
    artifacts = show_artifacts()
    chain = show_chain_self_check()
    dependencies = probe_dependencies(settings)
    push_log = show_push_log(settings)

    _section("结论")
    missing = [row["path"] for row in artifacts if not row["exists"]]
    print(f"  交付物齐备: {not missing}" + ("" if not missing else f"（缺: {', '.join(missing)}）"))
    print(f"  五段链路接线: {'自证通过（Mock 全绿 + 降级如实记账）' if chain['green']['verified'] else '异常'}")
    print(
        "  真机依赖: "
        + "; ".join(
            f"{key}={value.get('reachable') or value.get('configured')}"
            for key, value in dependencies.items()
        )
    )
    print("  说明: 本脚本证明接线正确与降级诚实，不构成真机性能结论（见 README「性能数字的边界」）。")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "settings": {
                        "enabled": settings.enabled,
                        "vllm_base_url": settings.vllm_base_url,
                        "vllm_model": settings.vllm_model,
                        "forecast_steps": settings.forecast_steps,
                    },
                    "artifacts": artifacts,
                    "chain_self_check": chain,
                    "dependencies": dependencies,
                    "push_log": push_log,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  结果已写入 {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
