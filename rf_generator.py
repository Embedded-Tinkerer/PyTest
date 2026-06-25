from instrument import BaseInstrument

class RFGenerator(BaseInstrument):
    """
    Driver for SCPI-compliant RF Signal Generators and Microwave Synthesizers.
    Fully compatible with Keysight/Agilent E8257D (PSG) and Hittite T2240 models.
    """
    
    def __init__(self, resource_address, name="RF_Generator"):
        super().__init__(resource_address, name)

    def reset(self):
        """
        Resets the signal generator to a safe, default factory state.
        Disables RF output and clears any active status registers.
        """
        self.logger.info(f"Resetting {self.name} to safe default state.")
        self.write("*RST")
        self.write("*CLS")
        self.rf_off()

    def set_frequency(self, freq_hz):
        """
        Sets the Continuous Wave (CW) RF output frequency.
        Uses the standard SCPI short-form to ensure Hittite/Agilent cross-compatibility.
        
        Args:
            freq_hz (float/int): Target frequency in Hertz.
        """
        self.logger.info(f"Setting {self.name} Frequency to {freq_hz / 1e9:.4f} GHz")
        # standard SCPI short-form, accepted by both E8257D and T2240
        self.write(f"FREQ {freq_hz}")

    def set_power(self, power_dbm):
        """
        Sets the absolute RF output power level.
        
        Args:
            power_dbm (float): Target power level in dBm.
        """
        self.logger.info(f"Setting {self.name} Power to {power_dbm} dBm")
        # standard SCPI short-form for amplitude
        self.write(f"POW {power_dbm}")

    def rf_on(self):
        """Enables the RF output stage."""
        self.logger.info(f"Enabling RF Output on {self.name}")
        self.write("OUTP ON")

    def rf_off(self):
        """Safely disables the RF output stage."""
        self.logger.info(f"Disabling RF Output on {self.name}")
        self.write("OUTP OFF")

    def set_modulation(self, enable=False):
        """
        Toggles global modulation (AM/FM/PM/Pulse) on or off.
        Useful when switching between pure CW tests and Pulsed RF tests.
        
        Args:
            enable (bool): True to enable modulation, False for pure CW.
        """
        state = "ON" if enable else "OFF"
        self.logger.info(f"Setting Modulation State to {state} on {self.name}")
        self.write(f"OUTP:MOD {state}")

    def check_errors(self):
        """
        Queries the instrument's error queue.
        Returns a list of error strings, or an empty list if no errors exist.
        """
        errors = []
        while True:
            try:
                # SYST:ERR? returns '+0,"No error"' when the queue is clean
                err_str = self.query("SYST:ERR?").strip()
                if "No error" in err_str or err_str == '+0,"No error"':
                    break
                errors.append(err_str)
                self.logger.error(f"{self.name} Hardware Error: {err_str}")
            except Exception as e:
                self.logger.error(f"Failed to query errors from {self.name}: {str(e)}")
                break
        return errors