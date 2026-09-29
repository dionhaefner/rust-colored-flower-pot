FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libstdc++6 zlib1g ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY bundle /opt/rcfp
COPY fixture /data

ENV TC_DRIVER_PATH=/data/catalog.sqlite
ENV TC_SERVER_PORT=5000
EXPOSE 5000

ENTRYPOINT ["/opt/rcfp/bin/rcfp"]
CMD ["serve", "-d", "/data/catalog.sqlite", "--bind", "0.0.0.0:5000"]
