import sys
import pyvisa
import time
import math
import csv
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QCheckBox,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit, QFormLayout, QTabWidget, QComboBox, QGroupBox, QFileDialog, QSplitter, QGridLayout)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg

# Import your validated backend drivers
from vna import VectorNetworkAnalyzer
from power_supply import PowerSupply
from spectrum_analyzer import SignalAnalyzer
from waveform_gen import WaveformGenerator
from oscilloscope import Oscilloscope

from rf_generator import RFGenerator
from power_meter import PowerMeter

# =============================================================================
# BACKGROUND WORKER THREADS (The Controller Layer)
# =============================================================================

class VNACalWorker(QThread):
    log_message = pyqtSignal(str)
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, pna_address, vna_params):
        super().__init__()
        self.pna_address = pna_address
        self.params = vna_params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            vna = VectorNetworkAnalyzer(self.pna_address)
            if vna.connect(rm):
                self.log_message.emit("Configuring VNA for ECal Calibration...")
                vna.reset()
                
                # Delete old traces and set up clean S-parameter traces
                vna.write("CALC1:PAR:DEL:ALL")
                vna.write("CALC1:PAR:EXT 'Meas_S11', 'S11'")
                vna.write("CALC1:PAR:EXT 'Meas_S21', 'S21'")
                vna.write("CALC1:PAR:EXT 'Meas_S12', 'S12'")
                vna.write("CALC1:PAR:EXT 'Meas_S22', 'S22'")
                
                vna.write("CALC1:PAR:SEL 'Meas_S11'")
                
                # Display them on Window 1 so the calibration engine can link to them
                vna.write("DISP:WIND1:STATE ON")
                vna.write("DISP:WIND1:TRAC1:FEED 'Meas_S11'")
                vna.write("DISP:WIND1:TRAC2:FEED 'Meas_S21'")
                vna.write("DISP:WIND1:TRAC3:FEED 'Meas_S12'")
                vna.write("DISP:WIND1:TRAC4:FEED 'Meas_S22'")
                
                # Apply the VNA Channel State
                vna.write(f"SENS1:FREQ:STAR {self.params['f_start']}")
                vna.write(f"SENS1:FREQ:STOP {self.params['f_stop']}")
                vna.write(f"SENS1:SWE:POIN {self.params['points']}")
                vna.write(f"SENS1:BAND {self.params['ifbw']}")
                vna.write(f"SOUR1:POW {self.params['power']}")
                
                if self.params['avg_enable']:
                    vna.write("SENS1:AVER ON")
                    vna.write(f"SENS1:AVER:COUN {self.params['avg_factor']}")
                else:
                    vna.write("SENS1:AVER OFF")

                # Extend PyVISA timeout for calibration (120 seconds)
                for attr in ['device', 'instrument', 'resource', 'instr']:
                    if hasattr(vna, attr):
                        res = getattr(vna, attr)
                        if res and hasattr(res, 'timeout'):
                            res.timeout = 120000 
                            break
                
                vna.write("SENS1:CORR:COLL:METHod SPARSOLT")
                vna.write("SENS1:CORR:PREFerence:ECAL:ORIentation ON")
                
                self.log_message.emit("Executing 2-Port ECal... Please wait (Do NOT disturb cables).")
                vna.write("SENS1:CORR:COLL ECAL1,CHAR0")
                vna.query("*OPC?")
                
                self.log_message.emit("Computing and applying calibration coefficients...")
                vna.write("SENS1:CORR:COLL:SAVE")
                vna.write("SENS1:CORR:STAT ON")
                
                vna.disconnect()
                self.log_message.emit("ECal Calibration Completed Successfully.")
                self.sequence_complete.emit()
            else:
                self.error_occurred.emit(f"Failed to connect to VNA at {self.pna_address} for calibration.")
        except Exception as e:
            self.error_occurred.emit(f"ECAL FAULT: {str(e)}")

class GainCompCalWorker(QThread):
    """
    Dedicated worker to perform Absolute Source Power Calibration 
    for accurate Gain Compression testing on the PNA-X.
    """
    log_message = pyqtSignal(str)
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, pna_address, params):
        super().__init__()
        self.pna_address = pna_address
        self.params = params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            vna = VectorNetworkAnalyzer(self.pna_address)
            
            if vna.connect(rm):
                self.log_message.emit("Configuring VNA for Source Power Calibration...")
                
                # Clear active traces to setup pure power measurements
                vna.write("CALC1:PAR:DEL:ALL")
                vna.write("CALC1:PAR:DEF 'Meas_Pout',POW:PORT1")
                vna.write("DISP:WIND1:STATE ON")
                vna.write("DISP:WIND1:TRAC1:FEED 'Meas_Pout'")
                
                # Apply Stimulus before Power Cal
                vna.write(f"SENS1:FREQ:STAR {self.params['f_start']}")
                vna.write(f"SENS1:FREQ:STOP {self.params['f_stop']}")
                vna.write(f"SENS1:SWE:POIN {self.params['points']}")
                vna.write(f"SOUR1:POW {self.params['power']}")
                
                # Extend timeout for power cal (Requires settling time for power sensors)
                for attr in ['device', 'instrument', 'resource', 'instr']:
                    if hasattr(vna, attr):
                        res = getattr(vna, attr)
                        if res and hasattr(res, 'timeout'):
                            res.timeout = 120000 
                            break

                self.log_message.emit("Executing Source Power Cal on Port 1... (Ensure Sensor is Connected)")
                vna.write("SENS1:CORR:POWER:COLL:SEL PORT1")
                vna.write("SENS1:CORR:POWER:COLL:ACQ")
                vna.query("*OPC?") # Block until power sweep finishes
                
                self.log_message.emit("Saving Power Calibration coefficients...")
                vna.write("SENS1:CORR:POWER:COLL:SAVE")
                vna.write("SENS1:CORR:POWER:STAT ON") # Enable power correction
                
                vna.disconnect()
                self.log_message.emit("Power Calibration Completed Successfully.")
                self.sequence_complete.emit()
            else:
                self.error_occurred.emit(f"Failed to connect to VNA at {self.pna_address}.")
        except Exception as e:
            self.error_occurred.emit(f"POWER CAL FAULT: {str(e)}")

class VNASweepWorker(QThread):
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, pna_address, vna_params, addresses, bias_params):
        super().__init__()
        self.pna_address = pna_address
        self.params = vna_params
        self.addr = addresses
        self.bias = bias_params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            vna = VectorNetworkAnalyzer(self.pna_address)
            gate, drain = None, None
            vg_meas, ig_meas, vd_meas, id_meas = 0.0, 0.0, 0.0, 0.0

            if self.bias['enable']:
                gate = PowerSupply(self.addr['gate'], name="Gate_PSU")
                drain = PowerSupply(self.addr['drain'], name="Drain_PSU")
                if not all([gate.connect(rm), drain.connect(rm)]):
                    raise Exception("Failed to connect to Power Supplies for S-Parameter Bias.")
                
                gate.reset()
                drain.reset()
                
                # Apply Gate Pinch-Off first
                gate.configure_channel(1, self.bias['vg_start'], self.bias['vg_comp'])
                gate.output_on(1)
                time.sleep(0.5)
                
                # Apply Drain Voltage
                drain.configure_channel(1, self.bias['vd'], self.bias['vd_comp'])
                drain.output_on(1)
                time.sleep(1.0)
                
                # Capture static telemetry values for S-parameter logging
                vg_meas = gate.measure_voltage(1)
                ig_meas = gate.measure_current(1)
                vd_meas = drain.measure_voltage(1)
                id_meas = drain.measure_current(1)

            if vna.connect(rm):
                vna.write("CALC1:PAR:DEL:ALL")
                vna.write(f"SENS1:FREQ:STAR {self.params['f_start']}")
                vna.write(f"SENS1:FREQ:STOP {self.params['f_stop']}")
                vna.write(f"SENS1:SWE:POIN {self.params['points']}")
                vna.write(f"SENS1:BAND {self.params['ifbw']}")
                vna.write(f"SOUR1:POW {self.params['power']}")
                
                vna.write("SENS1:CORR:INT ON")
                vna.write("SENS1:CORR:STAT ON")
                
                if self.params['avg_enable']:
                    vna.write("SENS1:AVER ON")
                    vna.write(f"SENS1:AVER:COUN {self.params['avg_factor']}")
                else:
                    vna.write("SENS1:AVER OFF")
                
                points = int(self.params['points'])
                ifbw = float(self.params['ifbw'])
                avg_factor = int(self.params['avg_factor']) if self.params['avg_enable'] else 1
                estimated_sweep_time = 2 * avg_factor * (points / ifbw)
                visa_timeout_ms = int(max(45, estimated_sweep_time * 2.0) * 1000)

                for attr in ['device', 'instrument', 'resource', 'instr']:
                    if hasattr(vna, attr):
                        res = getattr(vna, attr)
                        if res and hasattr(res, 'timeout'):
                            res.timeout = visa_timeout_ms
                            break

                vna.write("CALC1:PAR:EXT 'Meas_S11', 'S11'")
                vna.write("CALC1:PAR:EXT 'Meas_S21', 'S21'")
                vna.write("CALC1:PAR:EXT 'Meas_S12', 'S12'")
                vna.write("CALC1:PAR:EXT 'Meas_S22', 'S22'")
                
                vna.write("DISP:WIND1:STATE ON")
                vna.write("DISP:WIND1:TRAC1:FEED 'Meas_S11'")
                vna.write("DISP:WIND1:TRAC2:FEED 'Meas_S21'")
                vna.write("DISP:WIND1:TRAC3:FEED 'Meas_S12'")
                vna.write("DISP:WIND1:TRAC4:FEED 'Meas_S22'")
                
                vna.write("FORM ASC,0")
                vna.rf_on()
                vna.write("SENS1:SWE:MODE HOLD")
                
                if self.params['avg_enable']:
                    vna.write(f"SENS1:SWE:GRO:COUN {avg_factor}")
                    vna.write("SENS1:SWE:MODE GROup")
                else:
                    vna.write("SENS1:SWE:MODE SINGle")
                
                start_time = time.time()
                max_wait_time = max(60.0, estimated_sweep_time * 3.5)
                while True:
                    mode = vna.query("SENS1:SWE:MODE?").strip().upper()
                    if "HOLD" in mode:
                        break
                    if time.time() - start_time > max_wait_time:
                        raise TimeoutError("VNA sweep execution timed out on hardware.")
                    time.sleep(0.2)
                
                raw_data = {}
                s_params = ["S11", "S21", "S12", "S22"]
                for param in s_params:
                    vna.write(f"CALC1:PAR:SEL 'Meas_{param}'")
                    data_str = vna.query("CALC1:DATA? FDATA")
                    try:
                        raw_data[param] = [float(x) for x in data_str.strip().split(",") if x]
                    except Exception:
                        raw_data[param] = []

                vna.rf_off()
                vna.disconnect()
                
                f_start, f_stop = float(self.params['f_start']), float(self.params['f_stop'])
                freqs_list = [f_start + i * (f_stop - f_start) / (points - 1) for i in range(points)] if points > 1 else [f_start]
                raw_data["Frequency"] = freqs_list
                
                # Compute Stability K-Factor
                s11_list = raw_data.get("S11", [])
                s21_list = raw_data.get("S21", [])
                s12_list = raw_data.get("S12", [])
                s22_list = raw_data.get("S22", [])
                
                k_factor_list = []
                for i in range(len(s11_list)):
                    try:
                        m_s11, m_s22 = 10.0 ** (float(s11_list[i]) / 20.0), 10.0 ** (float(s22_list[i]) / 20.0)
                        m_s12, m_s21 = 10.0 ** (float(s12_list[i]) / 20.0), 10.0 ** (float(s21_list[i]) / 20.0)
                        delta_mag = abs(m_s11 * m_s22 - m_s12 * m_s21)
                        
                        num = 1.0 - (m_s11 ** 2) - (m_s22 ** 2) + (delta_mag ** 2)
                        den = 2.0 * abs(m_s12 * m_s21)
                        k_factor_list.append(num / den if den != 0 else float('nan'))
                    except Exception:
                        k_factor_list.append(float('nan'))
                        
                raw_data["K_Factor"] = k_factor_list
                
                # Log DC state for export
                raw_data["Vg_meas"] = vg_meas
                raw_data["Ig_meas"] = ig_meas
                raw_data["Vd_meas"] = vd_meas
                raw_data["Id_meas"] = id_meas
                
                if self.bias['enable']:
                    drain.output_off(1)
                    time.sleep(0.5)
                    gate.output_off(1)
                    gate.disconnect()
                    drain.disconnect()

                self.data_ready.emit(raw_data)
            else:
                self.error_occurred.emit(f"Failed to connect to VNA at {self.pna_address}.")
        except Exception as e:
            self.error_occurred.emit(str(e))

