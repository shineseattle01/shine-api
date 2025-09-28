# ./Dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000 \
    TZ=America/Sao_Paulo

WORKDIR /app

# Dependências do sistema (opcional, mas útil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

# Instala as libs Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY app ./app

EXPOSE 10000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","10000"]
