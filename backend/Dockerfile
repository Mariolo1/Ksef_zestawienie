FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -u 1000 -m app && mkdir -p /data && chown app /data
USER app
ENV DATA_DIR=/data
EXPOSE 8000
# 1 worker: wątek raportów działa w procesie backendu (wersja testowa)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
