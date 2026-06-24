from instrument import BaseInstrument

class Oscilloscope(BaseInstrument):
    """Driver for SCPI-compliant Digital Oscilloscopes (Keysight Infiniium / LeCroy)"""
    
    def __init__(self, resource_address, name="Scope"):
        super().__init__(resource_address, name)

    def configure_trigger(self, channel, level, edge="POS"):
        """Configures the scope trigger source, level, and edge direction."""
        chan_str = str(channel).strip().upper()
        
        # Dynamically build channel header based on input type
        if chan_str.isdigit():
            source = f"CHAN{chan_str}"
        elif "CHAN" in chan_str:
            source = chan_str
        else:
            source = chan_str # e.g. "AUX", "EXT"
            
        self.logger.info(f"Setting Scope Trigger: {source}, {level}V, {edge} edge")
        self.write(f"TRIG:EDGE:SOUR {source}")
        self.write(f"TRIG:EDGE:LEV {level}")
        self.write(f"TRIG:EDGE:SLOP {edge}")
        self.write("TRIG:MODE EDGE")

    def set_timebase(self, period):
        """Calculates horizontal scale based on the master period."""
        scale = period / 10.0
        self.write(f"TIM:SCAL {scale}")

    def measure_pulse_top(self, channel):
        """Measures the 'Top' voltage of a captured pulse, ignoring the baseline."""
        chan_str = str(channel).strip().upper()
        target = f"CHAN{chan_str}" if chan_str.isdigit() else chan_str
        
        self.write(f"MEAS:VTOP {target}")
        val_str = self.query(f"MEAS:VTOP? {target}")
        if val_str:
            try:
                return float(val_str)
            except ValueError:
                self.logger.error("Failed to parse scope voltage.")
                return 0.0
        return 0.0