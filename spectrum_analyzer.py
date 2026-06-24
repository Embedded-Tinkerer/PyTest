from instrument import BaseInstrument

class SignalAnalyzer(BaseInstrument):
    """
    Specific driver for Keysight X-Series Signal Analyzers (e.g., N9030B PXA).
    """
    def __init__(self, resource_address, name="N9030B_PXA"):
        super().__init__(resource_address, name)

    def measure_peak_power(self, center_freq_hz, span_hz=5e6, rbw_hz=100e3):
        """
        Centers the analyzer on a specific frequency, triggers a single sweep,
        and returns the absolute peak power found within the span.
        """
        self.logger.info(f"Measuring peak at {center_freq_hz / 1e9:.3f} GHz")
        
        # 1. Setup the frequency window
        self.write(f"SENS:FREQ:CENT {center_freq_hz}")
        self.write(f"SENS:FREQ:SPAN {span_hz}")
        self.write(f"SENS:BAND {rbw_hz}")
        
        # 2. Trigger a single clean sweep
        self.write("INIT:CONT OFF")
        self.query("INIT:IMM; *OPC?")
        
        # 3. Command the marker to snap to the highest peak on screen
        self.write("CALC:MARK1:MAX")
        
        # 4. Read the Y-axis value (Power in dBm) of that marker
        power_str = self.query("CALC:MARK1:Y?")
        
        if power_str:
            try:
                return float(power_str)
            except ValueError:
                self.logger.error("Failed to parse marker power.")
                return -999.0
        return -999.0