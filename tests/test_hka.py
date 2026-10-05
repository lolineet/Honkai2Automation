import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from hka.config import ConfigError, execution_day, schedule_matches, validate_config
from hka.adb import AdbDevice
from hka.defaults import DEFAULT_CONFIG
from hka.notify import send_notifications
from hka.ocr import TextHit, stamina
from hka.game_flows import GameFlows
from hka.runner import SkipTask, TaskRunner
from hka.scheduler import DailySchedule
from hka.vision import crop_template, find_image
from hka.web import serve


def screen_png():
    image = Image.new("RGB", (240, 120), "#1b2d46")
    draw = ImageDraw.Draw(image)
    draw.rectangle((95, 38, 143, 76), fill="#eac065")
    draw.line((100, 42, 138, 72), fill="#29394d", width=4)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeDevice:
    serial = "fake"
    package = "game.package"
    emulator_path = ""

    def __init__(self, screenshot):
        self.screenshot = screenshot
        self.taps = []
        self.launched = False

    def connect(self):
        pass

    def installed(self):
        return True

    def size(self):
        return (240, 120)

    def screen(self):
        return self.screenshot

    def tap(self, x, y):
        self.taps.append((x, y))

    def launch_game(self):
        self.launched = True

    def stop_emulator(self):
        pass


class ManualStartTests(unittest.TestCase):
    def test_opening_web_page_does_not_run_tasks_even_with_old_timer_config(self):
        with patch('hka.web.ThreadingHTTPServer') as server, \
             patch('hka.web.TaskRunner') as runner, \
             patch('hka.web.threading.Thread') as thread, \
             patch('hka.web.load_config', return_value={'runtime': {'scheduler_enabled': True}}), \
             patch.dict(os.environ, {'HKA_NO_BROWSER': '1'}):
            serve(Path('config.json'))
            server.return_value.serve_forever.assert_called_once()
            runner.assert_not_called()
            thread.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows console behavior')
    def test_adb_and_emulator_helpers_do_not_create_console_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / 'manager.exe'
            executable.touch()
            device = AdbDevice({'adb_path': 'adb.exe', 'serial': '127.0.0.1:16416',
                                'package': 'test.game', 'executable': str(executable), 'launch_delay': 0})
            result = Mock(returncode=0, stdout=b'device', stderr=b'')
            with patch('hka.adb.subprocess.run', return_value=result) as run, \
                 patch('hka.adb.subprocess.Popen') as popen:
                device.connect()
                device.tap(100, 100)
                device.start_emulator()
                self.assertEqual(run.call_count, 3)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(popen.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)


class ConfigTests(unittest.TestCase):
    def test_game_day_changes_at_four_am(self):
        settings = {"daily_reset_at": "04:00"}
        self.assertEqual(execution_day(settings, datetime(2026, 10, 3, 3, 59)), date(2026, 10, 2))
        self.assertEqual(execution_day(settings, datetime(2026, 10, 3, 4)), date(2026, 10, 3))
    def test_schedule_types(self):
        day = date(2026, 10, 1)
        self.assertTrue(schedule_matches({"type": "dates", "dates": ["10-01"]}, day))
        self.assertFalse(schedule_matches({"type": "dates", "dates": ["10-02"]}, day))
        self.assertTrue(schedule_matches({"type": "range", "start": "2026-09-28", "end": "2026-10-01"}, day))
        self.assertTrue(schedule_matches({"type": "weekdays", "days": [3]}, day))

    def test_reject_bad_step(self):
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        data["tasks"][0]["steps"] = [{"type": "tap", "x": 4, "y": 0.5}]
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_reject_invalid_scheduler_time(self):
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        data["runtime"]["scheduler_enabled"] = True
        data["runtime"]["daily_at"] = "25:90"
        with self.assertRaises(ConfigError):
            validate_config(data)


