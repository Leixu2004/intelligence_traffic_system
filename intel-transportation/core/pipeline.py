import cv2
import numpy as np
from typing import List, Dict, Any, Optional

import config
from core.detector import VehicleDetector
from core.ocr import PlateOCR
from core.validator import PlateValidator
from core.transform import auto_rectify_plate

class PlateRecognition:
    """
    车牌识别全流水线系统 (LPR Pipeline) - 解耦版
    """
    def __init__(
        self,
        model_path: Optional[str] = None,
        conf_thresh: float = 0.5,
        detector=None,
        ocr=None,
        validator=None,
    ):
        self.model_path = model_path if model_path else config.YOLO_PLATE_MODEL_PATH
        self.conf_thresh = conf_thresh
        
        # YOLO 留在主线程调用，避免 GPU 竞争
        self.detector = detector or VehicleDetector(self.model_path)
        # OCR 允许在子线程调用
        self.ocr = ocr or PlateOCR()
        self.validator = validator or PlateValidator()

    def recognize(self, img: np.ndarray, apply_warp: bool = True) -> List[Dict[str, Any]]:
        """执行检测、校正、OCR 与校验，返回稳定的结构化结果列表。"""
        if img is None or not isinstance(img, np.ndarray) or img.size == 0:
            return []
        return [
            self.recognize_plate(plate_info, apply_warp=apply_warp)
            for plate_info in self.detect_plate_rois(img)
        ]

    def detect_plate_rois(self, img: np.ndarray) -> List[Dict[str, Any]]:
        """
        Step 1: (主线程执行) 仅负责检测车牌位置，返回坐标和置信度
        """
        results = self.detector.detect(img, conf=self.conf_thresh)
        plates_info = []
        if not results or results[0].boxes is None:
            return plates_info
            
        for box in results[0].boxes:
            box_conf = float(box.conf[0].item())
            if box_conf < self.conf_thresh:
                continue
                
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            
            # 边界外扩保护 (Padding) 预留一点空间，供后续找角点
            pad = 8
            h, w = img.shape[:2]
            crop_y1 = max(0, y1 - pad)
            crop_y2 = min(h, y2 + pad)
            crop_x1 = max(0, x1 - pad)
            crop_x2 = min(w, x2 + pad)
            
            roi = img[crop_y1:crop_y2, crop_x1:crop_x2]
            if roi.shape[0] > 0 and roi.shape[1] > 0:
                plates_info.append({
                    "box": [x1, y1, x2, y2],  # 原始车牌相对于输入 img 的坐标
                    "det_conf": round(box_conf, 3),
                    "roi": roi
                })
        return plates_info

    def recognize_plate(self, plate_info: Dict[str, Any], apply_warp: bool = True) -> Dict[str, Any]:
        """
        Step 2: (子线程执行) 执行透视校正与 OCR 识别
        """
        roi = plate_info["roi"]
        
        # 1. 前置 Padding (非常重要)：为紧贴字符的车牌补全物理边框，保证 Canny 能找到外围矩形
        if apply_warp:
            roi_padded = cv2.copyMakeBorder(roi, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=[0, 0, 0])
            processed = auto_rectify_plate(roi_padded)
            # 校正完后，中心裁剪掉我们人为加的 padding 区域 (大致减去边缘)
            ph, pw = processed.shape[:2]
            if ph > 20 and pw > 20:
                processed = processed[10:ph-10, 10:pw-10]
        else:
            processed = roi.copy()
            
        # 2. 图像增强
        processed = cv2.copyMakeBorder(processed, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[128, 128, 128])
        processed = cv2.resize(processed, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        
        gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        gray = cv2.filter2D(gray, -1, kernel)
        enhanced_roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        
        # 3. OCR 识别
        text, ocr_conf = self.ocr.read_plate(enhanced_roi)
        
        clean_text = ""
        is_valid = False
        if text:
            import re
            clean_text = text.upper()
            clean_text = re.sub(r'[^京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领A-Z0-9]', '', clean_text)
            is_valid = self.validator.is_valid(clean_text)
            
        result = dict(plate_info)
        result["text"] = clean_text
        result["conf"] = round(float(ocr_conf), 3) if text else 0.0
        result["is_valid"] = is_valid
        result["plate_type"] = "新能源车牌" if len(clean_text) == 8 else "普通车牌"
        return result
