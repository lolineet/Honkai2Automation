import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from hka.defaults import DEFAULT_CONFIG
from hka.config import save_config
from hka.inventory import EquipmentFull, check_equipment_capacity
from hka.game_flows import GameFlows
from hka.mail_credentials import protect_secret, unprotect_secret
from hka.notify import email_options, send_email, send_notifications
from hka.ocr import TextHit
from hka.runner import TaskRunner
from hka.scheduler import DailySchedule
from hka.web import config_snapshot, save_config_snapshot


class InventoryTests(unittest.TestCase):
    def test_full_inventory_outside_requested_crop_is_detected(self):
        flow = GameFlows.__new__(GameFlows)
        flow._check_cancel = Mock()
        flow._screen = Mock(return_value=b'frame')
        warning = TextHit('装备仓库已满', 1, 668, 285, 300, 40)
        with patch('hka.game_flows.read_text', return_value=[warning]) as ocr:
            with self.assertRaises(EquipmentFull):
                flow._hits((0, 0, 100, 100))
        ocr.assert_called_once()

    def test_full_inventory_texts(self):
        for text in ('装备仓库已满，请清理', '装备数量已达上限', '装备背包空间不足', '装备已经满了'):
            with self.subTest(text=text), self.assertRaises(EquipmentFull):
                check_equipment_capacity([TextHit(text, 1, 500, 400, 500, 40)])

    def test_equipment_level_limit_and_ordinary_menu_are_not_full_inventory(self):
        for text in ('装备等级已达上限', '装备仓库未满', '装备数量未达到上限', '装备仓库扩容', '装备上限提升'):
            check_equipment_capacity([TextHit(text, 1, 500, 400, 500, 40)])

    def test_inventory_stops_pipeline_even_when_continue_on_error_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['tasks'] = [{'id': name, 'name': name, 'enabled': True, 'steps': []}
                               for name in ('battle', 'later')]
            config['runtime']['stop_on_error'] = False
            device = Mock(serial='fake', package='fake', emulator_path='')
            device.size.return_value = (1600, 900)
            device.screen.return_value = b'screenshot'
            runner = TaskRunner(config, Path(directory) / 'config.json', device=device, log=lambda _: None)
            with patch.object(runner, '_steps', side_effect=EquipmentFull()) as steps, \
                 patch('hka.runner.send_notifications', return_value=[]) as notify:
                result = runner.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attention_required']['code'], 'equipment_full')
            self.assertEqual(steps.call_count, 1)
            self.assertFalse(runner.state_path.exists())
            self.assertTrue(Path(result['attention_required']['screenshot']).is_file())
            self.assertIn('装备仓库已满', notify.call_args.kwargs['title'])
            self.assertFalse(notify.call_args.kwargs['success'])
            self.assertEqual(result['not_run'][-1]['id'], 'later')

    def test_full_inventory_does_not_schedule_automatic_retry(self):
        schedule = DailySchedule()
        now = datetime(2026, 10, 17, 8)
        schedule.started(now)
        schedule.day = now.date().isoformat()
        result = {'mode':'daily', 'status':'failed', 'finished_at':now.isoformat(),
                  'attention_required':{'code':'equipment_full'}}
        self.assertFalse(schedule.due({'scheduler_enabled':True, 'daily_at':'07:30',
            'retry_attempts':3}, now + timedelta(hours=1), running=False, result=result))


