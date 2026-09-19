import json
from pathlib import Path
from threading import Lock

from kafka import KafkaProducer

class KafkaClient:
    def __init__(self, broker_url, offline_path=None):
        print(f"正在连接 Kafka: {broker_url}")
        self.offline_path = Path(offline_path) if offline_path else None
        self._offline_lock = Lock()
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=[broker_url],
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                acks=1,
                retries=3,
                request_timeout_ms=5000,
                max_block_ms=3000,
            )
            self.connected = True
            print("Kafka 连接成功！")
        except Exception as e:
            print(f"Kafka 连接失败，请检查 Docker 是否启动: {e}")
            self.connected = False

    def _write_offline(self, topic, data):
        if self.offline_path is None:
            return
        self.offline_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"topic": topic, "payload": data}
        with self._offline_lock, self.offline_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def send(self, topic, data):
        """异步发送事件；Kafka 不可用时写入 JSONL，避免识别结果静默丢失。"""
        if not self.connected:
            self._write_offline(topic, data)
            print(f"[离线模式] 事件已写入 {self.offline_path}: {topic}")
            return False
        try:
            def on_error(exc):
                self._write_offline(topic, data)
                print(f"Kafka 异步发送失败，事件已转存离线文件 [{topic}]: {exc}")

            self.producer.send(topic, data).add_errback(on_error)
            return True
        except Exception as exc:
            self._write_offline(topic, data)
            print(f"Kafka 发送失败，事件已转存离线文件: {exc}")
            return False

    def send_traffic(self, topic, data):
        return self.send(topic, data)

    def send_plate(self, topic, data):
        """发送与违章语义解耦的常规车牌识别事件。"""
        return self.send(topic, data)

    def send_violation(self, topic, data):
        """将违章数据发送到 Kafka 队列"""
        return self.send(topic, data)

    def close(self):
        if self.connected:
            self.producer.flush(timeout=5)
            self.producer.close(timeout=5)
