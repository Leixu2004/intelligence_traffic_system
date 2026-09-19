import time

class LineCrossingChecker:
    """压线违章检测器"""
    def __init__(self, line_y):
        self.line_y = line_y
        
    def check(self, bbox, vehicle_id=None):
        x1, y1, x2, y2 = bbox
        if self.line_y < y2 < self.line_y + 120:
            return True
        return False

class IllegalParkingChecker:
    """
    违停检测器 (遵循 LPR-VIOLATION-SPEC-v1.0)
    利用 YOLO 的 vehicle_id 进行进入时间追踪，有效过滤去重并大幅节省 OCR 算力
    """
    def __init__(self, zone_bbox, threshold_seconds):
        self.zx1, self.zy1, self.zx2, self.zy2 = zone_bbox
        self.threshold = threshold_seconds
        
        # 记录车辆进入禁停区的时间: {vehicle_id: entry_timestamp}
        self.vehicle_entry_times = {}
        
    def _is_in_zone(self, bbox):
        """判定车辆中心点是否在禁停区内"""
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        return (self.zx1 <= cx <= self.zx2) and (self.zy1 <= cy <= self.zy2)
        
    def check(self, bbox, vehicle_id):
        """
        每帧调用一次
        返回: (is_violation_triggered: bool, current_parking_time: float)
        """
        if vehicle_id == -1 or vehicle_id is None:
            return False, 0.0
            
        in_zone = self._is_in_zone(bbox)
        
        if in_zone:
            # 车辆在禁停区内
            if vehicle_id not in self.vehicle_entry_times:
                self.vehicle_entry_times[vehicle_id] = time.time()
                return False, 0.0
                
            parked_duration = time.time() - self.vehicle_entry_times[vehicle_id]
            if parked_duration >= self.threshold:
                return True, parked_duration
            else:
                return False, parked_duration
        else:
            # 车辆离开禁停区，清除其追踪倒计时
            if vehicle_id in self.vehicle_entry_times:
                del self.vehicle_entry_times[vehicle_id]
            return False, 0.0
