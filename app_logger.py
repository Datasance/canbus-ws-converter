#!/usr/bin/env python3

import asyncio
import logging
import signal
import sys
import os
import platform
import can
import numpy as np
from asammdf import MDF, Signal
import tarfile
import shutil
from datetime import datetime
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class CANLogger:
    def __init__(self):
        # Configuration from environment variables
        self.device_name = os.getenv("DEVICE_NAME", "device0")
        self.can_device = os.getenv("CAN_DEVICE", "can0")
        self.log_dir = os.getenv("LOG_DIR", "/logs")
        self.max_file_size_mb = int(os.getenv("MAX_FILE_SIZE_MB", "200"))
        self.batch_size = int(os.getenv("BATCH_SIZE", "2000"))
        
        # Internal state
        self.can_bus = None
        self.running = False
        self.shutdown_event = asyncio.Event()
        
        # Logging state
        self.current_mdf = None
        self.current_log_file = None
        self.batch_timestamps = []
        self.batch_can_ids = []
        self.batch_data_bytes = []
        self.messages_processed = 0
        
        # Performance optimization: cache disk space info
        self.last_disk_check = 0
        self.disk_check_interval = 1200  # Check disk space every 1200 seconds
        self.disk_space_ok = True
        
        # Performance optimization: cache file size info
        self.last_file_size_check = 0
        self.file_size_check_interval = 600  # Check file size every 600 seconds
        self.current_file_size_mb = 0
        
        # Performance monitoring
        self.last_performance_log = 0
        self.performance_log_interval = 600  # Log performance every 600 seconds
        
        # Ensure log directory exists
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Setup signal handlers
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        if platform.system() != 'Windows':
            try:
                loop = asyncio.get_event_loop()
                loop.add_signal_handler(signal.SIGTERM, self._signal_handler)
                loop.add_signal_handler(signal.SIGINT, self._signal_handler)
            except NotImplementedError:
                signal.signal(signal.SIGTERM, self._signal_handler)
                signal.signal(signal.SIGINT, self._signal_handler)
        else:
            signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum=None, frame=None):
        logger.info(f"Received shutdown signal, initiating graceful shutdown...")
        self.shutdown_event.set()
    
    def _initialize_can_bus(self):
        try:
            logger.info(f"Initializing CAN bus: {self.can_device}")
            self.can_bus = can.interface.Bus(self.can_device, interface='socketcan')
            logger.info("CAN bus initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize CAN bus: {e}")
            return False
    
    def _check_disk_space(self):
        """Optimized disk space checking with caching"""
        current_time = time.time()
        
        # Only check disk space periodically
        if current_time - self.last_disk_check < self.disk_check_interval:
            return self.disk_space_ok
        
        self.last_disk_check = current_time
        
        try:
            total, used, free = shutil.disk_usage(self.log_dir)
            free_mb = free / (1024 * 1024)
            
            if free_mb < 1024:
                logger.warning(f"Low disk space: {free_mb:.1f}MB free")
                
                # Only do cleanup if we're really low on space
                if free_mb < 500:
                    archive_files = []
                    for file in os.listdir(self.log_dir):
                        if file.endswith('.tar.bz2'):
                            file_path = os.path.join(self.log_dir, file)
                            archive_files.append((file_path, os.path.getmtime(file_path)))
                    
                    archive_files.sort(key=lambda x: x[1])
                    
                    deleted_count = 0
                    for file_path, _ in archive_files:
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                            logger.info(f"Deleted old archive: {os.path.basename(file_path)}")
                            
                            total, used, free = shutil.disk_usage(self.log_dir)
                            free_mb = free / (1024 * 1024)
                            if free_mb >= 2048:
                                break
                        except Exception as e:
                            logger.error(f"Failed to delete {file_path}: {e}")
                    
                    if deleted_count > 0:
                        logger.info(f"Deleted {deleted_count} old archives to free space")
                
                total, used, free = shutil.disk_usage(self.log_dir)
                free_mb = free / (1024 * 1024)
                if free_mb < 100:
                    logger.error(f"Critical: Only {free_mb:.1f}MB free space available")
                    self.disk_space_ok = False
                    return False
            
            self.disk_space_ok = True
            return True
        except Exception as e:
            logger.error(f"Error checking disk space: {e}")
            self.disk_space_ok = False
            return False
    
    def _create_new_log_file(self):
        try:
            if self.current_mdf:
                self.current_mdf.close()
            
            self.current_log_file = os.path.join(self.log_dir, f"{self.device_name}_log.mf4")
            
            # Check if existing file exists and handle it based on strategy
            if os.path.exists(self.current_log_file):
                file_size_mb = os.path.getsize(self.current_log_file) / (1024 * 1024)
                logger.info(f"Found existing log file: {self.current_log_file} ({file_size_mb:.1f}MB)")
                
                # Archive the existing file
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                archive_name = f"{self.device_name}_{timestamp}.mf4.tar.bz2"
                archive_path = os.path.join(self.log_dir, archive_name)
                
                with tarfile.open(archive_path, 'w:bz2') as tar:
                    tar.add(self.current_log_file, arcname=os.path.basename(self.current_log_file))
                
                os.remove(self.current_log_file)
                logger.info(f"Archived existing file as: {archive_name}")
            
            # Create new MDF file
            self.current_mdf = MDF()
            
            self.batch_timestamps = []
            self.batch_can_ids = []
            self.batch_data_bytes = []
            self.current_file_size_mb = 0
            self.last_file_size_check = 0
            
            logger.info(f"Created new log file: {self.current_log_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create new log file: {e}")
            return False
    
    def _archive_current_file(self):
        if not self.current_log_file or not os.path.exists(self.current_log_file):
            return
        
        try:
            if self.current_mdf:
                self.current_mdf.close()
                self.current_mdf = None
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{self.device_name}_{timestamp}.mf4.tar.bz2"
            archive_path = os.path.join(self.log_dir, archive_name)
            
            with tarfile.open(archive_path, 'w:bz2') as tar:
                tar.add(self.current_log_file, arcname=os.path.basename(self.current_log_file))
            
            os.remove(self.current_log_file)
            logger.info(f"Archived: {archive_name}")
            
        except Exception as e:
            logger.error(f"Failed to archive current file: {e}")
    
    def _write_batch_to_mdf(self):
        if not self.batch_timestamps:
            return
        
        try:
            # Pre-allocate numpy arrays for better performance
            batch_size = len(self.batch_timestamps)
            timestamps_np = np.array(self.batch_timestamps, dtype=np.float64)
            can_ids_np = np.array(self.batch_can_ids, dtype=np.uint32)
            
            # Find maximum data length once
            max_data_length = max(len(d) for d in self.batch_data_bytes) if self.batch_data_bytes else 0
            
            signals = []
            
            # Create CAN ID signal
            can_id_sig = Signal(
                samples=can_ids_np,
                timestamps=timestamps_np,
                name="CAN_ID",
                unit="",
            )
            signals.append(can_id_sig)
            
            # Pre-allocate data array for better performance
            if max_data_length > 0:
                data_array = np.zeros((batch_size, max_data_length), dtype=np.uint8)
                
                # Fill data array efficiently
                for i, data_bytes in enumerate(self.batch_data_bytes):
                    data_length = len(data_bytes)
                    if data_length > 0:
                        data_array[i, :data_length] = data_bytes
                
                # Create byte signals efficiently
                for i in range(max_data_length):
                    sig = Signal(
                        samples=data_array[:, i],
                        timestamps=timestamps_np,
                        name=f"Byte_{i}",
                        unit="",
                    )
                    signals.append(sig)
            
            self.current_mdf.append(signals)
            self.current_mdf.save(self.current_log_file, overwrite=True)
            
            # Update file size cache
            self.current_file_size_mb = os.path.getsize(self.current_log_file) / (1024 * 1024)
            self.last_file_size_check = time.time()
            
            self.batch_timestamps = []
            self.batch_can_ids = []
            self.batch_data_bytes = []
            
            logger.debug(f"Wrote batch of {batch_size} messages to MDF4")
            
        except Exception as e:
            logger.error(f"Failed to write batch to MDF4: {e}")
    
    def _check_file_size_and_rotate(self):
        if not self.current_log_file or not os.path.exists(self.current_log_file):
            return
        
        current_time = time.time()
        
        # Only check file size periodically
        if current_time - self.last_file_size_check < self.file_size_check_interval:
            return
        
        self.last_file_size_check = current_time
        
        try:
            # Use cached file size if available, otherwise check
            if self.current_file_size_mb == 0:
                self.current_file_size_mb = os.path.getsize(self.current_log_file) / (1024 * 1024)
            
            if self.current_file_size_mb >= self.max_file_size_mb:
                logger.info(f"File size {self.current_file_size_mb:.1f}MB exceeds limit {self.max_file_size_mb}MB, rotating...")
                
                self._write_batch_to_mdf()
                self._archive_current_file()
                self._create_new_log_file()
                
        except Exception as e:
            logger.error(f"Error checking file size: {e}")
    
    async def _read_can_messages(self):
        recovery_cycle_seconds = int(os.getenv("RECOVERY_CYCLE_SECONDS", "60"))
        log_interval = int(os.getenv("LOG_INTERVAL", "10000"))
        
        logger.info("CAN message logger started - waiting for data...")
        
        while self.running:
            if self.shutdown_event.is_set():
                logger.info("Shutdown signal received, stopping CAN logger")
                break
            
            # Check disk space less frequently
            if not self._check_disk_space():
                logger.error("Insufficient disk space, stopping logging")
                await asyncio.sleep(30)
                continue
            
            try:
                loop = asyncio.get_event_loop()
                message = await loop.run_in_executor(None, lambda: self.can_bus.recv(timeout=1.0))
                
                if message:
                    self.batch_timestamps.append(message.timestamp)
                    self.batch_can_ids.append(message.arbitration_id)
                    self.batch_data_bytes.append(list(message.data))
                    
                    self.messages_processed += 1
                    
                    if len(self.batch_timestamps) >= self.batch_size:
                        self._write_batch_to_mdf()
                        self._check_file_size_and_rotate()
                    
                    # Performance monitoring
                    current_time = time.time()
                    if current_time - self.last_performance_log >= self.performance_log_interval:
                        self.last_performance_log = current_time
                        messages_per_second = self.messages_processed / (current_time - self.last_performance_log + self.performance_log_interval)
                        logger.info(f"Performance: {self.messages_processed} messages processed, ~{messages_per_second:.1f} msg/s")
                    
                    if self.messages_processed % log_interval == 0:
                        logger.info(f"CAN logging: {self.messages_processed} messages processed")
                
                else:
                    logger.debug("No CAN messages received (timeout)")
                    
            except Exception as e:
                logger.exception("Error reading from CAN bus")
                
                logger.info(f"CAN bus error, entering recovery mode for {recovery_cycle_seconds} seconds...")
                await asyncio.sleep(recovery_cycle_seconds)
                
                logger.info("Attempting to reinitialize CAN bus...")
                if not self._initialize_can_bus():
                    logger.error("Failed to reinitialize CAN bus, continuing recovery...")
                    await asyncio.sleep(recovery_cycle_seconds)
        
        logger.info("CAN message logger stopped")
    
    async def shutdown(self):
        if not self.running:
            return
        
        self.running = False
        logger.info("Shutting down CAN logger...")
        
        self._write_batch_to_mdf()
        self._archive_current_file()
        
        if self.can_bus:
            self.can_bus.shutdown()
            logger.info("CAN bus connection closed")
        
        logger.info("Logger shutdown complete")
    
    async def start(self):
        self.running = True
        
        if not self._initialize_can_bus():
            logger.error("Failed to initialize CAN bus. Exiting.")
            return
        
        if not self._create_new_log_file():
            logger.error("Failed to create initial log file. Exiting.")
            return
        
        logger.info(f"CAN Logger started")
        logger.info(f"Configuration:")
        logger.info(f"  Device Name: {self.device_name}")
        logger.info(f"  CAN Device: {self.can_device}")
        logger.info(f"  Log Directory: {self.log_dir}")
        logger.info(f"  Max File Size: {self.max_file_size_mb}MB")
        logger.info(f"  Batch Size: {self.batch_size}")
        logger.info("=== CAN LOGGER IS READY ===")
        
        can_task = asyncio.create_task(self._read_can_messages())
        
        try:
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())
            await asyncio.wait(
                [can_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )
        except Exception as e:
            logger.exception("Logger error")
        finally:
            self.running = False
            await self.shutdown()

async def main():
    logger = CANLogger()
    
    try:
        await logger.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Fatal error")
        await logger.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 