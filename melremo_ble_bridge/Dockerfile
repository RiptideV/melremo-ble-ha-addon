ARG BUILD_FROM
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apk add --no-cache \
    bluez-libs \
    dbus-libs \
    && apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    linux-headers

COPY rootfs /

RUN pip install --no-cache-dir -r /app/requirements.txt \
    && apk del .build-deps
