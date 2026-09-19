import os
import duckdb
import pandas as pd

# 设置控制台输出的编码为 UTF-8
import sys
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

# 确保 data 目录存在
os.makedirs('D:/intelligent_transportation/intel-transportation/data', exist_ok=True)

csv_path = 'D:/intelligent_transportation/vehicle_records.csv'
parquet_path = 'D:/intelligent_transportation/intel-transportation/data/traffic_archive.parquet'

print("="*60)
print(" 智慧交通 - 边缘端 DuckDB 数据分析与归档任务")
print("="*60)

# 纯内存执行 DuckDB，极大提升单机执行速度
con = duckdb.connect(':memory:')

# -------------------------------------------------------------
# 改进点 1：本地“超速违章车辆追踪”分析
# -------------------------------------------------------------
print("\n[模块 1]: 今日超速违章车辆报表 (限速 > 50km/h)")
query_violation = f"""
    SELECT 
        vehicle_id AS "车牌号",
        MIN(time) AS "首次出现时间", 
        MAX(time) AS "最后出现时间",
        COUNT(*) AS "抓拍次数",
        MAX(speed_kmh) AS "最高时速(km/h)"
    FROM read_csv_auto('{csv_path}')
    WHERE speed_kmh > 50
    GROUP BY vehicle_id
    ORDER BY MAX(speed_kmh) DESC
"""
df_violation = con.execute(query_violation).df()
print("-" * 50)
print(df_violation.to_string(index=False))
print("-" * 50)

# -------------------------------------------------------------
# 改进点 2：将历史原始记录导出为 Parquet 进行冷数据归档
# -------------------------------------------------------------
print(f"\n[模块 2]: 离线数据归档为 Parquet 格式")
con.execute(f"COPY (SELECT * FROM read_csv_auto('{csv_path}')) TO '{parquet_path}' (FORMAT PARQUET)")
file_size = os.path.getsize(parquet_path)
print(f"[*] 归档成功！")
print(f"[*] Parquet 存档路径: {parquet_path}")
print(f"[*] Parquet 文件大小: {file_size} 字节 (体积小、查询快)")

# -------------------------------------------------------------
# 改进点 3：利用 Pandas 输出智能分析报告
# -------------------------------------------------------------
print("\n[模块 3]: 交通流量车速基础数据统计特征 (Pandas .describe)")
query_all = f"SELECT speed_kmh AS Speed FROM read_csv_auto('{csv_path}')"
df_all = con.execute(query_all).df()
print("-" * 50)
print(df_all.describe().round(2))
print("-" * 50)

print("\n[*] 分析脚本执行完毕。")
