import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from captcha import CaptchaError, check_config, solve_turnstile
from tempmail import (API, create_account, list_messages, load_config, load_mails, login,
                      proxy_dict, take_proxy)

DIR = Path(__file__).parent
DONE_FILE = DIR / "done.txt"

PRIVY = "https://auth.privy.io"
PRIVY_APP_ID = "cmqe96yef01kj0cldmkvtwpu0"
PRIVY_CLIENT_ID = "client-WY6aRH8RnCTZaD3YTJiVdaWKXUyLU53tegi4xYefa7Lw3"

# Turnstile сайта: без его токена /api/signup не добавляет в вейтлист
TURNSTILE_KEY = "0x4AAAAAAETcKJy2JJAT8noh"
TURNSTILE_ACTION = "waitlist_signup"
TIMEZONE = "America/New_York"  # поле timezone в /api/signup (в config.txt можно задать Timezone=...)

CODE_WAIT = 10      # пауза перед чтением почты, сек
CODE_TIMEOUT = 120  # сколько всего ждать письмо, сек

LOCK = threading.Lock()  # чтобы потоки не писали в консоль/файл одновременно

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def get_link():
    """Адрес сайта без пути и параметров (реф-ссылка ?ref=... тоже подходит)."""
    link = load_config().get("Link")
    if not link:
        sys.exit("В config.txt нет переменной Link=...")
    u = urlsplit(link)
    return f"{u.scheme}://{u.netloc}"


def get_ref():
    """Реф-код из Link: .../r/КОД или ...?ref=КОД. Иначе None.
    Сайт передаёт его только в /api/signup (поле "refs")."""
    u = urlsplit(load_config().get("Link", ""))
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "r":
        return parts[1].strip().upper()
    ref = parse_qs(u.query).get("ref", [None])[0]
    return ref.strip().upper() if ref else None


def make_session(link, proxy):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": link,
        "Referer": link + "/",
    })
    if proxy:
        s.proxies.update(proxy_dict(proxy))
    return s


def privy_headers():
    return {
        "Content-Type": "application/json",
        "privy-app-id": PRIVY_APP_ID,
        "privy-client-id": PRIVY_CLIENT_ID,
        "privy-client": "js-sdk-core:0.67.1",
        "privy-ca-id": str(uuid.uuid4()),
    }


def check_eligible(s, link, email):
    r = s.post(f"{link}/api/signup-eligibility",
               json={"contact": {"channel": "email", "value": email}}, timeout=30)
    try:
        ok = r.ok and r.json().get("eligible") is True
    except ValueError:
        ok = False
    if not ok:
        raise RuntimeError(f"почта не допущена ({r.status_code}: {r.text[:200]})")


def send_code(s, headers, email):
    r = s.post(f"{PRIVY}/api/v1/passwordless/init", json={"email": email},
               headers=headers, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Send code {r.status_code}: {r.text[:200]}")


def get_code(address):
    """Ждёт CODE_WAIT сек, потом читает почту и достаёт 6-значный код."""
    time.sleep(CODE_WAIT)
    _, ms = login(address)
    end = time.time() + CODE_TIMEOUT
    while time.time() < end:
        for m in list_messages(ms):
            msg = ms.get(f"{API}/messages/{m['id']}", timeout=20).json()
            text = f"{msg.get('subject') or ''} {msg.get('text') or ''}"
            found = re.search(r"\b(\d{6})\b", text)
            if found:
                return found.group(1)
        time.sleep(5)
    return None


def verify_code(s, headers, email, code):
    r = s.post(f"{PRIVY}/api/v1/passwordless/authenticate",
               json={"email": email, "code": code, "mode": "login-or-sign-up"},
               headers=headers, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Verify {r.status_code}: {r.text[:200]}")
    return r.json()  # token, identity_token, refresh_token, user


def signup_on_site(s, link, email, data, turnstile_token):
    """Запись в вейтлист: тот же запрос, что делает сайт после подтверждения кода.
    Возвращает ответ сайта (профиль: rank, code...). При ошибке — RuntimeError."""
    access_token = data.get("token")
    if not access_token:
        raise RuntimeError("Privy не вернул token")

    payload = {
        "turnstileToken": turnstile_token,
        "marketingConsent": True,
        "contact": {"channel": "email", "value": email},
        "timezone": load_config().get("Timezone") or TIMEZONE,
        "attribution": {"referral_source": None, "dub_click_id": None,
                        "first_touch": {}, "last_touch": {}},
    }
    ref_code = get_ref()
    if ref_code:
        payload["refs"] = [ref_code]

    headers = {"Authorization": f"Bearer {access_token}"}
    if data.get("identity_token"):
        headers["X-Privy-Identity-Token"] = data["identity_token"]

    r = s.post(f"{link}/api/signup", json=payload, headers=headers, timeout=30)
    if not r.ok:
        raise RuntimeError(f"signup {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"signup: не JSON ({r.text[:200]})")


def log(address, text):
    with LOCK:
        print(f"[{address}] {text}", flush=True)


def process(link, address, proxy):
    s = make_session(link, proxy)
    headers = privy_headers()
    try:
        check_eligible(s, link, address)
        send_code(s, headers, address)
        log(address, "код отправлен, жду письмо...")
        code = get_code(address)
        if not code:
            return "код не пришёл"
        log(address, f"код: {code}")
        
        data = verify_code(s, headers, address, code)
        user_id = (data.get("user") or {}).get("id", "")

        # токен одноразовый и живёт ~5 минут: решаем прямо перед /api/signup
        log(address, "код подтверждён, решаю капчу...")
        try:
            token = solve_turnstile(load_config(), TURNSTILE_KEY, link + "/", TURNSTILE_ACTION)
        except CaptchaError as e:
            return f"код подтверждён {user_id}, капча не решена: {e}"

        profile = signup_on_site(s, link, address, data, token)
        rank = profile.get("rank")
        return f"в вейтлисте rank={rank} {user_id}".strip()
    except Exception as e:
        return f"ошибка: {e}"


def save_result(address, status):
    log(address, f"-> {status}")
    with LOCK, DONE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{address}:{status}\n")


def new_mailbox():
    while True:
        with LOCK:
            proxy = take_proxy()
        if proxy is None:
            return None
        try:
            address = create_account(proxy)
            return address, proxy
        except Exception as e:
            log(proxy.split("@")[-1], f"прокси не подошёл ({e}), беру следующий")


def check_captcha():
    try:
        check_config(load_config())
    except CaptchaError as e:
        sys.exit(f"Капча: {e}")


def run_new():
    check_captcha()
    cfg = load_config()
    link = get_link()
    count = int(cfg.get("Mail_count", 1))
    threads = max(1, int(cfg.get("Threads", 1)))
    print(f"Новых ящиков: {count}, потоков: {threads}")
    
    ref = get_ref()
    if ref:
        print(f"Обнаружен реферальный код: {ref}")

    def worker(_):
        created = new_mailbox()
        if created is None:
            log("-", "прокси в Proxy.txt закончились")
            return False
        address, proxy = created
        log(address, "ящик создан")
        save_result(address, process(link, address, proxy))
        return True

    with ThreadPoolExecutor(max_workers=threads) as pool:
        done = sum(pool.map(worker, range(count)))
    print(f"Готово: {done} из {count}")


def run_one(address):
    check_captcha()
    acc = next((m for m in load_mails() if m[0] == address), None)
    if acc is None:
        sys.exit(f"Ящик {address} не найден в mail.txt")
    save_result(address, process(get_link(), address, acc[2]))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_one(sys.argv[1])
    else:
        run_new()