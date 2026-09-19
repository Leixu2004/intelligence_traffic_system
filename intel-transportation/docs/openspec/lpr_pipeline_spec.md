# OpenSpec: 车牌检测、透视校正与识别流水线规范 (LPR-SPEC-v1.1)

> **版本**: 1.1.0  
> **更新时间**: 2026-09-16  
> **适用范围**: 智慧交通系统 (`intel-transportation`)、车牌检测定位、几何透视校正、PaddleOCR 字符识别及系统解耦集成。

> **部署规范**: ONNX 制品、量化、Jetson/Atlas 适配与性能验收统一遵循 [`docs/deployment/edge_inference.md`](../deployment/edge_inference.md)。本文件只定义 LPR 业务流水线，不把导出入口等同于边缘设备部署完成。

---

## 1. 架构总览与构建逻辑 (Pipeline Architecture)

车牌检测识别系统采用分层解耦的流水线（Pipeline）设计，分为五个标准阶段：

```
[Phase 1: 图像输入]
   └─ cv2.imread / cv2.VideoCapture (获取原始图像帧 BGR)
          ↓
[Phase 2: 车牌定位 (YOLOv8 Plate Detect)]
   └─ 权重: models/exp-7.pt 或 plate_detect.pt
   └─ 输出: Bounding Box [x1, y1, x2, y2]、置信度 conf (>= 0.5)
          ↓
[Phase 3: 几何透视校正 (Perspective Warp)]
   └─ 4 点单应性变换 (tl, tr, br, bl) -> 映射至正向无畸变矩形 (width x height)
   └─ 消除斜拍与安装倾斜带来的字符粘连与首字丢失
          ↓
[Phase 4: 字符识别 (PaddleOCR / PP-OCRv6)]
   └─ PaddleOCR 3.x 关闭文档级预处理；2.x 使用兼容参数
   └─ 识别省份简称汉字 + 字母 + 数字序列，提取置信度
          ↓
[Phase 5: 结构化输出与业务消费]
   └─ 运行结果: box、det_conf、roi、text、conf、is_valid、plate_type
   └─ 外部协议: 序列化前移除 roi，仅发送业务字段
   └─ 对接下游: 违章判定 (ViolationChecker)、数据总线 (KafkaClient)、证据持久化
```

---

## 2. 模块技术规范与实现方法

### 2.1 模块一：车牌检测 (Plate Detection)

- **核心目的**: 从输入图像或车辆 ROI 中精确定位车牌矩形或多边形区域。
- **技术选型**: YOLOv8 目标检测网络（微调车牌权重）。
- **标准参数**:
  - 默认置信度过滤阈值: `conf = 0.5`
  - 边界裁剪保护: 坐标转换为整数并进行越界截断 `max(0, coord)`。
- **标准实现范式**:
```python
import cv2
from ultralytics import YOLO

class PlateDetector:
    def __init__(self, model_path: str = "models/exp-7.pt", conf_thresh: float = 0.5):
        self.model = YOLO(model_path)
        self.conf_thresh = conf_thresh

    def detect(self, img):
        results = self.model(img, conf=self.conf_thresh, verbose=False)
        boxes_list = []
        if len(results) > 0 and results[0].boxes is not None:
            for box in results[0].boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
                conf = float(box.conf[0].item())
                boxes_list.append({"box": [x1, y1, x2, y2], "conf": conf})
        return boxes_list
```

---

### 2.2 模块二：4 点透视变换校正 (Perspective Warp)

- **核心目的**: 纠正车辆斜向行驶或摄像头俯仰/侧向拍摄导致的车牌梯形、平行四边形畸变。
- **数学原理**:
  1. 设定车牌 4 个有序角点：`Top-Left (tl)`, `Top-Right (tr)`, `Bottom-Right (br)`, `Bottom-Left (bl)`。
  2. 计算变换后正视图尺寸：
     $$Width = \max(\|\text{br} - \text{bl}\|_2, \|\text{tr} - \text{tl}\|_2)$$
     $$Height = \max(\|\text{tr} - \text{br}\|_2, \|\text{tl} - \text{bl}\|_2)$$
  3. 目标矩形映射坐标：
     $$\text{dst} = [[0, 0], [Width-1, 0], [Width-1, Height-1], [0, Height-1]]$$
  4. 利用 `cv2.getPerspectiveTransform(rect, dst)` 计算透视变换矩阵 $M$，执行 `cv2.warpPerspective`。
