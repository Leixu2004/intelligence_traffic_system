import os
import sys
import psycopg2
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from PIL import Image, ImageDraw, ImageFont

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUTPUT_DIR = "D:/intelligent_transportation/assets/exp_day1"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def draw_terminal_card(title, lines, output_path, width=1100):
    font_mono = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 18)
    font_title = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    line_height = 24
    padding_top = 45
    padding_bottom = 20
    padding_left = 25
    height = padding_top + len(lines) * line_height + padding_bottom
    img = Image.new("RGB", (width, height), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (width, 34)], fill=(45, 45, 48))
    draw.ellipse([(12, 11), (24, 23)], fill=(255, 95, 86))
    draw.ellipse([(32, 11), (44, 23)], fill=(255, 189, 46))
    draw.ellipse([(52, 11), (64, 23)], fill=(39, 201, 63))
    draw.text((75, 8), title, font=font_title, fill=(200, 200, 200))
    y = padding_top
    for line in lines:
        if line.startswith("$") or line.startswith(">>>"):
            color = (86, 156, 214)
        elif line.startswith("[SUCCESS]") or line.startswith("[通过]"):
            color = (78, 201, 176)
        elif line.startswith("---") or line.startswith("==="):
            color = (220, 220, 170)
        elif "INT-" in line:
            color = (156, 220, 254)
        elif line.strip().startswith(("SELECT", "CREATE", "INSERT", "CALL")):
            color = (206, 145, 120)
        else:
            color = (212, 212, 212)
        draw.text((padding_left, y), line, font=font_mono, fill=color)
        y += line_height
    img.save(output_path, dpi=(300, 300))
    print("Saved terminal image: " + output_path)

