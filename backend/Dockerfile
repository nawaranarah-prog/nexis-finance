FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
COPY scripts/ /app/scripts/
COPY data/samples/ /app/data/samples/
RUN useradd --create-home nexis && mkdir -p /app/data/reports /app/data/demo && chown -R nexis /app
USER nexis
ENV NEXIS_DATABASE_URL=sqlite:////app/data/nexis.db NEXIS_REPORTS_DIR=/app/data/reports PORT=8000
EXPOSE 8000
# Render (and most PaaS) inject $PORT; default 8000 for docker run / compose.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
