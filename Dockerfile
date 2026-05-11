FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXSCHOOL_HOST=0.0.0.0 \
    EXSCHOOL_PORT=8010

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p storage

EXPOSE 8010

CMD ["sh", "-c", "uvicorn exschool_game.app:app --host ${EXSCHOOL_HOST:-0.0.0.0} --port ${EXSCHOOL_PORT:-8010} --proxy-headers"]
