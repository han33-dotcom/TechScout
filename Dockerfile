FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/techscout

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY config ./config
COPY tests ./tests

RUN useradd --create-home techscout \
    && mkdir -p /var/lib/techscout/evidence \
    && chown techscout /var/lib/techscout/evidence \
    && chmod 700 /var/lib/techscout/evidence
USER techscout
ENV EVIDENCE_DIR=/var/lib/techscout/evidence

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
