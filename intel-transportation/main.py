from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import os
from threading import Lock
import time

import cv2

import config
from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, utc_now_iso
from core.detector import VehicleDetector
from core.pipeline import PlateRecognition
from core.violation import IllegalParkingChecker, LineCrossingChecker
from db.kafka_client import KafkaClient


class TrafficSystem:
    def __init__(self, recognition_mode: str | None = None):
        print("智慧交通系统初始化")
        self.recognition_mode = config.normalize_recognition_mode(
            recognition_mode or config.RECOGNITION_MODE
        )
        self.vehicle_detector = VehicleDetector(config.YOLO_VEHICLE_MODEL_PATH)
        self.plate_pipeline = PlateRecognition(config.YOLO_PLATE_MODEL_PATH)
        self.line_checker = LineCrossingChecker(config.LINE_Y_COORDINATE)
        self.parking_checker = IllegalParkingChecker(config.NO_PARKING_ZONE, config.PARKING_THRESHOLD)

        self.captured_plates: set[str] = set()
        self.captured_vehicle_ids: set[int] = set()
        self.published_vehicle_ids: set[int] = set()
        self.ocr_inflight_vehicle_ids: set[int] = set()
        self.state_lock = Lock()
        self.kafka = KafkaClient(config.KAFKA_BROKER, config.KAFKA_OFFLINE_PATH)
        self.ocr_executor = ThreadPoolExecutor(max_workers=config.OCR_WORKERS, thread_name_prefix="plate-ocr")
        self.pending_ocr: set[Future] = set()

    @staticmethod
    def _box_confidence(box) -> float | None:
        try:
            return round(float(box.conf[0]), 4)
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _vehicle_type(self, cls_id: int) -> str:
        names = getattr(self.vehicle_detector.model, "names", {})
        if isinstance(names, dict):
            return str(names.get(cls_id, cls_id))
        if isinstance(names, list) and 0 <= cls_id < len(names):
            return str(names[cls_id])
        return str(cls_id)

    def _publish_traffic_observation(self, vehicle_id: int, bbox: list[int], vehicle_type: str, confidence):
        if vehicle_id == -1 or vehicle_id in self.published_vehicle_ids:
            return
        event = TrafficObservation(
            time=utc_now_iso(),
            vehicle_id=f"TRACK-{vehicle_id}",
            checkpoint_id=config.CHECKPOINT_ID,
            camera_id=config.CAMERA_ID,
            gps_lng=config.CHECKPOINT_GPS_LNG,
            gps_lat=config.CHECKPOINT_GPS_LAT,
            speed_kmh=None,
            vehicle_type=vehicle_type,
            confidence=confidence,
            bbox=bbox,
        )
        self.kafka.send_traffic(config.KAFKA_TOPIC_TRAFFIC, event.to_payload())
        self.published_vehicle_ids.add(vehicle_id)

    def _process_plate(
        self,
        plate_data,
        full_frame,
        vehicle_id,
        vehicle_type,
        violation_type,
        vehicle_bbox,
    ):
        result = self.plate_pipeline.recognize_plate(plate_data, apply_warp=True)
        plate_text = result["text"]
        if not result["is_valid"]:
            return
        with self.state_lock:
            if plate_text in self.captured_plates:
                return
            self.captured_plates.add(plate_text)
            if vehicle_id != -1:
                self.captured_vehicle_ids.add(vehicle_id)

        confidence = result["conf"]
        plate_event = PlateRecognitionEvent(
            time=utc_now_iso(),
            plate=plate_text,
            checkpoint_id=config.CHECKPOINT_ID,
            camera_id=config.CAMERA_ID,
            track_id=str(vehicle_id) if vehicle_id != -1 else None,
            vehicle_type=vehicle_type,
            ocr_confidence=confidence,
            detector_confidence=result.get("det_conf"),
            bbox=vehicle_bbox,
        )
        self.kafka.send_plate(config.KAFKA_TOPIC_PLATES, plate_event.to_payload())

        if violation_type is None:
            print(f"【常规识别成功】车牌 {plate_text} (置信度: {confidence:.2f})")
            return

        print(f"【{violation_type}抓拍成功】车牌 {plate_text} (置信度: {confidence:.2f})")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(config.VIOLATIONS_DIR, f"{violation_type}_{plate_text}_{timestamp}.jpg")
        if not cv2.imwrite(filepath, full_frame):
            raise OSError(f"违章证据保存失败: {filepath}")

        event = ViolationEvent(
            time=utc_now_iso(),
            plate=plate_text,
            violation_type=violation_type,
            checkpoint_id=config.CHECKPOINT_ID,
            camera_id=config.CAMERA_ID,
            image_path=filepath,
            track_id=str(vehicle_id) if vehicle_id != -1 else None,
            vehicle_type=vehicle_type,
            confidence=confidence,
            bbox=vehicle_bbox,
        )
        self.kafka.send_violation(config.KAFKA_TOPIC_VIOLATIONS, event.to_payload())

    def _submit_ocr(self, *args):
        vehicle_id = args[2]
        with self.state_lock:
            if vehicle_id != -1:
                if vehicle_id in self.captured_vehicle_ids or vehicle_id in self.ocr_inflight_vehicle_ids:
                    return
                self.ocr_inflight_vehicle_ids.add(vehicle_id)
            if len(self.pending_ocr) >= config.OCR_WORKERS * 2:
                if vehicle_id != -1:
                    self.ocr_inflight_vehicle_ids.discard(vehicle_id)
                return
        future = self.ocr_executor.submit(self._process_plate, *args)
        with self.state_lock:
            self.pending_ocr.add(future)

        def finished(completed: Future):
            with self.state_lock:
                self.pending_ocr.discard(completed)
                if vehicle_id != -1:
                    self.ocr_inflight_vehicle_ids.discard(vehicle_id)
            error = completed.exception()
            if error is not None:
                print(f"OCR 子任务失败: {error}")

        future.add_done_callback(finished)

    def run(self, video_path, show_window=True, max_frames=None):
        print(f"开始处理视频流: {video_path}")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频源: {video_path}")

        if show_window:
            cv2.namedWindow("Smart Traffic System", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("Smart Traffic System", 1280, 720)

        frame_count = 0
        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_count += 1
                if max_frames is not None and frame_count > max_frames:
                    break
                if frame_count % 2 != 0:
                    continue

                results = self.vehicle_detector.track(frame)
                cv2.line(
                    frame,
                    (0, config.LINE_Y_COORDINATE),
                    (frame.shape[1], config.LINE_Y_COORDINATE),
                    (0, 0, 255),
                    2,
                )
                zx1, zy1, zx2, zy2 = config.NO_PARKING_ZONE
                cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (0, 165, 255), 2)
                cv2.putText(
                    frame,
                    "NO PARKING ZONE",
                    (zx1, zy1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 165, 255),
                    2,
                )

                for detection_result in results:
                    boxes = detection_result.boxes
                    for box in boxes:
                        x1, y1, x2, y2 = [int(value) for value in box.xyxy[0]]
                        cls_id = int(box.cls[0])
                        if cls_id not in config.VEHICLE_CLASSES:
                            continue

                        vehicle_id = int(box.id[0]) if box.id is not None else -1
                        bbox = [x1, y1, x2, y2]
                        vehicle_type = self._vehicle_type(cls_id)
                        self._publish_traffic_observation(
                            vehicle_id, bbox, vehicle_type, self._box_confidence(box)
                        )
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)

                        if vehicle_id != -1 and vehicle_id in self.captured_vehicle_ids:
                            cv2.putText(
                                frame,
                                f"ID:{vehicle_id} (Captured)",
                                (x1, max(y1 - 10, 0)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (0, 255, 0),
                                2,
                            )
                            continue

                        is_line_crossing = self.line_checker.check(bbox, vehicle_id)
                        is_parking_violation, park_duration = self.parking_checker.check(bbox, vehicle_id)
                        label = f"ID:{vehicle_id}"
                        color = (255, 0, 0)
                        if park_duration > 0 and not is_parking_violation:
                            label = f"ID:{vehicle_id} Parked: {park_duration:.1f}s"
                            color = (0, 165, 255)
                        cv2.putText(
                            frame,
                            label,
                            (x1, max(y1 - 10, 0)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            color,
                            2,
                        )

                        has_violation = is_line_crossing or is_parking_violation
                        if not config.should_recognize_vehicle(
                            has_violation, self.recognition_mode
                        ):
                            continue
                        violation_type = None
                        if has_violation:
                            violation_type = "违章停车" if is_parking_violation else "压线违章"
                            cv2.putText(
                                frame,
                                f"{violation_type}!",
                                (x1, y1 + 20),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.8,
                                (0, 0, 255),
                                2,
                            )
                        car_img = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
                        if car_img.size == 0:
                            continue
                        plates = self.plate_pipeline.detect_plate_rois(car_img)
                        if plates:
                            plate_info = max(plates, key=lambda item: item["det_conf"])
                            self._submit_ocr(
                                plate_info,
                                frame.copy(),
                                vehicle_id,
                                vehicle_type,
                                violation_type,
                                bbox,
                            )

                if show_window:
                    cv2.imshow("Smart Traffic System", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        finally:
            cap.release()
            if show_window:
                cv2.destroyAllWindows()
            self.ocr_executor.shutdown(wait=True)
            self.kafka.close()
        print("====== 视频处理结束 ======")


def _parse_args():
    parser = argparse.ArgumentParser(description="智慧交通车辆、车牌与违章识别流水线")
    parser.add_argument("video", nargs="?", default=config.VIDEO_SOURCE, help="视频文件、摄像头编号或流地址")
    parser.add_argument("--headless", action="store_true", help="不创建 OpenCV 窗口，适合服务器运行")
    parser.add_argument("--max-frames", type=int, help="最多读取的帧数，用于烟雾测试")
    parser.add_argument(
        "--recognition-mode",
        choices=("all", "violation_only"),
        default=config.RECOGNITION_MODE,
        help="all 识别全部车辆，violation_only 仅识别违章车辆",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    TrafficSystem(args.recognition_mode).run(
        args.video,
        show_window=not args.headless,
        max_frames=args.max_frames,
    )
