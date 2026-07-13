# ==============================================================
# Dockerfile — Prospector Bot
# Imagem baseada em Python 3.11 slim com Playwright e Chromium
# ==============================================================

FROM python:3.11-slim

LABEL maintainer="prospector-bot"
LABEL description="Prospector Bot + Dashboard administrativo"
LABEL version="1.0.0"

# Evita geração de bytecode e garante output imediato nos logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Instala dependências de sistema necessárias para rodar o Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1-0-0 \
    libcairo2 \
    libxml2 \
    libxslt1.1 \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Instala o navegador Chromium do Playwright e suas dependências de biblioteca
RUN playwright install chromium && \
    playwright install-deps chromium

# Copia a base de arquivos do projeto para o container
COPY main.py .
COPY dashboard.py .
COPY src/ ./src/
COPY config/ ./config/
COPY templates/ ./templates/

# Cria pastas para persistência de dados
RUN mkdir -p data/exports logs && \
    chmod -R 755 data/

# Expõe a porta do Dashboard
EXPOSE 5000

# Executa o bot no modo completo (Scheduler em background + Dashboard web)
CMD ["python", "main.py", "all"]
