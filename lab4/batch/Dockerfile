FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORY_MB=256

WORKDIR /app

COPY batch.py .

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "batch.py"]
