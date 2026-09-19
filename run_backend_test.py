import subprocess
import time
import urllib.request
import json
import os
import signal

venv_python = r"D:\intelligent_transportation\intel-transportation\.venv\Scripts\python.exe"
backend_dir = r"D:\intelligent_transportation\intel-transportation\backend"

print("="*50)
print(" 🚀 开始端到端全链路自动化测试")
print("="*50)

print("\n[1] 启动 消息消费者(Consumer) 与 大屏API服务...")
# 使用 subprocess 启动后台进程
consumer_proc = subprocess.Popen([venv_python, os.path.join(backend_dir, "traffic_consumer.py")])
api_proc = subprocess.Popen([venv_python, os.path.join(backend_dir, "dashboard_api.py")])

# 等待服务完全启动并连接到数据库/Kafka
time.sleep(5) 

print("\n[2] 启动 边缘端模拟器 (生成 15 条模拟抓拍数据)...")
sim_file = os.path.join(backend_dir, "simulate_edge_traffic.py")
with open(sim_file, "r", encoding="utf-8") as f:
    sim_code = f.read()

# 将死循环替换为只发送15条数据就退出，并加快发送速度避免测试等待过长
sim_code_patched = sim_code.replace("while True:", "for _ in range(15):").replace("time.sleep(random.uniform(0.2, 1.0))", "time.sleep(0.1)")

patched_sim_file = os.path.join(backend_dir, "simulate_edge_traffic_test.py")
with open(patched_sim_file, "w", encoding="utf-8") as f:
    f.write(sim_code_patched)

# 运行模拟器
subprocess.run([venv_python, patched_sim_file])

print("\n[3] 等待 Kafka 队列消费并入库 (2秒)...")
time.sleep(2)

print("\n[4] 模拟大屏前端请求接口：GET /api/traffic_trend")
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/traffic_trend")
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        print(">>> 接口返回状态:", response.status)
        print(">>> 接口返回数据:")
        print(json.dumps(res_data, indent=2, ensure_ascii=False))
except Exception as e:
    print("❌ 请求 API 失败:", e)

print("\n[5] 清理后台驻留进程...")
consumer_proc.terminate()
api_proc.terminate()
if os.path.exists(patched_sim_file):
    os.remove(patched_sim_file)

print("\n🎉 测试圆满结束！")
