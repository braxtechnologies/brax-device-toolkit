import unittest
from unittest.mock import patch
from brax_snwriter.discovery import usb_location_from_path, check_connection


class DiscoveryTests(unittest.TestCase):
    def test_usb_topology_matches_pyserial_for_direct_and_hub_ports(self):
        self.assertEqual(usb_location_from_path('PCIROOT(0)#PCI(0801)#USBROOT(0)#USB(1)'), '1-1')
        self.assertEqual(usb_location_from_path('PCIROOT(0)#USBROOT(1)#USB(3)#USB(2)'), '2-3.2')
        with self.assertRaises(ValueError): usb_location_from_path('PCIROOT(0)')

    def test_wrong_firmware_stops_before_windows_topology_lookup(self):
        with patch('brax_snwriter.discovery.subprocess.check_output',side_effect=['BRAXUNIT00000001 device','BRAXUNIT00000001','iode','normal']) as command:
            with self.assertRaisesRegex(ValueError,'BaseOS'):
                check_connection('adb','BRAXUNIT00000001')
            self.assertEqual(command.call_count,4)

    def test_debugging_problems_stop_before_reading_properties(self):
        for state, message in [('unauthorized','Authorization pending'),('offline','offline'),('', 'not visible')]:
            with self.subTest(state=state), patch('brax_snwriter.discovery.subprocess.check_output',
                    return_value='BRAXUNIT00000001 '+state) as command:
                with self.assertRaisesRegex(ValueError,message):
                    check_connection('adb','BRAXUNIT00000001')
                self.assertEqual(command.call_count,1)

    def test_invalid_serial_never_reaches_a_shell(self):
        with patch('brax_snwriter.discovery.subprocess.check_output') as command:
            with self.assertRaises(ValueError):check_connection('adb',"'; echo bad")
            command.assert_not_called()
