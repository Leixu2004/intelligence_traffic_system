# -*- coding: utf-8 -*-
"""多模态视频解说演示自检（9/18 课件交付）。

五段输出：
  [1/5] 交付物清单     —— 课件要求的 6 类文件是否齐备
  [2/5] 依赖与配置     —— OpenCV / httpx / ultralytics / torch、密钥、Prompt 模板
  [3/5] 抽帧与关键帧   —— 对演示片段真跑一遍，给出前后帧数对比
  [4/5] 端到端视觉链路 —— 默认打本机 Mock 多模态端点；--live 打真实 Qwen-VL
  [5/5] REST/WS 接口   —— TestClient 直调 FastAPI，验证 4 个接口

第 4 段的 Mock 端点会**真读**请求里的 base64 图像（按演示片段中只在事故时段出现的
橙色警示三角判定有没有事故），所以能证明「图像→模型→结构化标签→解说→告警」这条链
是通的；但它不是 Qwen-VL，不能用来宣称任何识别准确率。真实模型效果请用 --live 跑。

用法：
    python backend/scripts/vision_smoke.py            # 离线，本机 Mock 端点
    python backend/scripts/vision_smoke.py --live     # 用 .env 里的真实密钥
    python backend/scripts/vision_smoke.py --json data/vision/smoke.json
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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

from backend.vision.config import PROMPT_NAMES, VisionSettings, load_vision_settings  # noqa: E402
from backend.vision.demo_clip import COLLISION_START, generate_demo_video, scene_ground_truth  # noqa: E402
from backend.vision.multimodal_system import VideoAnalysisSystem  # noqa: E402
from backend.vision.prompt_store import PromptStore  # noqa: E402
from backend.vision.video_processor import (  # noqa: E402
    VideoDecodeUnavailable,
    extract_frames,
    opencv_available,
    select_keyframes,
)
from backend.vision.vl_analyzer import VLAnalyzer  # noqa: E402

DEMO_CLIP = ROOT / "data" / "vision" / "demo" / "road_demo.mp4"
# 演示片段里只有事故时段会画这个橙色三角（232,128,24），Mock 端点据此判断画面异常
ALERT_COLOR = (232, 128, 24)
ALERT_COLOR_TOLERANCE = 40
ALERT_PIXEL_RATIO = 0.001
MOCK_MODEL = "mock-qwen-vl"

DELIVERABLES = (
    "backend/vision/multimodal_system.py",
    "backend/vision/video_processor.py",
    "backend/vision/vl_analyzer.py",
    "backend/vision/prompts",
    "backend/vision/api_server.py",
    "backend/vision/README.md",
)
SUPPORTING = (
    "backend/vision/config.py",
    "backend/vision/prompt_store.py",
    "backend/vision/service.py",
    "backend/vision/router.py",
    "backend/vision/contracts.py",
    "backend/vision/demo_clip.py",
    "backend/vision/tests",
)


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _has(content: str, *markers: str) -> bool:
    return all(marker in content for marker in markers)


def show_deliverables() -> bool:
    _section("[1/5] 交付物清单（课件《多模态理解与监控视频智能解说》）")
    ok = True
    for relative in DELIVERABLES + SUPPORTING:
        path = ROOT / relative
        exists = path.exists()
        ok = ok and exists
        label = "[有]" if exists else "[缺]"
        detail = ""
        if relative.endswith("prompts") and exists:
            store = PromptStore(path)
            missing = store.missing(PROMPT_NAMES)
            detail = f"模板 {len(PROMPT_NAMES) - len(missing)}/{len(PROMPT_NAMES)}"
            ok = ok and not missing
        elif exists and path.is_file():
            detail = f"{path.stat().st_size:>6} B"
        print(f"  {label} {relative:<42} {detail}")
    return ok


def show_dependencies() -> dict[str, Any]:
    _section("[2/5] 依赖与配置")
    report: dict[str, Any] = {}
    for name in ("cv2", "PIL", "httpx", "ultralytics", "torch", "transformers"):
        try:
            __import__(name)
            report[name] = True
        except ImportError:
            report[name] = False
    optional = {"ultralytics": "YOLOv8 车辆计数", "torch": "本机推理 Qwen2-VL", "transformers": "本机推理 Qwen2-VL"}
    for module, capability in optional.items():
        mark = "[有]" if report[module] else "[缺]"
        print(f"  {mark} {module:<14} 可选能力: {capability}")
    for module in ("cv2", "PIL", "httpx"):
        mark = "[有]" if report[module] else "[缺]"
        print(f"  {mark} {module:<14} 必需")

    settings = load_vision_settings()
    summary = settings.describe()
    print(f"  backend={summary['backend']} model={summary['model']} api_key_present={summary['api_key_present']}")
    print(
        f"  抽帧间隔={summary['frame_interval_seconds']}s 关键帧阈值={summary['keyframe_threshold']} "
        f"单视频上限={summary['max_frames_per_video']}帧"
    )
    missing = PromptStore(settings.prompts_dir).missing(PROMPT_NAMES)
    print(f"  Prompt 缺失: {list(missing) or '无'}")
    report["settings"] = summary
    report["missing_prompts"] = list(missing)
    report["required_ok"] = all(report[name] for name in ("cv2", "PIL", "httpx")) and not missing
    return report


def show_extraction(settings) -> dict[str, Any]:
    _section("[3/5] 视频抽帧与关键帧筛选")
    if not opencv_available():
        print("  [待办] 未安装 OpenCV，无法抽帧：pip install opencv-python-headless")
        return {"ok": False}
    try:
        clip = generate_demo_video(DEMO_CLIP, seconds=40, fps=10)
    except RuntimeError as exc:  # 编码器不可用
        print(f"  [待办] 演示片段生成失败：{exc}")
        return {"ok": False}
    print(f"  演示片段: {clip.relative_to(ROOT)}（合成画面，非真实事故影像）")
    for row in scene_ground_truth():
        print(f"    脚本 {row['at_seconds']:>5.1f}s  {row['scripted_scene']}")
    try:
        frames = extract_frames(clip, interval=settings.frame_interval_seconds, max_frames=settings.max_frames_per_video)
    except VideoDecodeUnavailable as exc:
        print(f"  [待办] {exc}")
        return {"ok": False}
    keyframes, dropped = select_keyframes(frames, settings.keyframe_threshold)
    print(f"  抽帧: {len(frames)} 帧（间隔 {settings.frame_interval_seconds}s，帧号 {frames[0].index}→{frames[-1].index}）")
    print(f"  关键帧: {len(keyframes)} 保留 / {len(dropped)} 跳过")
    if frames:
        ratio = len(keyframes) / len(frames)
        print(f"  送模型比例: {ratio:.0%}（课件优化项「减少 80% 无效分析」的本地实测口径）")
    saved = []
    if keyframes:
        directory = settings.frames_dir / "demo"
        directory.mkdir(parents=True, exist_ok=True)
        for frame in keyframes[:3]:
            path = directory / f"frame_{frame.index:05d}_{frame.time_label.replace(':', 'm')}.jpg"
            path.write_bytes(frame.to_jpeg(quality=settings.jpeg_quality, max_side=settings.image_max_side))
            saved.append(path.name)
        print(f"  样例帧已存: {directory.relative_to(ROOT)} -> {', '.join(saved)}")
    unique = {frame.image.tobytes() for frame in frames}
    print(f"  帧去重校验: {len(unique)}/{len(frames)} 唯一（全黑/重复说明解码有问题）")
    return {
        "ok": bool(frames) and len(unique) == len(frames),
        "clip": str(clip.relative_to(ROOT)),
        "frames": len(frames),
        "keyframes": len(keyframes),
        "dropped": len(dropped),
    }


def _orange_ratio(data_url: str) -> float:
    """把请求里的 base64 图像解码，算橙色警示三角的像素占比。"""
    import numpy as np
    from PIL import Image

    payload = base64.b64decode(data_url.split(",", 1)[1])
    array = np.asarray(Image.open(io.BytesIO(payload)).convert("RGB"), dtype="int16")
    near = np.abs(array - np.array(ALERT_COLOR, dtype="int16")).max(axis=2) <= ALERT_COLOR_TOLERANCE
    return float(near.mean())


def _mock_completion(body: dict[str, Any], stats: dict[str, int]) -> str:
    parts = body["messages"][0]["content"]
    prompt = next((part["text"] for part in parts if part["type"] == "text"), "")
    data_url = next((part["image_url"]["url"] for part in parts if part["type"] == "image_url"), "")
    hit = bool(data_url) and _orange_ratio(data_url) >= ALERT_PIXEL_RATIO
    stats["calls"] += 1
    if prompt.startswith("你是交通监控分析师"):
        stats["analysis"] += 1
        return json.dumps(
            {
                "accident": hit,
                "accident_type": "追尾" if hit else "无",
                "vehicle_count": 2 if hit else None,
                "severity": "中等" if hit else "无",
                "location": "画面中部第一车道" if hit else None,
                "lane": "第 1 车道" if hit else None,
                "actions": ["封闭第 1 车道", "调度拖车", "上游限速诱导"] if hit else [],
                "evidence": "两车在同一车道停住且车距重叠，路面出现故障警示三角"
                if hit
                else "车辆间距正常、无停止车辆",
            },
            ensure_ascii=False,
        )
    if prompt.startswith("你是交通监控告警生成器"):
        stats["alert"] += 1
        return json.dumps(
            {
                "title": "第 1 车道追尾告警",
                "level": "alert",
                "summary": "两车在第 1 车道停住，仅其余车道可通行",
                "actions": ["封闭第 1 车道", "派交警与拖车"],
                "push_text": "第 1 车道发生事故，请从第 2、3 车道减速通过。",
            },
            ensure_ascii=False,
        )
    if prompt.startswith("你是交通监控视频解说员"):
        stats["narration"] += 1
        return (
            "模拟解说：视频前段三车道通行正常；约第 18 秒起第 1 车道两车停住并有警示三角，"
            "同车道后车开始排队，建议封闭第 1 车道并派拖车。"
        )
    stats["describe"] += 1
    return "三车道沥青路面，多辆轿车分车道行驶，车道线清晰，无异常事件。" if not hit else (
        "第 1 车道两车首尾相接停住，车后放置橙色三角警示牌，同车道后续车辆减速排队。"
    )


class _MockHandler(BaseHTTPRequestHandler):
    server_version = "MockVisionVL/1.0"
    stats: dict[str, int] = {}

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            text = _mock_completion(body, self.stats)
        except Exception as exc:  # noqa: BLE001 - 让客户端拿到非 200 而不是挂死线程
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))
            return
        payload = json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:  # 静音访问日志
        return


def start_mock_server() -> tuple[ThreadingHTTPServer, dict[str, int]]:
    _MockHandler.stats = {"calls": 0, "describe": 0, "analysis": 0, "narration": 0, "alert": 0}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, _MockHandler.stats


def build_system(settings, *, base_url: str | None, api_key: str, model: str = "") -> VideoAnalysisSystem:
    """用给定端点装配分析系统（Mock 与真实密钥走同一条代码路径）。"""
    values = {name: getattr(settings, name) for name in VisionSettings.__dataclass_fields__}
    values.update(
        enabled=True,
        backend="remote",
        api_key=api_key,
        base_url=base_url or settings.base_url,
        max_retries=0,
    )
    if model:
        values["model"] = model
    resolved = VisionSettings(**values)
    return VideoAnalysisSystem(resolved, analyzer=VLAnalyzer(resolved, PromptStore(resolved.prompts_dir)))


def run_end_to_end(system: VideoAnalysisSystem, clip: Path) -> dict[str, Any]:
    report = system.analyze_video(clip).as_dict()
    timeline = report["timeline"]
    print(f"  分析帧数: {len(timeline)}，模型调用: {system.analyzer.client.name}")
    print("  时间线（前 6 行）:")
    for row in timeline[:6]:
        vehicles = row["vehicles"] if row["vehicles"] is not None else "未检测"
        print(f"    {row['time']}  {row['status']:<6} 车辆:{vehicles}  {row['description'][:34]}")
    accidents = [row for row in timeline if row["accident"] and row["accident"]["accident"]]
    print(f"  判定为事故的帧: {len(accidents)}  " + ", ".join(row["time"] for row in accidents[:6]))
    if accidents:
        first = accidents[0]
        print(f"    首帧标签: type={first['accident']['event_type']} severity={first['accident']['severity']} "
              f"lane={first['accident']['lane']}")
    print(f"  解说文本: {report['narration'][:70] or '(无)'}")
    print(f"  告警条数: {len(report['alerts'])}  verified={report['verified']}")
    for item in report["degradations"]:
        print(f"  降级: {item}")
    scripted = [row for row in timeline if row["timestamp_seconds"] >= COLLISION_START]
    flagged = [row for row in scripted if row["accident"] and row["accident"]["accident"]]
    if scripted:
        print(f"  对照生成脚本: 事故时段 {len(scripted)} 帧，其中 {len(flagged)} 帧被判为事故")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="多模态视频解说系统自检")
    parser.add_argument("--live", action="store_true", help="用 .env 里的真实视觉端点，而不是本机 Mock")
    parser.add_argument("--json", dest="json_path", default=None, help="把结果写入 JSON 文件")
    parser.add_argument("--source", default=None, help="自定义视频或图片目录，默认用合成演示片段")
    args = parser.parse_args()

    settings = load_vision_settings()
    deliverables_ok = show_deliverables()
    dependencies = show_dependencies()
    extraction = show_extraction(settings)

    _section("[4/5] 端到端视觉链路")
    clip = Path(args.source) if args.source else DEMO_CLIP
    server = None
    stats: dict[str, int] = {}
    if args.live:
        if not settings.vl_configured:
            print("  [待办] 真实端点缺少密钥：请在 .env 填入 DASHSCOPE_API_KEY 或 TRAFFIC_VL_API_KEY")
            return 1
        system = build_system(settings, base_url=None, api_key=settings.api_key)
        mode = "live"
        print(f"  模式: 真实端点 {settings.base_url} model={settings.model}")
    else:
        server, stats = start_mock_server()
        port = server.server_address[1]
        system = build_system(
            settings, base_url=f"http://127.0.0.1:{port}/v1", api_key="mock-key", model=MOCK_MODEL
        )
        mode = "mock"
        print(f"  模式: 本机 Mock OpenAI 兼容端点 http://127.0.0.1:{port}/v1（{MOCK_MODEL}）")
        print("  说明: Mock 会真读请求里的图像，因此本段证明链路可用，不代表识别准确率。")
    try:
        report = run_end_to_end(system, clip)
        # 「关键帧筛选跳过 N 帧」是预期收益，不是故障；只有真降级才算失败
        failures = [item for item in report["degradations"] if "跳过" not in item]
        end_to_end_ok = bool(report["timeline"]) and not failures

        _section("[5/5] REST / WebSocket 接口")
        api_result = check_api(system)
        api_ok = all(item["ok"] for item in api_result)
        for item in api_result:
            print(f"  {'[OK ]' if item['ok'] else '[FAIL]'} {item['name']:<28} {item['detail']}")
    finally:
        # Mock 端点必须活到第 5 段之后：接口自检走的是同一个 system
        if server is not None:
            server.shutdown()

    _section("结论")
    print(f"  交付物齐备: {deliverables_ok}")
    print(f"  必需依赖齐备: {dependencies['required_ok']}")
    print(f"  抽帧可用: {extraction.get('ok')}")
    print(f"  端到端链路: {'通过' if end_to_end_ok else '未通过'}（模式: {mode}）")
    print(f"  接口自检: {'通过' if api_ok else '未通过'}")
    if mode == "mock":
        print(f"  Mock 端点调用次数: {stats['calls']}（描述 {stats['describe']} / 分析 {stats['analysis']} "
              f"/ 解说 {stats['narration']} / 告警 {stats['alert']}）")
        print("  真实模型效果: python backend/scripts/vision_smoke.py --live")
    print("  提示: 演示片段为合成画面，输出仅作链路验证，不作为识别准确率证据。")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "mode": mode,
                    "deliverables_ok": deliverables_ok,
                    "dependencies": {key: value for key, value in dependencies.items() if key != "settings"},
                    "extraction": extraction,
                    "report": report,
                    "api": api_result,
                    "mock_stats": stats,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  结果已写入 {target}")

    return 0 if (deliverables_ok and dependencies["required_ok"] and extraction.get("ok") and end_to_end_ok and api_ok) else 1


def check_api(system: VideoAnalysisSystem) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        from fastapi.testclient import TestClient

        from backend.vision.api_server import create_app
        from backend.vision.service import VisionService
    except ImportError as exc:
        return [{"name": "FastAPI 依赖", "ok": False, "detail": str(exc)}]
    service = VisionService(system.settings, system)
    with TestClient(create_app(system.settings, service)) as client:
        health = client.get("/api/v1/vision/health").json()["data"]
        rows.append(
            {"name": "GET /health", "ok": health["available"], "detail": f"model={health['model']} backend={health['backend']}"}
        )
        video = client.post(
            "/api/v1/vision/video",
            files={"file": ("road_demo.mp4", DEMO_CLIP.read_bytes(), "video/mp4")} if DEMO_CLIP.is_file() else None,
        )
        data = video.json().get("data") or {}
        rows.append(
            {
                "name": "POST /video",
                "ok": video.status_code == 200 and bool(data.get("timeline")),
                "detail": f"status={video.status_code} 帧数={len(data.get('timeline') or [])} 告警={len(data.get('alerts') or [])}",
            }
        )
        image_frame = next((path for path in (system.settings.frames_dir / "demo").glob("*.jpg")), None)
        if image_frame is None:
            rows.append({"name": "POST /analyze", "ok": False, "detail": "缺少样例帧，先跑第 3 段"})
        else:
            analyze = client.post(
                "/api/v1/vision/analyze",
                files={"file": (image_frame.name, image_frame.read_bytes(), "image/jpeg")},
                data={"question": "画面里有几辆车？"},
            )
            payload = (analyze.json().get("data") or {}).get("timeline") or [{}]
            rows.append(
                {
                    "name": "POST /analyze",
                    "ok": analyze.status_code == 200 and bool(payload[0].get("description")),
                    "detail": f"status={analyze.status_code} answer={str(payload[0].get('answer'))[:24]}",
                }
            )
        results = client.get("/api/v1/vision/results", params={"limit": 3}).json()["data"]
        rows.append(
            {"name": "GET /results", "ok": bool(results["items"]), "detail": f"{len(results['items'])} 条分析记录"}
        )
        try:
            with client.websocket_connect("/api/v1/vision/stream") as socket:
                socket.send_json({"source": str(DEMO_CLIP)})
                types = []
                while True:
                    message = socket.receive_json()
                    types.append(message["type"])
                    if message["type"] in {"result", "error"}:
                        break
            rows.append(
                {
                    "name": "WS /stream",
                    "ok": types[0] == "start" and "frame" in types and types[-1] == "result",
                    "detail": f"{types[0]} → {types.count('frame')} 帧 → {types[-1]}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - 自检要把失败说清楚而不是抛栈
            rows.append({"name": "WS /stream", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
