FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEDIA_DIR=/tmp/media \
    TZ=Europe/Rome

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates curl tesseract-ocr tesseract-ocr-eng tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p /tmp/media

CMD ["python", "bot.py"]