class GaNBiasWorker(QThread):
    log_message = pyqtSignal(str)
    telemetry_update = pyqtSignal(float, float, float)
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, addresses, bias_params):
        super().__init__()
        self.addr = addresses
        self.params = bias_params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            gate = PowerSupply(self.addr['gate'], name="Gate_PSU")
            drain = PowerSupply(self.addr['drain'], name="Drain_PSU")

            instruments = [gate.connect(rm), drain.connect(rm)]
            scope = None

            if self.params['target_mode']:
                scope = Oscilloscope(self.addr['scope'])
                instruments.append(scope.connect(rm))

            if not all(instruments):
                self.error_occurred.emit("Failed to connect to Power Supplies (or Scope).")
                return

            gate.reset()
            drain.reset()

            self.log_message.emit("Applying Gate Pinch-Off Voltage...")
            current_vg = self.params['vg_start']
            gate.configure_channel(1, current_vg, self.params['vg_comp'])
            gate.output_on(1)
            time.sleep(0.5)
            
            vg_actual = gate.measure_voltage(1)
            self.telemetry_update.emit(vg_actual, 0.0, 0.0)

            self.log_message.emit("Gate verified. Applying Drain Voltage...")
            drain.configure_channel(1, self.params['vd'], self.params['vd_comp'])
            drain.output_on(1)
            time.sleep(0.5)

            if self.params['target_mode']:
                self.log_message.emit("Initiating Closed-Loop Target Bias Sweep...")
                target_id = self.params['target_idd']
                tol = self.params['id_tol']
                step = abs(self.params['vg_step'])
                stop_vg = self.params['vg_stop']
                
                direction = 1 if stop_vg > current_vg else -1
                achieved = False

                for _ in range(100): 
                    vd_actual = drain.measure_voltage(1)
                    sensor_volts = scope.measure_pulse_top(self.params['scope_chan'])
                    id_actual = sensor_volts * self.params['scope_scale']
                    
                    self.telemetry_update.emit(current_vg, vd_actual, id_actual)

                    if abs(id_actual - target_id) <= tol:
                        self.log_message.emit(f"Target IDD achieved: {id_actual:.3f}A at Vg={current_vg:.2f}V")
                        achieved = True
                        break
                    
                    if (direction == 1 and current_vg >= stop_vg) or (direction == -1 and current_vg <= stop_vg):
                        self.log_message.emit("Reached Vg Stop limit without achieving target IDD.")
                        break

                    if id_actual < target_id:
                        current_vg += direction * step
                    else:
                        current_vg -= direction * step

                    if (direction == 1 and current_vg > stop_vg): current_vg = stop_vg
                    if (direction == -1 and current_vg < stop_vg): current_vg = stop_vg

                    gate.configure_channel(1, current_vg, self.params['vg_comp'])
                    time.sleep(0.3)

                if not achieved:
                    self.log_message.emit(f"Targeting finished at {current_vg:.2f}V (Not within tolerance).")
                
                time.sleep(2.0)
            else:
                self.log_message.emit("Standard bias applied. Monitoring telemetry...")
                for _ in range(5):
                    vd_actual = drain.measure_voltage(1)
                    id_actual = drain.measure_current(1)
                    self.telemetry_update.emit(current_vg, vd_actual, id_actual)
                    time.sleep(1)

            self.log_message.emit("Bias sequence complete. Initiating Safe Shutdown...")
            drain.output_off(1)
            time.sleep(0.5)
            gate.output_off(1)

            gate.disconnect()
            drain.disconnect()
            if scope: scope.disconnect()
            self.sequence_complete.emit()

        except Exception as e:
            try:
                drain.emergency_shutdown()
                gate.output_off(1)
            except:
                pass
            self.error_occurred.emit(f"BIAS FAULT: {str(e)}")

class GainCompressionWorker(QThread):
    log_message = pyqtSignal(str)
    # Args: freq, pin_list, pout_list, gain_list, pae_list, vg_m_list, ig_m_list, vd_m_list, id_m_list
    data_ready = pyqtSignal(float, list, list, list, list, list, list, list, list) 
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, addresses, pulse_params, bias_params, sweep_params):
        super().__init__()
        self.addr = addresses
        self.pulse = pulse_params
        self.bias = bias_params
        self.sweep = sweep_params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            is_pulsed = (self.pulse['mode'] == "Pulsed RF")
            
            vna = VectorNetworkAnalyzer(self.addr['vna'])
            if not vna.connect(rm): raise Exception("VNA connection failed.")
            
            gate, drain, wg, scope = None, None, None, None
            
            if self.bias['enable']:
                gate = PowerSupply(self.addr['gate'])
                drain = PowerSupply(self.addr['drain'])
                if not all([gate.connect(rm), drain.connect(rm)]): raise Exception("PSU connection failed.")
                
            if is_pulsed:
                wg = WaveformGenerator(self.addr['wg'])
                scope = Oscilloscope(self.addr['scope'])
                if not all([wg.connect(rm), scope.connect(rm)]): raise Exception("Timing Hardware connection failed.")

            if self.bias['enable']:
                self.log_message.emit("Biasing Device...")
                gate.configure_channel(1, self.bias['vg'], self.bias['vg_comp'])
                gate.output_on(1)
                time.sleep(0.5)
                drain.configure_channel(1, self.bias['vd'], self.bias['vd_comp'])
                drain.output_on(1)
                time.sleep(1)

            vna.setup_unratioed_power_measure()
            vna.write("SENS1:SWE:POIN 1") 
            vna.write("SENS1:CORR:POWER:STAT ON") # Force the Source Power Cal to remain active

            if is_pulsed:
                self.log_message.emit("Configuring Pulsed Mode...")
                wg.configure_pulse_trigger(self.pulse['width'], self.pulse['period'], self.pulse['delay'], self.pulse['vhigh'], self.pulse['vlow'])
                scope.configure_trigger(self.pulse['trig_chan'], self.pulse['trig_level'])
                scope.set_timebase(self.pulse['period'])
                vna.write("TRIG:SOUR EXT") 
            else:
                self.log_message.emit("Configuring CW Mode...")
                vna.write("TRIG:SOUR IMM") 
                vna.rf_on()

            num_f_points = int(abs(self.sweep['f_max'] - self.sweep['f_min']) / self.sweep['f_step']) + 1 if self.sweep['f_step'] != 0 else 1
            freqs = [self.sweep['f_min'] + (i * self.sweep['f_step']) for i in range(num_f_points)]
            
            num_p_points = int(abs(self.sweep['p_max'] - self.sweep['p_min']) / self.sweep['p_step']) + 1 if self.sweep['p_step'] != 0 else 1
            powers = [self.sweep['p_min'] + (i * self.sweep['p_step']) for i in range(num_p_points)]

            for freq in freqs:
                self.log_message.emit(f"Sweeping {freq/1e9:.2f} GHz...")
                vna.set_cw_frequency(f"{freq} Hz")
                
                pin_results, pout_results, gain_results, pae_results = [], [], [], []
                vg_meas_results, ig_meas_results, vd_meas_results, id_meas_results = [], [], [], []

                for pin in powers:
                    vna.set_power_level(pin)
                    time.sleep(0.05) 
                    
                    id_current = 0.0
                    if is_pulsed:
                        wg.fire_single_pulse()
                        time.sleep(self.pulse['delay'] + self.pulse['width'] + 0.1) 
                        pout = vna.measure_single_point()
                        
                        if self.bias['enable']:
                            sensor_volts = scope.measure_pulse_top(self.pulse['scope_chan'])
                            id_current = sensor_volts * self.pulse['scope_scale']
                    else:
                        pout = vna.measure_single_point()
                        if self.bias['enable']:
                            id_current = drain.measure_current(1)
                    
                    gain = pout - pin
                    
                    if self.bias['enable']:
                        vg_val = gate.measure_voltage(1)
                        ig_val = gate.measure_current(1)
                        vd_val = drain.measure_voltage(1)
                        id_val = id_current if is_pulsed else drain.measure_current(1)
                    else:
                        vg_val, ig_val, vd_val, id_val = 0.0, 0.0, 0.0, 0.0

                    if self.bias['enable'] and id_current > 0.001: 
                        pout_w = 10 ** ((pout - 30) / 10)
                        pin_w = 10 ** ((pin - 30) / 10)
                        pdc_w = self.bias['vd'] * id_current
                        pae = ((pout_w - pin_w) / pdc_w) * 100.0
                    else:
                        pae = 0.0
                    
                    pin_results.append(pin)
                    pout_results.append(pout)
                    gain_results.append(gain)
                    pae_results.append(pae)
                    
                    vg_meas_results.append(vg_val)
                    ig_meas_results.append(ig_val)
                    vd_meas_results.append(vd_val)
                    id_meas_results.append(id_val)
                
                self.data_ready.emit(freq, pin_results, pout_results, gain_results, pae_results,
                                     vg_meas_results, ig_meas_results, vd_meas_results, id_meas_results)

            vna.rf_off()
            if self.bias['enable']:
                self.log_message.emit("Sweep complete. Safe DC Shutdown...")
                drain.output_off(1)
                time.sleep(0.5)
                gate.output_off(1)
            
            self.sequence_complete.emit()

        except Exception as e:
            if self.bias['enable']:
                try:
                    drain.emergency_shutdown()
                    gate.output_off(1)
                except:
                    pass
            self.error_occurred.emit(f"COMPRESSION FAULT: {str(e)}")

