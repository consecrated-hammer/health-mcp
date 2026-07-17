FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV HOME=/data

RUN groupadd --gid 1000 healthmcp \
    && useradd --uid 1000 --gid 1000 --home-dir /data --create-home healthmcp \
    && pip install --no-cache-dir cryptography==45.0.5

WORKDIR /app
COPY app.py /app/app.py

RUN mkdir -p /app /data && chown -R 1000:1000 /app /data

ENTRYPOINT ["python", "/app/app.py"]
