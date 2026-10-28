FROM registry.k8s.io/kubectl:v1.32.3 AS kubectl
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
WORKDIR /app
COPY gate.py .
USER 10001
ENTRYPOINT ["python", "gate.py"]
