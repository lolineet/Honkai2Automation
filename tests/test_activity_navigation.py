import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from hka.game_flows import GameFlows
from hka.ocr import TextHit
from hka.runner import SkipTask


def hit(text, x=1190, y=270):
    return TextHit(text, 0.99, x, y, 135, 30)


def card(y=270):
    return [hit("难度指数：B", y=y), hit("剩余时间：9天", 1350, y)]


class ActivityNavigationTests(unittest.TestCase):
    def make_flow(self, frames):
        flow = GameFlows.__new__(GameFlows)
        flow._battle_picker = Mock()
        flow._click = Mock()
        flow._hits = Mock(side_effect=frames)
        flow._tap = Mock()
        flow._swipe = Mock()
        flow._screen = Mock(return_value=b"map")
        flow._text = Mock(return_value=None)
        flow._home = Mock()
        flow.sleep = Mock()
        return flow

    @patch("hka.game_flows.stage_labels", return_value=[hit("1-1")])
    def test_retained_list_searches_right_column_up(self, _):
        flow = self.make_flow([[hit("多元裂缝")], card()])
        flow._activity()
        flow._click.assert_called_once_with("活动", (35, 175, 185, 490))
        flow._swipe.assert_called_once_with(1290, 350, 1290, 650, 650, 0.8)
        flow._tap.assert_called_once_with(1000, 385, 1.0)

    @patch("hka.game_flows.stage_labels", return_value=[hit("BONUS")])
    def test_top_boundary_changes_direction(self, _):
        top = [hit("版本活动")]
        flow = self.make_flow([top, top, top, card()])
        flow._activity()
        self.assertEqual(flow._swipe.call_args_list[-1].args,
                         (1290, 740, 1290, 440, 650, 0.8))

    @patch("hka.game_flows.stage_labels", return_value=[])
    def test_wrong_chapter_returns_home_and_never_accepts_entry(self, _):
        flow = self.make_flow([card()])
        with self.assertRaisesRegex(RuntimeError, "未确认日常关卡地图"):
            flow._activity()
        flow._home.assert_called_once()
        flow._tap.assert_called_once()

    def test_missing_activity_stops_at_both_boundaries(self):
        flow = self.make_flow([[hit("多元裂缝")]] * 4)
        with self.assertRaises(SkipTask):
            flow._activity()
        self.assertEqual(flow._swipe.call_count, 2)
        flow._tap.assert_not_called()

    def test_expired_and_clipped_cards_are_not_clicked(self):
        frames = [card(160) + [hit("难度指数：B"), hit("剩余时间：0天", 1350)]] * 4
        flow = self.make_flow(frames)
        with self.assertRaises(SkipTask):
            flow._activity()
        flow._tap.assert_not_called()

    def test_new_map_return_button_restores_battle_picker(self):
        tabs = [hit("主线", 89, 214), hit("活动", 89, 311)]
        flow = self.make_flow([[], [], tabs, card()])
        flow._dismiss_navigation_notice = Mock(return_value=False)
        flow._dismiss_navigation_card = Mock(return_value=False)
        flow.log = Mock()
        def match(screen, directory, step):
            return (1344, 200, 1.0) if step["image"] == "battle_reset_compact.png" else None
        with patch("hka.game_flows.find_image", side_effect=match):
            GameFlows._battle_picker(flow)
        self.assertEqual(flow._tap.call_args_list[-1].args, (1344, 200, 0.8))


class ChapterHomeTests(unittest.TestCase):
    def make_flow(self, with_icon):
        flow = GameFlows.__new__(GameFlows)
        flow._hits = Mock(side_effect=[[], [hit("首页", 788, 84)], [hit("首页", 788, 84)]]
                          if with_icon else [[]] * 8)
        frame = Image.new("RGB", (1600, 900), "#eeeeee")
        if with_icon:
            with Image.open(Path(__file__).resolve().parents[1] / "hka/assets/navigation_home.png") as icon:
                frame.paste(icon, (156, 32))
        data = io.BytesIO()
        frame.save(data, format="PNG")
        blank = Image.new("RGB", (1600, 900), "#eeeeee")
        picker = io.BytesIO()
        blank.save(picker, format="PNG")
        with Image.open(Path(__file__).resolve().parents[1] / "hka/assets/home_active.png") as active:
            blank.paste(active, (780, 40))
        home = io.BytesIO()
        blank.save(home, format="PNG")
        flow._screen = Mock(side_effect=[data.getvalue(), picker.getvalue(), home.getvalue()]
                            if with_icon else [picker.getvalue()] * 8)
        flow._dismiss_navigation_notice = Mock(return_value=False)
        flow._dismiss_navigation_card = Mock(return_value=False)
        flow._tap = Mock()
        flow.log = Mock()
        flow.sleep = Mock()
        return flow

    def test_icon_returns_to_picker_then_uses_normal_home_tab(self):
        flow = self.make_flow(True)
        flow._home()
        self.assertEqual(flow._tap.call_args_list[0].args, (191, 64, 1.5))
        self.assertEqual(flow._tap.call_args_list[1].args, (855, 99, 2.5))

    def test_unknown_screen_does_not_receive_a_blind_tap(self):
        flow = self.make_flow(False)
        with self.assertRaisesRegex(RuntimeError, "未确认顶部首页入口"):
            flow._home()
        flow._tap.assert_not_called()

    def test_missed_home_click_is_retried_until_active(self):
        flow = self.make_flow(True)
        frames = flow._screen.side_effect
        next(frames)  # Chapter frame is unnecessary for this case.
        picker, home = list(frames)
        flow._screen = Mock(side_effect=[picker, picker, home])
        flow._hits = Mock(return_value=[hit("首页", 788, 84)])
        flow._home()
        self.assertEqual(flow._tap.call_count, 2)

    def test_home_label_alone_cannot_confirm_arrival(self):
        flow = self.make_flow(False)
        flow._hits = Mock(return_value=[hit("首页", 788, 84)])
        with self.assertRaisesRegex(RuntimeError, "首页激活状态"):
            flow._home()
        self.assertEqual(flow._tap.call_count, 8)

    def test_active_home_in_task_submenu_still_returns_to_root(self):
        flow = self.make_flow(True)
        frames = list(flow._screen.side_effect)
        active = frames[-1]
        root = [hit("首页", 788, 84)]
        task_panel = root + [hit("存在感", 45, 365), hit("每周任务", 23, 463),
                             hit("使魔探险", 30, 550)]
        flow._screen = Mock(return_value=active)
        flow._hits = Mock(side_effect=[task_panel, task_panel, root])
        flow._home()
        self.assertEqual(flow._tap.call_count, 2)


if __name__ == "__main__":
    unittest.main()
