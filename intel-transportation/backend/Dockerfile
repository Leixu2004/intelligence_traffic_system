FROM python:3.10-slim

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY acceptance ./acceptance
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "backend.dashboard_api:app", "--host", "0.0.0.0", "--port", "8000"]
