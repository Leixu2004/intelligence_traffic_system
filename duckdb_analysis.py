import psycopg2
import pandas as pd
import duckdb
import warnings
warnings.filterwarnings('ignore')

print("="*50)
print(" 智慧交通 - DuckDB 离线分析与报表导出")
print("="*50)

# 1. 模拟从数据库/边缘端获取最新数据为 detections.csv
try:
    conn = psycopg2.connect("host=localhost port=5432 user=postgres password=your_password dbname=timescaledb")
    df_raw = pd.read_sql("SELECT * FROM traffic_data", conn)
    df_raw.to_csv("detections.csv", index=False)
    print(f"[*] 成功从 TimescaleDB 提取 {len(df_raw)} 条记录到本地 detections.csv")
except Exception as e:
    print(f"[!] 数据库连接失败: {e}")
    exit(1)

# 2. Pandas 加载与 DuckDB 查询 (严格按照 PPT 第 7 页规范)
df = pd.read_csv("detections.csv")
print("[*] 正在启动 DuckDB 内存引擎进行高速 OLAP 分析...")

result = duckdb.sql("""
  SELECT camera_id,
         vehicle_type,
         COUNT(*) AS cnt,
         ROUND(AVG(confidence)::numeric, 4) AS avg_conf
  FROM df
  GROUP BY camera_id, vehicle_type
  ORDER BY cnt DESC
""").df()

print("\n--- DuckDB 统计分析结果 ---")
print(result.to_string(index=False))
print("-" * 30)

# 3. 结果导出 (PPT 第 12 页规范：导出 CSV / Excel)
result.to_csv("traffic_report.csv", index=False, encoding="utf-8-sig")
result.to_excel("traffic_report.xlsx", index=False)
print("\n[*] 成功导出报表至: traffic_report.csv")
print("[*] 成功导出报表至: traffic_report.xlsx")
print("="*50)
