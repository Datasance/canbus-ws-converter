import asyncio
import logging
import signal
import sys
import websockets
import os
import platform

# Configure simple logging to stdout only
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class CANBusWebSocketServer:
    """Lightweight CAN Bus to WebSocket server for high-volume streaming"""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8088):
        self.host = host
        self.port = port
        self.clients = set()
        self.running = False
        self.server = None
        self.shutdown_event = asyncio.Event()
        
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
        # Set the shutdown event instead of calling asyncio.create_task
        self.shutdown_event.set()
    
    async def shutdown(self):
        """Gracefully shutdown the server"""
        if not self.running:
            return  # Prevent multiple shutdown calls
        
        self.running = False
        logger.info("Shutting down WebSocket server...")
        
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
        
        # Send raw message to all clients
        disconnected_clients = set()
        for client in self.clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
            except Exception as e:
                logger.error(f"Error sending message to client: {e}")
                disconnected_clients.add(client)
        
        # Clean up disconnected clients
        self.clients -= disconnected_clients
        if disconnected_clients:
            logger.info(f"Removed {len(disconnected_clients)} disconnected clients. Active: {len(self.clients)}")
    
    async def _handle_client(self, websocket):
        """Handle individual client connections"""
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
    
    async def _read_stdin(self):
        """Read CAN bus data from stdin with cyclic recovery mechanism"""
        loop = asyncio.get_event_loop()
        recovery_cycle_seconds = int(os.getenv("RECOVERY_CYCLE_SECONDS", "60"))
        
        logger.info("Stdin reader started - waiting for data...")
        
        while self.running:
            # Check for shutdown signal
            if self.shutdown_event.is_set():
                logger.info("Shutdown signal received, stopping stdin reader")
                break
            
            # Phase 1: Try to read stdin with 5x1sec attempts
            logger.info("Attempting to read stdin data...")
            if await self._try_read_stdin_cycle():
                # Success - continue normal reading
                logger.info("Stdin data stream active - entering continuous reading mode")
                await self._read_stdin_continuously()
            else:
                # Failed - enter hibernate mode
                logger.info(f"CAN bus inactive (engine off/garage mode) - entering hibernate mode for {recovery_cycle_seconds} seconds...")
                logger.info("System status: HEALTHY - WebSocket server running, waiting for CAN bus activity...")
                await asyncio.sleep(recovery_cycle_seconds)
                logger.info("Recovery cycle completed, attempting to read CAN bus data again...")
        
        logger.info("Stdin reader stopped")
    
    async def _try_read_stdin_cycle(self):
        """Try to read from stdin with 5 attempts, 1 second each"""
        loop = asyncio.get_event_loop()
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                # Check for shutdown signal
                if self.shutdown_event.is_set():
                    return False
                
                # Read line from stdin asynchronously with timeout
                try:
                    line = await asyncio.wait_for(
                        loop.run_in_executor(None, sys.stdin.readline),
                        timeout=1.0  # 1 second timeout
                    )
                except asyncio.TimeoutError:
                    # Timeout occurred - no data available
                    line = None
                
                if line:
                    # Success - process the line
                    content_data = line.strip()
                    if content_data:  # Only process non-empty lines
                        await self._broadcast_message(content_data)
                    return True  # Successfully read data
                else:
                    # No data available
                    if attempt < max_retries - 1:  # Not the last attempt
                        logger.info(f"CAN bus check {attempt + 1}/{max_retries} - no activity (engine may be off)")
                        await asyncio.sleep(1)  # Wait before retry
                    else:
                        logger.warning(f"CAN bus health check: No activity detected after {max_retries} attempts (normal when engine is off)")
                        
            except Exception as e:
                logger.exception("Error reading from stdin")
                if attempt < max_retries - 1:  # Not the last attempt
                    await asyncio.sleep(1)  # Wait before retry
                else:
                    logger.error("Stdin health check failed - error after all retry attempts")
        
        return False  # Failed to read after all attempts
    
    async def _read_stdin_continuously(self):
        """Read from stdin continuously until no data is available"""
        loop = asyncio.get_event_loop()
        lines_processed = 0
        
        while self.running:
            try:
                # Check for shutdown signal
                if self.shutdown_event.is_set():
                    return
                
                # Read line from stdin asynchronously with timeout
                try:
                    line = await asyncio.wait_for(
                        loop.run_in_executor(None, sys.stdin.readline),
                        timeout=1.0  # 1 second timeout
                    )
                except asyncio.TimeoutError:
                    # Timeout occurred - no data available
                    line = None
                
                if not line:
                    # No more data available, exit continuous reading
                    logger.info(f"CAN bus data stream ended after processing {lines_processed} lines (engine may have stopped)")
                    return
                
                content_data = line.strip()
                if content_data:  # Only process non-empty lines
                    await self._broadcast_message(content_data)
                    lines_processed += 1
                    
                    # Log progress every 100 lines
                    if lines_processed % 100 == 0:
                        logger.info(f"CAN bus processing: {lines_processed} messages processed")
                    
            except Exception as e:
                logger.exception("Error reading from stdin during continuous mode")
                return  # Exit continuous reading on error
    
    async def start(self):
        """Start the WebSocket server"""
        self.running = True
        
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
        
        # Start stdin reader
        stdin_task = asyncio.create_task(self._read_stdin())
        
        try:
            # Wait for either stdin to finish or shutdown signal
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())
            await asyncio.wait(
                [stdin_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )
        except Exception as e:
            logger.exception("Server error")  # Log full stack trace
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
        logger.exception("Fatal error")  # Log full stack trace
        await server.shutdown()  # Ensure cleanup on unhandled errors
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
