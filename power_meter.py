from instrument import BaseInstrument
import time

class PowerMeter(BaseInstrument):
    """
    Driver for SCPI-compliant Dual-Channel Power Meters.
    Compatible with Keysight N1914A, E4416A, and similar models.
    """
    
    def __init__(self, resource_address, name="Power_Meter"):
        super().__init__(resource_address, name)

    def reset(self):
        """Resets the power meter to default state."""
        self.logger.info(f"Resetting {self.name} to safe default state.")
        self.write("*RST")
        self.write("*CLS")

    def set_frequency(self, channel, freq_hz):
        """
        Sets the measurement frequency for a specific sensor channel 
        to apply the correct calibration factor.
        """
        # Ensure channel is 1 (A) or 2 (B)
        self.write(f"SENS{channel}:FREQ {freq_hz}")

    def measure_power(self, channel):
        """
        Triggers and fetches the absolute power reading from the specified channel.
        Returns the power in dBm.
        """
        try:
            # INITiate and FETCh is generally faster and more reliable than MEASure?
            val_str = self.query(f"MEAS{channel}:POW:AC?").strip()
            return float(val_str)
        except Exception as e:
            self.logger.error(f"Failed to measure power on Channel {channel}: {str(e)}")
            return -999.0

    def zero_and_calibrate(self, channel):
        """
        Initiates the internal zeroing and calibration routine for the sensor.
        Note: This can take several seconds to complete.
        """
        self.logger.info(f"Zeroing and Calibrating Sensor on Channel {channel}...")
        self.write(f"CAL{channel}:ZERO:AUTO ONCE")
        self.query("*OPC?") # Block until zeroing is complete
        self.logger.info(f"Sensor {channel} Zeroing Complete.")