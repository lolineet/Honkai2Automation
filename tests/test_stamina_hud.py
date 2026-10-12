import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from hka.game_flows import GameFlows
from hka.ocr import TextHit, double_stamina, read_stamina_hud, stamina


def hud(value, *, doubled=False):
    return [TextHit('双倍体力' if doubled else '体力', .99, 298, 83, 92, 24),
            TextHit(value, .99, 395, 88, 90, 20)]


class StaminaHudTests(unittest.TestCase):
    def flow(self):
        flow = GameFlows.__new__(GameFlows)
        flow._screen = Mock(return_value=b'fresh frame')
        flow.sleep = Mock()
        flow.log = Mock()
        return flow

    def test_transient_missing_hundreds_is_not_accepted(self):
        flow = self.flow()
        with patch('hka.game_flows.read_stamina_hud', side_effect=[
                hud('70/204'), hud('270/204'), hud('270/204')]):
            self.assertEqual(flow._stamina(), 270)
        self.assertEqual(flow._screen.call_count, 3)

    def test_other_resource_interrupts_confirmation(self):
        flow = self.flow()
        with patch('hka.game_flows.read_stamina_hud', side_effect=[
                hud('70/204'), hud('25/110', doubled=True), [],
                hud('270/204'), hud('270/204')]):
            self.assertEqual(flow._stamina(), 270)

    def test_actual_low_or_zero_stamina_remains_valid(self):
        for value in (70, 0):
            with self.subTest(value=value):
                with patch('hka.game_flows.read_stamina_hud', return_value=hud(f'{value}/204')):
                    self.assertEqual(self.flow()._stamina(), value)

    def test_double_stamina_requires_its_own_label_and_confirmation(self):
        with patch('hka.game_flows.read_stamina_hud', side_effect=[
                hud('270/204'), hud('25/110', doubled=True), hud('25/110', doubled=True)]):
            self.assertEqual(self.flow()._double_stamina(), 25)

    def test_unstable_or_unreadable_value_fails_instead_of_guessing(self):
        for frames in ([hud('70/204'), hud('270/204')] * 10,
                       [hud('270/204'), []] * 10):
            with patch('hka.game_flows.read_stamina_hud', side_effect=frames):
                with self.assertRaisesRegex(RuntimeError, '无法连续确认普通体力'):
                    self.flow()._stamina()

    def test_split_double_label_cannot_be_ordinary_stamina(self):
        hits = [TextHit('双倍', .99, 298, 83, 44, 24),
                TextHit('体力', .99, 348, 83, 44, 24),
                TextHit('25/110', .99, 395, 88, 70, 20)]
        self.assertIsNone(stamina(hits))
        self.assertEqual(double_stamina(hits), 25)

    def test_partial_and_ambiguous_fractions_are_rejected(self):
        for text in ('x270/204', '270/204x', '270/0'):
            self.assertIsNone(stamina(hud(text)))
        self.assertIsNone(stamina(hud('270/204') + [TextHit('70/204', .99, 395, 88, 90, 20)]))
        self.assertEqual(stamina(hud('1270/204')), 1270)

    def test_upscaled_crop_keeps_full_number_and_screen_coordinates(self):
        output = io.BytesIO()
        Image.new('RGB', (1600, 900)).save(output, format='PNG')
        def read(png, **kwargs):
            with Image.open(io.BytesIO(png)) as crop:
                self.assertEqual(crop.size, (660, 204))
            return [TextHit('270/204', .99, 345, 48, 270, 60)]
        with patch('hka.ocr.read_text', side_effect=read):
            hits = read_stamina_hud(output.getvalue())
        self.assertEqual((hits[0].x, hits[0].y, hits[0].width), (395, 88, 90))


if __name__ == '__main__':
    unittest.main()
