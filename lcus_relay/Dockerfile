FROM ghcr.io/home-assistant/aarch64-base:3.20

RUN apk add --no-cache \
    python3 \
    py3-paho-mqtt \
    py3-pyserial

COPY rootfs/app /app

CMD ["python3", "/app/lcus.py"]