class QQMailTests(unittest.TestCase):
    def test_qq_number_derives_secure_self_delivery(self):
        email = email_options({'provider':'qq', 'qq_number':'123456789', 'recipient':'wrong@example.com'})
        self.assertEqual(email['host'], 'smtp.qq.com')
        self.assertEqual(email['port'], 465)
        self.assertEqual(email['security'], 'ssl')
        self.assertEqual([email[key] for key in ('username','sender','recipient')], ['123456789@qq.com'] * 3)

    def test_invalid_qq_number(self):
        for number in ('', '123@qq.com', 'abc', '123\r\nTo:someone'):
            with self.assertRaises(ValueError):
                email_options({'provider':'qq','qq_number':number})

    def test_legacy_smtp_qq_address_keeps_existing_recipient(self):
        settings = {'host':'smtp.qq.com','username':'existing@qq.com','recipient':'other@example.com'}
        self.assertEqual(email_options(settings)['recipient'], 'other@example.com')

    def test_mail_uses_ssl_and_sends_to_self(self):
        settings = {'provider':'qq','qq_number':'123456789','password_env':'HKA_TEST_AUTH'}
        with patch.dict(os.environ, {'HKA_TEST_AUTH':'fake-authorization-code'}), \
             patch('hka.notify.smtplib.SMTP_SSL') as smtp, patch('hka.notify.smtplib.SMTP') as plain:
            send_email(settings, title='装备仓库已满', body='请清理装备')
        plain.assert_not_called()
        self.assertEqual(smtp.call_args.args, ('smtp.qq.com', 465))
        client = smtp.return_value.__enter__.return_value
        client.login.assert_called_once_with('123456789@qq.com', 'fake-authorization-code')
        self.assertEqual(client.send_message.call_args.args[0]['To'], '123456789@qq.com')

    def test_error_notifications_respect_switch(self):
        config = {'notifications':{'on_error':False,'email':{'enabled':True}}}
        with patch('hka.notify.send_email') as send:
            self.assertEqual(send_notifications(config, success=False, title='test', body='test'), [])
        send.assert_not_called()

    def test_legacy_smtp_uses_starttls(self):
        settings = {'provider':'smtp','host':'smtp.example.com','port':587,
                    'username':'sender@example.com','sender':'sender@example.com',
                    'recipient':'recipient@example.com','password_env':'HKA_TEST_AUTH'}
        with patch.dict(os.environ, {'HKA_TEST_AUTH':'fake-test-code'}), \
             patch('hka.notify.smtplib.SMTP') as smtp:
            send_email(settings, title='test', body='test')
        smtp.return_value.__enter__.return_value.starttls.assert_called_once()

    def test_smtp_error_does_not_echo_authorization_code(self):
        settings = {'provider':'qq','qq_number':'123456789','password_env':'HKA_TEST_AUTH'}
        with patch.dict(os.environ, {'HKA_TEST_AUTH':'fake-test-code'}), \
             patch('hka.notify.smtplib.SMTP_SSL', side_effect=RuntimeError('bad fake-test-code')):
            with self.assertRaises(ValueError) as caught:
                send_email(settings, title='test', body='test')
        self.assertNotIn('fake-test-code',str(caught.exception))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_credentials_saved_encrypted_redacted_and_retained_on_blank_save(self):
        secret = 'test-only-authorization-code'
        self.assertEqual(unprotect_secret(protect_secret(secret)), secret)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(path, copy.deepcopy(DEFAULT_CONFIG))
            browser = config_snapshot(path)
            email = browser['notifications']['email']
            email.update(qq_number='123456789', authorization_code=secret, enabled=True)
            save_config_snapshot(path, browser)
            self.assertNotIn(secret, path.read_text(encoding='utf-8'))
            browser = config_snapshot(path)
            self.assertTrue(browser['notifications']['email']['has_authorization_code'])
            self.assertNotIn('authorization_code_encrypted', browser['notifications']['email'])
            self.assertNotIn(secret, json.dumps(browser))
            browser['notifications']['email']['authorization_code'] = ''
            save_config_snapshot(path, browser)
            email = json.loads(path.read_text(encoding='utf-8'))['notifications']['email']
            self.assertEqual(unprotect_secret(email['authorization_code_encrypted']), secret)
            with patch('hka.notify.smtplib.SMTP_SSL') as smtp:
                send_email(email, title='test', body='test')
            smtp.return_value.__enter__.return_value.login.assert_called_once_with('123456789@qq.com', secret)
            browser = config_snapshot(path)
            browser['notifications']['email']['qq_number'] = '987654321'
            save_config_snapshot(path, browser)
            self.assertFalse(config_snapshot(path)['notifications']['email']['has_authorization_code'])
