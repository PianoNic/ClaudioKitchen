FROM python:3.12-slim
WORKDIR /app

# Install deps first so the layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

VOLUME /data
ENV FILES_DIR=/data/generated
EXPOSE 8000

# Liveness probe against the unauthenticated /health route.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else sys.exit(1)"

CMD ["python", "server.py"]
