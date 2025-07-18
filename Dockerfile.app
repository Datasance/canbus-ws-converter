FROM python:3.13-alpine3.21

# Install system dependencies for CAN bus and Python extensions
RUN apk add --no-cache can-utils websocat coreutils

# Install Python dependencies
RUN python3 -m pip install --no-cache-dir \
    cantools \
    websockets \
    python-can

# Copy application files
COPY j1939.dbc /app/j1939.dbc
COPY app.py /app/app.py

WORKDIR /app

# Environment variables for configuration and optimization
ENV CAN_DEVICE=can0
ENV WS_HOST=0.0.0.0
ENV WS_PORT=8088
ENV DBC_PATH=/app/j1939.dbc
ENV RECOVERY_CYCLE_SECONDS=60
ENV LOG_INTERVAL=100000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONGC=1

# Run the application
CMD ["python3", "app.py"] 