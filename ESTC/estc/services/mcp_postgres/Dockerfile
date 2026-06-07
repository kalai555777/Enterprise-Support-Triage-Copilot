FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY server.py ./server.py

# Drop privileges: run as a non-root user (least privilege).
RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "server.py"]
