# CAN Bus to WebSocket Converter

A lightweight, high-performance CAN bus data streaming system that converts CAN bus messages to WebSocket streams for real-time monitoring. Optimized for high-volume streaming on public buses and other vehicle applications.

## Architecture

```
CAN Bus Device → candump → cantools decode → WebSocket Server → Multiple Clients
     ↓              ↓           ↓              ↓
  Raw CAN      Raw CAN    Decoded J1939   WebSocket Stream
  Messages     Data       Messages        (Raw Text)
```

## Quick Start

### Using Docker

```bash
# Build the container
docker build -t canbus-ws-converter .

# Run with default settings
docker run --network host --privileged canbus-ws-converter

# Run with custom configuration
docker run --network host --privileged \
  -e CAN_DEVICE=can1 \
  -e WS_HOST=0.0.0.0 \
  -e WS_PORT=8080 \
  canbus-ws-converter
```

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
candump can0 | python3 -m cantools decode --single-line j1939.dbc | python3 wsServer.py
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CAN_DEVICE` | `can0` | CAN bus device name |
| `WS_HOST` | `0.0.0.0` | WebSocket server host |
| `WS_PORT` | `8080` | WebSocket server port |
| `DBC_PATH` | `/app/j1939.dbc` | Path to J1939 DBC file |

### Ports

- **8080**: WebSocket server (main data stream)

## WebSocket API

### Connection

Connect to `ws://localhost:8080` to receive CAN bus data.


## Monitoring and Logging

### Log Format
```
2024-01-15 10:30:45,123 - INFO - CAN Bus WebSocket Server started on 0.0.0.0:8080
2024-01-15 10:30:45,124 - INFO - Waiting for client connections...
2024-01-15 10:30:50,456 - INFO - New client connected from 192.168.1.100. Active clients: 1
```


## Security Considerations

- **Network Access**: WebSocket server binds to all interfaces (0.0.0.0)
- **No Authentication**: Direct access to CAN data stream
- **Data Privacy**: CAN bus data is transmitted in plain text
- **Access Control**: Use firewall rules to restrict access


### Recommended Setup
```bash
# Production deployment
docker run -d \
  --name canbus-converter \
  --restart unless-stopped \
  --network host \
  --privileged \
  -e CAN_DEVICE=can0 \
  -e WS_HOST=0.0.0.0 \
  -e WS_PORT=8080 \
  canbus-ws-converter
```

### Monitoring
```bash
# Check container status
docker ps

# View real-time logs
docker logs -f canbus-converter

# Monitor resource usage
docker stats canbus-converter
```
