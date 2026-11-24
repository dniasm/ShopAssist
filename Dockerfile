# One image for the orchestrator and the five gRPC services (the compose file picks the command).
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY orchestrator/ orchestrator/
COPY services/ services/
COPY shopassist_proto/ shopassist_proto/
COPY protos/ protos/
COPY data/ data/
COPY scripts/ scripts/
COPY --from=web /web/dist web/dist
RUN useradd --system --no-create-home app
USER app
CMD ["python", "-m", "orchestrator.main"]
