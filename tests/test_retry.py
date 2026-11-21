import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hka.cancellation import Cancelled
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows
from hka.inventory import EquipmentFull
from hka.ocr import TextHit
from hka.retry import RetryExhausted, retry_call
from hka.runner import TaskRunner


class RetryTests(unittest.TestCase):
    def test_delayed_observation_succeeds_on_third_attempt(self):
        read = Mock(side_effect=[RuntimeError('loading'), RuntimeError('loading'), 42])
        sleep = Mock()
        self.assertEqual(retry_call(read, sleep=sleep, log=Mock(), label='read'), 42)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_cancellation_and_full_inventory_stop_immediately(self):
        for error in (Cancelled(), EquipmentFull()):
            read = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                retry_call(read, sleep=Mock(), log=Mock(), label='read')
            read.assert_called_once()

    def test_exhaustion_stops_following_tasks_and_forces_failure_notification(self):
        with tempfile.TemporaryDirectory() as temp:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['runtime']['stop_on_error'] = False
            config['notifications']['on_error'] = False
            config['tasks'] = [dict(id=name, name=name, enabled=True,
                                    schedule={'type': 'daily'}, steps=[])
                               for name in ('delayed', 'following')]
            device = Mock()
            device.size.return_value = (1600, 900)
            runner = TaskRunner(config, Path(temp) / 'config.json', device=device,
                                sleep=Mock(), log=Mock())
            observation = Mock(side_effect=RuntimeError('still loading'))
            runner._steps = Mock(side_effect=lambda _: retry_call(
                observation, sleep=runner.sleep, log=runner.log, label='observation'))
            runner._save_screenshot = Mock(return_value=Path(temp) / 'error.png')
            with patch('hka.runner.send_notifications', return_value=[]) as notify:
                result = runner.run()
            self.assertEqual(observation.call_count, 3)
            runner._steps.assert_called_once()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attention_required']['code'], 'retry_exhausted')
            self.assertEqual(result['not_run'][0]['status'], 'stopped_after_error')
            notify.assert_called_once()
            self.assertTrue(notify.call_args.args[0]['notifications']['on_error'])
            self.assertFalse(notify.call_args.kwargs['success'])
            self.assertIn('3 次', notify.call_args.kwargs['body'])
            self.assertFalse(config['notifications']['on_error'])

    def test_task_panel_accepts_shifted_sidebar_and_delayed_text(self):
        flow = GameFlows.__new__(GameFlows)
        flow.sleep = Mock()
        flow.log = Mock()
        flow._home = Mock()
        flow._tap = Mock()
        flow._hits = Mock(side_effect=[[], [], [TextHit('使魔探险', 1, 30, 450, 100, 30)]])
        flow._tasks()
        self.assertEqual(flow._hits.call_count, 3)
        flow._tap.assert_called_once()

    def test_connect_retry_starts_configured_emulator_once(self):
        with tempfile.TemporaryDirectory() as temp:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['tasks'] = [dict(id='ready', name='ready', enabled=True,
                                    schedule={'type': 'daily'}, steps=[])]
            device = Mock(emulator_path='emulator.exe')
            device.connect.side_effect = [RuntimeError('offline'), None]
            device.size.return_value = (1600, 900)
            runner = TaskRunner(config, Path(temp) / 'config.json', device=device,
                                sleep=Mock(), log=Mock())
            with patch('hka.runner.send_notifications', return_value=[]):
                self.assertEqual(runner.run()['status'], 'success')
            self.assertEqual(device.connect.call_count, 2)
            device.start_emulator.assert_called_once()

    def test_result_polling_preserves_progress_and_has_three_windows(self):
        flow = GameFlows.__new__(GameFlows)
        flow.sleep = Mock()
        flow.log = Mock()
        self.assertEqual(list(flow._poll(2, 'result')), [0, 1, 0, 1, 0, 1])
        self.assertEqual(flow.log.call_count, 3)

