ARG BUILD_IMAGE=rcfp-lambda-build
FROM ${BUILD_IMAGE} AS artifact

FROM public.ecr.aws/lambda/python:3.12 AS catalog
ARG DEMO_BUCKET=""
COPY --from=artifact /bundle /bundle
WORKDIR /source
COPY packaging/demo/prepare.py packaging/demo/prepare.py
COPY docs/examples/data docs/examples/data
RUN python packaging/demo/prepare.py --binary /bundle/bin/rcfp --out /demo --bucket "${DEMO_BUCKET}"

FROM public.ecr.aws/lambda/provided:al2023
COPY --from=artifact /bundle /var/runtime/runtime
COPY packaging/lambda/bootstrap /var/runtime/bootstrap
COPY --from=catalog /demo/catalog.sqlite /demo/manifest.json /var/task/
COPY --from=catalog /demo/data /var/task/data
ENV TC_DRIVER_PATH=/var/task/catalog.sqlite \
    TC_COMPAT_PROFILE=native \
    TC_RASTER_WORKERS=1 \
    TC_RASTER_QUEUE_CAPACITY=4 \
    TC_RASTER_TIMEOUT_MS=20000 \
    TC_EXPOSE_METRICS=false
CMD ["bootstrap"]
