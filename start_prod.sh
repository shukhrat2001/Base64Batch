#!/bin/bash
set -Eeuo pipefail

source venv/bin/activate
mkdir -p logs

exec gunicorn main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --bind 127.0.0.1:8000 \
  --timeout 30 \
  --keep-alive 5 \
  --access-logfile logs/access.log \
  --error-logfile logs/error.log \
  --log-level info
