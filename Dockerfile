FROM node:22-alpine AS ui-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run typecheck && npm run build


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV HOME=/data

ARG BUILD_VERSION=dev

RUN groupadd --gid 1000 healthmcp \
    && useradd --uid 1000 --gid 1000 --home-dir /data --create-home healthmcp

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py mcp_apps.py output_schemas.py server.py /app/
COPY --from=ui-build /build/web/dist /app/web/dist
RUN printf '%s\n' "$BUILD_VERSION" > /app/version.txt \
    && date -u '+%Y-%m-%dT%H:%M:%SZ' > /app/build_timestamp.txt

RUN mkdir -p /app /data && chown -R 1000:1000 /app /data

ENTRYPOINT ["python", "/app/server.py"]
