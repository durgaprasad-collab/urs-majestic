# Docker runtime for the URS Majestic backend.
#
# Reason for Docker (vs Render's native Python build): receipt OCR needs the
# `tesseract-ocr` SYSTEM binary, which a native build can't apt-install. The
# Aptfile is a no-op on Render native; this Dockerfile is what actually installs
# tesseract. Everything else is a normal FastAPI + uvicorn image.
FROM python:3.12-slim

# tesseract for receipt OCR (app/services/receipt_parse.py)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT; bind it (fall back to 8000 for local `docker run`).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
