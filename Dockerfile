FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (layer caches unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY examples/ ./examples/

# Copy pipeline data (SQLite + Qdrant — baked into image, rebuild on pipeline update)
COPY data/ ./data/

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8080"]
