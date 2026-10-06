# Home Assistant builds this image on your machine when the add-on is installed.

# 1) Frontend (React + Vite) -> static files
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build --outDir /ui/dist --emptyOutDir

# 2) Backend (FastAPI) serving the API and the built frontend
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUDGET_DATA_DIR=/data \
    BUDGET_STATIC_DIR=/srv/static

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=ui /ui/dist ./static

EXPOSE 8099
# Do NOT use --proxy-headers: X-Forwarded-For would replace the client address and break the
# "request came through the Home Assistant ingress proxy" check (every request would get 403).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--no-proxy-headers"]
