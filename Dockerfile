FROM node:24.14.1-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY tests/fixtures/v2/ /build/tests/fixtures/v2/
COPY tests/fixtures/accounts.json /build/tests/fixtures/accounts.json
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/techscout

COPY requirements.txt .
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps --only-shell chromium

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY config ./config
COPY examples ./examples
COPY tests ./tests
COPY --from=frontend /build/frontend/dist ./frontend/dist

RUN useradd --create-home techscout \
    && mkdir -p /var/lib/techscout/evidence \
    && chown techscout /var/lib/techscout/evidence \
    && chmod 700 /var/lib/techscout/evidence
USER techscout
ENV EVIDENCE_DIR=/var/lib/techscout/evidence

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
