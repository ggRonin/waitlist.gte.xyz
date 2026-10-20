"""Временная почта через API mail.tm.

python tempmail.py new             создать Mail_count ящиков (из config.txt) через прокси
python tempmail.py read [адрес]    прочитать письма ящика (по умолчанию — последний из mail.txt)
python tempmail.py wait [адрес]    ждать новое письмо и показать его

Каждый ящик дописывается в mail.txt строкой: адрес:пароль|прокси
Использованный прокси сразу удаляется из Proxy.txt.
"""
import json
import random
import string
import sys
import threading
import time
from pathlib import Path

import requests

API = "https://api.mail.tm"
DIR = Path(__file__).parent
CONFIG_FILE = DIR / "config.txt"
PROXY_FILE = DIR / "Proxy.txt"
MAIL_FILE = DIR / "mail.txt"
ACCOUNT_FILE = DIR / "account.json"  # старый формат, используется как запасной вариант
MAIL_LOCK = threading.Lock()  # запись в mail.txt из нескольких потоков


def rand(n):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ---------- конфиг и прокси ----------

def load_config():
    cfg = {"Mail_count": "1"}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    return cfg


def take_proxy():
    """Берёт первый прокси из Proxy.txt и сразу удаляет его из файла."""
    if not PROXY_FILE.exists():
        return None
    lines = [l.strip() for l in PROXY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    proxy = lines.pop(0)
    PROXY_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return proxy


def proxy_dict(proxy):
    url = proxy if "://" in proxy else f"http://{proxy}"
    return {"http": url, "https": url}


# ---------- создание ящиков ----------

def create_account(proxy):
    proxies = proxy_dict(proxy)
    r = requests.get(f"{API}/domains", proxies=proxies, timeout=20)
    r.raise_for_status()
    domain = r.json()["hydra:member"][0]["domain"]
    address, password = f"{rand(10)}@{domain}", rand(16)
    r = requests.post(f"{API}/accounts", json={"address": address, "password": password},
                      proxies=proxies, timeout=20)
    r.raise_for_status()
    with MAIL_LOCK, MAIL_FILE.open("a", encoding="utf-8") as f:  # дописываем, а не перезаписываем
        f.write(f"{address}:{password}|{proxy}\n")
    return address


def create_many():
    count = int(load_config().get("Mail_count", 1))
    created = 0
    while created < count:
        proxy = take_proxy()
        if proxy is None:
            print("Прокси закончились в Proxy.txt.")
            break
        try:
            address = create_account(proxy)
            created += 1
            print(f"[{created}/{count}] {address}")
        except Exception as e:
            # прокси уже удалён из файла, просто берём следующий
            print(f"Ошибка через прокси {proxy.split('@')[-1]}: {e}. Беру следующий.")
    print(f"Создано ящиков: {created}. Сохранено в {MAIL_FILE.name}")


# ---------- чтение писем ----------

def load_mails():
    """Список ящиков из mail.txt: [(адрес, пароль, прокси|None), ...]."""
    result = []
    if MAIL_FILE.exists():
        for line in MAIL_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            cred, _, proxy = line.partition("|")
            address, _, password = cred.partition(":")
            result.append((address, password, proxy or None))
    return result


def find_account(address=None):
    mails = load_mails()
    if address:
        for acc in mails:
            if acc[0] == address:
                return acc
        sys.exit(f"Ящик {address} не найден в {MAIL_FILE.name}")
    if mails:
        return mails[-1]
    if ACCOUNT_FILE.exists():
        acc = json.loads(ACCOUNT_FILE.read_text())
        return acc["address"], acc["password"], None
    sys.exit("Нет ни одного ящика. Сначала: python tempmail.py new")


def login(address=None):
    address, password, proxy = find_account(address)
    proxies = proxy_dict(proxy) if proxy else None
    r = requests.post(f"{API}/token", json={"address": address, "password": password},
                      proxies=proxies, timeout=20)
    r.raise_for_status()
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json()['token']}"
    if proxies:
        s.proxies.update(proxies)
    return address, s


def list_messages(s):
    r = s.get(f"{API}/messages", timeout=20)
    r.raise_for_status()
    return r.json()["hydra:member"]


def show_message(msg_id, s):
    r = s.get(f"{API}/messages/{msg_id}", timeout=20)
    r.raise_for_status()
    m = r.json()
    print("=" * 60)
    print("От:  ", m["from"]["address"], m["from"].get("name") or "")
    print("Тема:", m["subject"])
    print("Дата:", m["createdAt"])
    print("-" * 60)
    print((m.get("text") or "").strip() or "(пустое текстовое тело)")


def read_all(address=None):
    address, s = login(address)
    msgs = list_messages(s)
    print(f"Ящик {address}: писем {len(msgs)}")
    for m in reversed(msgs):
        show_message(m["id"], s)


def wait_new(address=None, timeout=600):
    address, s = login(address)
    seen = {m["id"] for m in list_messages(s)}
    print(f"Жду письмо на {address} ...")
    end = time.time() + timeout
    while time.time() < end:
        for m in list_messages(s):
            if m["id"] not in seen:
                show_message(m["id"], s)
                return
        time.sleep(5)
    print("Письмо не пришло.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "new"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "new":
        create_many()
    elif cmd == "read":
        read_all(arg)
    elif cmd == "wait":
        wait_new(arg)
    else:
        print(__doc__)
