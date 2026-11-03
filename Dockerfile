FROM node:24-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .
COPY --from=ui /build/frontend/dist ./frontend/dist
RUN useradd --create-home --uid 10001 workbench && mkdir /data && chown workbench /data
USER workbench
ENV WORKBENCH_DATA=/data WORKBENCH_RUNNER=disabled
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "workbench.main:app", "--host", "0.0.0.0", "--port", "8000"]
