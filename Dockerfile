FROM python:3.13-alpine3.21

RUN apk add --no-cache can-utils websocat

RUN python3 -m pip install cantools websockets

COPY j1939.dbc /app/j1939.dbc
COPY wsServer.py /app/wsServer.py

WORKDIR /app

# Environment variables for configuration
ENV CAN_DEVICE=can0
ENV WS_HOST=0.0.0.0
ENV WS_PORT=8088
ENV DBC_PATH=/app/j1939.dbc
ENV RECOVERY_CYCLE_SECONDS=60

CMD [ "sh", "-c", "candump $CAN_DEVICE | python3 -m cantools decode --single-line $DBC_PATH | python3 wsServer.py"]