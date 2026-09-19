import os
from pathlib import Path
from threading import Lock


# 将 PaddleX/PaddleOCR 缓存固定在项目内，避免服务账号读取个人目录时出现权限错误。
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "paddlex_cache"
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(DEFAULT_CACHE_DIR))

from paddleocr import PaddleOCR

class PlateOCR:
    def __init__(self):
        """初始化 PaddleOCR 模块"""
        print("正在加载 PaddleOCR 模型...")
        model_options = {}
        for environment_name, option_name in (
            ("PADDLE_TEXT_DETECTION_MODEL_DIR", "text_detection_model_dir"),
            ("PADDLE_TEXT_RECOGNITION_MODEL_DIR", "text_recognition_model_dir"),
        ):
            configured = os.getenv(environment_name, "").strip()
            if not configured:
                continue
            model_dir = Path(configured).expanduser().resolve()
            if not (model_dir / "inference.yml").is_file():
                raise FileNotFoundError(f"{environment_name} 缺少 inference.yml: {model_dir}")
            model_options[option_name] = str(model_dir)
        try:
            try:
                # PaddleOCR 3.x 仅启用车牌所需的文本检测和识别，关闭文档级预处理模型。
                self.ocr_model = PaddleOCR(
                    lang="ch",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=False,
                    **model_options,
                )
            except TypeError:
                # PaddleOCR 2.x 参数兼容分支。
                self.ocr_model = PaddleOCR(use_angle_cls=True, lang="ch", enable_mkldnn=False)
        except Exception as exc:
            raise RuntimeError(
                "PaddleOCR 模型加载失败；请检查项目 data/paddlex_cache，或配置 "
                "PADDLE_TEXT_DETECTION_MODEL_DIR 与 PADDLE_TEXT_RECOGNITION_MODEL_DIR"
            ) from exc
        self._inference_lock = Lock()
        
    def read_plate(self, plate_image):
        """
        接收裁剪下来的车牌图片，返回识别到的文本
        """
        # PaddleOCR predictor 不是线程安全对象；线程池共享实例时串行进入推理内核。
        with self._inference_lock:
            result = self.ocr_model.ocr(plate_image)
        # 兼容新老版本 PaddleOCR 的解析方式
        if result and len(result) > 0:
            res_data = result[0]
            # 新版 Paddlex 返回的往往是 dict
            if isinstance(res_data, dict) and 'rec_texts' in res_data:
                texts = res_data.get('rec_texts', [])
                scores = res_data.get('rec_scores', [])
                if texts and len(texts) > 0:
                    text = "".join(texts)
                    confidence = sum(scores) / len(scores) if scores else 0.0
                    return text, confidence
            # 老版本 PaddleOCR 返回的是 list of lists (每个 list 是一个检测框)
            elif isinstance(res_data, list) and len(res_data) > 0:
                try:
                    # 车牌可能会被错误地识别为多个独立的文字框，我们需要把它们拼接起来
                    text = "".join([box[1][0] for box in res_data])
                    confidence = sum([box[1][1] for box in res_data]) / len(res_data)
                    return text, confidence
                except Exception:
                    pass
        return None, 0.0
