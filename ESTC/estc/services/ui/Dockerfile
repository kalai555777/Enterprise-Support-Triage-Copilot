# ui-client image (Phase 5, task 5.5.1).
# Built from THIS sub-directory (context: ./estc/services/ui) — unlike orchestrator-app's
# repo-root context — so it stays lean (streamlit + httpx only; no torch/chromadb). The app
# imports nothing from estc.* at runtime, so the estc/ tree is not needed in the image.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# app.py, state.py, orchestrator_client.py, components/ — the whole ui/ tree.
COPY . .

EXPOSE 8501

# headless flags keep Streamlit from trying to open a browser / collect usage stats in-container.
CMD ["streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501", \
     "--server.headless", "true", "--browser.gatherUsageStats", "false"]
