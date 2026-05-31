# Orchestrator-app image (Phase 4.6, task 4.6.2).
# NOTE: build context is the REPO ROOT (set in docker-compose.yml), not this directory —
# the app imports across estc/* (shared, graph, rag, and the in-process MCP tool modules),
# so the whole estc/ tree must be copied and importable as estc.* (PYTHONPATH=/app).
FROM python:3.11-slim

WORKDIR /app

# Install deps first so the layer caches across code-only changes.
COPY estc/services/orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the full package tree (estc.shared, estc.services.*, estc.tests/fixtures for the
# GitHub mock). chroma_db is supplied at runtime via a read-only volume mount.
COPY estc/ ./estc/

ENV PYTHONPATH=/app

EXPOSE 8002

CMD ["python", "-m", "uvicorn", "estc.services.orchestrator.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
