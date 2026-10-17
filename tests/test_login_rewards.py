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


def birthday():
    return [hit('HAPPY', 1039, 235), hit('BIRTHDAY', 813, 353),
            hit('10/18', 869, 482), hit('希儿·芙乐艾', 1219, 524), hit('生日快乐', 1088, 646)]


def birthday_receipt():
    return [hit('有你的祝福，希儿很开心！', 618, 275), hit('领取', 757, 553)]


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

    def test_birthday_follows_daily_and_consecutive_without_configuration(self):
        frames = [panel('每日签到'), panel('烈火新生连登福利'),
                  birthday(), birthday(), birthday_receipt(), birthday_receipt(), [], [], []]
        with patch.object(self.flow, '_hits', side_effect=frames):
            result = self.flow.claim_login_rewards({})
        self.assertEqual(result['pages'], ['每日签到', '烈火新生连登福利', '角色生日奖励'])
        self.assertEqual(self.runner.device.tap.call_count, 4)

    def test_can_resume_at_birthday_claim_dialog(self):
        with patch.object(self.flow, '_hits', side_effect=[birthday_receipt(), [], [], []]):
            self.assertEqual(self.flow.claim_login_rewards({})['pages'], ['角色生日奖励'])
        self.runner.device.tap.assert_called_once_with(807, 568)

    def test_birthday_is_recognized_as_entered_game(self):
        with patch.object(self.flow, '_hits', return_value=birthday()):
            self.flow.enter_game({})
        self.runner.device.tap.assert_not_called()

    def test_birthday_button_alone_is_not_enough_to_click(self):
        with patch.object(self.flow, '_hits', return_value=[hit('生日快乐', 1088, 646),
                hit('领取', 757, 553)]):
            self.assertEqual(self.flow.claim_login_rewards({})['claimed'], 0)
        self.runner.device.tap.assert_not_called()

    def test_stuck_birthday_wish_is_clicked_only_once(self):
        with patch.object(self.flow, '_hits', return_value=birthday()):
            with self.assertRaisesRegex(RuntimeError, '重复点击'):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_called_once()

    def test_stuck_birthday_claim_is_clicked_only_once(self):
        with patch.object(self.flow, '_hits', return_value=birthday_receipt()):
            with self.assertRaisesRegex(RuntimeError, '重复点击'):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_called_once()

    def test_missing_birthday_claim_is_reported(self):
        with patch.object(self.flow, '_hits', side_effect=[birthday(), [], [], []]):
            with self.assertRaisesRegex(RuntimeError, '未确认生日奖励'):
                self.flow.claim_login_rewards({})

    def test_birthday_receipt_button_outside_dialog_is_not_clicked(self):
        with patch.object(self.flow, '_hits', return_value=[birthday_receipt()[0],
                hit('领取', 1200, 100)]):
            with self.assertRaisesRegex(RuntimeError, '未确认领取按钮'):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_not_called()

    def test_cancel_prevents_birthday_wish(self):
        self.runner.cancel.set()
        with patch.object(self.flow, '_hits', return_value=birthday()):
            with self.assertRaises(Cancelled):
                self.flow.claim_login_rewards({})
        self.runner.device.tap.assert_not_called()

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
        self.assertFalse(any(t['id'] == 'birthday_rewards' for t in config['tasks']))
