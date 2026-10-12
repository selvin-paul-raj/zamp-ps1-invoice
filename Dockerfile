# One container = the whole app (UI + API + worker). Free-tier friendly (Render, Hugging Face Spaces, Fly, Koyeb).
#   docker build -t zamp-ps1 . && docker run -p 8000:8000 zamp-ps1   ->  http://localhost:8000
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build            # static export -> /ui/out (calls the API at /api on the same origin)

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIPELINE_STEP_DELAY_MS=350 FRONTEND_DIR=/app/static
# poppler-utils: pdftotext / pdftoppm.  tesseract-ocr: OCR for scanned invoices.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=ui /ui/out ./static
EXPOSE 8000
# PORT is set by most hosts (Render, Spaces use 7860 via PORT). Seeds the demo data on first start.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
