import time
from instrument import BaseInstrument

class PowerSupply(BaseInstrument):
    """
    Polymorphic driver that automatically detects the model of the connected supply.
    Supports standard multi-channel SCPI power supplies as well as single-channel 
    Keithley SMUs (2400, 2410, 2420, 2425, 2430, 2440) using the same high-level API.
    """
    
    def __init__(self, resource_address, name="DC_Supply"):
        super().__init__(resource_address, name)
        self.is_keithley = False
        self.idn_string = ""

    def connect(self, resource_manager):
        """Connects and automatically queries the instrument to determine its command set."""
        connected = super().connect(resource_manager)
        if connected:
            try:
                # Query identity to check if it's an SMU or a typical power supply
                self.idn_string = self.query("*IDN?").upper()
                self.logger.info(f"Connected to: {self.idn_string.strip()}")
                
                # Check for Keithley 24xx Series SMUs
                if "KEITHLEY" in self.idn_string and any(model in self.idn_string for model in ["2400", "2410", "2420", "2425", "2430", "2440"]):
                    self.is_keithley = True
                    self.logger.info("Keithley 2400 Series SMU detected. Activating single-channel SMU mode.")
                    # Pre-configure Keithley into standard voltage-source mode
                    self.write(":SOUR:FUNC VOLT")
                    self.write(":SOUR:VOLT:MODE FIXED")
                    self.write(":SENS:FUNC 'CURR'") # Prepare current sense
                    self.write(":SENS:CURR:RANG:AUTO ON")
                else:
                    self.is_keithley = False
                    self.logger.info("Standard multi-channel SCPI Power Supply detected.")
            except Exception as e:
                self.logger.error(f"Error identification sequence failed: {e}")
                self.is_keithley = False
        return connected

    def reset(self):
        """Resets the instrument to default safe states."""
        self.logger.info(f"Resetting {self.name}...")
        self.write("*RST")
        time.sleep(0.5)
        if self.is_keithley:
            # Re-verify safe voltage sourcing configuration on reset
            self.write(":SOUR:FUNC VOLT")
            self.write(":SOUR:VOLT:MODE FIXED")
            self.write(":SENS:FUNC 'CURR'")
            self.write(":SENS:CURR:RANG:AUTO ON")

    def configure_channel(self, channel, voltage, compliance_current):
        """
        Configures target output voltage and current compliance.
        Translates channel arguments dynamically depending on detected hardware.
        """
        self.logger.info(f"Configuring output: V={voltage}V, Compliance={compliance_current}A")
        if self.is_keithley:
            # Keithley Single Channel SMU commands
            self.write(f":SOUR:VOLT:LEV {voltage}")
            self.write(f":SENS:CURR:PROT {compliance_current}")
        else:
            # Standard multi-channel SCPI Power Supply (Keysight / Rigol / etc)
            try:
                # Try selecting the active channel first
                self.write(f"INST:SEL OUT{channel}")
                self.write(f"SOUR:VOLT {voltage}")
                self.write(f"SOUR:CURR {compliance_current}")
            except Exception:
                # Fallback to direct channel parameter addressing
                self.write(f"APPL CH{channel}, {voltage}, {compliance_current}")

    def output_on(self, channel):
        """Enables the output state of the supply."""
        self.logger.info(f"Enabling output state on channel {channel}")
        if self.is_keithley:
            self.write(":OUTP ON")
        else:
            try:
                self.write(f"OUTP ON, (@{channel})")
            except Exception:
                self.write(f"OUTP:CH{channel} ON")

    def output_off(self, channel):
        """Safely disables the output state."""
        self.logger.info(f"Disabling output state on channel {channel}")
        if self.is_keithley:
            self.write(":OUTP OFF")
        else:
            try:
                self.write(f"OUTP OFF, (@{channel})")
            except Exception:
                self.write(f"OUTP:CH{channel} OFF")

    def measure_voltage(self, channel):
        """Measures and returns the instantaneous voltage at the terminals."""
        if self.is_keithley:
            # Keithley returns a comma-separated string: [voltage, current, resistance, timestamp, status]
            reading = self.query(":READ?")
            try:
                parts = reading.split(",")
                if len(parts) >= 1:
                    return float(parts[0])
            except (ValueError, IndexError):
                self.logger.error(f"Failed to parse Keithley voltage reading: {reading}")
            return 0.0
        else:
            try:
                return float(self.query(f"MEAS:VOLT? (@{channel})"))
            except Exception:
                return float(self.query(f"MEAS:VOLT? CH{channel}"))

    def measure_current(self, channel):
        """Measures and returns the current being drawn by the load."""
        if self.is_keithley:
            # Keithley returns a comma-separated string: [voltage, current, resistance, timestamp, status]
            reading = self.query(":READ?")
            try:
                parts = reading.split(",")
                if len(parts) >= 2:
                    return float(parts[1])
            except (ValueError, IndexError):
                self.logger.error(f"Failed to parse Keithley current reading: {reading}")
            return 0.0
        else:
            try:
                return float(self.query(f"MEAS:CURR? (@{channel})"))
            except Exception:
                return float(self.query(f"MEAS:CURR? CH{channel}"))

    def emergency_shutdown(self):
        """Force immediately turns off terminal generation without safety checks."""
        self.logger.warn("EMERGENCY SHUTDOWN INITIATED")
        if self.is_keithley:
            self.write(":OUTP OFF")
        else:
            # Send general system shutdown signals
            self.write("OUTP:ALL OFF")
            self.write("OUTP OFF")