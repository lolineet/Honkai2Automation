import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from hka.adb import AdbDevice, AdbError
from hka.cancellation import Cancelled


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.device = AdbDevice({'adb_path': 'adb', 'serial': '127.0.0.1:16416', 'package': 'game'})
        output = io.BytesIO()
        Image.new('RGB', (16, 9)).save(output, format='PNG')
        self.png = output.getvalue()

    def test_healthy_capture_does_not_reconnect(self):
        with patch.object(self.device, '_run', return_value=self.png) as run, \
             patch.object(self.device, '_execute') as execute:
            self.assertEqual(self.device.screen(), self.png)
        self.assertEqual(run.call_args.args, ('exec-out', 'screencap', '-p'))
        execute.assert_not_called()

    def test_timeout_recovers_only_selected_device_and_remembers_working_method(self):
        with patch.object(self.device, '_run', side_effect=[AdbError('timed out'), self.png, self.png]) as run, \
             patch.object(self.device, '_execute') as execute, \
             patch.object(self.device, 'connect') as connect:
            self.assertEqual(self.device.screen(), self.png)
            self.assertEqual(self.device.screen(), self.png)
        execute.assert_called_once_with([self.device.adb_path, 'disconnect', '127.0.0.1:16416'], 5)
        connect.assert_called_once()
        self.assertEqual(run.call_args_list[1].args, ('shell', '-T', 'screencap', '-p'))
        self.assertEqual(run.call_args_list[2].args, ('shell', '-T', 'screencap', '-p'))

    def test_incomplete_png_is_retried_instead_of_returned(self):
        with patch.object(self.device, '_run', side_effect=[self.png[:-12], self.png]), \
             patch.object(self.device, '_execute'), patch.object(self.device, 'connect'):
            self.assertEqual(self.device.screen(), self.png)

    def test_repeated_failure_reports_recovery_and_device(self):
        with patch.object(self.device, '_run', side_effect=[AdbError('timed out'), b'bad image', b'bad image']) as run, \
             patch.object(self.device, '_wait'), \
             patch.object(self.device, '_execute'), patch.object(self.device, 'connect'):
            with self.assertRaisesRegex(AdbError, '16416.*重连.*timed out.*截图无效'):
                self.device.screen()
        self.assertEqual(run.call_count, 3)

    def test_second_recovery_can_resume_after_transient_connect_failure(self):
        with patch.object(self.device, '_run', side_effect=[AdbError('error: closed'), self.png]) as run, \
             patch.object(self.device, '_execute') as execute, patch.object(self.device, '_wait'), \
             patch.object(self.device, 'connect', side_effect=[AdbError('device offline'), None]):
            self.assertEqual(self.device.screen(), self.png)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(execute.call_count, 1)

    def test_failed_reconnection_does_not_loop(self):
        with patch.object(self.device, '_run', side_effect=AdbError('timed out')) as run, \
             patch.object(self.device, '_execute'), \
             patch.object(self.device, 'connect', side_effect=AdbError('device offline')):
            with self.assertRaisesRegex(AdbError, 'device offline'):
                self.device.screen()
        self.assertEqual(run.call_count, 1)

    def test_cancel_during_capture_does_not_reconnect(self):
        with patch.object(self.device, '_run', side_effect=Cancelled()), \
             patch.object(self.device, '_execute') as execute:
            with self.assertRaises(Cancelled):
                self.device.screen()
        execute.assert_not_called()

    def test_cancel_during_reconnect_does_not_try_another_capture(self):
        with patch.object(self.device, '_run', side_effect=AdbError('timed out')) as run, \
             patch.object(self.device, '_execute'), \
             patch.object(self.device, 'connect', side_effect=Cancelled()):
            with self.assertRaises(Cancelled):
                self.device.screen()
        self.assertEqual(run.call_count, 1)


class MuMuAddressTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        manager = Path(self.folder.name) / 'MuMuManager.exe'
        manager.touch()
        self.device = AdbDevice({'adb_path': 'adb', 'serial': '127.0.0.1:16416', 'package': 'game',
            'executable': str(manager), 'arguments': ['control', '-v', '1', 'launch']})

    @staticmethod
    def result(info):
        return subprocess.CompletedProcess([], 0, json.dumps(info).encode(), b'')

    def test_connect_uses_current_port_for_configured_instance(self):
        info = self.result({'index': '1', 'is_process_started': True, 'is_android_started': True,
                            'adb_host_ip': '127.0.0.1', 'adb_port': 16417})
        with patch.object(self.device, '_execute', side_effect=[info, self.result({})]) as execute, \
             patch.object(self.device, '_run', return_value='device'):
            self.device.connect()
        self.assertEqual(self.device.serial, '127.0.0.1:16417')
        self.assertEqual(execute.call_args_list[0].args[0][-3:], ['info', '-v', '1'])
        self.assertEqual(execute.call_args_list[1].args[0][-2:], ['connect', '127.0.0.1:16417'])

    def test_stopped_instance_is_not_accepted_as_healthy_stale_adb(self):
        with patch.object(self.device, '_execute', return_value=self.result({
                'index': '1', 'is_process_started': False, 'is_android_started': False})), \
             patch.object(self.device, '_run', return_value='device') as run:
            with self.assertRaisesRegex(AdbError, '实例 1 尚未启动'):
                self.device.connect()
        run.assert_not_called()

    def test_auto_uses_explicit_instance_instead_of_guessing_between_devices(self):
        self.device.serial = 'auto'
        with patch.object(self.device, '_execute', side_effect=[self.result({
                'index': '1', 'adb_port': 16417}), self.result({})]), \
             patch.object(self.device, '_run', return_value='device'), \
             patch.object(self.device, '_detect_emulator') as detect:
            self.device.connect()
        detect.assert_not_called()
        self.assertEqual(self.device.serial, '127.0.0.1:16417')

    def test_no_single_configured_instance_does_not_guess(self):
        for args in ([], ['control', '-v', 'all', 'launch'], ['control', '-v', '0,1', 'launch']):
            self.device.emulator_args = args
            with patch.object(self.device, '_execute') as execute:
                self.device._refresh_mumu_address()
            execute.assert_not_called()

    def test_unrecognized_cli_output_preserves_manual_address(self):
        with patch.object(self.device, '_execute', return_value=self.result({'index': '2', 'adb_port': 16417})):
            self.device._refresh_mumu_address()
        self.assertEqual(self.device.serial, '127.0.0.1:16416')

    def test_unavailable_cli_preserves_ordinary_adb_connection(self):
        with patch.object(self.device, '_execute', side_effect=OSError('unavailable')):
            self.device._refresh_mumu_address()
        self.assertEqual(self.device.serial, '127.0.0.1:16416')

    def test_cancel_during_instance_query_is_preserved(self):
        with patch.object(self.device, '_execute', side_effect=Cancelled()):
            with self.assertRaises(Cancelled):
                self.device._refresh_mumu_address()


class ConnectionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.device = AdbDevice({'adb_path': 'adb', 'serial': '127.0.0.1:16416', 'package': 'game'})
        self.response = subprocess.CompletedProcess([], 0, b'already connected to 127.0.0.1:16416', b'')

    def test_already_connected_is_not_ready_until_get_state_succeeds(self):
        with patch.object(self.device, '_execute', return_value=self.response), \
             patch.object(self.device, '_run', side_effect=[AdbError('device offline'), 'offline', 'device']) as run, \
             patch.object(self.device, '_wait') as wait:
            self.device.connect()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(wait.call_count, 2)

    def test_final_error_keeps_real_device_failure_and_connect_response(self):
        with patch.object(self.device, '_execute', return_value=self.response), \
             patch.object(self.device, '_run', side_effect=AdbError('device offline')) as run, \
             patch.object(self.device, '_wait'):
            with self.assertRaisesRegex(AdbError, 'device offline.*already connected'):
                self.device.connect()
        self.assertEqual(run.call_count, 4)

    def test_cancel_during_ready_wait_stops_retry(self):
        with patch.object(self.device, '_execute', return_value=self.response), \
             patch.object(self.device, '_run', side_effect=AdbError('offline')) as run, \
             patch.object(self.device, '_wait', side_effect=Cancelled()):
            with self.assertRaises(Cancelled):
                self.device.connect()
        self.assertEqual(run.call_count, 1)
