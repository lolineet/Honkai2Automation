import copy
import os
from http.server import BaseHTTPRequestHandler
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hka.adb import AdbDevice, AdbError
from hka.defaults import DEFAULT_CONFIG
from hka.runner import Cancelled, TaskRunner
from hka.web import LocalServer


class CancellationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive port binding')
    def test_two_services_cannot_share_the_same_port(self):
        server = LocalServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):
                second = LocalServer(server.server_address, BaseHTTPRequestHandler)
                second.server_close()
        finally:
            server.server_close()

    def test_stop_interrupts_long_wait_and_does_not_mark_done_or_tap(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['tasks'] = [{'id': 'wait', 'name': 'wait', 'enabled': True,
                                'steps': [{'type': 'sleep', 'seconds': 60},
                                          {'type': 'tap', 'x': 1, 'y': 1}]}]
            device = Mock(emulator_path='', serial='fake', package='fake')
            device.size.return_value = (1600, 900)
            runner = TaskRunner(config, Path(temporary) / 'config.json', device=device,
                                log=lambda _: None)
            waiting = threading.Event()
            original = runner.sleep

            def wait(seconds):
                waiting.set()
                original(seconds)

            runner.sleep = wait
            results = []
            worker = threading.Thread(target=lambda: results.append(runner.run()))
            worker.start()
            self.assertTrue(waiting.wait(2))
            runner.cancel.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0]['status'], 'cancelled')
            device.tap.assert_not_called()
            self.assertFalse(runner.state_path.exists())

    def test_stop_terminates_blocked_adb_process(self):
        device = AdbDevice({'adb_path': 'adb', 'serial': 'fake', 'package': 'fake'})
        device.cancel = threading.Event()
        real_popen = subprocess.Popen
        processes = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            device.cancel.set()
            return process

        with patch('hka.adb.subprocess.Popen', side_effect=spawn):
            started = time.monotonic()
            with self.assertRaises(Cancelled):
                device._execute([sys.executable, '-c', 'import time; time.sleep(60)'], 60)
            self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(processes[0].poll())

    def test_cancel_during_connection_does_not_start_emulator(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['tasks'] = [{'id': 'task', 'name': 'task', 'enabled': True, 'steps': []}]
            device = Mock(emulator_path='emulator.exe')
            device.connect.side_effect = Cancelled()
            runner = TaskRunner(config, Path(temporary) / 'config.json', device=device,
                                log=lambda _: None)
            self.assertEqual(runner.run()['status'], 'cancelled')
            device.start_emulator.assert_not_called()

    def test_adb_directory_is_resolved_and_missing_executable_is_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {'adb_path': temporary, 'serial': 'fake', 'package': 'fake'}
            with self.assertRaisesRegex(AdbError, 'adb.exe'):
                AdbDevice(config)
            executable = Path(temporary) / 'adb.exe'
            executable.touch()
            self.assertEqual(AdbDevice(config).adb_path, str(executable.resolve()))

    def test_auto_selects_emulator_without_selecting_usb_phone(self):
        device = AdbDevice({'adb_path': 'adb', 'serial': 'auto', 'package': 'fake'})
        result = subprocess.CompletedProcess([], 0,
            b'List of devices attached\nphone123\tdevice\n127.0.0.1:16416\tdevice\n', b'')
        with patch.object(device, '_execute', return_value=result), \
             patch.object(device, '_run', return_value='device'):
            device.connect()
        self.assertEqual(device.serial, '127.0.0.1:16416')

    def test_auto_does_not_guess_between_multiple_emulators(self):
        device = AdbDevice({'adb_path': 'adb', 'serial': 'auto', 'package': 'fake'})
        result = subprocess.CompletedProcess([], 0,
            b'127.0.0.1:16416\tdevice\n127.0.0.1:16448\tdevice\n', b'')
        with patch.object(device, '_execute', return_value=result):
            with self.assertRaisesRegex(AdbError, '16448'):
                device.connect()

    def test_old_placeholder_falls_back_to_actual_connected_emulator(self):
        device = AdbDevice({'adb_path': 'adb', 'serial': '127.0.0.1:5555', 'package': 'fake'})
        result = subprocess.CompletedProcess([], 0, b'127.0.0.1:16416\tdevice\n', b'')
        with patch.object(device, '_execute', return_value=result), \
             patch.object(device, '_run', side_effect=[AdbError('not found'), 'device']):
            device.connect()
        self.assertEqual(device.serial, '127.0.0.1:16416')
