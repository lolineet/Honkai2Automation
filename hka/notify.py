from __future__ import annotations

import json
import os
import re
import ssl
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any
from .mail_credentials import unprotect_secret


def email_options(settings: dict) -> dict:
    email = dict(settings)
    provider = email.get('provider') or ('qq' if email.get('qq_number') or not email.get('host') else 'smtp')
    if provider == 'qq':
        number = str(email.get('qq_number', '')).strip()
        if not re.fullmatch(r'[1-9][0-9]{4,14}', number):
            raise ValueError('请填写有效 QQ 号（SMTP 邮件通知）')
        address = number + '@qq.com'
        email.update(host='smtp.qq.com', port=465, security='ssl',
                     username=address, sender=address, recipient=address)
    return email


def send_email(settings: dict, *, title: str, body: str) -> None:
    email = email_options(settings)
    encrypted = email.get('authorization_code_encrypted', '')
    secret = unprotect_secret(encrypted) if encrypted else os.getenv(email.get('password_env', 'HKA_SMTP_PASSWORD'), '')
    if not secret:
        raise ValueError('请填写 SMTP 授权码，或设置 SMTP 密码环境变量')
    message = EmailMessage()
    message['Subject'] = title
    message['From'] = email.get('sender', '')
    message['To'] = email.get('recipient', '')
    message.set_content(body)
    context = ssl.create_default_context()
    secure = email.get('security', 'ssl' if int(email.get('port', 587)) == 465 else 'starttls') == 'ssl'
    try:
        if secure:
            client = smtplib.SMTP_SSL(email['host'], int(email.get('port', 465)), timeout=10, context=context)
        else:
            client = smtplib.SMTP(email['host'], int(email.get('port', 587)), timeout=10)
        with client as smtp:
            if not secure:
                smtp.starttls(context=context)
            smtp.login(email['username'], secret)
            smtp.send_message(message)
    except Exception as exc:
        raise ValueError(str(exc).replace(secret, '[已隐藏]')) from None


def send_notifications(config: dict[str, Any], *, success: bool, title: str, body: str) -> list[str]:
    settings = config.get("notifications", {})
    if not settings.get("on_success" if success else "on_error", True):
        return []
    errors: list[str] = []
    for index, item in enumerate(settings.get("webhooks", [])):
        if not item.get("enabled", True):
            continue
        url = item.get("url", "")
        if not url.startswith(("https://", "http://")):
            errors.append(f"Webhook {index + 1}: URL 无效")
            continue
        payload = json.dumps({"title": title, "content": body, "success": success}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        except Exception as exc:
            errors.append(f"Webhook {index + 1}: {exc}")
    email = settings.get("email", {})
    if email.get("enabled"):
        try:
            send_email(email, title=title, body=body)
        except Exception as exc:
            errors.append(f"邮件: {exc}")
    return errors