- **标准实现范式**:
```python
import cv2
import numpy as np

def warp_plate(img: np.ndarray, pts: list or np.ndarray) -> np.ndarray:
    """
    4 点透视变换校正倾斜车牌
    :param img: 输入原图或车辆局部图
    :param pts: 4 个顶点坐标 [(x1,y1), (x2,y1), (x2,y2), (x1,y2)] 或检测角点
    :return: 矫正后的水平正向车牌图像
    """
    rect = np.array(pts, dtype="float32")
    (tl, tr, br, bl) = rect

    # 计算水平与垂直方向实际像素跨度
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b))

    # 目标映射平面
    dst = np.array([
        [0, 0],
        [max_width - 1, 0],
        [max_width - 1, max_height - 1],
        [0, max_height - 1]
    ], dtype="float32")

    # 计算单应性矩阵并执行变换
    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img, matrix, (max_width, max_height))
    return warped
```

---

### 2.3 模块三：车牌 OCR 字符识别 (Plate OCR)

- **核心目的**: 对校正后的车牌图进行高精度字符识别与置信度打分。
- **技术选型**: PaddleOCR/PaddleX 的 PP-OCRv6 中文检测与识别模型。PaddleOCR 3.x 关闭文档方向分类、文档矫正和文本行方向分类，仅保留车牌所需的文本检测与识别；PaddleOCR 2.x 通过 `use_angle_cls=True` 兼容。
- **标准实现范式**:
```python
from threading import Lock
from paddleocr import PaddleOCR

class PlateOCR:
    def __init__(self):
        try:
            self.ocr = PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )
        except TypeError:
            self.ocr = PaddleOCR(use_angle_cls=True, lang="ch", enable_mkldnn=False)
        self.lock = Lock()

    def read_plate(self, plate_img):
        with self.lock:
            result = self.ocr.ocr(plate_img)
        if not result:
            return None, 0.0
        data = result[0]
        if isinstance(data, dict) and "rec_texts" in data:
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
        elif isinstance(data, list):
            texts = [box[1][0] for box in data]
            scores = [box[1][1] for box in data]
        else:
            return None, 0.0
        return "".join(texts), sum(scores) / len(scores) if scores else 0.0
```

项目实际实现位于 `core/ocr.py`。PaddleOCR predictor 不是线程安全对象，共享实例必须通过锁串行进入推理内核；模型缓存固定在项目 `data/paddlex_cache`，也可通过环境变量指定离线检测和识别模型目录。

---

### 2.4 模块四：系统集成与标准接口契约 (PlateRecognitionSystem)

- **核心目的**: 对外屏蔽底层检测、几何变换与 OCR 细节，提供统一的 `recognize(img)` 接口。
- **输入**: BGR 格式的 `np.ndarray` 图像帧。
- **输出**: `List[PlateInfo]` 结构化列表。
- **运行时数据契约**: `recognize()` 返回的字典保留 `roi: np.ndarray`，用于当前进程内的图像处理。写入 API、Kafka、JSON 或数据库前必须移除 `roi`，只序列化下列业务字段。
```json
[
  {
    "text": "京A8F2K6",
    "conf": 0.967,
    "det_conf": 0.931,
    "box": [120, 340, 260, 385],
    "is_valid": true,
    "plate_type": "普通车牌"
  }
]
```

- **标准调用方式**:
```python
from core.pipeline import PlateRecognition

pipeline = PlateRecognition(model_path="models/exp-7.pt", conf_thresh=0.5)
for result in pipeline.recognize(frame, apply_warp=True):
    if not result["is_valid"]:
        continue
    event_payload = {
        key: result[key]
        for key in ("text", "conf", "det_conf", "box", "is_valid", "plate_type")
    }
```

---

## 3. 当前状态与后续任务 (Status & Action Items)

`core/transform.py`、`core/pipeline.py` 和 `main.py` 的统一流水线集成已经完成。当前 `main.py` 在主线程执行 YOLO 检测，并通过有界线程池调用共享 PaddleOCR 实例完成车牌识别，随后将有效结果交给违章判定和 Kafka 事件链。

后续工作集中在可验证的部署与接口收口：

1. 为 `PlateRecognition` 增加明确的可序列化结果类型或转换函数，避免 `roi: np.ndarray` 进入 API、Kafka 或 JSON。
2. 使用固定真实交通验证集持续检查车牌召回率、整牌准确率、非法格式误判率和透视校正收益。
3. 按 [`docs/deployment/edge_inference.md`](../deployment/edge_inference.md) 完成 ONNX、FP16、INT8、TensorRT 和目标设备的分层验收，不把导出入口当作实机部署结果。
4. 目标硬件、运行时版本和摄像头并发规模明确后，再制定完整 LPR 流水线的 p95 延迟、FPS、内存、温度和功耗门槛。
