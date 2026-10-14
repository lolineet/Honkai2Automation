import threading
import unittest
from unittest.mock import Mock, patch

from hka.game_flows import GameFlows
from hka.ocr import TextHit
from hka.runner import Cancelled


def hit(text, x=600, y=400):
    return TextHit(text, 0.99, x, y, 100, 30)


class StartupPromptTests(unittest.TestCase):
    def setUp(self):
        runner = Mock()
        runner.cancel = threading.Event()
        runner._size.return_value = (1600, 900)
        self.flow = GameFlows(runner)

    def test_resource_update_confirmation(self):
        self.assertEqual(self.flow._startup_prompt([
            hit('检测到资源更新，需要下载 120 MB'), hit('确定', 900, 650)]), 'update')
        self.flow.device.tap.assert_called_once_with(950, 665)

    def test_agreement_acceptance(self):
        self.assertEqual(self.flow._startup_prompt([
            hit('请阅读用户协议和隐私政策'), hit('不同意', 400, 700),
            hit('同意并继续', 900, 700)]), 'agreement')
        self.flow.device.tap.assert_called_once_with(950, 715)

    def test_live_game_resource_dialog(self):
        self.assertEqual(self.flow._startup_prompt([
            hit('提示'), hit('资源包大小为1480.1M，当前使用'),
            hit('Wifi流量，主人是否希望现在下载？'), hit('快给我下', 900, 650),
            hit('退出游戏', 400, 650)]), 'update')
        self.flow.device.tap.assert_called_once_with(950, 665)

    def test_live_game_privacy_dialog_accept(self):
        self.assertEqual(self.flow._startup_prompt([
            hit('用户协议和隐私政策更新提示'), hit('拒绝', 400, 650),
            hit('接受', 900, 650)]), 'agreement')
        self.flow.device.tap.assert_called_once_with(950, 665)

    def test_unrelated_confirm_is_not_clicked(self):
        self.assertIsNone(self.flow._startup_prompt([hit('购买资源'), hit('确定')]))
        self.flow.device.tap.assert_not_called()

    def test_update_progress_waits_without_tapping(self):
        self.assertEqual(self.flow._startup_prompt([
            hit('资源更新'), hit('正在下载 36%')]), 'updating')
        self.flow.device.tap.assert_not_called()

    def test_ambiguous_buttons_are_not_clicked(self):
        self.assertIsNone(self.flow._startup_prompt([
            hit('资源更新'), hit('确定'), hit('下载')]))
        self.flow.device.tap.assert_not_called()

    def test_prompts_are_handled_before_entering_home(self):
        frames = [[hit('用户协议'), hit('同意')],
                  [hit('资源更新'), hit('开始下载')],
                  [hit('资源更新'), hit('下载中')], [hit('首页', 800, 100)]]
        with patch.object(self.flow, '_hits', side_effect=frames):
            self.flow.enter_game({})
        self.assertEqual(self.flow.device.tap.call_count, 2)

    def test_stop_prevents_confirmation(self):
        self.flow.runner.cancel.set()
        with self.assertRaises(Cancelled):
            self.flow._startup_prompt([hit('用户协议'), hit('同意')])
        self.flow.device.tap.assert_not_called()
