FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
RUN mkdir -p /data/images \
    && python -m compileall -q app \
    && ADMIN_PASSWORD=build-test-password \
       SESSION_SECRET=build-test-session-secret-32-characters \
       INGEST_API_TOKEN=build-test-ingest-token-32-characters \
       python -m pytest -q

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
