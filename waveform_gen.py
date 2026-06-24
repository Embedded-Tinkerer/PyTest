from instrument import BaseInstrument

class WaveformGenerator(BaseInstrument):
    """Driver for SCPI-compliant Waveform/Pulse Generators (e.g. Keysight 33500B)"""
    
    def __init__(self, resource_address, name="WaveformGen"):
        super().__init__(resource_address, name)

    def configure_pulse_trigger(self, width, period, delay=0):
        """Sets the generator to output a precise pulse train to trigger other instruments."""
        self.logger.info(f"Configuring Pulse: W={width}s, Per={period}s, Del={delay}s")
        self.write("FUNC PULS")
        self.write(f"PULS:WIDT {width}")
        self.write(f"PULS:PER {period}")
        self.write(f"PULS:DEL {delay}")
        
        # Configure the Sync output to trigger the rising edge
        self.write("OUTP:SYNC ON")
        self.write("OUTP:SYNC:MODE NORM") 

    def fire_single_pulse(self):
        """Arms and fires one single cycle (Burst)."""
        self.write("BURS:MODE TRIG")
        self.write("BURS:NCYC 1")
        self.write("BURS:STAT ON")
        self.write("*TRG") # Send software trigger to start the burst