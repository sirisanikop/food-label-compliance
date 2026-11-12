FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
COPY services/verifier/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY src /app/src
COPY prompts /app/prompts
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8001
CMD ["uvicorn", "foodlabel.verifier.app:app", "--host", "0.0.0.0", "--port", "8001"]
