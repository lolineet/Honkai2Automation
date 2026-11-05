import unittest
from unittest.mock import patch

from hka.adb import AdbDevice, AdbError
from hka.cancellation import Cancelled


class InputTests(unittest.TestCase):
    def setUp(self):
        self.device = AdbDevice({'adb_path': 'adb', 'serial': 'fake', 'package': 'game'})

    def test_permission_denial_identifies_action_without_replaying_input(self):
        for method, args, label in [('tap', (100, 200), '点按'),
                                    ('swipe', (100, 200, 300, 400, 6500), '滑动/长按'),
                                    ('key', (4,), '按键')]:
            with self.subTest(method=method), patch.object(
                    self.device, '_run', side_effect=AdbError('SecurityException: INJECT_EVENTS')) as run:
                with self.assertRaisesRegex(AdbError, f'拒绝{label}输入.*本轮已停止') as error:
                    getattr(self.device, method)(*args)
                self.assertIn('SecurityException: INJECT_EVENTS', str(error.exception))
                self.assertEqual(run.call_count, 1)

    def test_unrelated_failure_and_cancellation_are_preserved(self):
        for error in [AdbError('device offline'), Cancelled()]:
            with self.subTest(error=error), patch.object(self.device, '_run', side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    self.device.tap(100, 200)
                self.assertIs(caught.exception, error)

    def test_swipe_duration_and_coordinates_are_unchanged(self):
        with patch.object(self.device, '_run') as run:
            self.device.swipe(210, 690, 345, 690, 6500)
        run.assert_called_once_with('shell', 'input', 'swipe', '210', '690', '345', '690', '6500')

    def test_foreground_package_comes_from_resumed_activity(self):
        for text, expected in [
                ('topResumedActivity=ActivityRecord{ae26e6 u0 app.lawnchair/.LawnchairLauncher t2}', 'app.lawnchair'),
                ('mResumedActivity: ActivityRecord{abc u0 com.game/.Main t3}', 'com.game'),
                ('ResumedActivity: ActivityRecord{abc u0 com.game/.Main t3}', 'com.game'),
                ('', None)]:
            with self.subTest(text=text), patch.object(self.device, '_run', return_value=text):
                self.assertEqual(self.device.foreground_package(), expected)


if __name__ == '__main__':
    unittest.main()
