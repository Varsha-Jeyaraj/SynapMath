# Use a slim Python 3.11 base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Set working directory
WORKDIR /app

# Install system dependencies (including poppler-utils for PDF processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Build ChromaDB vectors at image build time so they persist across restarts.
# HF_API_KEY must be passed as a Docker build arg (set in Render dashboard).
ARG HF_API_KEY
ARG EMBEDDING_PROVIDER=huggingface
ARG HF_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN if [ -n "$HF_API_KEY" ]; then \
      HF_API_KEY=$HF_API_KEY \
      EMBEDDING_PROVIDER=$EMBEDDING_PROVIDER \
      HF_EMBEDDING_MODEL=$HF_EMBEDDING_MODEL \
      python -c "from rag.ingest import ingest_all; ingest_all(force=True)"; \
    else \
      echo "WARN: HF_API_KEY not set at build time, skipping pre-build ingestion"; \
    fi

EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 180 "app:app"
