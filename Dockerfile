# Optional container build (Windows-native start.ps1 is the primary path).
# Not verified in this environment: Docker is not installed on the development machine.

FROM node:20-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit --ignore-scripts
COPY frontend/ ./
RUN npx --no-install vite build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
COPY backend/ backend/
COPY --from=frontend /src/frontend/dist frontend/dist
ENV AISECC_DATA_DIR=/data
RUN mkdir -p /data /projects && chown app:app /data /projects
USER app
EXPOSE 8765
# Inside the container the server must listen on all interfaces; docker-compose.yml publishes the
# port on the host's 127.0.0.1 only.
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
