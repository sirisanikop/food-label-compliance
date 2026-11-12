FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY src /app/src
COPY prompts /app/prompts
COPY contracts /app/contracts
RUN useradd -m app && mkdir -p /app/data/quarantine && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "foodlabel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
