import unittest
from unittest.mock import Mock

from hka.game_flows import GameFlows
from hka.ocr import TextHit


def hit(text, y=400):
    return TextHit(text, 0.95, 950, y, 280, 70)


class RiftNavigationTests(unittest.TestCase):
    def make_flow(self, screens):
        flow = GameFlows.__new__(GameFlows)
        flow._battle_picker = Mock()
        flow._click = Mock()
        flow._hits = Mock(side_effect=screens)
        flow._tap = Mock()
        flow._swipe = Mock()
        flow.sleep = Mock()
        return flow

    def test_retained_bottom_position_searches_up(self):
        flow = self.make_flow([
            [hit("精神羁绊"), hit("集成电玩室", 700)],
            [hit("多元裂缝")],
            [hit("使魔的爱"), hit("虚轴之庭")],
        ])
        flow._rift()
        flow._click.assert_called_once_with("活动", (35, 175, 185, 490))
        flow._swipe.assert_called_once_with(1290, 350, 1290, 650, 650, 0.8)
        flow._tap.assert_called_once_with(1090, 435, 1.0)

    def test_top_boundary_changes_search_direction(self):
        top = [hit("版本活动"), hit("空想迷域", 700)]
        flow = self.make_flow([
            top, top, top, [hit("崩坏之塔")], [hit("多元裂缝")],
            [], [hit("使魔的爱"), hit("虚轴之庭")],
        ])
        flow._rift()
        self.assertEqual(flow._swipe.call_args_list[0].args,
                         (1290, 350, 1290, 650, 650, 0.8))
        self.assertEqual(flow._swipe.call_args_list[-1].args,
                         (1290, 740, 1290, 440, 650, 0.8))
        flow.sleep.assert_called_once_with(0.6)

    def test_clipped_entry_is_scrolled_into_view_before_click(self):
        flow = self.make_flow([
            [hit("多元裂缝", 185)], [hit("多元裂缝", 450)],
            [hit("使魔的爱"), hit("虚轴之庭")],
        ])
        flow._rift()
        flow._swipe.assert_called_once()
        flow._tap.assert_called_once_with(1090, 485, 1.0)

    def test_unconfirmed_map_stops_without_more_navigation(self):
        flow = self.make_flow([[hit("多元裂缝")]] + [[hit("其他活动")]] * 6)
        with self.assertRaisesRegex(RuntimeError, "未确认关卡地图"):
            flow._rift()
        flow._tap.assert_called_once()
        flow._swipe.assert_not_called()

    def test_missing_entry_stops_at_both_boundaries(self):
        flow = self.make_flow([[hit("精神羁绊")]] * 4)
        with self.assertRaisesRegex(RuntimeError, "双向查找"):
            flow._rift()
        self.assertEqual(flow._swipe.call_count, 2)
        flow._tap.assert_not_called()

    def test_blank_ocr_has_a_finite_search_limit(self):
        flow = self.make_flow([[]] * 18)
        with self.assertRaisesRegex(RuntimeError, "双向查找"):
            flow._rift()
        self.assertEqual(flow._swipe.call_count, 16)
        flow._tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
