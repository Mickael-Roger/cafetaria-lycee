FROM python:3.13-slim

LABEL org.opencontainers.image.title="cafetaria-lycee" \
      org.opencontainers.image.description="School cafeteria reservation PWA + server"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

# Application code (web/ must sit next to server/ - the API serves web/ as static files)
COPY web/ /app/web/
COPY server/ /app/server/

# Configuration and SQLite database live outside the image:
# mount config.yml and a persistent volume on /data.
RUN useradd --uid 1000 --create-home cafetaria \
    && mkdir -p /data \
    && chown -R cafetaria:cafetaria /app /data
USER cafetaria

ENV CAFETARIA_CONFIG=/data/config.yml

EXPOSE 8080
VOLUME ["/data"]

CMD ["python", "server/run.py"]
