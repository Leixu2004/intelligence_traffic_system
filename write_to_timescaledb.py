import psycopg2
from psycopg2 import pool
import time
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# PPT 第12页要求：必须使用连接池
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 10,
        host="localhost",
        dbname="timescaledb",
        user="postgres",
        password="your_password",
        port=5432
    )
    if db_pool:
        logging.info("TimescaleDB 连接池创建成功！")
except Exception as e:
    logging.error(f"连接池创建失败: {e}")
    db_pool = None

def insert_records(records, retries=3, delay=2):
    '''
    PPT 第12页要求：批量写入 + 包含错误重试机制
    records: list of tuples (time, camera_id, plate, vehicle_type, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2)
    '''
    if not db_pool:
        logging.error("无可用连接池，无法插入")
        return False

    insert_query = '''
        INSERT INTO traffic_data 
        (time, camera_id, plate, vehicle_type, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    '''
    
    for attempt in range(retries):
        conn = None
        try:
            conn = db_pool.getconn()
            cur = conn.cursor()
            # 批量写入核心：executemany
            cur.executemany(insert_query, records)
            conn.commit()
            cur.close()
            logging.info(f"[批量入库] 成功插入 {len(records)} 条交通检测记录")
            return True
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            logging.warning(f"[重试机制] 插入失败，正在进行第 {attempt+1}/{retries} 次重试... 错误: {e}")
            time.sleep(delay)
        finally:
            if conn:
                db_pool.putconn(conn)
                
    logging.error("批量插入失败：达到最大重试次数")
    return False

if __name__ == "__main__":
    # 模拟一条产线产生了一批 AI 推理数据，批量打入数据库
    test_records = [
        (datetime.utcnow(), 'CAM-01', '粤B12345', 'car', 0.95, 100, 200, 300, 400),
        (datetime.utcnow(), 'CAM-02', '粤B67890', 'truck', 0.88, 150, 250, 350, 450),
        (datetime.utcnow(), 'CAM-01', '粤A00001', 'bus', 0.99, 120, 210, 320, 420)
    ]
    logging.info("测试：执行批量入库...")
    insert_records(test_records)
