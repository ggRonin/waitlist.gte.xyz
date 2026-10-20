"""Решение Cloudflare Turnstile через сервис капчи (CapSolver, 2Captcha, CapMonster).

В config.txt:
    Captcha_key=ключ API сервиса
    Captcha_service=capsolver      (или 2captcha, capmonster; по умолчанию capsolver)
"""
import time

import requests

# у всех трёх сервисов один протокол: createTask -> getTaskResult
SERVICES = {
    "capsolver": ("https://api.capsolver.com", "AntiTurnstileTaskProxyLess"),
    "2captcha": ("https://api.2captcha.com", "TurnstileTaskProxyless"),
    "capmonster": ("https://api.capmonster.cloud", "TurnstileTask"),
}

SOLVE_TIMEOUT = 180  # сколько ждать решения, сек


class CaptchaError(Exception):
    pass


def check_config(cfg):
    """Проверка настроек до старта, чтобы не тратить прокси и ящики впустую."""
    if not cfg.get("Captcha_key"):
        raise CaptchaError("в config.txt нет Captcha_key=...")
    service = cfg.get("Captcha_service", "capsolver").lower()
    if service not in SERVICES:
        raise CaptchaError(f"Captcha_service={service}: поддерживаются {', '.join(SERVICES)}")


def solve_turnstile(cfg, site_key, page_url, action=None):
    """Возвращает токен Turnstile."""
    key = cfg["Captcha_key"]
    api, task_type = SERVICES[cfg.get("Captcha_service", "capsolver").lower()]

    task = {"type": task_type, "websiteURL": page_url, "websiteKey": site_key}
    if action:
        if task_type == "AntiTurnstileTaskProxyLess":
            task["metadata"] = {"action": action}
        else:
            task["action"] = action

    r = requests.post(f"{api}/createTask", json={"clientKey": key, "task": task}, timeout=30).json()
    if r.get("errorId"):
        raise CaptchaError(f"createTask: {r.get('errorCode')} {r.get('errorDescription') or ''}".strip())
    task_id = r["taskId"]

    end = time.time() + SOLVE_TIMEOUT
    while time.time() < end:
        time.sleep(3)
        r = requests.post(f"{api}/getTaskResult", json={"clientKey": key, "taskId": task_id}, timeout=30).json()
        if r.get("errorId"):
            raise CaptchaError(f"getTaskResult: {r.get('errorCode')} {r.get('errorDescription') or ''}".strip())
        if r.get("status") == "ready":
            token = (r.get("solution") or {}).get("token")
            if not token:
                raise CaptchaError(f"пустое решение: {r}")
            return token
    raise CaptchaError(f"не решена за {SOLVE_TIMEOUT} с")
