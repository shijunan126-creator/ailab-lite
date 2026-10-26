# Headless Chromium + Crawl4AI behind one authenticated endpoint (server.py). Infrastructure, like
# ClamAV or SearXNG: no service credentials, no route to the platform; its only egress is the
# platform-egress proxy (ADR 0021).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 10002 -m crawler \
 && chmod -R a+rX /opt/ms-playwright
COPY server.py test_server.py NOTICE Crawl4AI-LICENSE ./
RUN python -m unittest -q test_server
USER 10002
ENV HOME=/tmp CRAWL4_AI_BASE_DIRECTORY=/tmp
EXPOSE 8090
CMD ["python", "server.py"]