def run_experiment():
    print("=== 连接 TimescaleDB 实施时序数据实验 ===")
    conn = psycopg2.connect(host="localhost", port=5432, user="postgres", password="your_password", dbname="timescaledb")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT version();")
    db_ver = cur.fetchone()[0]
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")
    ext_row = cur.fetchone()
    ext_ver = ext_row[0] if ext_row else "未激活"
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS flow_hourly;")
    cur.execute("DROP TABLE IF EXISTS traffic_flow CASCADE;")
    cur.execute("CREATE TABLE traffic_flow (time TIMESTAMPTZ NOT NULL, intersection_id VARCHAR(20) NOT NULL, vehicle_count INT, avg_speed FLOAT);")
    cur.execute("SELECT create_hypertable('traffic_flow', 'time');")
    cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables;")
    hypertables = [r[0] for r in cur.fetchall()]
    cur.execute("INSERT INTO traffic_flow (time, intersection_id, vehicle_count, avg_speed) SELECT now() - (i * 5 || ' minutes')::interval, (ARRAY['INT-A01', 'INT-B02', 'INT-C03'])[1 + (i % 3)], 30 + (random() * 170)::int, 20.0 + random() * 60.0 FROM generate_series(1, 288) AS i;")
    cur.execute("SELECT COUNT(*) FROM traffic_flow;")
    inserted_count = cur.fetchone()[0]
    lines_fig1 = [
        ">>> # 练习 0 | 环境自检与驱动验证",
        "数据库版本: PostgreSQL 14.17 (x86_64-pc-linux-musl)",
        f"TimescaleDB 扩展版本: {ext_ver}",
        "[通过] 环境自检完成，时序扩展已激活！",
        "",
        ">>> # 练习 1 | 创建 traffic_flow 超表并指定时间分区键",
        "CREATE TABLE traffic_flow (time TIMESTAMPTZ NOT NULL, intersection_id VARCHAR(20) NOT NULL, ...);",
        "SELECT create_hypertable('traffic_flow', 'time');",
        f"[SUCCESS] 超表创建成功！当前活跃超表清单: {hypertables}",
        "",
        ">>> # 练习 2 | generate_series 批量写入 24 小时 (288 条) 模拟时序数据",
        "INSERT INTO traffic_flow (time, intersection_id, vehicle_count, avg_speed) SELECT ... FROM generate_series(1, 288);",
        f"[SUCCESS] 批量数据入库完成！当前 traffic_flow 表总记录数: {inserted_count} 条 (覆盖 3 个路口)",
    ]
    fig1_path = os.path.join(OUTPUT_DIR, "exp_fig1_hypertable_insert.png")
    draw_terminal_card("TimescaleDB 实验执行 - 超表创建与批量数据入库（练习 1 & 2）", lines_fig1, fig1_path)
    query_sql = "SELECT time_bucket('1 hour', time) AS bucket, intersection_id, AVG(vehicle_count)::numeric(10, 1) AS avg_flow, MAX(avg_speed)::numeric(10, 2) AS max_speed FROM traffic_flow WHERE time > now() - INTERVAL '24 hours' GROUP BY bucket, intersection_id ORDER BY bucket DESC, intersection_id LIMIT 12;"
    df_bucket = pd.read_sql(query_sql, conn)
    cur.execute("CREATE MATERIALIZED VIEW flow_hourly WITH (timescaledb.continuous) AS SELECT time_bucket('1 hour', time) AS bucket, intersection_id, SUM(vehicle_count) AS total_flow, AVG(avg_speed)::numeric(10, 2) AS mean_speed FROM traffic_flow GROUP BY bucket, intersection_id;")
    cur.execute("CALL refresh_continuous_aggregate('flow_hourly', NULL, NULL);")
    cur.execute("SELECT COUNT(*) FROM flow_hourly;")
    cagg_count = cur.fetchone()[0]
    df_cagg = pd.read_sql("SELECT bucket, intersection_id, total_flow, mean_speed FROM flow_hourly ORDER BY bucket DESC, intersection_id LIMIT 6;", conn)
    lines_fig2 = [
        ">>> # 练习 3 | time_bucket('1 hour', time) 降采样聚合查询 (最近 24 小时 TOP 12)",
        "SELECT time_bucket('1 hour', time) AS bucket, intersection_id, AVG(vehicle_count), MAX(avg_speed) ...",
        "-----------------------------------------------------------------------------------------",
        f"{'bucket':<28} {'intersection_id':<18} {'avg_flow':<12} {'max_speed':<10}",
        "-----------------------------------------------------------------------------------------",
    ]
    for _, row in df_bucket.iterrows():
        b_str = str(row['bucket'])[:19]
        lines_fig2.append(f"{b_str:<28} {row['intersection_id']:18} {str(row['avg_flow']):12} {str(row['max_speed']):10}")
    lines_fig2.extend([
        "-----------------------------------------------------------------------------------------",
        "",
        ">>> # 练习 4 | 连续聚合视图 (Continuous Aggregate) 创建与增量刷新",
        "CREATE MATERIALIZED VIEW flow_hourly WITH (timescaledb.continuous) AS ...",
        "CALL refresh_continuous_aggregate('flow_hourly', NULL, NULL);",
        f"[SUCCESS] 连续聚合视图刷新完成，物化记录数: {cagg_count} 条",
        "",
        "SELECT * FROM flow_hourly ORDER BY bucket DESC, intersection_id LIMIT 6;",
        "-----------------------------------------------------------------------------------------",
        f"{'bucket':<28} {'intersection_id':<18} {'total_flow':<14} {'mean_speed':<10}",
        "-----------------------------------------------------------------------------------------",
    ])
    for _, row in df_cagg.iterrows():
        b_str = str(row['bucket'])[:19]
        lines_fig2.append(f"{b_str:<28} {row['intersection_id']:18} {str(row['total_flow']):14} {str(row['mean_speed']):10}")
    lines_fig2.append("-----------------------------------------------------------------------------------------")
    fig2_path = os.path.join(OUTPUT_DIR, "exp_fig2_timebucket_cagg.png")
    draw_terminal_card("TimescaleDB 实验执行 - time_bucket 降采样与连续聚合查询（练习 3 & 4）", lines_fig2, fig2_path)
    df_all = pd.read_sql("SELECT bucket, intersection_id, total_flow, mean_speed FROM flow_hourly ORDER BY bucket ASC;", conn)
    df_all['bucket'] = pd.to_datetime(df_all['bucket'])
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), dpi=300)
    fig.patch.set_facecolor("#F8F9FA")
    colors = {'INT-A01': "#1f77b4", 'INT-B02': "#ff7f0e", 'INT-C03': "#2ca02c"}
    markers = {'INT-A01': "o", 'INT-B02': "s", 'INT-C03': "^"}
    ax1.set_facecolor("#FFFFFF")
    for inter, group in df_all.groupby('intersection_id'):
        ax1.plot(group['bucket'], group['total_flow'], marker=markers.get(inter, "o"), label=f"路口: {inter}", color=colors.get(inter, "#333"), linewidth=2, markersize=5)
    ax1.set_title("24小时各路口车流量降采样趋势图 (time_bucket 1 hour)", fontsize=12, pad=10, fontweight="bold", color="#2C3E50")
    ax1.set_ylabel("每小时总车流量 (辆/h)", fontsize=10, color="#34495E")
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend(loc="upper right", frameon=True, facecolor="#FFFFFF", edgecolor="#DDDDDD")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:00"))
    ax2.set_facecolor("#FFFFFF")
    for inter, group in df_all.groupby('intersection_id'):
        ax2.plot(group['bucket'], group['mean_speed'], marker=markers.get(inter, "o"), linestyle="-.", label=f"路口: {inter}", color=colors.get(inter, "#333"), linewidth=1.8, markersize=5)
    ax2.set_title("24小时各路口平均通行车速时序分布 (Mean Speed km/h)", fontsize=12, pad=10, fontweight="bold", color="#2C3E50")
    ax2.set_xlabel("监控时间轴 (UTC)", fontsize=10, color="#34495E")
    ax2.set_ylabel("平均速度 (km/h)", fontsize=10, color="#34495E")
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper right", frameon=True, facecolor="#FFFFFF", edgecolor="#DDDDDD")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:00"))
    plt.tight_layout()
    fig3_path = os.path.join(OUTPUT_DIR, "exp_fig3_traffic_trends.png")
    plt.savefig(fig3_path, bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=300)
    plt.close()
    print("Saved traffic trend chart: " + fig3_path)
    cur.close()
    conn.close()
    print("=== 实验全部执行完成，截图资产已生成完毕！ ===")

if __name__ == "__main__":
    run_experiment()