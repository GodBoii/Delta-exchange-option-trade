FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN chown -R app:app /app

USER app
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)); r=data['realtime']; assert data['success'] and data['service'] == 'binance-market-data-api' and r['connected'] and r['bookSynced'] and r['eventAgeMs'] is not None and r['eventAgeMs'] < 30000"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
