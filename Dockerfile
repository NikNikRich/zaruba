# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала код и метаданные: слой зависимостей кэшируется до изменения pyproject.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Контент — данные, а не код: он монтируется рядом, чтобы правка YAML не требовала
# пересборки образа. Пути задаём явно: пакет установлен в site-packages, и
# относительный BASE_DIR из config.py на /app уже не указывает.
COPY content ./content
ENV ZARUBA_ENV=prod \
    ZARUBA_CONTENT_DIR=/app/content/editions \
    ZARUBA_VENUES_FILE=/app/content/venues.yaml \
    ZARUBA_GLOSSARY_FILE=/app/content/glossary.yaml \
    ZARUBA_FAQ_FILE=/app/content/faq.yaml \
    ZARUBA_HISTORY_FILE=/app/content/history.yaml \
    FORWARDED_ALLOW_IPS=127.0.0.1

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin zaruba \
    && chown -R zaruba:zaruba /app
USER zaruba

EXPOSE 8000

# --proxy-headers обязателен: без него canonical, robots.txt и sitemap отдают
# http://127.0.0.1:8000 вместо публичного адреса.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["uvicorn", "zaruba.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header"]
