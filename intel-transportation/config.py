import os


VALID_RECOGNITION_MODES = frozenset({"all", "violation_only"})


def normalize_recognition_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in VALID_RECOGNITION_MODES:
        raise ValueError("recognition_mode 必须为 all 或 violation_only")
    return mode


def should_recognize_vehicle(has_violation: bool, mode: str) -> bool:
    return normalize_recognition_mode(mode) == "all" or has_violation

# 基础路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, 'models')
DATA_DIR = os.path.join(BASE_DIR, 'data')
VIOLATIONS_DIR = os.path.join(DATA_DIR, 'violations')
os.makedirs(VIOLATIONS_DIR, exist_ok=True)

def _optional_float(name, default=None):
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


# 模型路径配置，可直接切换为 Ultralytics 支持的 .onnx / .engine 边缘模型。
YOLO_VEHICLE_MODEL_PATH = os.getenv("YOLO_VEHICLE_MODEL_PATH", os.path.join(BASE_DIR, "yolov8n.pt"))
YOLO_PLATE_MODEL_PATH = os.getenv("YOLO_PLATE_MODEL_PATH", os.path.join(MODELS_DIR, "exp-7.pt"))

# 官方 yolov8n 模型中，"车辆"对应的类别 ID (2:car, 3:motorcycle, 5:bus, 7:truck)
VEHICLE_CLASSES = [2, 3, 5, 7]

# Kafka / 数据库配置
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC_TRAFFIC = os.getenv("KAFKA_TOPIC_TRAFFIC", "traffic_stream")
KAFKA_TOPIC_PLATES = os.getenv("KAFKA_TOPIC_PLATES", "plate_recognitions")
KAFKA_TOPIC_VIOLATIONS = os.getenv("KAFKA_TOPIC_VIOLATIONS", "traffic_violations")
KAFKA_OFFLINE_PATH = os.getenv("KAFKA_OFFLINE_PATH", os.path.join(DATA_DIR, "offline_events.jsonl"))

# 当前摄像头/卡口元数据。没有测速标定时 speed_kmh 保持为空，避免伪造数据。
CAMERA_ID = os.getenv("CAMERA_ID", "CAM-01")
CHECKPOINT_ID = os.getenv("CHECKPOINT_ID", "CP-NORTH-01")
CHECKPOINT_GPS_LNG = _optional_float("CHECKPOINT_GPS_LNG", 116.4074)
CHECKPOINT_GPS_LAT = _optional_float("CHECKPOINT_GPS_LAT", 39.9042)
OCR_WORKERS = max(1, int(os.getenv("OCR_WORKERS", "2")))
VIDEO_SOURCE = os.getenv("VIDEO_SOURCE", r"D:\test_vedio\traffic.mp4")
RECOGNITION_MODE = normalize_recognition_mode(os.getenv("RECOGNITION_MODE", "violation_only"))

# ----------------- 违章判定配置 -----------------
# 1. 压线违章配置 (y 坐标)
LINE_Y_COORDINATE = 1800

# 2. 违停检测配置
# 禁停区坐标 [x1, y1, x2, y2] (请根据实际摄像头画面调整)
NO_PARKING_ZONE = [300, 200, 800, 600] 
# 违停判定阈值 (秒)，测试阶段可设短一些，实际业务通常为 3 分钟 (180s)
PARKING_THRESHOLD = 5.0  
