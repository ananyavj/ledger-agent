# The Python image: runs the API, and the one-off setup job (seed data + contract index).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: Docker caches this layer, so code changes don't reinstall everything
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY evals ./evals
COPY data ./data

# Download the embedding model at BUILD time, so containers start fast and work offline
RUN python -c "from app.embeddings import _model; _model()"

# Don't run as root inside the container
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
