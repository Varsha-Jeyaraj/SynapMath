# Use a slim Python 3.11 base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860

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

# HF Spaces injects secrets as env vars at runtime, so ChromaDB ingestion
# runs on first startup (see app.py). The vectors are cached in the container
# filesystem and persist until the Space is rebuilt.

EXPOSE 7860

CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 180 "app:app"
