import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hka.web import LocalServer, serve


class WebStartupTests(unittest.TestCase):
    def test_occupied_port_uses_another_port_and_publishes_actual_url(self):
        with tempfile.TemporaryDirectory() as temporary, \
             LocalServer(('127.0.0.1', 0), object) as occupied:
            port = occupied.server_address[1]
            ready = Path(temporary) / 'assistant-url.txt'
            addresses = []

            def started(server):
                self.assertNotEqual(server.server_address[1], port)
                addresses.append(f'http://127.0.0.1:{server.server_address[1]}/')
                self.assertEqual(ready.read_text(encoding='utf-8'), addresses[-1])

            with patch.object(LocalServer, 'serve_forever', started), \
                 patch('hka.web.webbrowser.open') as browser, \
                 patch.dict(os.environ, {'HKA_NO_BROWSER': '0'}):
                serve(Path('config.json'), port, fallback_port=True, ready_path=ready)
            browser.assert_called_once_with(addresses[0])
            self.assertFalse(ready.exists())

    def test_explicit_port_does_not_silently_change(self):
        with LocalServer(('127.0.0.1', 0), object) as occupied:
            with self.assertRaises(OSError):
                serve(Path('config.json'), occupied.server_address[1])

    def test_other_startup_errors_are_preserved(self):
        error = OSError(errno.EACCES, 'permission denied')
        with patch('hka.web.LocalServer', side_effect=error) as server:
            with self.assertRaises(PermissionError):
                serve(Path('config.json'), fallback_port=True)
        self.assertEqual(server.call_count, 1)
