import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from hka.config import load_config, upgrade_legacy_mail, validate_config
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows, run_game_flow
from hka.ocr import TextHit
from hka.runner import Cancelled, SkipTask
from hka.web import config_snapshot


def hit(text, x, y):
    return TextHit(text, 1, x, y, 100, 30)


def inbox():
    return [hit('体力领取', 1082, 152), hit('一键领取', 1397, 153), hit('一键删除', 851, 153)]


class MailRewardsTests(unittest.TestCase):
    def setUp(self):
        runner = Mock()
        runner.cancel = threading.Event()
        runner._size.return_value = (1600, 900)
        self.flow = GameFlows(runner)
        self.flow._home = Mock()
        self.flow._dismiss_navigation_card = Mock(return_value=False)

    def test_navigation_retries_social_then_claims_and_returns_home(self):
        frames = [[hit('社交', 1350, 82)], [hit('社交', 1350, 82)],
                  [hit('我的消息', 792, 427)], inbox(),
                  [hit('已领取以下奖励', 650, 80)], inbox(), inbox(), inbox(), inbox()]
        with patch.object(self.flow, '_hits', side_effect=frames):
            self.assertEqual(self.flow.claim_mail({}), {'claimed': True})
        self.assertEqual(self.flow._home.call_count, 2)
        taps = [call.args for call in self.flow.device.tap.call_args_list]
        self.assertEqual(taps[:3], [(1400, 97), (1400, 97), (842, 442)])
        self.assertEqual(taps[3], (1447, 168))
        self.assertEqual(len(taps), 5)

    def test_no_attachments_is_skipped_after_confirming_inbox(self):
        with patch.object(self.flow, '_hits', return_value=inbox()):
            with self.assertRaisesRegex(SkipTask, '没有可领取附件'):
                self.flow.claim_mail({})
        self.flow.device.tap.assert_called_once_with(1447, 168)
        self.assertEqual(self.flow._home.call_count, 2)

    def test_unrecognized_page_is_not_treated_as_empty_inbox(self):
        with patch.object(self.flow, '_hits', side_effect=[inbox()] + [[hit('网络异常', 700, 400)]] * 12):
            with self.assertRaisesRegex(RuntimeError, '未确认处理结果'):
                self.flow.claim_mail({})
        self.flow.device.tap.assert_called_once()

    def test_shop_claim_button_does_not_count_as_mailbox(self):
        with patch.object(self.flow, '_hits', return_value=[hit('一键领取', 1397, 153)]):
            with self.assertRaisesRegex(RuntimeError, '未确认我的消息邮箱'):
                self.flow.claim_mail({})
        self.flow.device.tap.assert_not_called()

    def test_ambiguous_claim_buttons_are_not_clicked(self):
        with patch.object(self.flow, '_hits', return_value=inbox() + [hit('一键领取', 1450, 153)]):
            with self.assertRaisesRegex(RuntimeError, '未确认邮箱的一键领取'):
                self.flow.claim_mail({})
        self.flow.device.tap.assert_not_called()

    def test_cancel_during_navigation_does_not_click(self):
        self.flow.runner.cancel.set()
        with patch.object(self.flow, '_hits', return_value=[hit('社交', 1350, 82)]):
            with self.assertRaises(Cancelled):
                self.flow.claim_mail({})
        self.flow.device.tap.assert_not_called()

    def test_mail_flow_is_registered(self):
        with patch.object(GameFlows, 'claim_mail', return_value={'claimed': True}) as claim:
            self.assertEqual(run_game_flow(self.flow.runner, {'flow': 'claim_mail'}), {'claimed': True})
        claim.assert_called_once_with({})


class MailConfigTests(unittest.TestCase):
    def legacy(self):
        data = copy.deepcopy(DEFAULT_CONFIG)
        task = next(t for t in data['tasks'] if t['id'] == 'mail')
        task['enabled'] = True
        task['steps'] = [{'type': 'tap_image', 'image': image} for image in
                         ['social.png', 'mail.png', 'mail_claim.png', 'back.png', 'home_nav.png']]
        return data, task

    def test_known_legacy_mail_steps_upgrade_in_load_and_editor_without_rewriting_file(self):
        data, task = self.legacy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            raw = json.dumps(data).encode('utf-8')
            path.write_bytes(raw)
            for config in (load_config(path), config_snapshot(path)):
                mail = next(t for t in config['tasks'] if t['id'] == 'mail')
                self.assertTrue(mail['enabled'])
                self.assertEqual(mail['steps'], [{'type': 'game_flow', 'flow': 'claim_mail'}])
            self.assertEqual(path.read_bytes(), raw)

    def test_custom_mail_steps_are_preserved(self):
        data, task = self.legacy()
        task['steps'][1]['offset_x'] = 0.1
        original = copy.deepcopy(task)
        upgrade_legacy_mail(data)
        self.assertEqual(task, original)

    def test_default_mail_no_longer_needs_templates(self):
        validate_config(DEFAULT_CONFIG)
        task = next(t for t in DEFAULT_CONFIG['tasks'] if t['id'] == 'mail')
        self.assertEqual(task['steps'], [{'type': 'game_flow', 'flow': 'claim_mail'}])