class HarmonicsWorker(QThread):
    log_message = pyqtSignal(str)
    data_ready = pyqtSignal(list, list) 
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, addresses, f0_hz, pin_dbm, bias_params, active_harmonics):
        super().__init__()
        self.addr = addresses
        self.f0_hz = f0_hz
        self.pin_dbm = pin_dbm
        self.bias = bias_params
        self.active_harmonics = active_harmonics

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            vna = VectorNetworkAnalyzer(self.addr['vna'])
            sa = SignalAnalyzer(self.addr['sa'])
            
            instruments = [vna.connect(rm), sa.connect(rm)]
            if self.bias['enable']:
                gate = PowerSupply(self.addr['gate'])
                drain = PowerSupply(self.addr['drain'])
                instruments.extend([gate.connect(rm), drain.connect(rm)])
                
            if not all(instruments): raise Exception("Hardware connection failure.")

            if self.bias['enable']:
                self.log_message.emit("Biasing Device...")
                gate.configure_channel(1, self.bias['vg'], self.bias['vg_comp'])
                gate.output_on(1)
                time.sleep(0.5)
                drain.configure_channel(1, self.bias['vd'], self.bias['vd_comp'])
                drain.output_on(1)
                time.sleep(1)

            vna.set_cw_frequency(self.f0_hz)
            vna.set_power_level(self.pin_dbm)
            vna.rf_on()
            time.sleep(0.5) 
            
            self.log_message.emit("Measuring Fundamental (f0)...")
            fund_dbm = sa.measure_peak_power(self.f0_hz)
            
            labels = ["Fundamental"]
            powers_dbc = [0.0] 

            for label, multiplier in self.active_harmonics.items():
                target_freq = self.f0_hz * multiplier
                self.log_message.emit(f"Hunting for {label} at {target_freq/1e9:.3f} GHz...")
                harm_dbm = sa.measure_peak_power(target_freq)
                dbc_value = harm_dbm - fund_dbm
                labels.append(label)
                powers_dbc.append(dbc_value)

            self.data_ready.emit(labels, powers_dbc)

            vna.rf_off()
            if self.bias['enable']:
                self.log_message.emit("Safe DC Shutdown...")
                drain.output_off(1)
                time.sleep(0.5)
                gate.output_off(1)
            
            self.sequence_complete.emit()

        except Exception as e:
            if self.bias['enable']:
                try:
                    drain.emergency_shutdown()
                    gate.output_off(1)
                except: pass
            self.error_occurred.emit(f"HARMONICS FAULT: {str(e)}")

class LinearCalWorker(QThread):
    """
    Performs A/B Power Meter Calibration to de-embed input and output cable losses.
    Step 1: Input Cable Loss (RF Gen -> Sensor A Coupler -> Input Cable -> Sensor B)
    Step 2: Total Cable Loss (RF Gen -> Sensor A Coupler -> Input Cable -> Output Cable -> Sensor B)
    """
    log_message = pyqtSignal(str)
    calibration_complete = pyqtSignal(int, dict) # step, data_dict
    error_occurred = pyqtSignal(str)

    def __init__(self, addresses, step, sweep_params):
        super().__init__()
        self.addr = addresses
        self.step = step
        self.sweep = sweep_params

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            rf_gen = RFGenerator(self.addr['rfgen'])
            pmeter = PowerMeter(self.addr['pmeter'])
            
            if not all([rf_gen.connect(rm), pmeter.connect(rm)]):
                raise Exception("Failed to connect to RF Generator or Power Meter.")

            num_f_points = int(abs(self.sweep['f_max'] - self.sweep['f_min']) / self.sweep['f_step']) + 1 if self.sweep['f_step'] != 0 else 1
            freqs = [self.sweep['f_min'] + (i * self.sweep['f_step']) for i in range(num_f_points)]
            
            cal_results = {}
            cal_power = self.sweep['cal_power']
            
            rf_gen.set_power(cal_power)
            rf_gen.rf_on()
            time.sleep(1.0) # Settle power

            for freq in freqs:
                self.log_message.emit(f"Calibrating at {freq/1e9:.3f} GHz...")
                rf_gen.set_frequency(freq)
                pmeter.set_frequency(1, freq)
                pmeter.set_frequency(2, freq)
                time.sleep(0.2) # Settle PLL
                
                p_a = pmeter.measure_power(1)
                p_b = pmeter.measure_power(2)
                
                # The delta between Reference (A) and Output (B) represents the path loss
                offset = p_a - p_b
                cal_results[freq] = offset

            rf_gen.rf_off()
            rf_gen.disconnect()
            pmeter.disconnect()
            
            self.log_message.emit(f"Step {self.step} Calibration Complete.")
            self.calibration_complete.emit(self.step, cal_results)
            
        except Exception as e:
            self.error_occurred.emit(f"LINEAR CAL FAULT: {str(e)}")

class LinearSweepWorker(QThread):
    """
    Performs Gain Compression Sweep using discrete RF Generator and A/B Power Meters.
    Applies real-time cable loss de-embedding based on prior calibration data.
    """
    log_message = pyqtSignal(str)
    # Args: freq, pin_list, pout_list, gain_list, pae_list, vg_m_list, ig_m_list, vd_m_list, id_m_list
    data_ready = pyqtSignal(float, list, list, list, list, list, list, list, list) 
    sequence_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, addresses, pulse_params, bias_params, sweep_params, cal_data):
        super().__init__()
        self.addr = addresses
        self.pulse = pulse_params
        self.bias = bias_params
        self.sweep = sweep_params
        self.cal_data = cal_data # Dictionary containing 'input' and 'total' loss offsets per frequency

    def run(self):
        try:
            rm = pyvisa.ResourceManager()
            is_pulsed = (self.pulse['mode'] == "Pulsed RF")
            
            rf_gen = RFGenerator(self.addr['rfgen'])
            pmeter = PowerMeter(self.addr['pmeter'])
            
            instruments = [rf_gen.connect(rm), pmeter.connect(rm)]
            gate, drain, wg, scope = None, None, None, None
            
            if self.bias['enable']:
                gate = PowerSupply(self.addr['gate'])
                drain = PowerSupply(self.addr['drain'])
                instruments.extend([gate.connect(rm), drain.connect(rm)])
                
            if is_pulsed:
                wg = WaveformGenerator(self.addr['wg'])
                scope = Oscilloscope(self.addr['scope'])
                instruments.extend([wg.connect(rm), scope.connect(rm)])

            if not all(instruments): raise Exception("Hardware connection failure.")

            if self.bias['enable']:
                self.log_message.emit("Biasing Device...")
                gate.configure_channel(1, self.bias['vg'], self.bias['vg_comp'])
                gate.output_on(1)
                time.sleep(0.5)
                drain.configure_channel(1, self.bias['vd'], self.bias['vd_comp'])
                drain.output_on(1)
                time.sleep(1)

            if is_pulsed:
                self.log_message.emit("Configuring Pulsed Timing...")
                rf_gen.set_modulation(enable=True)
                wg.configure_pulse_trigger(self.pulse['width'], self.pulse['period'], self.pulse['delay'], self.pulse['vhigh'], self.pulse['vlow'])
                scope.configure_trigger(self.pulse['trig_chan'], self.pulse['trig_level'])
                scope.set_timebase(self.pulse['period'])
            else:
                rf_gen.set_modulation(enable=False)

            num_f_points = int(abs(self.sweep['f_max'] - self.sweep['f_min']) / self.sweep['f_step']) + 1 if self.sweep['f_step'] != 0 else 1
            freqs = [self.sweep['f_min'] + (i * self.sweep['f_step']) for i in range(num_f_points)]
            
            num_p_points = int(abs(self.sweep['p_max'] - self.sweep['p_min']) / self.sweep['p_step']) + 1 if self.sweep['p_step'] != 0 else 1
            powers = [self.sweep['p_min'] + (i * self.sweep['p_step']) for i in range(num_p_points)]

            rf_gen.rf_on()

            for freq in freqs:
                self.log_message.emit(f"Sweeping {freq/1e9:.2f} GHz...")
                rf_gen.set_frequency(freq)
                pmeter.set_frequency(1, freq)
                pmeter.set_frequency(2, freq)
                
                # Fetch calibration data (Fallback to 0 dB offset if cal is missing)
                in_offset = self.cal_data['input'].get(freq, 0.0)
                tot_offset = self.cal_data['total'].get(freq, 0.0)
                out_loss = tot_offset - in_offset
                
                pin_results, pout_results, gain_results, pae_results = [], [], [], []
                vg_meas_results, ig_meas_results, vd_meas_results, id_meas_results = [], [], [], []

                for pin_set in powers:
                    rf_gen.set_power(pin_set)
                    time.sleep(0.1) # Settle thermal/power
                    
                    id_current = 0.0
                    if is_pulsed:
                        wg.fire_single_pulse()
                        time.sleep(self.pulse['delay'] + self.pulse['width'] + 0.1) 
                        if self.bias['enable']:
                            sensor_volts = scope.measure_pulse_top(self.pulse['scope_chan'])
                            id_current = sensor_volts * self.pulse['scope_scale']
                    else:
                        if self.bias['enable']:
                            id_current = drain.measure_current(1)
                    
                    # Read Power Meters
                    raw_p_a = pmeter.measure_power(1)
                    raw_p_b = pmeter.measure_power(2)
                    
                    # De-embed measurements to DUT reference planes
                    pin_dut = raw_p_a - in_offset
                    pout_dut = raw_p_b + out_loss
                    gain = pout_dut - pin_dut
                    
                    if self.bias['enable']:
                        vg_val = gate.measure_voltage(1)
                        ig_val = gate.measure_current(1)
                        vd_val = drain.measure_voltage(1)
                        id_val = id_current if is_pulsed else drain.measure_current(1)
                    else:
                        vg_val, ig_val, vd_val, id_val = 0.0, 0.0, 0.0, 0.0

                    # PAE Calculation
                    if self.bias['enable'] and id_current > 0.001: 
                        pout_w = 10 ** ((pout_dut - 30) / 10)
                        pin_w = 10 ** ((pin_dut - 30) / 10)
                        pdc_w = self.bias['vd'] * id_current
                        pae = ((pout_w - pin_w) / pdc_w) * 100.0
                    else:
                        pae = 0.0
                    
                    pin_results.append(pin_dut)
                    pout_results.append(pout_dut)
                    gain_results.append(gain)
                    pae_results.append(pae)
                    
                    vg_meas_results.append(vg_val)
                    ig_meas_results.append(ig_val)
                    vd_meas_results.append(vd_val)
                    id_meas_results.append(id_val)
                
                self.data_ready.emit(freq, pin_results, pout_results, gain_results, pae_results,
                                     vg_meas_results, ig_meas_results, vd_meas_results, id_meas_results)

            rf_gen.rf_off()
            if self.bias['enable']:
                self.log_message.emit("Sweep complete. Safe DC Shutdown...")
                drain.output_off(1)
                time.sleep(0.5)
                gate.output_off(1)
            
            for inst in instruments: inst.disconnect()
            self.sequence_complete.emit()

        except Exception as e:
            if self.bias['enable']:
                try:
                    drain.emergency_shutdown()
                    gate.output_off(1)
                except: pass
            self.error_occurred.emit(f"LINEAR SWEEP FAULT: {str(e)}")

