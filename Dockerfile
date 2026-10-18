FROM python:3.12-slim-bookworm

# Calibre provides ebook-convert; the extra libs let it run headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        calibre libegl1 libopengl0 libxkbcommon0 libgl1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
