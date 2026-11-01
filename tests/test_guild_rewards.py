import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from hka.config import load_config, upgrade_guild_daily, validate_config
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows, run_game_flow
from hka.ocr import TextHit
from hka.runner import Cancelled, SkipTask
from hka.web import config_snapshot


def hit(text, x, y):
    return TextHit(text, 1, x, y, 80, 40)


def guild(status='可领'):
    return [hit('社团名', 628, 288), hit('成员数', 1073, 443),
            hit('活跃度：1190', 520, 614), hit(status, 1198, 545)]


def preview():
    return guild() + [hit('昨日社团贡献奖励', 661, 188),
                      hit('领取', 653, 643), hit('返回', 933, 643)]


def success():
    return [hit('提示', 750, 289), hit('奖励领取成功', 527, 382),
            hit('确定', 749, 540), hit('社团宣言', 404, 669), hit('不可领', 1182, 548)]


class GuildRewardsTests(unittest.TestCase):
    def setUp(self):
        runner = Mock()
        runner.cancel = threading.Event()
        runner._size.return_value = (1600, 900)
        self.flow = GameFlows(runner)
        self.flow._home = Mock()
        self.flow._dismiss_navigation_card = Mock(return_value=False)

    def test_complete_daily_claim_checks_preview_and_success(self):
        home = [hit('社交', 1350, 82)]
        frames = [home, home, [hit('我的社团', 791, 284)], guild(),
                  preview(), preview(), success(), success(), guild('不可领')]
        with patch.object(self.flow, '_hits', side_effect=frames):
            self.assertEqual(self.flow.claim_guild_daily({}), {'claimed': True})
        self.assertEqual(self.flow.device.tap.call_count, 5)
        self.assertEqual(self.flow._home.call_count, 2)
        taps = [call.args for call in self.flow.device.tap.call_args_list]
        self.assertEqual(taps[2:], [(1238, 565), (693, 663), (789, 560)])

    def test_already_claimed_does_not_click(self):
        with patch.object(self.flow, '_hits', return_value=guild('不可领')):
            with self.assertRaisesRegex(SkipTask, '当前不可领'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_resume_at_success_dialog_confirms_without_claiming_again(self):
        with patch.object(self.flow, '_hits', side_effect=[success(), guild('不可领')]):
            self.assertEqual(self.flow.claim_guild_daily({}), {'claimed': True})
        self.flow.device.tap.assert_called_once_with(789, 560)

    def test_unjoined_guild_is_skipped_without_joining(self):
        with patch.object(self.flow, '_hits', return_value=[hit('尚未加入社团', 600, 350)]):
            with self.assertRaisesRegex(SkipTask, '尚未加入社团'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_unrelated_claim_does_not_count_as_guild(self):
        with patch.object(self.flow, '_hits', return_value=[hit('可领', 1198, 545)]):
            with self.assertRaisesRegex(RuntimeError, '未确认我的社团'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_stuck_preview_does_not_claim_repeatedly(self):
        with patch.object(self.flow, '_hits', return_value=preview()):
            with self.assertRaisesRegex(RuntimeError, '停止重复点击'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_called_once_with(693, 663)

    def test_stuck_available_button_is_clicked_once(self):
        with patch.object(self.flow, '_hits', return_value=guild()):
            with self.assertRaisesRegex(RuntimeError, '停止重复点击'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_called_once_with(1238, 565)

    def test_unknown_status_is_not_reported_as_claimed(self):
        with patch.object(self.flow, '_hits', return_value=guild('捐献')):
            with self.assertRaisesRegex(RuntimeError, '未确认已领取状态'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_ambiguous_preview_claim_is_not_clicked(self):
        with patch.object(self.flow, '_hits', return_value=preview() + [hit('领取', 700, 643)]):
            with self.assertRaisesRegex(RuntimeError, '未确认社团贡献奖励弹窗'):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_cancel_prevents_claim(self):
        self.flow.runner.cancel.set()
        with patch.object(self.flow, '_hits', return_value=guild()):
            with self.assertRaises(Cancelled):
                self.flow.claim_guild_daily({})
        self.flow.device.tap.assert_not_called()

    def test_flow_is_registered(self):
        with patch.object(GameFlows, 'claim_guild_daily', return_value={'claimed': True}) as claim:
            self.assertEqual(run_game_flow(self.flow.runner, {'flow': 'claim_guild_daily'}), {'claimed': True})
        claim.assert_called_once_with({})


class GuildConfigTests(unittest.TestCase):
    def test_existing_standard_daily_config_gets_enabled_guild_after_mail(self):
        data = copy.deepcopy(DEFAULT_CONFIG)
        data['tasks'] = [t for t in data['tasks'] if t['id'] != 'guild_daily']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            raw = json.dumps(data).encode('utf-8')
            path.write_bytes(raw)
            for config in (load_config(path), config_snapshot(path)):
                ids = [t['id'] for t in config['tasks']]
                task = config['tasks'][ids.index('mail') + 1]
                self.assertEqual(task['id'], 'guild_daily')
                self.assertTrue(task['enabled'])
                self.assertEqual(task['schedule'], {'type': 'daily'})
                validate_config(config)
            self.assertEqual(path.read_bytes(), raw)

    def test_existing_guild_task_is_not_duplicated_or_reenabled(self):
        data = copy.deepcopy(DEFAULT_CONFIG)
        task = next(t for t in data['tasks'] if t['id'] == 'guild_daily')
        task['enabled'] = False
        original = copy.deepcopy(data)
        upgrade_guild_daily(data)
        upgrade_guild_daily(data)
        self.assertEqual(data, original)

    def test_custom_partial_workflow_does_not_get_new_tasks(self):
        data = {'tasks': [{'id': 'custom', 'steps': []}]}
        original = copy.deepcopy(data)
        upgrade_guild_daily(data)
        self.assertEqual(data, original)
