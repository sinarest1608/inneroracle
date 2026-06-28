# Pinned to 3.12-slim for broad wheel availability (ML deps in later milestones).
FROM python:3.12-slim

WORKDIR /app

# Core deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