# =============================================================================
# MAIN GRAPHICAL INTERFACE (The View Layer)
# =============================================================================

class TestExecutiveGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GaN RF Test Executive Framework")
        self.resize(1200, 950)
        
        self.compression_results = []
        self.vna_results = None
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # --- GLOBAL KILL SWITCH ---
        self.btn_kill = QPushButton("GLOBAL EMERGENCY SHUTDOWN")
        self.btn_kill.setMinimumHeight(50)
        self.btn_kill.setStyleSheet("background-color: #B22222; color: white; font-weight: bold; font-size: 16px;")
        self.btn_kill.clicked.connect(self.global_emergency_kill)
        main_layout.addWidget(self.btn_kill)

        # --- HARDWARE SELECTORS ---
        hw_group = QGroupBox("Hardware Bus Routing")
        hw_layout = QHBoxLayout()
        self.vna_combo = QComboBox(); self.vna_combo.setEditable(True); self.vna_combo.addItem("GPIB0::17::INSTR")
        self.sa_combo = QComboBox(); self.sa_combo.setEditable(True); self.sa_combo.addItem("GPIB0::18::INSTR")
        self.gate_combo = QComboBox(); self.gate_combo.setEditable(True); self.gate_combo.addItem("GPIB0::10::INSTR")
        self.drain_combo = QComboBox(); self.drain_combo.setEditable(True); self.drain_combo.addItem("GPIB0::11::INSTR")
        self.wg_combo = QComboBox(); self.wg_combo.setEditable(True); self.wg_combo.addItem("GPIB0::20::INSTR")
        self.scope_combo = QComboBox(); self.scope_combo.setEditable(True); self.scope_combo.addItem("GPIB0::7::INSTR")
        
        self.rfgen_combo = QComboBox(); self.rfgen_combo.setEditable(True); self.rfgen_combo.addItem("GPIB0::19::INSTR")
        self.pmeter_combo = QComboBox(); self.pmeter_combo.setEditable(True); self.pmeter_combo.addItem("GPIB0::13::INSTR")

        hw_layout.addWidget(QLabel("VNA:")); hw_layout.addWidget(self.vna_combo)
        hw_layout.addWidget(QLabel("Gate:")); hw_layout.addWidget(self.gate_combo)
        hw_layout.addWidget(QLabel("Drain:")); hw_layout.addWidget(self.drain_combo)
        hw_layout.addWidget(QLabel("SA:")); hw_layout.addWidget(self.sa_combo)
        hw_layout.addWidget(QLabel("Clock:")); hw_layout.addWidget(self.wg_combo)
        hw_layout.addWidget(QLabel("Scope:")); hw_layout.addWidget(self.scope_combo)
        hw_layout.addWidget(QLabel("RF Gen:")); hw_layout.addWidget(self.rfgen_combo)
        hw_layout.addWidget(QLabel("P-Meter:")); hw_layout.addWidget(self.pmeter_combo)
        
        btn_scan = QPushButton("Scan")
        btn_scan.clicked.connect(self.scan_hardware)
        hw_layout.addWidget(btn_scan)
        hw_group.setLayout(hw_layout)
        main_layout.addWidget(hw_group)

        # --- DUT METADATA GROUP ---
        dut_group = QGroupBox("DUT Metadata Tracker")
        dut_layout = QHBoxLayout()
        self.input_pn = QLineEdit("GAN-AMP-10W")
        self.input_sn = QLineEdit("DUT-001")
        self.input_lot = QLineEdit("LOT-2026")
        
        dut_layout.addWidget(QLabel("Part Number:"))
        dut_layout.addWidget(self.input_pn)
        dut_layout.addWidget(QLabel("Serial Number:"))
        dut_layout.addWidget(self.input_sn)
        dut_layout.addWidget(QLabel("Lot Number:"))
        dut_layout.addWidget(self.input_lot)
        dut_group.setLayout(dut_layout)
        main_layout.addWidget(dut_group)

        # --- THE TAB MANAGER ---
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        self.tab_vna = QWidget(); self.build_vna_tab(); self.tabs.addTab(self.tab_vna, "S-Parameters (Linear)")
        self.tab_bias = QWidget(); self.build_bias_tab(); self.tabs.addTab(self.tab_bias, "DC Bias Control")
        self.tab_comp = QWidget(); self.build_compression_tab(); self.tabs.addTab(self.tab_comp, "Gain Compression Test")
        self.tab_harm = QWidget(); self.build_harmonics_tab(); self.tabs.addTab(self.tab_harm, "Spectral Harmonics")
        
        self.tab_linear_comp = QWidget(); self.build_linear_comp_tab(); self.tabs.addTab(self.tab_linear_comp, "Linear Compression (RF Gen)")
        self.linear_cal_data = {'input': {}, 'total': {}}
        self.linear_results = []

        # --- GLOBAL STATUS BAR ---
        self.status_label = QLabel("System Ready.")
        self.status_label.setStyleSheet("font-weight: bold; color: #00AA00;")
        main_layout.addWidget(self.status_label)

    # =========================================================================
    # TAB CONSTRUCTION
    # =========================================================================

    def build_vna_tab(self):
        layout = QVBoxLayout(self.tab_vna)
        
        vna_cfg_group = QGroupBox("VNA Linear Parameters")
        cfg_layout = QGridLayout()
        
        self.input_vna_fstart = QLineEdit("1e9")
        self.input_vna_fstop = QLineEdit("10e9")
        self.input_vna_points = QLineEdit("201")
        self.input_vna_power = QLineEdit("-20.0")
        self.input_vna_ifbw = QLineEdit("1000")
        
        cfg_layout.addWidget(QLabel("Start Freq (Hz):"), 0, 0)
        cfg_layout.addWidget(self.input_vna_fstart, 0, 1)
        cfg_layout.addWidget(QLabel("Stop Freq (Hz):"), 0, 2)
        cfg_layout.addWidget(self.input_vna_fstop, 0, 3)
        
        cfg_layout.addWidget(QLabel("Points:"), 1, 0)
        cfg_layout.addWidget(self.input_vna_points, 1, 1)
        cfg_layout.addWidget(QLabel("RF Power (dBm):"), 1, 2)
        cfg_layout.addWidget(self.input_vna_power, 1, 3)
        
        cfg_layout.addWidget(QLabel("IF Bandwidth (Hz):"), 2, 0)
        cfg_layout.addWidget(self.input_vna_ifbw, 2, 1)
        
        vna_cfg_group.setLayout(cfg_layout)
        layout.addWidget(vna_cfg_group)
        
        avg_group = QGroupBox("VNA Averaging Setup")
        avg_layout = QHBoxLayout()
        self.check_vna_avg = QCheckBox("Enable Averaging")
        self.input_vna_avg_factor = QLineEdit("16")
        avg_layout.addWidget(self.check_vna_avg)
        avg_layout.addWidget(QLabel("Averaging Factor:"))
        avg_layout.addWidget(self.input_vna_avg_factor)
        avg_group.setLayout(avg_layout)
        layout.addWidget(avg_group)
        
        bias_setup_group = QGroupBox("S-Parameter DC Bias Control")
        bias_setup_layout = QHBoxLayout()
        self.check_vna_bias = QCheckBox("Enable DC Bias Sequence")
        self.check_vna_bias.setChecked(True)
        bias_setup_layout.addWidget(self.check_vna_bias)
        bias_setup_group.setLayout(bias_setup_layout)
        layout.addWidget(bias_setup_group)
        
        btn_layout = QHBoxLayout()
        
        self.btn_cal = QPushButton("Run 2-Port ECal Calibration")
        self.btn_cal.setStyleSheet("background-color: #005A9E; color: white; font-weight: bold;")
        self.btn_cal.clicked.connect(self.trigger_ecal_calibration)
        btn_layout.addWidget(self.btn_cal)
        
        self.btn_sweep = QPushButton("Trigger S-Parameter Sweep")
        self.btn_sweep.clicked.connect(self.trigger_vna_sweep)
        btn_layout.addWidget(self.btn_sweep)

        self.btn_export_vna = QPushButton("Export S-Parameters to CSV")
        self.btn_export_vna.setEnabled(False)
        self.btn_export_vna.clicked.connect(self.export_vna_csv)
        btn_layout.addWidget(self.btn_export_vna)

        layout.addLayout(btn_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        
        self.plot_widget = pg.PlotWidget(title="Live S-Parameter Matrix (dB)")
        self.plot_widget.addLegend(offset=(10, 10))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        splitter.addWidget(self.plot_widget)
        
        self.k_plot_widget = pg.PlotWidget(title="Rollett Stability K-Factor (Linear)")
        self.k_plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.k_plot_widget.setLabel('left', 'K', units='')
        self.k_plot_widget.setLabel('bottom', 'Frequency', units='Hz')
        splitter.addWidget(self.k_plot_widget)
        
        layout.addWidget(splitter)

    def build_bias_tab(self):
        layout = QVBoxLayout(self.tab_bias)
        
        psu_group = QGroupBox("Base Targets & PSU Compliance")
        psu_layout = QFormLayout()
        
        self.input_vd = QLineEdit("28.0")
        self.input_vd_comp = QLineEdit("1.5")
        self.input_vg_comp = QLineEdit("0.05")
        
        psu_layout.addRow("Vd Target Voltage (V):", self.input_vd)
        psu_layout.addRow("Vd Overcurrent Compliance (A):", self.input_vd_comp)
        psu_layout.addRow("Vg Overcurrent Compliance (A):", self.input_vg_comp)
        psu_group.setLayout(psu_layout)
        layout.addWidget(psu_group)

        target_group = QGroupBox("Active Target Biasing (Closed-Loop via Oscilloscope)")
        target_layout = QFormLayout()
        
        self.check_target_bias = QCheckBox("Enable Oscilloscope Target IDD Feedback")
        self.check_target_bias.setChecked(True)
        target_layout.addRow(self.check_target_bias)

        self.input_vg_start = QLineEdit("-5.0")
        self.input_vg_stop = QLineEdit("-1.0")
        self.input_vg_step = QLineEdit("0.05")
        self.input_target_idd = QLineEdit("0.5")
        self.input_id_tol = QLineEdit("0.05")
        self.input_scope_scale = QLineEdit("10.0")

        target_layout.addRow("Vg Sweep Start / Pinch-Off (V):", self.input_vg_start)
        target_layout.addRow("Vg Sweep Stop Limit (V):", self.input_vg_stop)
        target_layout.addRow("Vg Step Size (V):", self.input_vg_step)
        target_layout.addRow("Target IDD (A):", self.input_target_idd)
        target_layout.addRow("Id Tolerance (A):", self.input_id_tol)
        target_layout.addRow("Scope Voltage-to-Amps Scale (A/V):", self.input_scope_scale)
        
        target_group.setLayout(target_layout)
        layout.addWidget(target_group)

        self.btn_bias = QPushButton("Execute Sequenced GaN Bias")
        self.btn_bias.setMinimumHeight(40)
        self.btn_bias.clicked.connect(self.trigger_bias_sequence)
        layout.addWidget(self.btn_bias)
        
        self.telemetry_label = QLabel("Telemetry State: Vg=0.00V | Vd=0.00V | Id=0.000A")
        self.telemetry_label.setStyleSheet("font-size: 14px; font-family: monospace; padding: 10px; background-color: #1E1E1E; color: #00FF00;")
        layout.addWidget(self.telemetry_label)
        layout.addStretch()

    def build_compression_tab(self):
        layout = QVBoxLayout(self.tab_comp)
        
        control_layout = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Continuous Wave (CW)", "Pulsed RF"])
        self.check_comp_bias = QCheckBox("Enable DC Bias Sequence")
        self.check_comp_bias.setChecked(True)
        control_layout.addWidget(QLabel("Test Mode:"))
        control_layout.addWidget(self.mode_combo)
        control_layout.addWidget(self.check_comp_bias)
        layout.addLayout(control_layout)

        time_group = QGroupBox("Pulsed Timing & Level Parameters")
        time_layout = QVBoxLayout()
        
        row_timing = QHBoxLayout()
        self.input_width = QLineEdit("1e-6")
        self.input_period = QLineEdit("1e-3")
        self.input_delay = QLineEdit("0")
        row_timing.addWidget(QLabel("Pulse Width (s):")); row_timing.addWidget(self.input_width)
        row_timing.addWidget(QLabel("Period (s):")); row_timing.addWidget(self.input_period)
        row_timing.addWidget(QLabel("Measurement Delay (s):")); row_timing.addWidget(self.input_delay)
        time_layout.addLayout(row_timing)
        
        row_levels = QHBoxLayout()
        self.input_vhigh = QLineEdit("3.3")
        self.input_vlow = QLineEdit("0.0")
        row_levels.addWidget(QLabel("Pulse Vhigh (V):")); row_levels.addWidget(self.input_vhigh)
        row_levels.addWidget(QLabel("Pulse Vlow (V):")); row_levels.addWidget(self.input_vlow)
        time_layout.addLayout(row_levels)
        
        time_group.setLayout(time_layout)
        layout.addWidget(time_group)

        sweep_group = QGroupBox("Gain Compression RF Sweep Parameters")
        sweep_layout = QFormLayout()
        
        row1 = QHBoxLayout(); row1.addWidget(QLabel("F_Min (Hz):")); self.input_fmin = QLineEdit("1e9"); row1.addWidget(self.input_fmin)
        row1.addWidget(QLabel("F_Max (Hz):")); self.input_fmax = QLineEdit("10e9"); row1.addWidget(self.input_fmax)
        row1.addWidget(QLabel("F_Step (Hz):")); self.input_fstep = QLineEdit("1e9"); row1.addWidget(self.input_fstep)
        
        row2 = QHBoxLayout(); row2.addWidget(QLabel("P_Min (dBm):")); self.input_pmin = QLineEdit("-20"); row2.addWidget(self.input_pmin)
        row2.addWidget(QLabel("P_Max (dBm):")); self.input_pmax = QLineEdit("5.0"); row2.addWidget(self.input_pmax)
        row2.addWidget(QLabel("P_Step (dBm):")); self.input_pstep = QLineEdit("1.0"); row2.addWidget(self.input_pstep)
        
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Power Calibration Stimulus (dBm):"))
        self.input_comp_cal_power = QLineEdit("-10.0")
        row3.addWidget(self.input_comp_cal_power)
        
        sweep_layout.addRow(row1); sweep_layout.addRow(row2); sweep_layout.addRow(row3)
        sweep_group.setLayout(sweep_layout)
        layout.addWidget(sweep_group)
        
        btn_layout = QHBoxLayout()
        
        self.btn_comp_cal = QPushButton("Run Power Calibration")
        self.btn_comp_cal.setStyleSheet("background-color: #005A9E; color: white; font-weight: bold;")
        self.btn_comp_cal.setMinimumHeight(40)
        self.btn_comp_cal.clicked.connect(self.trigger_gain_comp_power_calibration)
        
        self.btn_comp = QPushButton("Start Gain Compression Sweep")
        self.btn_comp.setMinimumHeight(40)
        self.btn_comp.clicked.connect(self.trigger_compression_sweep)
        
        self.btn_export_comp = QPushButton("Export Sweep to CSV")
        self.btn_export_comp.setMinimumHeight(40)
        self.btn_export_comp.setEnabled(False)
        self.btn_export_comp.clicked.connect(self.export_compression_csv)
        
        btn_layout.addWidget(self.btn_comp_cal)
        btn_layout.addWidget(self.btn_comp)
        btn_layout.addWidget(self.btn_export_comp)
        layout.addLayout(btn_layout)
        
        self.comp_plot = pg.PlotWidget(title="Gain Compression & Efficiency")
        self.comp_plot.addLegend(offset=(10, 10))
        self.comp_plot.showGrid(x=True, y=True, alpha=0.3)
        self.comp_plot.setLabel('left', 'Power (dBm) / PAE (%)', units='')
        self.comp_plot.setLabel('bottom', 'Pin', units='dBm')
        layout.addWidget(self.comp_plot)

    def build_harmonics_tab(self):
        layout = QVBoxLayout(self.tab_harm)
        self.check_harm_bias = QCheckBox("Enable DC Bias Sequence")
        self.check_harm_bias.setChecked(True)
        layout.addWidget(self.check_harm_bias)

        form_layout = QFormLayout()
        self.input_harm_f0 = QLineEdit("2e9") 
        self.input_harm_pin = QLineEdit("-10.0")
        form_layout.addRow("Fundamental CW Freq (Hz):", self.input_harm_f0)
        form_layout.addRow("Drive Power (dBm):", self.input_harm_pin)
        layout.addLayout(form_layout)

        self.harm_checks = {}
        harm_layout = QHBoxLayout()
        harm_layout.addWidget(QLabel("Select Target Tones:"))
        tones = {"f/3": 1/3, "f/2": 0.5, "2f": 2.0, "3f": 3.0, "4f": 4.0, "5f": 5.0}
        
        for name, mult in tones.items():
            chk = QCheckBox(name)
            if mult >= 2.0 and mult <= 3.0: chk.setChecked(True) 
            self.harm_checks[name] = (chk, mult)
            harm_layout.addWidget(chk)
            
        layout.addLayout(harm_layout)
        self.btn_harm = QPushButton("Run Spectral Scan")
        self.btn_harm.clicked.connect(self.trigger_harmonics)
        layout.addWidget(self.btn_harm)
        
        self.harm_plot = pg.PlotWidget(title="Relative Harmonic Levels (dBc)")
        self.harm_plot.showGrid(x=True, y=True, alpha=0.3)
        self.harm_plot.setLabel('left', 'Power Relative to f0', units='dBc')
        layout.addWidget(self.harm_plot)

    def build_linear_comp_tab(self):
        layout = QVBoxLayout(self.tab_linear_comp)
        
        control_layout = QHBoxLayout()
        self.lin_mode_combo = QComboBox()
        self.lin_mode_combo.addItems(["Continuous Wave (CW)", "Pulsed RF"])
        self.check_lin_bias = QCheckBox("Enable DC Bias Sequence")
        self.check_lin_bias.setChecked(True)
        control_layout.addWidget(QLabel("Test Mode:"))
        control_layout.addWidget(self.lin_mode_combo)
        control_layout.addWidget(self.check_lin_bias)
        layout.addLayout(control_layout)

        time_group = QGroupBox("Pulsed Timing & Level Parameters")
        time_layout = QVBoxLayout()
        row_timing = QHBoxLayout()
        self.lin_input_width = QLineEdit("1e-6")
        self.lin_input_period = QLineEdit("1e-3")
        self.lin_input_delay = QLineEdit("0")
        row_timing.addWidget(QLabel("Pulse Width (s):")); row_timing.addWidget(self.lin_input_width)
        row_timing.addWidget(QLabel("Period (s):")); row_timing.addWidget(self.lin_input_period)
        row_timing.addWidget(QLabel("Measurement Delay (s):")); row_timing.addWidget(self.lin_input_delay)
        time_layout.addLayout(row_timing)
        row_levels = QHBoxLayout()
        self.lin_input_vhigh = QLineEdit("3.3")
        self.lin_input_vlow = QLineEdit("0.0")
        row_levels.addWidget(QLabel("Pulse Vhigh (V):")); row_levels.addWidget(self.lin_input_vhigh)
        row_levels.addWidget(QLabel("Pulse Vlow (V):")); row_levels.addWidget(self.lin_input_vlow)
        time_layout.addLayout(row_levels)
        time_group.setLayout(time_layout)
        layout.addWidget(time_group)

        sweep_group = QGroupBox("Linear RF Sweep Parameters")
        sweep_layout = QFormLayout()
        row1 = QHBoxLayout(); row1.addWidget(QLabel("F_Min (Hz):")); self.lin_input_fmin = QLineEdit("1e9"); row1.addWidget(self.lin_input_fmin)
        row1.addWidget(QLabel("F_Max (Hz):")); self.lin_input_fmax = QLineEdit("10e9"); row1.addWidget(self.lin_input_fmax)
        row1.addWidget(QLabel("F_Step (Hz):")); self.lin_input_fstep = QLineEdit("1e9"); row1.addWidget(self.lin_input_fstep)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("P_Min (dBm):")); self.lin_input_pmin = QLineEdit("-20"); row2.addWidget(self.lin_input_pmin)
        row2.addWidget(QLabel("P_Max (dBm):")); self.lin_input_pmax = QLineEdit("5.0"); row2.addWidget(self.lin_input_pmax)
        row2.addWidget(QLabel("P_Step (dBm):")); self.lin_input_pstep = QLineEdit("1.0"); row2.addWidget(self.lin_input_pstep)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Loss Calibration Power (dBm):"))
        self.lin_input_cal_power = QLineEdit("0.0")
        row3.addWidget(self.lin_input_cal_power)
        sweep_layout.addRow(row1); sweep_layout.addRow(row2); sweep_layout.addRow(row3)
        sweep_group.setLayout(sweep_layout)
        layout.addWidget(sweep_group)
        
        cal_layout = QHBoxLayout()
        self.btn_lin_cal_step1 = QPushButton("Step 1: Calibrate Input Cable (Sensor B at DUT Input)")
        self.btn_lin_cal_step1.setStyleSheet("background-color: #D2691E; color: white; font-weight: bold;")
        self.btn_lin_cal_step1.clicked.connect(self.trigger_linear_cal_step1)
        
        self.btn_lin_cal_step2 = QPushButton("Step 2: Calibrate Input+Output Cables (Sensor B at End)")
        self.btn_lin_cal_step2.setStyleSheet("background-color: #D2691E; color: white; font-weight: bold;")
        self.btn_lin_cal_step2.setEnabled(False) # Require Step 1 first
        self.btn_lin_cal_step2.clicked.connect(self.trigger_linear_cal_step2)
        cal_layout.addWidget(self.btn_lin_cal_step1)
        cal_layout.addWidget(self.btn_lin_cal_step2)
        layout.addLayout(cal_layout)

        btn_layout = QHBoxLayout()
        self.btn_lin_comp = QPushButton("Start Linear Compression Sweep")
        self.btn_lin_comp.setStyleSheet("background-color: #005A9E; color: white; font-weight: bold;")
        self.btn_lin_comp.setMinimumHeight(40)
        self.btn_lin_comp.clicked.connect(self.trigger_linear_sweep)
        
        self.btn_lin_export = QPushButton("Export Sweep to CSV")
        self.btn_lin_export.setMinimumHeight(40)
        self.btn_lin_export.setEnabled(False)
        self.btn_lin_export.clicked.connect(self.export_linear_csv)
        btn_layout.addWidget(self.btn_lin_comp)
        btn_layout.addWidget(self.btn_lin_export)
        layout.addLayout(btn_layout)
        
        self.lin_plot = pg.PlotWidget(title="De-Embedded Gain & Efficiency (Linear A/B Setup)")
        self.lin_plot.addLegend(offset=(10, 10))
        self.lin_plot.showGrid(x=True, y=True, alpha=0.3)
        self.lin_plot.setLabel('left', 'Gain (dB) / PAE (%)', units='')
        self.lin_plot.setLabel('bottom', 'P_in at DUT (dBm)', units='dBm')
        layout.addWidget(self.lin_plot)

    def trigger_linear_cal_step1(self):
        self.btn_lin_cal_step1.setEnabled(False)
        self.btn_lin_comp.setEnabled(False)
        
        sweep_params = {
            'f_min': float(self.lin_input_fmin.text()), 'f_max': float(self.lin_input_fmax.text()), 'f_step': float(self.lin_input_fstep.text()),
            'cal_power': float(self.lin_input_cal_power.text())
        }
        
        self.status_label.setText("Ensure Input Cable is connected to Sensor B, then Press OK.")
        # In a real GUI we might pop a QMessageBox here, but avoiding block to adhere to standards.
        
        self.lin_cal_thread = LinearCalWorker({'rfgen': self.rfgen_combo.currentText(), 'pmeter': self.pmeter_combo.currentText()}, 1, sweep_params)
        self.lin_cal_thread.log_message.connect(self.status_label.setText)
        self.lin_cal_thread.calibration_complete.connect(self.on_linear_cal_complete)
        self.lin_cal_thread.error_occurred.connect(self.handle_error)
        self.lin_cal_thread.start()

    def trigger_linear_cal_step2(self):
        self.btn_lin_cal_step2.setEnabled(False)
        self.btn_lin_comp.setEnabled(False)
        
        sweep_params = {
            'f_min': float(self.lin_input_fmin.text()), 'f_max': float(self.lin_input_fmax.text()), 'f_step': float(self.lin_input_fstep.text()),
            'cal_power': float(self.lin_input_cal_power.text())
        }
        
        self.lin_cal_thread = LinearCalWorker({'rfgen': self.rfgen_combo.currentText(), 'pmeter': self.pmeter_combo.currentText()}, 2, sweep_params)
        self.lin_cal_thread.log_message.connect(self.status_label.setText)
        self.lin_cal_thread.calibration_complete.connect(self.on_linear_cal_complete)
        self.lin_cal_thread.error_occurred.connect(self.handle_error)
        self.lin_cal_thread.start()

    def on_linear_cal_complete(self, step, cal_results):
        if step == 1:
            self.linear_cal_data['input'] = cal_results
            self.btn_lin_cal_step1.setEnabled(True)
            self.btn_lin_cal_step2.setEnabled(True)
            self.status_label.setText("Input Cable Cal Complete. Proceed to Step 2.")
        elif step == 2:
            self.linear_cal_data['total'] = cal_results
            self.btn_lin_cal_step1.setEnabled(True)
            self.btn_lin_cal_step2.setEnabled(True)
            self.btn_lin_comp.setEnabled(True)
            self.status_label.setText("Total Cable Cal Complete. System is ready to sweep.")

    def trigger_linear_sweep(self):
        self.btn_lin_comp.setEnabled(False)
        self.btn_lin_export.setEnabled(False)
        self.lin_plot.clear()
        self.linear_results = []
        
        addresses = {
            'rfgen': self.rfgen_combo.currentText(),
            'pmeter': self.pmeter_combo.currentText(),
            'gate': self.gate_combo.currentText(),
            'drain': self.drain_combo.currentText(),
            'wg': self.wg_combo.currentText(),
            'scope': self.scope_combo.currentText()
        }
        
        pulse_params = {
            'mode': self.lin_mode_combo.currentText(),
            'width': float(self.lin_input_width.text()), 'period': float(self.lin_input_period.text()), 'delay': float(self.lin_input_delay.text()),
            'vhigh': float(self.lin_input_vhigh.text()), 'vlow': float(self.lin_input_vlow.text()),   
            'trig_chan': 1, 'trig_level': 0.5, 'scope_chan': 1, 'scope_scale': float(self.input_scope_scale.text())
        }
        
        bias_params = {
            'enable': self.check_lin_bias.isChecked(),
            'vg': float(self.input_vg_start.text()), 'vg_comp': float(self.input_vg_comp.text()),
            'vd': float(self.input_vd.text()), 'vd_comp': float(self.input_vd_comp.text())
        }
        
        sweep_params = {
            'f_min': float(self.lin_input_fmin.text()), 'f_max': float(self.lin_input_fmax.text()), 'f_step': float(self.lin_input_fstep.text()),
            'p_min': float(self.lin_input_pmin.text()), 'p_max': float(self.lin_input_pmax.text()), 'p_step': float(self.lin_input_pstep.text())
        }
        
        self.lin_sweep_thread = LinearSweepWorker(addresses, pulse_params, bias_params, sweep_params, self.linear_cal_data)
        self.lin_sweep_thread.log_message.connect(self.status_label.setText)
        self.lin_sweep_thread.data_ready.connect(self.update_linear_plot)
        self.lin_sweep_thread.sequence_complete.connect(self.on_linear_sweep_complete)
        self.lin_sweep_thread.error_occurred.connect(self.handle_error)
        self.lin_sweep_thread.start()

    def update_linear_plot(self, freq, pin, pout, gain, pae, vg_m, ig_m, vd_m, id_m):
        self.linear_results.append({
            'frequency': freq, 'pin': pin, 'pout': pout, 'gain': gain, 'pae': pae,
            'vg_m': vg_m, 'ig_m': ig_m, 'vd_m': vd_m, 'id_m': id_m
        })
        self.lin_plot.plot(pin, gain, pen=pg.mkPen('g', width=2), symbol='s', name=f"Gain @ {freq/1e9:.2f} GHz (dB)")
        self.lin_plot.plot(pin, pae, pen=pg.mkPen('m', width=2), symbol='x', name=f"PAE @ {freq/1e9:.2f} GHz (%)")

    def on_linear_sweep_complete(self):
        self.btn_lin_comp.setEnabled(True)
        if self.linear_results:
            self.btn_lin_export.setEnabled(True)
            self.status_label.setText("Linear Compression Sweep completed. Data ready to export.")

    def export_linear_csv(self):
        if not self.linear_results: return
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Linear Compression Data", "", "CSV Files (*.csv)")
        if file_path:
            try:
                pn = self.input_pn.text()
                sn = self.input_sn.text()
                lot = self.input_lot.text()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                file_exists = os.path.exists(file_path) and os.path.getsize(file_path) > 0
                with open(file_path, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["Execution Timestamp", "Part Number", "Serial Number", "Lot Number", "Frequency (Hz)", "De-Embedded Pin (dBm)", "De-Embedded Pout (dBm)", "Gain (dB)", "PAE (%)", "Gate Voltage (V)", "Gate Current (A)", "Drain Voltage (V)", "Drain Current (A)"])
                    for sweep in self.linear_results:
                        f_hz = sweep['frequency']
                        pins, pouts, gains, paes = sweep['pin'], sweep['pout'], sweep['gain'], sweep['pae']
                        vgs, igs, vds, ids = sweep['vg_m'], sweep['ig_m'], sweep['vd_m'], sweep['id_m']
                        for i in range(len(pins)):
                            writer.writerow([timestamp, pn, sn, lot, f_hz, pins[i], pouts[i], gains[i], paes[i], vgs[i], igs[i], vds[i], ids[i]])
                self.status_label.setText(f"Exported successfully to {os.path.basename(file_path)}.")
            except Exception as e:
                self.handle_error(f"Failed to export CSV: {str(e)}")

    # =========================================================================
    # EXECUTION LOGIC
    # =========================================================================

    def scan_hardware(self):
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
            if resources:
                for combo in [self.vna_combo, self.sa_combo, self.gate_combo, self.drain_combo, self.wg_combo, self.scope_combo]:
                    combo.clear()
                    combo.addItems(resources)
                self.status_label.setText("Scan Complete.")
        except Exception as e:
            self.status_label.setText(f"Scan failed: {e}")

    def trigger_ecal_calibration(self):
        self.btn_cal.setEnabled(False)
        self.btn_sweep.setEnabled(False)
        self.btn_export_vna.setEnabled(False)
        
        vna_params = {
            'f_start': float(self.input_vna_fstart.text()),
            'f_stop': float(self.input_vna_fstop.text()),
            'points': int(self.input_vna_points.text()),
            'power': float(self.input_vna_power.text()),
            'ifbw': float(self.input_vna_ifbw.text()),
            'avg_enable': self.check_vna_avg.isChecked(),
            'avg_factor': int(self.input_vna_avg_factor.text())
        }
        
        self.cal_thread = VNACalWorker(self.vna_combo.currentText(), vna_params)
        self.cal_thread.log_message.connect(self.status_label.setText)
        self.cal_thread.error_occurred.connect(self.handle_error)
        self.cal_thread.sequence_complete.connect(self.on_ecal_complete)
        self.cal_thread.start()

    def on_ecal_complete(self):
        self.btn_cal.setEnabled(True)
        self.btn_sweep.setEnabled(True)
        self.status_label.setText("ECal Complete. VNA is calibrated and ready to test.")

    def trigger_vna_sweep(self):
        self.btn_cal.setEnabled(False)
        self.btn_sweep.setEnabled(False)
        self.btn_export_vna.setEnabled(False)
        self.vna_results = None
        
        vna_params = {
            'f_start': float(self.input_vna_fstart.text()),
            'f_stop': float(self.input_vna_fstop.text()),
            'points': int(self.input_vna_points.text()),
            'power': float(self.input_vna_power.text()),
            'ifbw': float(self.input_vna_ifbw.text()),
            'avg_enable': self.check_vna_avg.isChecked(),
            'avg_factor': int(self.input_vna_avg_factor.text())
        }
        
        addresses = {
            'gate': self.gate_combo.currentText(),
            'drain': self.drain_combo.currentText()
        }
        
        bias_params = {
            'enable': self.check_vna_bias.isChecked(),
            'vg_start': float(self.input_vg_start.text()),
            'vg_comp': float(self.input_vg_comp.text()),
            'vd': float(self.input_vd.text()),
            'vd_comp': float(self.input_vd_comp.text())
        }
        
        self.vna_thread = VNASweepWorker(self.vna_combo.currentText(), vna_params, addresses, bias_params)
        self.vna_thread.data_ready.connect(self.update_vna_plots)
        self.vna_thread.error_occurred.connect(self.handle_error)
        self.vna_thread.start()

    def update_vna_plots(self, data):
        self.plot_widget.clear()
        self.k_plot_widget.clear()
        
        self.vna_results = data
        
        if "S11" in data: self.plot_widget.plot(data["S11"], pen='y', name="S11")
        if "S21" in data: self.plot_widget.plot(data["S21"], pen='g', name="S21")
        if "S12" in data: self.plot_widget.plot(data["S12"], pen='c', name="S12")
        if "S22" in data: self.plot_widget.plot(data["S22"], pen='m', name="S22")
        
        if "K_Factor" in data:
            self.k_plot_widget.plot(data["K_Factor"], pen=pg.mkPen('w', width=2), name="K-Factor")
            boundary_line = pg.InfiniteLine(pos=1.0, angle=0, pen=pg.mkPen('r', width=1.5, style=Qt.PenStyle.DashLine))
            self.k_plot_widget.addItem(boundary_line)
        
        self.btn_cal.setEnabled(True)
        self.btn_sweep.setEnabled(True)
        self.btn_export_vna.setEnabled(True)
        self.status_label.setText("S-Parameter Sweep Complete. Data ready to export.")

    def export_vna_csv(self):
        if not self.vna_results:
            self.status_label.setText("No S-Parameter data found to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export S-Parameter Data", "", "CSV Files (*.csv)")
        if file_path:
            try:
                pn = self.input_pn.text()
                sn = self.input_sn.text()
                lot = self.input_lot.text()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

                file_exists = os.path.exists(file_path) and os.path.getsize(file_path) > 0

                with open(file_path, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    
                    if not file_exists:
                        writer.writerow([
                            "Execution Timestamp", "Part Number", "Serial Number", "Lot Number",
                            "Frequency (Hz)", "S11 (dB)", "S21 (dB)", "S12 (dB)", "S22 (dB)", "K-Factor",
                            "Gate Voltage (V)", "Gate Current (A)", "Drain Voltage (V)", "Drain Current (A)"
                        ])
                    
                    freqs = self.vna_results.get("Frequency", [])
                    s11 = self.vna_results.get("S11", [])
                    s21 = self.vna_results.get("S21", [])
                    s12 = self.vna_results.get("S12", [])
                    s22 = self.vna_results.get("S22", [])
                    k_factor = self.vna_results.get("K_Factor", [])
                    
                    vg_val = self.vna_results.get("Vg_meas", 0.0)
                    ig_val = self.vna_results.get("Ig_meas", 0.0)
                    vd_val = self.vna_results.get("Vd_meas", 0.0)
                    id_val = self.vna_results.get("Id_meas", 0.0)

                    num_points = max(len(s11), len(s21), len(s12), len(s22), len(freqs))
                    if len(freqs) == 0 and num_points > 0:
                        f_start = float(self.input_vna_fstart.text())
                        f_stop = float(self.input_vna_fstop.text())
                        freqs = [f_start + i * (f_stop - f_start) / (num_points - 1) for i in range(num_points)] if num_points > 1 else [f_start]

                    row_count = 0
                    for i in range(num_points):
                        f_val = freqs[i] if i < len(freqs) else ""
                        s11_val = s11[i] if i < len(s11) else ""
                        s21_val = s21[i] if i < len(s21) else ""
                        s12_val = s12[i] if i < len(s12) else ""
                        s22_val = s22[i] if i < len(s22) else ""
                        k_val = k_factor[i] if i < len(k_factor) else ""
                        
                        writer.writerow([
                            timestamp, pn, sn, lot,
                            f_val, s11_val, s21_val, s12_val, s22_val, k_val,
                            vg_val, ig_val, vd_val, id_val
                        ])
                        row_count += 1
                
                self.status_label.setText(f"Appended {row_count} S-Parameter rows to {os.path.basename(file_path)} successfully.")
            except Exception as e:
                self.handle_error(f"Failed to export S-Parameter CSV: {str(e)}")

    def trigger_bias_sequence(self):
        self.btn_bias.setEnabled(False)
        
        addresses = {
            'gate': self.gate_combo.currentText(),
            'drain': self.drain_combo.currentText(),
            'scope': self.scope_combo.currentText()
        }
        
        bias_params = {
            'vd': float(self.input_vd.text()),
            'vd_comp': float(self.input_vd_comp.text()),
            'vg_comp': float(self.input_vg_comp.text()),
            'vg_start': float(self.input_vg_start.text()),
            'vg_stop': float(self.input_vg_stop.text()),
            'vg_step': float(self.input_vg_step.text()),
            'target_idd': float(self.input_target_idd.text()),
            'id_tol': float(self.input_id_tol.text()),
            'target_mode': self.check_target_bias.isChecked(),
            'scope_chan': 1,
            'scope_scale': float(self.input_scope_scale.text())
        }
        
        self.bias_thread = GaNBiasWorker(addresses, bias_params)
        self.bias_thread.log_message.connect(self.status_label.setText)
        self.bias_thread.telemetry_update.connect(
            lambda vg, vd, id: self.telemetry_label.setText(f"Telemetry: Vg={vg:.2f}V | Vd={vd:.2f}V | Id={id:.3f}A")
        )
        self.bias_thread.sequence_complete.connect(lambda: self.btn_bias.setEnabled(True))
        self.bias_thread.error_occurred.connect(self.handle_error)
        self.bias_thread.start()

    def trigger_gain_comp_power_calibration(self):
        self.btn_comp_cal.setEnabled(False)
        self.btn_comp.setEnabled(False)
        self.btn_export_comp.setEnabled(False)
        
        try:
            f_min = float(self.input_fmin.text())
            f_max = float(self.input_fmax.text())
            f_step = float(self.input_fstep.text())
            power = float(self.input_comp_cal_power.text())
            
            points = int(abs(f_max - f_min) / f_step) + 1 if f_step != 0 else 1
            
            params = {
                'f_start': f_min,
                'f_stop': f_max,
                'points': points,
                'power': power
            }
            
            self.comp_cal_thread = GainCompCalWorker(self.vna_combo.currentText(), params)
            self.comp_cal_thread.log_message.connect(self.status_label.setText)
            self.comp_cal_thread.error_occurred.connect(self.handle_error)
            self.comp_cal_thread.sequence_complete.connect(self.on_comp_power_cal_complete)
            self.comp_cal_thread.start()
            
        except Exception as e:
            self.handle_error(f"Failed to parse power cal parameters: {e}")

    def on_comp_power_cal_complete(self):
        self.btn_comp_cal.setEnabled(True)
        self.btn_comp.setEnabled(True)
        if self.compression_results:
            self.btn_export_comp.setEnabled(True)
        self.status_label.setText("Source Power Calibration Complete. Ready for Gain Compression Sweep.")

    def trigger_compression_sweep(self):
        self.btn_comp_cal.setEnabled(False)
        self.btn_comp.setEnabled(False)
        self.btn_export_comp.setEnabled(False)
        self.comp_plot.clear()
        
        self.compression_results = []
        
        addresses = {
            'vna': self.vna_combo.currentText(),
            'gate': self.gate_combo.currentText(),
            'drain': self.drain_combo.currentText(),
            'wg': self.wg_combo.currentText(),
            'scope': self.scope_combo.currentText()
        }
        
        pulse_params = {
            'mode': self.mode_combo.currentText(),
            'width': float(self.input_width.text()),
            'period': float(self.input_period.text()),
            'delay': float(self.input_delay.text()),
            'vhigh': float(self.input_vhigh.text()), 
            'vlow': float(self.input_vlow.text()),   
            'trig_chan': 1,
            'trig_level': 0.5,
            'scope_chan': 1,
            'scope_scale': float(self.input_scope_scale.text())
        }
        
        bias_params = {
            'enable': self.check_comp_bias.isChecked(),
            'vg': float(self.input_vg_start.text()), 
            'vg_comp': float(self.input_vg_comp.text()),
            'vd': float(self.input_vd.text()),
            'vd_comp': float(self.input_vd_comp.text())
        }
        
        sweep_params = {
            'f_min': float(self.input_fmin.text()), 'f_max': float(self.input_fmax.text()), 'f_step': float(self.input_fstep.text()),
            'p_min': float(self.input_pmin.text()), 'p_max': float(self.input_pmax.text()), 'p_step': float(self.input_pstep.text())
        }
        
        self.comp_thread = GainCompressionWorker(addresses, pulse_params, bias_params, sweep_params)
        self.comp_thread.log_message.connect(self.status_label.setText)
        self.comp_thread.data_ready.connect(self.update_comp_plot)
        self.comp_thread.sequence_complete.connect(self.on_compression_complete)
        self.comp_thread.error_occurred.connect(self.handle_error)
        self.comp_thread.start()

    def update_comp_plot(self, freq, pin, pout, gain, pae, vg_m, ig_m, vd_m, id_m):
        self.compression_results.append({
            'frequency': freq,
            'pin': pin,
            'pout': pout,
            'gain': gain,
            'pae': pae,
            'vg_m': vg_m,
            'ig_m': ig_m,
            'vd_m': vd_m,
            'id_m': id_m
        })
        
        self.comp_plot.clear() 
        self.comp_plot.plot(pin, pout, pen=pg.mkPen('b', width=2), symbol='o', name=f"Pout @ {freq/1e9:.2f} GHz (dBm)")
        self.comp_plot.plot(pin, gain, pen=pg.mkPen('g', width=2), symbol='s', name=f"Gain @ {freq/1e9:.2f} GHz (dB)")
        self.comp_plot.plot(pin, pae, pen=pg.mkPen('m', width=2), symbol='x', name=f"PAE @ {freq/1e9:.2f} GHz (%)")

    def on_compression_complete(self):
        self.btn_comp_cal.setEnabled(True)
        self.btn_comp.setEnabled(True)
        if self.compression_results:
            self.btn_export_comp.setEnabled(True)
            self.status_label.setText("Gain Compression Sweep completed. Data ready to export.")

    def export_compression_csv(self):
        if not self.compression_results:
            self.status_label.setText("No sweep data found to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Gain Compression Data", "", "CSV Files (*.csv)")
        if file_path:
            try:
                pn = self.input_pn.text()
                sn = self.input_sn.text()
                lot = self.input_lot.text()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

                file_exists = os.path.exists(file_path) and os.path.getsize(file_path) > 0

                with open(file_path, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    
                    if not file_exists:
                        writer.writerow([
                            "Execution Timestamp", "Part Number", "Serial Number", "Lot Number",
                            "Frequency (Hz)", "Input Power (dBm)", "Output Power (dBm)", "Gain (dB)", "Power Added Efficiency (%)",
                            "Gate Voltage (V)", "Gate Current (A)", "Drain Voltage (V)", "Drain Current (A)"
                        ])
                    
                    row_count = 0
                    for sweep in self.compression_results:
                        f_hz = sweep['frequency']
                        pins = sweep['pin']
                        pouts = sweep['pout']
                        gains = sweep['gain']
                        paes = sweep['pae']
                        vgs = sweep['vg_m']
                        igs = sweep['ig_m']
                        vds = sweep['vd_m']
                        ids = sweep['id_m']
                        
                        for i in range(len(pins)):
                            writer.writerow([
                                timestamp, pn, sn, lot,
                                f_hz, pins[i], pouts[i], gains[i], paes[i],
                                vgs[i], igs[i], vds[i], ids[i]
                            ])
                            row_count += 1
                
                self.status_label.setText(f"Appended {row_count} rows to {os.path.basename(file_path)} successfully.")
            except Exception as e:
                self.handle_error(f"Failed to export CSV: {str(e)}")

    def trigger_harmonics(self):
        self.btn_harm.setEnabled(False)
        self.harm_plot.clear()
        
        active_targets = {name: mult for name, (chk, mult) in self.harm_checks.items() if chk.isChecked()}
        if not active_targets:
            self.status_label.setText("Please select at least one harmonic to measure.")
            self.btn_harm.setEnabled(True)
            return

        addresses = {
            'vna': self.vna_combo.currentText(), 'sa': self.sa_combo.currentText(),
            'gate': self.gate_combo.currentText(), 'drain': self.drain_combo.currentText()
        }
        
        bias_params = {
            'enable': self.check_harm_bias.isChecked(),
            'vg': float(self.input_vg_start.text()), 
            'vg_comp': float(self.input_vg_comp.text()),
            'vd': float(self.input_vd.text()),
            'vd_comp': float(self.input_vd_comp.text())
        }

        self.harm_thread = HarmonicsWorker(
            addresses, float(self.input_harm_f0.text()), float(self.input_harm_pin.text()),
            bias_params, active_targets
        )
        self.harm_thread.log_message.connect(self.status_label.setText)
        self.harm_thread.data_ready.connect(self.update_harmonics_plot)
        self.harm_thread.sequence_complete.connect(lambda: self.btn_harm.setEnabled(True))
        self.harm_thread.error_occurred.connect(self.handle_error)
        self.harm_thread.start()

    def update_harmonics_plot(self, labels, powers_dbc):
        self.harm_plot.clear()
        x = list(range(len(labels)))
        bg = pg.BarGraphItem(x=x, height=powers_dbc, width=0.6, brush='c')
        self.harm_plot.addItem(bg)
        ax = self.harm_plot.getAxis('bottom')
        ticks = [list(zip(x, labels))]
        ax.setTicks(ticks)

    def handle_error(self, msg):
        self.status_label.setText(f"ERROR: {msg}")
        self.status_label.setStyleSheet("font-weight: bold; color: #AA0000;")
        if hasattr(self, 'btn_cal'): self.btn_cal.setEnabled(True)
        if hasattr(self, 'btn_sweep'): self.btn_sweep.setEnabled(True)
        if hasattr(self, 'btn_bias'): self.btn_bias.setEnabled(True)
        if hasattr(self, 'btn_comp_cal'): self.btn_comp_cal.setEnabled(True)
        if hasattr(self, 'btn_comp'): self.btn_comp.setEnabled(True)
        if hasattr(self, 'btn_harm'): self.btn_harm.setEnabled(True)
        if hasattr(self, 'btn_lin_cal_step1'): self.btn_lin_cal_step1.setEnabled(True)
        if hasattr(self, 'btn_lin_cal_step2'): self.btn_lin_cal_step2.setEnabled(True)
        if hasattr(self, 'btn_lin_comp'): self.btn_lin_comp.setEnabled(True)

    def global_emergency_kill(self):
        self.status_label.setText("EMERGENCY SHUTDOWN EXECUTED.")
        self.status_label.setStyleSheet("font-weight: bold; color: #AA0000;")
        try:
            rm = pyvisa.ResourceManager()
            VectorNetworkAnalyzer(self.vna_combo.currentText()).connect(rm) and VectorNetworkAnalyzer(self.vna_combo.currentText()).rf_off()
            PowerSupply(self.drain_combo.currentText()).connect(rm) and PowerSupply(self.drain_combo.currentText()).emergency_shutdown()
            PowerSupply(self.gate_combo.currentText()).connect(rm) and PowerSupply(self.gate_combo.currentText()).output_off(1)
        except: pass

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        main_window = TestExecutiveGUI()
        main_window.show()
        sys.exit(app.exec())
    except Exception as base_e:
        print(f"\nCRITICAL GUI FAILURE: {base_e}\n")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")