class RunnerTests(unittest.TestCase):
    def test_bonus_count_and_stop_reason_are_saved_in_the_run_report(self):
        with tempfile.TemporaryDirectory() as temp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config['tasks'] = [{'id': 'bonus', 'name': 'BONUS', 'enabled': True,
                                'schedule': {'type': 'daily'},
                                'steps': [{'type': 'game_flow', 'flow': 'stamina_farm'}]}]
            details = {'runs': 2, 'stop_reason': 'stamina_below_minimum',
                       'final_stamina': 70, 'max_runs': 30, 'min_stamina': 100}
            runner = TaskRunner(config, Path(temp) / 'config.json', device=FakeDevice(screen_png()),
                                log=lambda _: None, sleep=lambda _: None)
            with patch('hka.game_flows.run_game_flow', return_value=details):
                result = runner.run()
            report_path = next((Path(temp) / 'runs').glob('run_*.json'))
            report = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(report['tasks'][0]['details']['stamina_farm'], details)
            self.assertIn('2 次', result['tasks'][0]['message'])
            self.assertIn('70', result['tasks'][0]['message'])

    def test_preparation_repeats_only_when_daily_work_is_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config['tasks'] = [
                {'id':'launch','name':'准备','enabled':True,'always_run':True,'schedule':{'type':'daily'},'steps':[]},
                {'id':'claim','name':'领取','enabled':True,'schedule':{'type':'daily'},'steps':[]}]
            runner = TaskRunner(config, root/'config.json', log=lambda _: None)
            key = runner.today.isoformat()
            (root/'state.json').write_text(json.dumps({key:['launch']}))
            self.assertEqual([task['id'] for task in runner.plan()], ['launch','claim'])
            (root/'state.json').write_text(json.dumps({key:['launch','claim']}))
            self.assertEqual(runner.plan(), [])

    def test_failure_continues_or_stops_according_to_setting(self):
        for stop, required, expected in ((False, False, ["broken", "next"]), (True, False, ["broken"]), (False, True, ["broken"])):
            with self.subTest(stop=stop, required=required), tempfile.TemporaryDirectory() as temp:
                config = json.loads(json.dumps(DEFAULT_CONFIG))
                config["runtime"]["stop_on_error"] = stop
                config["tasks"] = [
                    {"id": "broken", "name": "失败项", "enabled": True, "required": required, "schedule": {"type": "daily"}, "steps": []},
                    {"id": "next", "name": "后续项", "enabled": True, "schedule": {"type": "daily"},
                     "steps": [{"type": "tap", "x": .5, "y": .5, "wait": 0}]}]
                runner = TaskRunner(config, Path(temp) / "config.json", device=FakeDevice(screen_png()),
                                    log=lambda _: None, sleep=lambda _: None)
                original = runner._steps
                def execute(steps):
                    if not steps:
                        raise RuntimeError("测试失败")
                    original(steps)
                runner._steps = execute
                result = runner.run()
                self.assertEqual([item["id"] for item in result["tasks"]], expected)
                self.assertEqual(len(runner.device.taps), 0 if stop or required else 1)
                if stop or required:
                    self.assertEqual(result["not_run"][0]["status"], "stopped_after_error")

    def test_completed_tasks_have_a_visible_skip_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "state.json").write_text(json.dumps({date.today().isoformat(): ["launch"]}))
            runner = TaskRunner(DEFAULT_CONFIG, root / "config.json", log=lambda _: None)
            item = runner.preview()[0]
            self.assertEqual(item["status"], "completed")
            self.assertIn("今天已完成", item["reason"])

    def test_template_crop_match_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = screen_png()
            crop_template(data, [0.395, 0.31, 0.605, 0.65], root / "templates" / "button.png")
            match = find_image(data, root / "templates", {"image": "button.png", "threshold": 0.8})
            self.assertIsNotNone(match)
            self.assertAlmostEqual(match[0], 120, delta=3)
            self.assertAlmostEqual(match[1], 58, delta=3)
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["tasks"] = [{"id": "claim", "name": "领取", "enabled": True, "schedule": {"type": "daily"},
                                "steps": [{"type": "tap_image", "image": "button.png", "threshold": 0.8, "wait": 0}]}]
            device = FakeDevice(data)
            runner = TaskRunner(config, root / "config.json", device=device, log=lambda _: None, sleep=lambda _: None)
            self.assertEqual(runner.run()["status"], "success")
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(runner.run()["status"], "nothing_to_do")

    def test_event_and_birthday_do_not_run_outside_dates(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["tasks"] = config["tasks"][-2:]
        for task in config["tasks"]:
            task["enabled"] = True
        runner = TaskRunner(config, Path("config.json"), device=FakeDevice(screen_png()), log=lambda _: None)
        runner.today = date(2026, 10, 1)
        self.assertEqual(runner.plan(), [])

    def test_cancel_prevents_task_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            device = FakeDevice(screen_png())
            cancel = threading.Event()
            cancel.set()
            runner = TaskRunner(config, Path(temp) / "config.json", device=device, cancel=cancel, log=lambda _: None)
            self.assertEqual(runner.run()["status"], "cancelled")
            self.assertFalse(device.launched)


class SchedulerTests(unittest.TestCase):
    def test_retry_waits_and_stops_at_the_configured_limit(self):
        schedule = DailySchedule()
        settings = {'scheduler_enabled':True,'daily_at':'07:30','retry_attempts':2,'retry_interval_minutes':15}
        self.assertFalse(schedule.due(settings, datetime(2026,10,3,7,29), running=False, result=None))
        for attempt, hour, minute in ((1,7,30),(2,7,46),(3,8,2)):
            now = datetime(2026,10,3,hour,minute)
            prior = None if attempt==1 else result
            self.assertTrue(schedule.due(settings, now, running=False, result=prior))
            schedule.started(now)
            result = {'status':'failed','mode':'daily','finished_at':(now.replace(minute=minute+1)).isoformat()}
            self.assertFalse(schedule.due(settings, now, running=False, result=result))
        self.assertFalse(schedule.due(settings, datetime(2026,10,3,9), running=False, result=result))
        self.assertTrue(schedule.due(settings, datetime(2026,10,4,7,30), running=False, result=result))

    def test_busy_cancelled_and_single_tests_do_not_trigger_retries(self):
        settings = {'scheduler_enabled':True,'daily_at':'07:30','retry_attempts':2}
        schedule = DailySchedule()
        now = datetime(2026,10,3,7,30)
        self.assertFalse(schedule.due(settings,now,running=True,result=None))
        schedule.started(now)
        for mode,status in [('daily','cancelled'),('daily','success'),('selected','failed')]:
            result={'mode':mode,'status':status,'finished_at':now.isoformat()}
            self.assertFalse(schedule.due(settings,datetime(2026,10,3,8),running=False,result=result))


class NotificationTests(unittest.TestCase):
    def test_missing_email_password_is_reported_without_sending(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["notifications"]["email"]["enabled"] = True
        config["notifications"]["email"]["password_env"] = "HKA_MISSING_TEST_PASSWORD"
        errors = send_notifications(config, success=False, title="test", body="test")
        self.assertEqual(len(errors), 1)
        self.assertIn("SMTP", errors[0])


class GameSafetyTests(unittest.TestCase):
    def test_navigation_closes_recognized_cards_and_announcements(self):
        flow = GameFlows.__new__(GameFlows)
        flow.log = lambda _: None
        taps = []
        flow._tap = lambda *args: taps.append(args)
        directory = Path(__file__).resolve().parents[1] / 'hka' / 'assets'
        for filename, position in (('navigation_close.png', (1270, 215)),
                                   ('navigation_announcement_close.png', (1405, 50))):
            frame = Image.new('RGB', (1600, 900), '#eeeeee')
            with Image.open(directory / filename) as close:
                frame.paste(close.convert('RGB'), position)
                expected = (position[0] + close.width // 2, position[1] + close.height // 2, 1.0)
            data = io.BytesIO()
            frame.save(data, format='PNG')
            flow._screen = lambda: data.getvalue()
            self.assertTrue(flow._dismiss_navigation_card())
            self.assertEqual(taps[-1], expected)

    def test_navigation_closes_only_the_delivery_notice(self):
        flow = GameFlows.__new__(GameFlows)
        flow.log = lambda _: None
        taps = []
        flow._tap = lambda *args: taps.append(args)
        notice = [TextHit('提示', 1, 760, 264, 80, 48),
                  TextHit('以下未领取的活动奖励已经送达背包啦', 1, 521, 337, 556, 30),
                  TextHit('确定', 1, 756, 581, 87, 52)]
        self.assertTrue(flow._dismiss_navigation_notice(notice))
        self.assertEqual(taps, [(799, 607, 1.0)])
        taps.clear()
        paid = [notice[0], TextHit('消耗100水晶购买体力', 1, 521, 337, 250, 30), notice[2]]
        self.assertFalse(flow._dismiss_navigation_notice(paid))
        self.assertEqual(taps, [])

    def test_battle_picker_retries_when_the_first_click_stays_on_home(self):
        flow = GameFlows.__new__(GameFlows)
        flow._home = lambda: None
        flow._screen = lambda: b'test frame'
        flow._dismiss_navigation_notice = lambda _: False
        tabs = iter([[], [TextHit('主线', 1, 50, 230, 80, 30),
                          TextHit('活动', 1, 50, 330, 80, 30)]])
        def hits(box=None, **kwargs):
            return next(tabs) if box == (35, 175, 185, 490) else []
        flow._hits = hits
        flow._text = lambda *args: TextHit('战斗', 1, 929, 79, 64, 41)
        flow.sleep = lambda _: None
        flow.log = lambda _: None
        taps = []
        flow._tap = lambda *args: taps.append(args)
        def match(frame, directory, step):
            return (815, 70, 1.0) if step['image'] == 'home_active.png' else None
        with patch('hka.game_flows.find_image', side_effect=match):
            flow._battle_picker()
        self.assertEqual(taps, [(955, 70, 1.5), (961, 99, 1.5)])

    def test_reward_closing_waits_for_a_busy_popup(self):
        flow = GameFlows.__new__(GameFlows)
        flow._tasks = lambda: None
        flow._click = lambda *args, **kwargs: None
        frames = iter([True, True, False])
        flow._text = lambda *args: next(frames)
        flow.log = lambda _: None
        flow.sleep = lambda _: None
        taps = []
        flow._tap = lambda *args: taps.append(args)
        flow.claim_presence({})
        self.assertEqual(taps, [(1450, 740, 1.0), (1450, 740, 1.0)])

    def test_bonus_repeats_until_regular_stamina_is_below_100(self):
        flow = GameFlows.__new__(GameFlows)
        flow._activity = lambda: None
        values = iter([300, 250, 200, 150, 100, 50])
        flow._stamina = lambda: next(values)
        stages = []
        flow._run_stage = lambda stage, options: stages.append(stage)
        flow.log = lambda _: None
        details = flow.stamina_farm({'first_clear': False, 'min_stamina': 100, 'max_runs': 30})
        self.assertEqual(stages, ['BONUS'] * 5)
        self.assertEqual(details['runs'], 5)
        self.assertEqual(details['final_stamina'], 50)
        self.assertEqual(details['stop_reason'], 'stamina_below_minimum')

    def test_bonus_single_run_limit_is_reported_explicitly(self):
        flow = GameFlows.__new__(GameFlows)
        flow._activity = lambda: None
        flow._stamina = lambda: 300
        stages = []
        flow._run_stage = lambda stage, options: stages.append(stage)
        flow.log = lambda _: None
        details = flow.stamina_farm({'first_clear': False, 'max_runs': 1})
        self.assertEqual(stages, ['BONUS'])
        self.assertEqual(details['stop_reason'], 'max_runs_reached')

    def test_axis_character_detail_accepts_daily_free_count(self):
        flow = GameFlows.__new__(GameFlows)
        flow._hits = lambda *args, **kwargs: [
            TextHit("妮娜·黄昏", 1, 770, 150, 160, 40),
            TextHit("进度：阶段一", 1, 770, 216, 164, 38),
            TextHit("已参与次数：0/3", 1, 770, 254, 206, 36),
            TextHit("今日免费6/6", 1, 778, 737, 157, 33),
            TextHit("选择助战好友", 1, 813, 789, 202, 44),
            TextHit("快捷战斗", 1, 1177, 789, 138, 46)]
        flow.sleep = lambda _: None
        flow.log = lambda _: None
        self.assertEqual(flow._quick_allowance("虚轴之庭"), 6)

    def test_axis_unknown_page_with_a_free_label_is_not_a_detail(self):
        flow = GameFlows.__new__(GameFlows)
        flow._hits = lambda *args, **kwargs: [TextHit("今日免费6/6", 1, 778, 737, 157, 33)]
        flow.sleep = lambda _: None
        with self.assertRaisesRegex(RuntimeError, "未确认.*详情页"):
            flow._quick_allowance("虚轴之庭")

    def test_daily_reward_recovers_home_before_opening_the_task_menu(self):
        flow = GameFlows.__new__(GameFlows)
        actions = []
        flow._home = lambda: actions.append("home")
        flow._tap = lambda *args, **kwargs: actions.append(("tap", args))
        flow._click = lambda text, *args, **kwargs: actions.append(text)
        flow._text = lambda text, *args: TextHit(text, 1, 0, 0, 1, 1) if text == "使魔探险" else None
        flow.sleep = lambda _: None
        with self.assertRaises(SkipTask):
            flow.claim_presence({})
        self.assertEqual(actions[:4], ["home", ("tap", (65, 205, 1.5)), "存在感", "一键领取"])

    def test_quick_battle_missing_detail_is_failure_not_exhaustion(self):
        flow = GameFlows.__new__(GameFlows)
        flow._hits = lambda *args, **kwargs: [TextHit("使魔的爱", 1, 730, 488, 163, 51)]
        flow.sleep = lambda _: None
        with self.assertRaisesRegex(RuntimeError, "未确认.*详情页"):
            flow._quick_allowance("使魔的爱")

    def test_quick_battle_waits_for_detail_before_reading_count(self):
        flow = GameFlows.__new__(GameFlows)
        screens = iter([[TextHit("使魔的爱", 1, 730, 488, 163, 51)],
            [TextHit("使魔的爱", 1, 770, 150, 160, 40),
             TextHit("今日剩余次数1/1", 1, 778, 736, 170, 30)]])
        flow._hits = lambda *args, **kwargs: next(screens)
        flow.sleep = lambda _: None
        flow.log = lambda _: None
        self.assertEqual(flow._quick_allowance("使魔的爱"), 1)

    def test_double_stamina_is_not_regular_stamina(self):
        bonus = [TextHit("双倍体力", 1, 300, 85, 75, 23),
                 TextHit("6/110", 1, 395, 88, 70, 20)]
        regular = [TextHit("体力", 1, 300, 85, 45, 23),
                   TextHit("286/204", 1, 395, 88, 80, 20)]
        self.assertIsNone(stamina(bonus))
        self.assertEqual(stamina(regular), 286)

    def test_refresh_with_zero_free_uses_no_refresh_tap(self):
        flow = GameFlows.__new__(GameFlows)
        flow._shop = lambda: None
        flow._click = lambda *args, **kwargs: True
        flow._hits = lambda *args, **kwargs: [TextHit("剩余次数 0次", 1, 1100, 170, 100, 22)]
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        with self.assertRaises(SkipTask):
            flow.refresh_resonance({})
        self.assertEqual(taps, [])

    def test_partly_hidden_gift_scrolls_before_checking_price(self):
        flow = GameFlows.__new__(GameFlows)
        flow._shop = lambda: None
        flow._click = lambda *args, **kwargs: True
        screens = iter([
            [TextHit("零时馈礼", 1, 1120, 675, 100, 25)],
            [TextHit("零时馈礼", 1, 1120, 260, 100, 25),
             TextHit("×0", 1, 1150, 560, 40, 25),
             TextHit("购买", 1, 1130, 625, 80, 30)],
            [TextHit("零时馈礼", 1, 650, 300, 100, 25),
             TextHit("购买", 1, 650, 550, 80, 30)]])
        flow._hits = lambda *args, **kwargs: next(screens)
        flow._text = lambda *args, **kwargs: TextHit("购买成功", 1, 650, 300, 100, 25)
        flow.log = lambda _: None
        actions = []
        flow._swipe = lambda *args, **kwargs: actions.append(("scroll", args))
        flow._tap = lambda *args, **kwargs: actions.append(("tap", args))
        flow.free_gift({})
        self.assertEqual(actions[0][0], "scroll")
        self.assertEqual(actions[1], ("tap", (1170, 640)))

    def test_paid_gift_has_no_purchase_taps(self):
        flow = GameFlows.__new__(GameFlows)
        flow._shop = lambda: None
        flow._click = lambda *args, **kwargs: True
        flow._hits = lambda *args, **kwargs: [TextHit("零时馈礼", 1, 1120, 260, 100, 25),
            TextHit("×6", 1, 1150, 560, 40, 25), TextHit("购买", 1, 1130, 625, 80, 30)]
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        with self.assertRaises(SkipTask):
            flow.free_gift({})
        self.assertEqual(taps, [])

    def test_skill_modes_do_not_override_movement_mode(self):
        flow = GameFlows.__new__(GameFlows)
        flow._check_cancel = lambda: None
        flow._hits = lambda *args, **kwargs: []
        flow._tap = lambda *args, **kwargs: None
        flow.sleep = lambda _: None
        swipes = []
        flow._swipe = lambda *args, **kwargs: swipes.append(args)
        with self.assertRaisesRegex(RuntimeError, "战斗超时"):
            flow._combat({"combat": {"mode": "move_shoot", "max_cycles": 1,
                "skills": [{"x": 1000, "y": 700, "mode": "off"}]}})
        self.assertEqual(swipes, [(210, 690, 345, 690, 6500, 0.5)])

    def test_unknown_battle_screen_never_receives_reset_tap(self):
        flow = GameFlows.__new__(GameFlows)
        flow._home = lambda: None
        flow._hits = lambda *args, **kwargs: []
        buffer = io.BytesIO()
        Image.new("RGB", (1600, 900), "white").save(buffer, format="PNG")
        flow._screen = lambda: buffer.getvalue()
        flow.sleep = lambda _: None
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        with self.assertRaises(RuntimeError):
            flow._battle_picker()
        self.assertEqual(taps, [(955, 70, 1.5)])

    def test_activity_uses_hours_remaining_and_excludes_maze(self):
        flow = GameFlows.__new__(GameFlows)
        flow._battle_picker = lambda: [
            TextHit("空想迷域", 1, 1230, 230, 120, 25),
            TextHit("难度指数：B", 1, 1320, 270, 150, 25),
            TextHit("剩余时间：5天", 1, 1300, 290, 150, 25),
            TextHit("难度指数：B", 1, 560, 270, 150, 25),
            TextHit("剩余时间：0天10小时", 1, 500, 290, 200, 25)]
        flow._text = lambda *args, **kwargs: None
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        flow._activity()
        self.assertEqual(taps, [(370, 385, 1.0)])

    def test_failed_result_is_not_recorded_as_a_win(self):
        flow = GameFlows.__new__(GameFlows)
        flow._check_cancel = lambda: None
        flow._hits = lambda *args, **kwargs: [TextHit("战斗结算", 1, 700, 145, 120, 25),
                                               TextHit("挑战失败", 1, 900, 300, 120, 25)]
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        with self.assertRaisesRegex(RuntimeError, "战斗失败"):
            flow._combat({})
        self.assertEqual(taps, [])

    def test_partial_first_clear_resumes_unfinished_stages(self):
        flow = GameFlows.__new__(GameFlows)
        flow._optional_stage_visible = lambda _: False
        flow._activity = lambda: None
        flow._stage = lambda _: None
        scores = iter([25, 0, 0, 0])
        current_stage = []
        flow._stage = lambda name: current_stage.__setitem__(slice(None), [name])
        flow._hits = lambda *args, **kwargs: [TextHit(current_stage[0], 1, 780, 160, 100, 25),
            TextHit(f"最高得分：{next(scores)}", 1, 780, 225, 150, 25)]
        flow._tap = lambda *args, **kwargs: None
        flow._stamina = lambda: 0
        flow.log = lambda _: None
        played = []
        flow._run_stage = lambda name, options: played.append(name)
        flow.stamina_farm({})
        self.assertEqual(played, ["1-2", "1-3", "2-1"])

    def test_startup_uses_only_the_last_login_server(self):
        flow = GameFlows.__new__(GameFlows)
        screens = iter([
            [TextHit("选择服务器", 1, 700, 100, 200, 35),
             TextHit("上次登录", 1, 400, 480, 200, 30),
             TextHit("进入", 1, 1150, 335, 80, 40),
             TextHit("进入", 1, 1150, 525, 80, 40)],
            [TextHit("首页", 1, 788, 84, 55, 30)]])
        flow._hits = lambda *args, **kwargs: next(screens)
        flow.sleep = lambda _: None
        flow.log = lambda _: None
        taps = []
        flow._tap = lambda *args, **kwargs: taps.append(args)
        flow.enter_game({})
        self.assertEqual(taps, [(1190, 545, 2)])


if __name__ == "__main__":
    unittest.main()
