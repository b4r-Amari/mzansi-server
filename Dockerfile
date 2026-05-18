FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.11-slim

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY --from=builder /root/.local /home/app/.local
COPY --chown=app:app . .

USER app

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
