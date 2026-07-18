FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

ENV ADMIN_PORT=9100
ENV FRONTEND_DIR=/app/frontend
ENV BACKUP_DIR=/backups
EXPOSE 9100

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9100"]
