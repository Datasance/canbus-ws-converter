import asyncio
import logging
import signal
import sys
import websockets
import os
import platform
import cantools
import cantools.database
import can

# Configure simple logging to stdout only
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class CANBusWebSocketServer:
    """Production-grade CAN Bus to WebSocket server using direct cantools integration"""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8088):
        self.host = host
        self.port = port
        self.clients = set()
        self.running = False
        self.server = None
        self.shutdown_event = asyncio.Event()
        
        # CAN bus configuration
        self.can_device = os.getenv("CAN_DEVICE", "can0")
        self.dbc_path = os.getenv("DBC_PATH", "/app/j1939.dbc")
        self.can_bus = None
        self.db = None
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Setup signal handlers in a platform-aware way"""
        if platform.system() != 'Windows':
            # Unix-like systems: use loop.add_signal_handler
            try:
                loop = asyncio.get_event_loop()
                loop.add_signal_handler(signal.SIGTERM, self._signal_handler)
                loop.add_signal_handler(signal.SIGINT, self._signal_handler)
            except NotImplementedError:
                # Fallback for platforms that don't support add_signal_handler
                signal.signal(signal.SIGTERM, self._signal_handler)
                signal.signal(signal.SIGINT, self._signal_handler)
        else:
            # Windows: rely on KeyboardInterrupt
            signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum=None, frame=None):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received shutdown signal, initiating graceful shutdown...")
        self.shutdown_event.set()
    
    async def shutdown(self):
        """Gracefully shutdown the server"""
        if not self.running:
            return  # Prevent multiple shutdown calls
        
        self.running = False
        logger.info("Shutting down WebSocket server...")
        
        # Close CAN bus connection
        if self.can_bus:
            self.can_bus.shutdown()
            logger.info("CAN bus connection closed")
        
        # Close all client connections
        if self.clients:
            logger.info(f"Closing {len(self.clients)} client connections...")
            await asyncio.gather(*[
                client.close(1000, "Server shutdown") 
                for client in self.clients
            ], return_exceptions=True)
        
        # Stop the server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        logger.info("Server shutdown complete")
    
    async def _broadcast_message(self, message: str):
        """Broadcast message to all connected clients"""
        if not self.clients:
            return
        
        # Prepare tasks for concurrent sending
        tasks = []
        client_list = list(self.clients)  # Create a snapshot
        disconnected_clients = set()
        
        for client in client_list:
            try:
                tasks.append(client.send(message))
            except Exception as e:
                logger.error(f"Error preparing message for client: {e}")
                disconnected_clients.add(client)
        
        # Send to all clients concurrently
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Check for exceptions and mark disconnected clients
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    if isinstance(result, websockets.exceptions.ConnectionClosed):
                        disconnected_clients.add(client_list[i])
                    else:
                        logger.error(f"Error sending message to client: {result}")
                        disconnected_clients.add(client_list[i])
        
        # Clean up disconnected clients
        self.clients -= disconnected_clients
        if disconnected_clients:
            logger.info(f"Removed {len(disconnected_clients)} disconnected clients. Active: {len(self.clients)}")
    
    async def _handle_client(self, websocket):
        """Handle individual client connections"""
        logger.info("=== CLIENT CONNECTION ATTEMPT ===")
        self.clients.add(websocket)
        client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
        
        logger.info(f"New client connected from {client_ip}. Active clients: {len(self.clients)}")
        
        try:
            # Keep connection alive and ignore any client messages
            async for message in websocket:
                pass
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client from {client_ip} disconnected normally")
        except Exception as e:
            logger.error(f"Error handling client from {client_ip}: {e}")
        finally:
            self.clients.discard(websocket)
            logger.info(f"Client from {client_ip} removed. Active clients: {len(self.clients)}")
    
    def _initialize_can_bus(self):
        """Initialize CAN bus connection and load DBC file"""
        try:
            # Load DBC file
            logger.info(f"Loading DBC file: {self.dbc_path}")
            self.db = cantools.database.load_file(self.dbc_path)
            logger.info(f"DBC loaded successfully. Messages: {len(self.db.messages)}")
            
            # Log available message IDs for debugging
            message_ids = [hex(msg.frame_id) for msg in self.db.messages]
            logger.info(f"Available message IDs in DBC: {message_ids[:10]}...")  # Show first 10
            
            # Initialize CAN bus using proper cantools pattern
            logger.info(f"Initializing CAN bus: {self.can_device}")
            self.can_bus = can.interface.Bus(self.can_device, interface='socketcan')
            logger.info("CAN bus initialized successfully")
            
            return True
            
        except cantools.database.UnsupportedDatabaseFormatError as e:
            logger.error(f"Unsupported DBC file format: {e}")
            return False
        except FileNotFoundError:
            logger.error(f"DBC file not found: {self.dbc_path}")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize CAN bus: {e}")
            return False
    
    async def _read_can_messages(self):
        """Read CAN bus messages using cantools"""
        recovery_cycle_seconds = int(os.getenv("RECOVERY_CYCLE_SECONDS", "60"))
        log_interval = int(os.getenv("LOG_INTERVAL", "100000"))
        lines_processed = 0
        
        logger.info("CAN message reader started - waiting for data...")
        
        while self.running:
            # Check for shutdown signal
            if self.shutdown_event.is_set():
                logger.info("Shutdown signal received, stopping CAN reader")
                break
            
            try:
                # Use asyncio to make CAN bus reading non-blocking (similar to wsServer.py pattern)
                loop = asyncio.get_event_loop()
                message = await loop.run_in_executor(None, lambda: self.can_bus.recv(timeout=1.0))
                
                if message:
                    # Log raw message for debugging
                    # logger.info(f"Received CAN message: ID={hex(message.arbitration_id)}, Data={message.data.hex()}")
                    
                    # Try to decode the message using cantools
                    try:
                        # Use cantools decode_message method - this is the standard pattern
                        decoded = self.db.decode_message(message.arbitration_id, message.data)
                        
                        # Convert to JSON-like string for WebSocket transmission
                        import json
                        message_obj = self.db.get_message_by_frame_id(message.arbitration_id)
                        
                        # Convert NamedSignalValue objects to strings for JSON serialization
                        def convert_named_signal_values(obj):
                            if hasattr(obj, 'name') and hasattr(obj, 'value'):
                                # This is a NamedSignalValue object - use the enum name from DBC
                                return obj.name  # VAL_ enum name from DBC
                            elif isinstance(obj, dict):
                                return {k: convert_named_signal_values(v) for k, v in obj.items()}
                            elif isinstance(obj, list):
                                return [convert_named_signal_values(item) for item in obj]
                            else:
                                return obj
                        
                        # Convert decoded data to JSON-serializable format
                        serializable_data = convert_named_signal_values(decoded)
                        
                        message_data = {
                            'timestamp': message.timestamp,
                            'can_id': hex(message.arbitration_id),
                            'data': serializable_data,
                            'raw': False,
                            'message_id': message_obj.name if message_obj else 'unknown'
                        }
                        
                        message_str = json.dumps(message_data)
                        await self._broadcast_message(message_str)
                        
                        lines_processed += 1
                        
                        # Log progress every configured interval
                        if lines_processed % log_interval == 0:
                            logger.info(f"CAN bus processing: {lines_processed} messages processed")
                    
                    except KeyError as decode_error:
                        # Message not found in DBC - send raw message
                        logger.debug(f"Message {hex(message.arbitration_id)} not found in DBC: {decode_error}")
                        
                        # Send raw message data
                        import json
                        message_data = {
                            'timestamp': message.timestamp,
                            'can_id': hex(message.arbitration_id),
                            'data': message.data.hex(),
                            'raw': True
                        }
                        
                        message_str = json.dumps(message_data)
                        await self._broadcast_message(message_str)
                        
                        lines_processed += 1
                        
                        # Log progress every configured interval
                        if lines_processed % log_interval == 0:
                            logger.info(f"CAN bus processing: {lines_processed} messages processed")
                    
                    except cantools.database.DecodeError as decode_error:
                        # Decode error (data doesn't match DBC specification)
                        logger.warning(f"Decode error for message {hex(message.arbitration_id)}: {decode_error}")
                        
                        # Send raw message data
                        import json
                        message_data = {
                            'timestamp': message.timestamp,
                            'can_id': hex(message.arbitration_id),
                            'data': message.data.hex(),
                            'raw': True,
                            'error': str(decode_error)
                        }
                        
                        message_str = json.dumps(message_data)
                        await self._broadcast_message(message_str)
                        
                        lines_processed += 1
                        
                        # Log progress every configured interval
                        if lines_processed % log_interval == 0:
                            logger.info(f"CAN bus processing: {lines_processed} messages processed")
                    
                    except Exception as decode_error:
                        # Other decode errors - log and send raw
                        logger.warning(f"Unexpected error decoding message {hex(message.arbitration_id)}: {decode_error}")
                        
                        # Send raw message data
                        import json
                        message_data = {
                            'timestamp': message.timestamp,
                            'can_id': hex(message.arbitration_id),
                            'data': message.data.hex(),
                            'raw': True,
                            'error': str(decode_error)
                        }
                        
                        message_str = json.dumps(message_data)
                        await self._broadcast_message(message_str)
                        
                        lines_processed += 1
                        
                        # Log progress every configured interval
                        if lines_processed % log_interval == 0:
                            logger.info(f"CAN bus processing: {lines_processed} messages processed")
                
                else:
                    # No message received within timeout
                    logger.debug("No CAN messages received (timeout)")
                    
            except Exception as e:
                logger.exception("Error reading from CAN bus")
                
                # If CAN bus error, try to recover
                logger.info(f"CAN bus error, entering recovery mode for {recovery_cycle_seconds} seconds...")
                logger.info("System status: HEALTHY - WebSocket server running, waiting for CAN bus activity...")
                await asyncio.sleep(recovery_cycle_seconds)
                
                # Try to reinitialize CAN bus
                logger.info("Attempting to reinitialize CAN bus...")
                if not self._initialize_can_bus():
                    logger.error("Failed to reinitialize CAN bus, continuing recovery...")
                    await asyncio.sleep(recovery_cycle_seconds)
        
        logger.info("CAN message reader stopped")
    
    async def start(self):
        """Start the WebSocket server"""
        self.running = True
        
        # Initialize CAN bus
        if not self._initialize_can_bus():
            logger.error("Failed to initialize CAN bus. Exiting.")
            return
        
        # Start the WebSocket server
        self.server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5
        )
        
        logger.info(f"CAN Bus WebSocket Server started on {self.host}:{self.port}")
        logger.info("Waiting for client connections...")
        logger.info(f"Server socket bound to: {self.server.sockets[0].getsockname()}")
        logger.info("=== WEB SOCKET SERVER IS READY ===")
        
        # Start CAN message reader
        can_task = asyncio.create_task(self._read_can_messages())
        
        try:
            # Wait for either CAN reader to finish or shutdown signal
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())
            await asyncio.wait(
                [can_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )
        except Exception as e:
            logger.exception("Server error")
        finally:
            self.running = False
            await self.shutdown()

async def main():
    """Main entry point"""
    # Get configuration from environment variables
    host = os.getenv("WS_HOST", "0.0.0.0")
    port = int(os.getenv("WS_PORT", "8088"))
    
    server = CANBusWebSocketServer(host, port)
    
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Fatal error")
        await server.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 