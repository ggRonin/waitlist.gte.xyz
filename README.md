# waitlist.gte.xyz

Бот для регистрации в вейтлисте [waitlist.gte.xyz](https://waitlist.gte.xyz) по реферальной ссылке.

На каждый аккаунт бот:
1. Берёт прокси из `Proxy.txt` и создаёт временный ящик на [mail.tm](https://mail.tm).
2. Проверяет, допущена ли почта (`/api/signup-eligibility`).
3. Запрашивает код входа у Privy и читает его из письма.
4. Подтверждает код и получает токены Privy.
5. Решает капчу Cloudflare Turnstile через сервис капчи.
6. Записывает аккаунт в вейтлист (`/api/signup`) с вашим реф-кодом.

Все запросы аккаунта, кроме сервиса капчи, идут через его прокси.

## Установка

Нужен Python 3.10+.

```bash
git clone https://github.com/ggRonin/waitlist.gte.xyz.git
cd waitlist.gte.xyz
pip install -r requirements.txt
```

Скопируйте примеры и заполните их:

```bash
copy config.example.txt config.txt
copy Proxy.example.txt Proxy.txt
```

## config.txt

| Переменная | Что это |
|---|---|
| `Mail_count` | сколько аккаунтов зарегистрировать |
| `Link` | реферальная ссылка: `https://waitlist.gte.xyz/r/КОД` или `...?ref=КОД` |
| `Threads` | сколько аккаунтов делать одновременно |
| `Captcha_key` | ключ API сервиса капчи |
| `Captcha_service` | `capsolver` (по умолчанию), `2captcha` или `capmonster` |
| `Timezone` | необязательно, часовой пояс для сайта (по умолчанию `America/New_York`) |

Без `Captcha_key` бот не запустится.

## Proxy.txt

Один прокси на строку: `user:pass@host:port` (схема `http://` подставится сама). Для каждого ящика берётся первый прокси, и он **сразу удаляется из файла**, чтобы не попасть на второй аккаунт. Если прокси не подошёл, бот берёт следующий.

## Запуск

```bash
python link.py                 # зарегистрировать Mail_count новых аккаунтов
python link.py адрес@домен     # заново провести ящик, который уже есть в mail.txt
```

Отдельно работа с почтой:

```bash
python tempmail.py new             # только создать ящики
python tempmail.py read [адрес]    # прочитать письма
python tempmail.py wait [адрес]    # ждать новое письмо
```

## Файлы, которые создаёт бот

| Файл | Формат |
|---|---|
| `mail.txt` | `адрес:пароль\|прокси` — все созданные ящики |
| `done.txt` | `адрес:статус` — результат по каждому аккаунту |

Статусы в `done.txt`:
- `в вейтлисте rank=N did:privy:...` — аккаунт записан;
- `код подтверждён ..., капча не решена: ...` — проблема с сервисом капчи (баланс, ключ);
- `ошибка: ...` — текст ошибки сайта, Privy или сети;
- `код не пришёл` — письмо не пришло за 2 минуты.

`config.txt`, `Proxy.txt`, `mail.txt` и `done.txt` содержат секреты и в git не попадают (см. `.gitignore`).

## Структура

```
link.py         основной сценарий регистрации
tempmail.py     временная почта mail.tm
captcha.py      решение Turnstile (CapSolver / 2Captcha / CapMonster)
```
