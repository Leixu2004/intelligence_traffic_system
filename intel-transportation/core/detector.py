from ultralytics import YOLO

class VehicleDetector:
    def __init__(self, model_path):
        """初始化 YOLO 车辆与车牌检测模型"""
        print(f"正在加载 YOLO 模型: {model_path}")
        self.model = YOLO(model_path)
        
    def detect(self, frame, conf=None):
        """
        输入一帧图像，返回检测结果。
        此处将 verbose=False 关掉以免控制台刷屏。
        增加设备自适应以提升推理速度。
        """
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        options = {"verbose": False, "device": device}
        if conf is not None:
            options["conf"] = conf
        results = self.model.predict(frame, **options)
        return results

    def track(self, frame):
        """
        使用 YOLO 内置的追踪器进行对象追踪 (ByteTrack/BoT-SORT)
        返回带 ID 的追踪结果。
        """
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        results = self.model.track(frame, persist=True, verbose=False, device=device)
        return results
