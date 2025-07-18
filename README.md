# CAN Bus to WebSocket Converter & Logger

A lightweight, high-performance CAN bus data streaming and logging system that converts CAN bus messages to WebSocket streams for real-time monitoring and logs raw CAN data to MDF4 files. Optimized for high-volume streaming on public buses and other vehicle applications.

## Architecture

The system consists of two independent containers:

### 1. WebSocket Server (`canbus-ws-server`)
- **Purpose**: Reads CAN bus data, decodes using DBC files, and broadcasts to WebSocket clients
- **Features**: 
  - Real-time CAN message decoding and broadcasting
  - J1939 DBC support for message interpretation
  - Graceful error handling and recovery
  - Configurable via environment variables

### 2. CAN Logger (`canbus-logger`)
- **Purpose**: Logs raw CAN bus data to MDF4 files with rotation and archiving
- **Features**:
  - High-performance MDF4 logging using asammdf
  - Automatic file rotation by size (default: 200MB)
  - Batch writing for optimal performance
  - Automatic archiving to `.tar.bz2` format
  - Disk space monitoring and cleanup
  - Configurable via environment variables


### Manual Docker Build

1. **Build the WebSocket server:**
   ```bash
   docker build -f Dockerfile.app -t canbus-ws-server .
   ```

2. **Build the logger:**
   ```bash
   docker build -f Dockerfile.logger -t canbus-logger .
   ```

3. **Run the containers:**
   ```bash
   # WebSocket server
   docker run -d \
     --name canbus-ws-server \
     --device=/dev/can0:/dev/can0 \
     -v $(pwd)/j1939.dbc:/app/j1939.dbc:ro \
     -p 8088:8088 \
     canbus-ws-server

   # Logger
   docker run -d \
     --name canbus-logger \
     --device=/dev/can0:/dev/can0 \
     -v $(pwd)/logs:/logs \
     canbus-logger
   ```

## Configuration

### Environment Variables

#### WebSocket Server (`canbus-ws-server`)
| Variable | Default | Description |
|----------|---------|-------------|
| `WS_HOST` | `0.0.0.0` | WebSocket server host |
| `WS_PORT` | `8088` | WebSocket server port |
| `CAN_DEVICE` | `can0` | CAN bus device |
| `DBC_PATH` | `/app/j1939.dbc` | Path to DBC file |
| `RECOVERY_CYCLE_SECONDS` | `60` | CAN bus recovery timeout |
| `LOG_INTERVAL` | `100000` | Progress logging interval |

#### CAN Logger (`canbus-logger`)
| Variable | Default | Description |
|----------|---------|-------------|
| `CAN_DEVICE` | `can0` | CAN bus device |
| `LOG_DIR` | `/logs` | Log directory |
| `MAX_FILE_SIZE_MB` | `200` | Maximum log file size before rotation |
| `BATCH_SIZE` | `1000` | Number of messages to batch before writing |
| `RECOVERY_CYCLE_SECONDS` | `60` | CAN bus recovery timeout |
| `LOG_INTERVAL` | `10000` | Progress logging interval |

## Log Files

### MDF4 Log Structure
```
/logs/
├── current_log.mf4              # Active logging file
├── archive_20241201_143022.mf4.tar.bz2
├── archive_20241201_143022.mf4.tar.bz2
└── ...
```

### Log File Features
- **Format**: MDF4 (Measurement Data Format)
- **Content**: Raw CAN messages with timestamps
- **Signals**: CAN ID and data bytes (Byte_0, Byte_1, etc.)
- **Rotation**: Automatic at 200MB (configurable)
- **Archiving**: Automatic compression to `.tar.bz2`
- **Cleanup**: Automatic deletion of old archives when disk space is low

## WebSocket API

### Connection
```javascript
const ws = new WebSocket('ws://localhost:8088');
```

### Message Format
```json
{
  "timestamp": 1234567890.123,
  "can_id": "0x123",
  "data": {
    "signal1": "value1",
    "signal2": "value2"
  },
  "raw": false,
  "message_id": "EngineData"
}
```

For raw (undecoded) messages:
```json
{
  "timestamp": 1234567890.123,
  "can_id": "0x123",
  "data": "0102030405060708",
  "raw": true
}
```


