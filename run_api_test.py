import subprocess
import time
import urllib.request
import json
import os

venv_python = r"D:\intelligent_transportation\intel-transportation\.venv\Scripts\python.exe"
backend_dir = r"D:\intelligent_transportation\intel-transportation\backend"

print("\n[1] 重新启动 大屏API服务...")
api_proc = subprocess.Popen([venv_python, os.path.join(backend_dir, "dashboard_api.py")])
time.sleep(4)

print("\n[2] 测试大屏 API 接口...")
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/traffic_trend")
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        print(">>> 接口返回数据:")
        print(json.dumps(res_data, indent=2, ensure_ascii=False))
except Exception as e:
    print("❌ 请求 API 失败:", e)

api_proc.terminate()
