FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src \
    HF_HOME=/app/.cache/huggingface
WORKDIR /app
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch
COPY services/indexer/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY src /app/src
RUN useradd -m app && mkdir -p /app/data/index /app/.cache/huggingface && chown -R app /app
USER app
CMD ["python", "-m", "foodlabel.ingest.pipeline"]
