from instrument import BaseInstrument
import time

class VectorNetworkAnalyzer(BaseInstrument):
    """
    Specific driver for Keysight PNA/PNA-X Series Network Analyzers.
    Inherits all connection and base SCPI logic from BaseInstrument.
    """
    def __init__(self, resource_address, name="PNA-X"):
        super().__init__(resource_address, name)
        # vna.py - Add this method to your VectorNetworkAnalyzer class

    def perform_compression_power_cal(self, port=1):
        """
        Performs a Source Power Calibration for Gain Compression testing.
        This calibrates absolute power accuracy at the specified port.
        """
        self.logger.info(f"Initiating Power Calibration on Port {port}")
        
        # 1. Select the port for source power calibration
        self.write(f"SENS1:CORR:POWER:COLL:SEL PORT{port}")
        
        # 2. Acquire the power calibration standards (this triggers the PNA-X power cal dialog)
        # Note: Ensure the PNA-X is physically connected to a power sensor if required,
        # or ensure 'User Characterization' is properly defined.
        self.write("SENS1:CORR:POWER:COLL:ACQ")
        
        # 3. Wait for the calibration to complete
        self.write("*OPC?") 
        
        # 4. Enable power correction for the measurement
        self.write("SENS1:CORR:POWER:STAT ON")
        self.logger.info("Power Calibration Complete and Enabled.")
        
    def setup_2port_s_parameters(self, start_freq, stop_freq, points, power_dbm):
        """
        Deletes old traces and sets up four new traces for a full 2-port 
        S-parameter characterization (S11, S21, S12, S22).
        """
        self.logger.info(f"Configuring 2-Port S-Parameters: {start_freq} to {stop_freq}, {points} points.")
        
        # 1. Delete any existing traces on Channel 1 to clear the memory allocation
        self.write("CALC1:PAR:DEL:ALL")
        
        # 2. Define the 4 custom extended traces on Channel 1
        self.write("CALC1:PAR:DEF:EXT 'T_S11', 'S11'")
        self.write("CALC1:PAR:DEF:EXT 'T_S21', 'S21'")
        self.write("CALC1:PAR:DEF:EXT 'T_S12', 'S12'")
        self.write("CALC1:PAR:DEF:EXT 'T_S22', 'S22'")
        
        # 3. Feed the traces to Window 1 so they display on the PNA-X screen (highly useful for bench monitoring)
        self.write("DISP:WIND1:STAT ON")
        self.write("DISP:WIND1:TRAC1:FEED 'T_S11'")
        self.write("DISP:WIND1:TRAC2:FEED 'T_S21'")
        self.write("DISP:WIND1:TRAC3:FEED 'T_S12'")
        self.write("DISP:WIND1:TRAC4:FEED 'T_S22'")
        
        # 4. Set global channel parameters
        self.write(f"SENS1:FREQ:STAR {start_freq}")
        self.write(f"SENS1:FREQ:STOP {stop_freq}")
        self.write(f"SENS1:SWE:POIN {points}")
        self.write(f"SOUR1:POW {power_dbm}")

    def rf_on(self):
        """Turns the RF power ON."""
        self.write("OUTP ON")
        self.logger.info("RF Output: ON")

    def rf_off(self):
        """Turns the RF power OFF."""
        self.write("OUTP OFF")
        self.logger.info("RF Output: OFF")
    def setup_unratioed_power_measure(self):
        """
        Configures the PNA-X to read absolute Output Power (dBm) directly 
        from the Port 2 receiver ('B') while driving Port 1.
        """
        self.logger.info("Configuring VNA for Unratioed Pout (Receiver B) measurements.")
        
        # 1. Clear the old S-parameter traces
        self.write("CALC1:PAR:DEL:ALL")
        
        # 2. Define a new trace looking at Receiver B, Source Port 1
        self.write("CALC1:PAR:DEF:EXT 'T_POUT', 'B,1'")
        
        # 3. Display it on the screen
        self.write("DISP:WIND1:STAT ON")
        self.write("DISP:WIND1:TRAC1:FEED 'T_POUT'")
        
        # 4. CRITICAL: Set format to Log Magnitude to get standard dBm instead of linear Watts/Volts
        self.write("CALC1:FORM MLOG")
        
        # 5. Select the trace so subsequent data queries pull from it
        self.write("CALC1:PAR:SEL 'T_POUT'")
    def set_cw_frequency(self, freq_hz):
        """Sets the VNA to a single CW frequency."""
        self.write(f"SENS1:SWEEP:TYPE CW")
        self.write(f"SENS1:FREQ:CW {freq_hz}")
    def measure_single_point(self):
        """
        Triggers a single CW sweep and returns the first data point.
        Perfect for software-timed power sweeps.
        """
        self.write("INIT1:CONT OFF")
        self.query("INIT1:IMM; *OPC?")
        
        raw_data = self.query("CALC1:DATA? FDATA")
        
        if raw_data:
            # The PNA returns an array, but since we set SWE:POIN to 1 for a CW sweep,
            # we just grab the first (and only) float in the list.
            pout_dbm = float(raw_data.split(',')[0])
            return pout_dbm
        else:
            self.logger.error("Failed to read Pout data point.")
            return -999.0    
    def set_power_level(self, power_dbm):
        """Changes the source power level dynamically."""
        self.write(f"SOUR1:POW {power_dbm}")
    def measure_analog_input(self, port=1):
        """
        Reads the instantaneous DC voltage present on the rear panel Analog In port.
        Used for hardware-synchronized telemetry (e.g., Drain Current).
        """
        # The SCPI command to query the Auxiliary/Analog Input voltages
        volts_str = self.query(f"CONT:AUX:INP{port}:VOLT?")
        
        if volts_str:
            try:
                return float(volts_str)
            except ValueError:
                self.logger.error("Failed to parse Analog Input voltage.")
                return 0.0
        return 0.0
    def measure_2port(self):
        """
        Triggers a single sweep for all traces simultaneously, then loops through
        each trace to extract the parsed float data arrays.
        Returns a dictionary containing lists for S11, S21, S12, and S22.
        """
        results = {"S11": [], "S21": [], "S12": [], "S22": []}
        
        self.logger.info("Triggering unified 2-port sweep...")
        
        # Disable continuous sweeping to enforce a controlled trigger
        self.write("INIT1:CONT OFF")
        
        # Trigger a single sweep and halt Python execution until the VNA hardware cycles completely
        self.query("INIT1:IMM; *OPC?")
        
        # Loop through each defined trace name, select it, and read out the formatted array
        trace_mapping = {
            "S11": "T_S11",
            "S21": "T_S21",
            "S12": "T_S12",
            "S22": "T_S22"
        }
        
        for param, trace_name in trace_mapping.items():
            # Select the specific trace to make it the focus of data queries
            self.write(f"CALC1:PAR:SEL '{trace_name}'")
            
            # Query the Formatted Data
            raw_data = self.query("CALC1:DATA? FDATA")
            
            if raw_data:
                # Parse the comma-separated string straight into a list of floats
                parsed_data = [float(x) for x in raw_data.split(',')]
                results[param] = parsed_data
                self.logger.info(f"Retrieved {len(parsed_data)} points for {param}")
            else:
                self.logger.error(f"Failed to retrieve data for parameter: {param}")
                results[param] = []
                
        return results
        
import pyvisa

if __name__ == "__main__":
    rm = pyvisa.ResourceManager()
    
    # Enforce your known physical GPIB address
    pna_address = "GPIB0::17::INSTR" 
    
    my_vna = VectorNetworkAnalyzer(pna_address)
    
    if my_vna.connect(rm):
        # Flush previous instrument states/errors
        my_vna.reset()
        
        # Setup the full 2-port matrix structure
        my_vna.setup_2port_s_parameters(
            start_freq="1 GHz", 
            stop_freq="10 GHz", 
            points=201, 
            power_dbm=-20.0
        )
        
        # Execute measurement sequence safely
        my_vna.rf_on()
        s_param_matrix = my_vna.measure_2port()
        my_vna.rf_off()
        
        # Display the verification log
        print("\n--- 2-Port S-Parameter Verification ---")
        for param, data in s_param_matrix.items():
            if data:
                print(f"{param} -> Total Points: {len(data)} | First Value: {data[0]:.4f} dB")
            else:
                print(f"{param} -> Data Capture Failed.")
                
        my_vna.disconnect()