import pyvisa
from instrument import BaseInstrument

def main():
    print("--- Initializing Hardware Scanner ---")
    
    # 1. Boot up the PyVISA Resource Manager
    rm = pyvisa.ResourceManager()
    
    # 2. Scan the bus for connected instruments
    resources = rm.list_resources()
    
    if not resources:
        print("No instruments found! Check your GPIB cables and USB adapters.")
        return

    print("\nFound the following connections:")
    for i, res in enumerate(resources):
        print(f"[{i}] {res}")
    
    # 3. Grab the first instrument on the list (or change the index to match a specific one)
    target_address = resources[0] 
    
    print(f"\nAttempting to handshake with {target_address}...")
    
    # Instantiate your new HAL class
    test_device = BaseInstrument(resource_address=target_address, name="Bench_Device_1")
    
    # Connect and test
    if test_device.connect(rm):
        # If it connected, ask it if there are any errors in its queue
        error_status = test_device.query("SYST:ERR?")
        print(f"Current Instrument Error Queue: {error_status.strip() if error_status else 'None'}")
        
        # Always clean up
        test_device.disconnect()
    else:
        print("Hardware handshake failed.")

if __name__ == "__main__":
    main()