FROM python:3.11-slim

WORKDIR /app
COPY dashboard/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard ./dashboard
COPY vehicle_records.csv detections.csv ./
COPY intel-transportation/data/vehicle_records.csv ./intel-transportation/data/vehicle_records.csv

EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
