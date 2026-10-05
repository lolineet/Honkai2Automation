from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any


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
        secret = os.getenv(email.get("password_env", "HKA_SMTP_PASSWORD"), "")
        if not secret:
            errors.append("邮件: 环境变量中没有 SMTP 密码")
        else:
            message = EmailMessage()
            message["Subject"] = title
            message["From"] = email.get("sender", "")
            message["To"] = email.get("recipient", "")
            message.set_content(body)
            try:
                with smtplib.SMTP(email["host"], int(email.get("port", 587)), timeout=10) as smtp:
                    smtp.starttls()
                    smtp.login(email["username"], secret)
                    smtp.send_message(message)
            except Exception as exc:
                errors.append(f"邮件: {exc}")
    return errors
