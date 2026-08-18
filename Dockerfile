FROM python:3.12-slim

WORKDIR /app

# Runtime deps only -- requirements-dev.txt (pytest, ruff, etc.) never enters the image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY taxonomy/ ./taxonomy/

ENV PORT=8080
EXPOSE 8080

CMD exec functions-framework --target=handle_pubsub_push --source=src/main.py --port=${PORT}
