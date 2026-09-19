import json
import time
import random
from datetime import datetime
from kafka import KafkaProducer

# 这模拟的是你安装在路口的 Jetson 边缘计算盒子
EDGE_BOX_CONFIG = {
    "checkpoint_id": "CP-NORTH-01",
    "gps_lng": 116.4074,
    "gps_lat": 39.9042
}

def simulate_edge_traffic():
    print(f"📡 启动边缘端流量模拟器... [卡口: {EDGE_BOX_CONFIG['checkpoint_id']}]")
    producer = KafkaProducer(
        bootstrap_servers=['localhost:9092'],
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    vehicle_counter = 1000
    try:
        while True:
            # 1. 模拟雷达测速 (高斯分布，平均50，标准差10)
            speed = round(random.gauss(50, 10), 1)
            
            # 2. 模拟 OCR 识别出的车牌
            plate = f"VEH-{vehicle_counter}"
            vehicle_counter += 1
            
            # 3. 打包数据发送给 Kafka
            record = {
                "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "vehicle_id": plate,
                "checkpoint_id": EDGE_BOX_CONFIG["checkpoint_id"],
                "gps_lng": EDGE_BOX_CONFIG["gps_lng"],
                "gps_lat": EDGE_BOX_CONFIG["gps_lat"],
                "speed_kmh": speed
            }
            
            producer.send('traffic_stream', record)
            print(f"[YOLO 抓拍] -> {record}")
            
            # 模拟现实中车流的间隔 (0.2 ~ 1 秒过一辆车)
            time.sleep(random.uniform(0.2, 1.0))
            
    except KeyboardInterrupt:
        print("停止模拟。")
        producer.close()

if __name__ == "__main__":
    simulate_edge_traffic()
