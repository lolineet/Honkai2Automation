import copy
import threading
import unittest
from unittest.mock import Mock, patch

from hka.config import validate_config
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows
from hka.ocr import TextHit
from hka.runner import Cancelled


def hit(text, x, y):
    return TextHit(text, 1, x, y, 100, 30)


def panel(title):
    return [hit(title, 630, 265), hit('第1天', 380, 475), hit('第2天', 510, 475),
            hit('领取', 1140, 605)]


class LoginRewardsTests(unittest.TestCase):
    def setUp(self):
        self.runner = Mock()
        self.runner.cancel = threading.Event()
        self.runner._size.return_value = (1600, 900)
        self.flow = GameFlows(self.runner)

    def test_daily_then_consecutive_in_one_flow(self):
        frames = [panel('每日签到'), panel('每日签到'), panel('烈火新生连登福利'), [], [], []]
        with patch.object(self.flow, '_hits', side_effect=frames):
            result = self.flow.claim_login_rewards({})
        self.assertEqual(result['pages'], ['每日签到', '烈火新生连登福利'])
        self.assertEqual(result['claimed'], 2)
        self.assertEqual(self.runner.device.tap.call_count, 2)

    def test_starts_at_consecutive_without_daily_panel(self):
        with patch.object(self.flow, '_hits', side_effect=[panel('烈火新生连登福利'), [], [], []]):
            self.assertEqual(self.flow.claim_login_rewards({})['claimed'], 1)

    def test_no_rewards_does_not_tap(self):
        with patch.object(self.flow, '_hits', return_value=[hit('首页', 800, 90)]):
            self.assertEqual(self.flow.claim_login_rewards({})['claimed'], 0)
        self.runner.device.tap.assert_not_called()

    def test_unrelated_claim_button_is_not_clicked(self):
        with patch.object(self.flow, '_hits', return_value=panel('礼包商店')):
            self.assertEqual(self.flow.claim_login_rewards({})['claimed'], 0)
        self.runner.device.tap.assert_not_called()

    def test_stuck_page_does_not_claim_twice(self):
        with patch.object(self.flow, '_hits', return_value=panel('每日签到')):
            with self.assertRaisesRegex(RuntimeError, '重复点击'):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_called_once()

    def test_reward_receipt_is_closed_before_following_panel(self):
        receipt = [hit('已领取以下奖励', 650, 80)]
        with patch.object(self.flow, '_hits', side_effect=[panel('每日签到'), receipt,
                panel('烈火新生连登福利'), [], [], []]):
            self.assertEqual(self.flow.claim_login_rewards({})['claimed'], 2)
        self.assertEqual(self.runner.device.tap.call_count, 3)

    def test_cancellation_prevents_claim(self):
        self.runner.cancel.set()
        with patch.object(self.flow, '_hits', return_value=panel('每日签到')):
            with self.assertRaises(Cancelled):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_not_called()

    def test_default_signin_is_the_combined_flow(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        validate_config(config)
        task = next(t for t in config['tasks'] if t['id'] == 'signin')
        self.assertTrue(task['enabled'])
        self.assertEqual(task['steps'], [{'type': 'game_flow', 'flow': 'claim_login_rewards'}])
