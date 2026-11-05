import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from hka.combat import weapon_controls, upgrade_combat_controls
from hka.config import ConfigError, load_config, validate_config
from hka.defaults import DEFAULT_CONFIG
from hka.game_flows import GameFlows
from hka.ocr import TextHit
from hka.web import config_snapshot, save_config_snapshot


class CombatControlsTests(unittest.TestCase):
    def legacy(self, modes):
        return {"shoot": {"x": 1480, "y": 565, "mode": "tap"},
                "skills": [dict(x=x, y=y, mode=mode) for (x, y), mode in
                           zip([(1105, 644), (1255, 570), (1040, 790)], modes)]}

    def flow(self, frames):
        flow = GameFlows.__new__(GameFlows)
        flow._check_cancel = Mock()
        flow.log = Mock()
        flow.device = Mock(package='game')
        flow.device.foreground_package.return_value = 'game'
        flow._hits = Mock(side_effect=frames)
        flow._tap = Mock()
        flow._swipe = Mock()
        flow._click = Mock()
        flow._dismiss_combat_result = Mock(return_value=True)
        flow.sleep = Mock()
        return flow

    def test_equipment_slot_modes_replace_legacy_four_button_plan(self):
        weapons = weapon_controls(self.legacy(['tap', 'hold', 'once']))
        self.assertEqual(weapons, {
            'A': dict(x=1255, y=790, mode='tap'),
            'B': dict(x=1480, y=790, mode='hold'),
            'C': dict(x=1480, y=565, mode='once')})

    def test_no_battle_input_when_game_loses_foreground(self):
        for states in [['app.lawnchair'], ['game', 'app.lawnchair'], [None]]:
            with self.subTest(states=states):
                flow = self.flow([[]])
                flow.device.foreground_package.side_effect = states
                with self.assertRaisesRegex(RuntimeError, '已停止战斗输入'):
                    flow._combat({})
                flow._tap.assert_not_called()
                flow._swipe.assert_not_called()

    def test_enabled_legacy_a_disables_implicit_c_shot(self):
        weapons = weapon_controls(self.legacy(['once', 'off', 'off']))
        self.assertEqual([w['mode'] for w in weapons.values()], ['once', 'off', 'off'])

    def test_legacy_shoot_only_and_custom_coordinates_survive(self):
        combat = self.legacy(['off', 'off', 'off'])
        self.assertEqual(weapon_controls(combat)['C']['mode'], 'tap')
        combat['shoot']['mode'] = 'off'
        self.assertTrue(all(w['mode'] == 'off' for w in weapon_controls(combat).values()))
        combat['skills'][0] = dict(x=1230, y=810, mode='hold')
        self.assertEqual(weapon_controls(combat)['A'], combat['skills'][0])

    def test_explicit_weapons_never_fall_back_to_legacy_shoot(self):
        combat = self.legacy(['tap', 'hold', 'once'])
        combat['weapons'] = {}
        flow = self.flow([[]])
        with self.assertRaisesRegex(RuntimeError, '战斗超时'):
            flow._combat({'combat': {**combat, 'mode': 'buttons', 'max_cycles': 1}})
        flow._tap.assert_not_called()
        flow._swipe.assert_not_called()

    def test_once_tap_hold_and_slot_order_in_both_movement_modes(self):
        weapons = weapon_controls(self.legacy(['once', 'tap', 'hold']))
        result = [TextHit('战斗结算', 1, 700, 145, 120, 25)]
        for mode in ['buttons', 'move_shoot']:
            with self.subTest(mode=mode):
                flow = self.flow([[], [], result])
                flow._combat({'combat': dict(weapons=weapons, mode=mode, hold_ms=750)})
                self.assertEqual(flow._tap.call_args_list, [call(1255, 790, .1),
                                                           call(1480, 790, .1), call(1480, 790, .1)])
                holds = [c for c in flow._swipe.call_args_list if c.args[0] == 1480]
                self.assertEqual(holds, [call(1480, 565, 1480, 565, 750, .1)] * 2)
                moves = [c for c in flow._swipe.call_args_list if c.args[0] == 210]
                self.assertEqual(len(moves), 2 if mode == 'move_shoot' else 0)

    def test_migration_round_trip_and_api_preserve_all_combat_steps(self):
        data = copy.deepcopy(DEFAULT_CONFIG)
        task = next(t for t in data['tasks'] if t['id'] == 'stamina_farm')
        task['steps'][0]['options']['combat'] = self.legacy(['hold', 'off', 'off'])
        task['steps'].append({'type': 'repeat', 'times': 1, 'steps': [
            {'type': 'game_flow', 'flow': 'bonus_double_stamina'}]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            loaded = load_config(path)
            upgraded = copy.deepcopy(loaded)
            upgrade_combat_controls(upgraded)
            self.assertEqual(upgraded, loaded)
            snapshot = config_snapshot(path)
            self.assertEqual(snapshot['tasks'], loaded['tasks'])
            save_config_snapshot(path, snapshot)
            self.assertEqual(load_config(path)['tasks'], loaded['tasks'])
            combat = next(t for t in loaded['tasks'] if t['id'] == 'stamina_farm')['steps'][0]['options']['combat']
            self.assertNotIn('shoot', combat)
            self.assertNotIn('skills', combat)
            self.assertEqual(combat['weapons']['A']['mode'], 'hold')
            self.assertEqual(combat['weapons']['C']['mode'], 'off')

    def test_once_uses_slot_order_and_waits_for_battle_screen_check(self):
        combat = {'weapons': weapon_controls(self.legacy(['tap', 'off', 'once'])),
                  'mode': 'buttons', 'max_cycles': 1}
        flow = self.flow([[]])
        with self.assertRaisesRegex(RuntimeError, '战斗超时'):
            flow._combat({'combat': combat})
        self.assertEqual(flow._tap.call_args_list, [call(1255, 790, .1), call(1480, 565, .1)])
        flow = self.flow([[TextHit('挑战失败', 1, 900, 300, 120, 25)]])
        with self.assertRaisesRegex(RuntimeError, '战斗失败'):
            flow._combat({'combat': combat})
        flow._tap.assert_not_called()

    def test_invalid_weapon_slots_and_positions_are_rejected(self):
        for weapons in ({'D': dict(x=1, y=1, mode='tap')}, [],
                        {'A': dict(x=1601, y=790, mode='tap')},
                        {'A': dict(x=1255, y=790, mode='invalid')}):
            with self.subTest(weapons=weapons):
                data = copy.deepcopy(DEFAULT_CONFIG)
                task = next(t for t in data['tasks'] if t['id'] == 'stamina_farm')
                task['steps'][0]['options']['combat']['weapons'] = weapons
                with self.assertRaises(ConfigError):
                    validate_config(data)

    def test_settlement_requires_full_result_layout(self):
        title = TextHit('战斗结算', 1, 729, 142, 112, 41)
        again = TextHit('再次挑战', 1, 738, 750, 123, 39)
        confirm = TextHit('确定', 1, 1012, 750, 68, 39)
        for hits, expected in [([title, again, confirm], True),
                               ([confirm], False), ([title, confirm], False),
                               ([title, again, TextHit('确定', 1, 600, 400, 68, 39)], False)]:
            with self.subTest(hits=hits):
                flow = GameFlows.__new__(GameFlows)
                flow._tap = Mock()
                flow.log = Mock()
                self.assertEqual(flow._dismiss_combat_result(hits), expected)
                if expected:
                    flow._tap.assert_called_once_with(1046, 769, 1.0)
                else:
                    flow._tap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
