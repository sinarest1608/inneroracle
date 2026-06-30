# Pinned to 3.12-slim for broad wheel availability (fastembed/onnxruntime).
FROM python:3.12-slim

WORKDIR /app

# Deps first for layer caching. requirements-embed brings fastembed (ONNX) —
# required by the default embedder at both serving and indexing time.
COPY requirements.txt requirements-embed.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-embed.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
