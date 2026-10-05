import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import io
from PIL import Image

from hka.config import ConfigError, save_config
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows
from hka.ocr import TextHit, double_stamina
from hka.runner import SkipTask
from hka.web import config_snapshot, save_config_snapshot


def hit(text, x=600, y=350):
    return TextHit(text, 1, x, y, 100, 25)


class DailyExtensions(unittest.TestCase):
    def test_second_page_first_clear_flips_forward_before_entering_stage(self):
        flow = GameFlows.__new__(GameFlows)
        flow._stage_detail_visible = Mock(return_value=True)
        flow._screen = Mock(return_value=b'frame')
        flow._hits = Mock(return_value=[hit('1/2', 760, 810)])
        flow._tap = Mock(); flow.sleep = Mock()
        with patch('hka.game_flows.stage_labels', side_effect=[
                [hit('1-1', 300, 400)], [hit('2-1', 600, 480)]]):
            flow._stage('2-1')
        self.assertEqual(flow._tap.call_args_list[0].args, (928, 828, 1.2))
        self.assertEqual(flow._tap.call_args_list[1].args, (650, 590, 1.2))

    def test_first_page_stage_flips_back_from_second_page(self):
        flow = GameFlows.__new__(GameFlows)
        flow._stage_detail_visible = Mock(return_value=True)
        flow._screen = Mock(return_value=b'frame')
        flow._hits = Mock(return_value=[hit('2/2', 760, 810)])
        flow._tap = Mock(); flow.sleep = Mock()
        with patch('hka.game_flows.stage_labels', side_effect=[
                [hit('2-1', 600, 480)], [hit('1-1', 300, 400)]]):
            flow._stage('1-1')
        self.assertEqual(flow._tap.call_args_list[0].args, (674, 828, 1.2))

    def test_stage_click_during_settlement_loading_is_retried(self):
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=b'frame'); flow._tap = Mock(); flow.sleep = Mock()
        flow._stage_detail_visible = Mock(side_effect=[False, True])
        with patch('hka.game_flows.stage_labels', return_value=[hit('2-3', 1270, 480)]):
            flow._stage('2-3')
        self.assertEqual(flow._tap.call_count, 2)

    def test_only_uncleared_second_page_stage_is_played_before_bonus(self):
        flow = GameFlows.__new__(GameFlows)
        flow._optional_stage_visible = Mock(return_value=False)
        current = []
        flow._stage = lambda name: current.__setitem__(slice(None), [name])
        flow._activity = Mock(); flow._tap = Mock(); flow.log = Mock(); flow.sleep = Mock()
        flow._hits = lambda *args: [hit(current[0], 780, 160),
            hit('最高得分：' + ('0' if current[0] == '2-1' else '25'), 780, 225)]
        flow._stamina = Mock(return_value=200)
        flow._run_stage = Mock()
        flow.stamina_farm({'first_clear': 'auto', 'max_runs': 1})
        self.assertEqual([call.args[0] for call in flow._run_stage.call_args_list], ['2-1', 'BONUS'])

    def test_completed_second_page_stage_is_not_replayed(self):
        flow = GameFlows.__new__(GameFlows)
        flow._optional_stage_visible = Mock(return_value=False)
        current = []
        flow._stage = lambda name: current.__setitem__(slice(None), [name])
        flow._activity = Mock(); flow._tap = Mock(); flow.log = Mock(); flow.sleep = Mock()
        flow._hits = lambda *args: [hit(current[0], 780, 160), hit('最高得分：25', 780, 225)]
        flow._stamina = Mock(return_value=99); flow._run_stage = Mock()
        flow.stamina_farm({'first_clear': 'auto'})
        flow._run_stage.assert_not_called()

    def test_hidden_22_is_checked_after_clearing_21_and_played_before_bonus(self):
        flow = GameFlows.__new__(GameFlows)
        current = []; played = []
        flow._stage = lambda name: current.__setitem__(slice(None), [name])
        flow._activity = Mock(); flow._tap = Mock(); flow.log = Mock(); flow.sleep = Mock()
        flow._hits = lambda *args: [hit(current[0], 780, 160),
            hit('最高得分：' + ('0' if current[0] in {'2-1', '2-2'} else '25'), 780, 225)]
        flow._stamina = Mock(return_value=200)
        flow._run_stage = lambda name, options: played.append(name)
        def visible(name):
            if name == '2-2':
                self.assertEqual(played, ['2-1'])
                return True
            self.assertEqual(name, '2-3')
            self.assertEqual(played, ['2-1', '2-2'])
            return False
        flow._optional_stage_visible = visible
        flow.stamina_farm({'first_clear': 'auto', 'max_runs': 1})
        self.assertEqual(played, ['2-1', '2-2', 'BONUS'])

    def test_second_page_chain_rechecks_after_every_newly_cleared_stage(self):
        flow = GameFlows.__new__(GameFlows)
        flow._activity = Mock(); flow.log = Mock()
        cleared = []
        flow._first_clear_stage = lambda name, options: cleared.append(name)
        def visible(name):
            number = int(name.split('-')[1])
            self.assertEqual(cleared[-1], f'2-{number - 1}')
            return number <= 3
        flow._optional_stage_visible = visible
        flow._stamina = Mock(return_value=99)
        flow.stamina_farm({'first_clear': 'auto'})
        self.assertEqual(cleared, ['1-1', '1-2', '1-3', '2-1', '2-2', '2-3'])

    def test_optional_stage_waits_for_loading_and_new_stage_reveal(self):
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=b'frame'); flow.log = Mock(); flow.sleep = Mock()
        with patch('hka.game_flows.read_text', side_effect=[[], [hit('2/2')], [hit('2/2')]]), \
             patch('hka.game_flows.stage_labels', side_effect=[[], [hit('2-1')], [hit('2-2')]]):
            self.assertTrue(flow._optional_stage_visible('2-2'))

    def test_optional_missing_stage_requires_two_confirmed_map_frames(self):
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=b'frame'); flow.log = Mock(); flow.sleep = Mock()
        with patch('hka.game_flows.read_text', return_value=[hit('2/2')]), \
             patch('hka.game_flows.stage_labels', return_value=[hit('2-1'), hit('BONUS')]) as labels:
            self.assertFalse(flow._optional_stage_visible('2-2'))
            self.assertEqual(labels.call_count, 2)

    def test_unknown_map_does_not_count_as_a_missing_optional_stage(self):
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=b'frame'); flow.log = Mock(); flow.sleep = Mock()
        with patch('hka.game_flows.read_text', return_value=[]), \
             patch('hka.game_flows.stage_labels', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, '未确认第二页地图'):
                flow._optional_stage_visible('2-2')

    def safe_dialog(self, amount=95, restored=190):
        return [hit('兑换双倍体力', 678, 291),
                hit(f'主人可兑换{amount}双倍体力，补充{restored}体力', 534, 382),
                hit('兑换', 616, 543), hit('取消', 883, 544)]

    def exchange(self, snapshots):
        flow = GameFlows.__new__(GameFlows)
        flow._double_stamina = Mock(side_effect=[110, 15])
        flow._stamina = Mock(side_effect=[14, 204])
        flow._click = Mock()
        flow._tap = Mock()
        flow._cancel_exchange = Mock()
        flow._exchange_snapshot = Mock(side_effect=snapshots)
        flow._text = Mock(return_value=hit('选择助战好友'))
        flow.sleep = Mock()
        flow.log = Mock()
        return flow

    def test_exchange_checks_both_snapshots_and_resource_deltas(self):
        safe = (self.safe_dialog(), False)
        flow = self.exchange([safe, safe, ([], False)])
        flow._exchange_double_stamina()
        flow._tap.assert_called_once_with(666, 555, 1.0)
        self.assertEqual(flow._double_exchange_details, {'double_spent': 95, 'restored_stamina': 190})
        self.assertEqual(flow._exchange_snapshot.call_count, 3)

    def test_paid_or_changed_exchange_is_cancelled_without_confirmation(self):
        safe = (self.safe_dialog(), False)
        for snapshots in ([ (self.safe_dialog(), True) ],
                          [safe, (self.safe_dialog(), True)],
                          [safe, (self.safe_dialog(50, 100), False)],
                          [([], False)]):
            with self.subTest(snapshots=snapshots):
                flow = self.exchange(snapshots)
                with self.assertRaises(SkipTask):
                    flow._exchange_double_stamina()
                flow._tap.assert_not_called()
                flow._cancel_exchange.assert_called_once()

    def test_unexpected_second_modal_after_exchange_is_cancelled(self):
        safe = (self.safe_dialog(), False)
        flow = self.exchange([safe, safe, ([hit('水晶兑换'), hit('取消', 990, 615)], True)])
        with self.assertRaisesRegex(RuntimeError, '额外确认'):
            flow._exchange_double_stamina()
        self.assertEqual(flow._tap.call_count, 1)
        flow._cancel_exchange.assert_called_once()

    def test_exchange_rejects_unknown_amount_or_wrong_resource_ratio(self):
        for amount, restored in [(111, 222), (0, 0), (95, 95)]:
            self.assertIsNone(GameFlows._safe_double_exchange(self.safe_dialog(amount, restored), False, 110))
        self.assertIsNone(GameFlows._safe_double_exchange(self.safe_dialog(), False, 49))

    def test_cancelling_double_prompt_also_closes_the_following_premium_prompt(self):
        flow = GameFlows.__new__(GameFlows)
        flow._tap = Mock(); flow.log = Mock()
        flow._exchange_snapshot = Mock(side_effect=[
            ([hit('主人可花费60水晶，补充205体力'), hit('取消', 883, 544)], True), ([], False)])
        flow._cancel_exchange(self.safe_dialog(), '检查未通过')
        self.assertEqual(flow._tap.call_count, 2)
        for args in flow._tap.call_args_list:
            self.assertGreaterEqual(args.args[0], 800)

    def test_premium_icon_is_detected_even_without_ocr_currency_text(self):
        frame = Image.new('RGB', (1600, 900), 'white')
        icon = Image.open(Path(__file__).resolve().parents[1] / 'hka/assets/premium_diamond.png')
        frame.paste(icon, (1160, 415))
        stream = io.BytesIO(); frame.save(stream, format='PNG')
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=stream.getvalue())
        with patch('hka.game_flows.read_text', return_value=[]):
            self.assertTrue(flow._exchange_snapshot()[1])

    def test_extra_bonus_is_exactly_one_even_with_max_runs_in_options(self):
        flow = GameFlows.__new__(GameFlows)
        flow._home = Mock(); flow._double_stamina = Mock(return_value=50)
        flow._activity = Mock(); flow._run_stage = Mock(); flow._stamina = Mock(return_value=100)
        flow.log = Mock()
        options = {'max_runs': 30}
        self.assertEqual(flow.bonus_double_stamina(options)['runs'], 1)
        flow._run_stage.assert_called_once_with('BONUS', options, double_extra=True)
        flow._run_stage.reset_mock(); flow._double_stamina.return_value = 49
        with self.assertRaises(SkipTask):
            flow.bonus_double_stamina(options)
        flow._run_stage.assert_not_called()

    def axis(self, counts):
        flow = GameFlows.__new__(GameFlows)
        flow._open_quick_detail = Mock()
        flow._quick_allowance = Mock(side_effect=counts)
        flow._execute_quick = Mock(return_value=3)
        flow.log = Mock()
        return flow

    def test_axis_six_free_battles_require_two_operations(self):
        flow = self.axis([6, 3, 0])
        self.assertEqual(flow.quick_battle({'stage': '虚轴之庭'}),
                         {'operations': 2, 'battles': 6, 'remaining_free': 0})
        self.assertEqual(flow._execute_quick.call_count, 2)

    def test_axis_nine_free_battles_require_three_operations(self):
        flow = self.axis([9, 6, 3, 0])
        self.assertEqual(flow.quick_battle({'stage': '虚轴之庭'})['operations'], 3)

    def test_axis_remaining_must_decrease_by_the_confirmed_count(self):
        for counts in ([6, 6], [6, 2], [6, 9]):
            with self.subTest(counts=counts):
                flow = self.axis(counts)
                with self.assertRaisesRegex(RuntimeError, '免费次数未按'):
                    flow.quick_battle({'stage': '虚轴之庭'})
                self.assertEqual(flow._execute_quick.call_count, 1)

    def test_axis_nonzero_stamina_cost_is_never_confirmed(self):
        flow = GameFlows.__new__(GameFlows)
        flow._click = Mock()
        flow._hits = Mock(return_value=[hit('快捷战斗'), hit('100', 650, 400),
            hit('99', 850, 400), hit('3', 720, 490)])
        with self.assertRaisesRegex(RuntimeError, '体力消耗与预期不符'):
            flow._execute_quick('虚轴之庭', 6)
        self.assertEqual(flow._click.call_count, 1)

    def test_axis_exhausted_page_with_paid_replay_is_never_entered(self):
        flow = GameFlows.__new__(GameFlows)
        flow._hits = Mock(return_value=[hit('进度: 阶段二'), hit('已参与次数:3/3'),
            hit('消耗体力35'), hit('选择助战好友')])
        flow.log = Mock()
        self.assertEqual(flow._quick_allowance('虚轴之庭', allow_zero=True), 0)
        with self.assertRaises(SkipTask):
            flow._quick_allowance('虚轴之庭')

    def test_axis_reduces_the_dialog_count_to_remaining_free_attempts(self):
        flow = GameFlows.__new__(GameFlows)
        def dialog(count):
            return [hit('快捷战斗'), hit('100', 650, 400), hit('100', 850, 400), hit(str(count), 720, 490)]
        flow._hits = Mock(side_effect=[dialog(3), dialog(2), dialog(1)])
        flow._click = Mock(); flow._tap = Mock(); flow.sleep = Mock()
        flow._text = Mock(return_value=hit('战斗结算'))
        self.assertEqual(flow._execute_quick('虚轴之庭', 1), 1)
        self.assertEqual(flow._tap.call_count, 2)

    def test_double_pool_requires_the_double_label(self):
        self.assertEqual(double_stamina([hit('双倍体力', 300, 85), hit('50/110', 400, 85)]), 50)
        self.assertIsNone(double_stamina([hit('体力', 300, 85), hit('500/204', 400, 85)]))
        self.assertIsNone(double_stamina([hit('50/110', 400, 85)]))

    def test_weekly_only_claims_rewards_after_confirming_its_page(self):
        flow = GameFlows.__new__(GameFlows)
        flow._tasks = Mock()
        flow._click = Mock()
        flow._hits = Mock(return_value=[hit('每周', 312, 209), hit('存在感', 395, 252)])
        flow._claim_task_rewards = Mock()
        flow.claim_weekly({})
        flow._click.assert_called_once_with('每周任务', (0, 430, 180, 525))
        flow._claim_task_rewards.assert_called_once_with('每周')
        flow._hits.return_value = []
        flow._claim_task_rewards.reset_mock()
        with self.assertRaisesRegex(RuntimeError, '未确认每周'):
            flow.claim_weekly({})
        flow._claim_task_rewards.assert_not_called()

    def test_old_browser_cannot_overwrite_the_new_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(path, copy.deepcopy(DEFAULT_CONFIG))
            old = config_snapshot(path)
            new = config_snapshot(path)
            new['tasks'][0]['name'] = '更新的流程'
            revision = save_config_snapshot(path, new)
            self.assertNotIn('_revision', json.loads(path.read_text(encoding='utf-8')))
            with self.assertRaisesRegex(ConfigError, '刷新页面'):
                save_config_snapshot(path, old)
            self.assertEqual(config_snapshot(path)['_revision'], revision)
            self.assertEqual(config_snapshot(path)['tasks'][0]['name'], '更新的流程')

    def test_daily_workflow_claims_weekly_and_reclaims_presence_after_bonus(self):
        from hka.config import load_config, validate_config
        validate_config(DEFAULT_CONFIG)
        for filename in ('config.example.json',):
            config = load_config(Path(__file__).resolve().parents[1] / filename)
            tasks = config['tasks']; ids = [t['id'] for t in tasks]
            self.assertEqual(ids[ids.index('presence') + 1], 'weekly_rewards')
            self.assertEqual(ids[ids.index('stamina_farm_after_presence') + 1], 'daily_rewards')
            self.assertTrue(tasks[ids.index('daily_rewards')]['enabled'])
            self.assertEqual(tasks[ids.index('daily_rewards')]['steps'][0]['flow'], 'claim_presence')
            self.assertEqual(tasks[ids.index('stamina_farm_after_presence')]['steps'][0]['options']['max_runs'], 1)
