FROM python:3.13-alpine3.21

RUN apk add --no-cache can-utils websocat coreutils

RUN python3 -m pip install --no-cache-dir cantools websockets

COPY j1939.dbc /app/j1939.dbc
COPY wsServer.py /app/wsServer.py

WORKDIR /app

ENV CAN_DEVICE=can0
ENV WS_HOST=0.0.0.0
ENV WS_PORT=8088
ENV DBC_PATH=/app/j1939.dbc
ENV RECOVERY_CYCLE_SECONDS=60
ENV LOG_INTERVAL=100000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONGC=1

CMD ["sh", "-c", "stdbuf -oL -eL candump $CAN_DEVICE | stdbuf -oL python3 -m cantools decode --single-line $DBC_PATH | stdbuf -oL python3 wsServer.py"]