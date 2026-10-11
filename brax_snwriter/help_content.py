"""Field tooltips for the repair app."""
FIELD_HELP = {'adb': 'Path to Android platform-tools adb.exe. Choose ADB can locate it. No phone is selected '
        'automatically.',
 'serial': 'The exact ADB serial of the BaseOS phone. Refresh the list first. Changing this '
           'selection invalidates any previous COM-port selection.',
 'usb': 'Physical USB connection identified by the app. Detect the phone before changing modes.',
 'port': 'COM port verified after META entry. Normal Android has no META COM port. Do not reuse a '
         'port belonging to another phone.',
 'new': 'Assigned serial number: 16 uppercase letters or digits. The app follows it after '
        'restarting.',
 'imei1': 'The assigned 15-digit IMEI for modem record 1, including its valid check digit. A valid '
          'check digit does not prove allocation.',
 'imei2': 'Assigned distinct 15-digit IMEI for SIM 2, including its check digit.',
 'bluetooth': 'Assigned Bluetooth address in AA:BB:CC:DD:EE:FF format. Leave empty to preserve the '
              'current address.',
 'wifi': 'Assigned factory Wi-Fi address in AA:BB:CC:DD:EE:FF format. Leave empty to preserve it. '
         'Android privacy addresses can differ.'}
