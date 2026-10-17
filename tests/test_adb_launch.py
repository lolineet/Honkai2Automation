import unittest
from unittest.mock import patch

from hka.adb import AdbDevice, AdbError
from hka.cancellation import Cancelled


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.device = AdbDevice({'adb_path': 'adb', 'serial': 'fake', 'package': 'com.game'})

    def test_launch_without_monkey_and_ignore_resolver_header(self):
        with patch.object(self.device, '_run', side_effect=[
                'priority=0 match=0x108000\ncom.game/.MainActivity',
                'Starting: Intent { cmp=com.game/.MainActivity }']) as run:
            self.device.launch_game()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[-2:], ('-n', 'com.game/.MainActivity'))

    def test_older_android_pm_resolver(self):
        with patch.object(self.device, '_run', side_effect=[
                AdbError('unknown command'), 'com.game/com.game.Main$Activity', '']) as run:
            self.device.launch_game()
        self.assertEqual(run.call_args_list[1].args[1], 'pm')

    def test_monkey_compatibility_fallback(self):
        with patch.object(self.device, '_run', side_effect=[
                'No activity found', AdbError('unsupported'), 'Events injected: 1']) as run:
            self.device.launch_game()
        self.assertEqual(run.call_args.args[1], 'monkey')

    def test_launch_failure_is_not_hidden_by_zero_exit_status(self):
        with patch.object(self.device, '_run', side_effect=[
                'com.game/.Main', 'Error type 3\nActivity does not exist',
                'No activity found', AdbError('monkey: inaccessible or not found')]):
            with self.assertRaisesRegex(AdbError, '(?s)包名.*Error type 3.*monkey'):
                self.device.launch_game()

    def test_cancellation_does_not_attempt_fallback(self):
        with patch.object(self.device, '_run', side_effect=Cancelled()) as run:
            with self.assertRaises(Cancelled):
                self.device.launch_game()
        self.assertEqual(run.call_count, 1)
