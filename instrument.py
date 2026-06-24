import pyvisa
import logging

class BaseInstrument:
    """
    The foundational Hardware Abstraction Layer for all GPIB/LAN equipment.
    """
    def __init__(self, resource_address, name="Generic_Instrument"):
        self.resource_address = resource_address
        self.name = name
        self.device = None
        
        # Configure logging for this specific instrument
        self.logger = logging.getLogger(self.name)
        if not self.logger.hasHandlers():
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def connect(self, resource_manager):
        """Establishes the physical VISA connection."""
        try:
            self.device = resource_manager.open_resource(self.resource_address)
            self.device.timeout = 5000  # 5000 ms timeout is standard for RF queries
            
            # Send the universal SCPI identification command
            idn = self.query("*IDN?")
            if idn:
                self.logger.info(f"Successfully connected! ID: {idn.strip()}")
                return True
            return False
            
        except pyvisa.VisaIOError as e:
            self.logger.error(f"Connection failed: {e}")
            return False

    def write(self, command):
        """Sends a SCPI command without expecting a response (e.g., setting a voltage)."""
        if self.device:
            self.logger.debug(f"Write: {command}")
            self.device.write(command)
        else:
            self.logger.warning(f"Cannot execute '{command}'. Instrument is disconnected.")

    def query(self, command):
        """Sends a SCPI command and waits for the hardware to return a string."""
        if self.device:
            self.logger.debug(f"Query: {command}")
            try:
                return self.device.query(command)
            except pyvisa.VisaIOError as e:
                self.logger.error(f"Timeout or failure querying '{command}': {e}")
                return None
        else:
            self.logger.warning(f"Cannot query '{command}'. Instrument is disconnected.")
            return None

    def reset(self):
        """Standard SCPI reset protocol."""
        self.logger.info("Sending *RST and *CLS to reset instrument state.")
        self.write("*RST")
        self.write("*CLS")

    def disconnect(self):
        """Safely closes the port so it isn't locked out for the next test."""
        if self.device:
            self.device.close()
            self.device = None
            self.logger.info("Connection closed.")