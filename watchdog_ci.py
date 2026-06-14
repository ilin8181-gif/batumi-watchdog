#!/usr/bin/env python3
"""Облачный сторож batumi-expert.ru для GitHub Actions (stdlib only).

Самодостаточная версия seo_machine/watchdog.py: без зависимостей от seo_machine,
секреты из env (TG_TOKEN, TG_CHAT). Крутится в GitHub Actions 24/7 независимо от Mac,
шлёт в Telegram только на СМЕНУ статуса (лёг/поднялся).

Три слоя (HTTP 200 недостаточно — WP fatal под кэшем отдаёт 200 с телом «критическая
ошибка»): код + тело (есть </html>, нет маркеров краха, размер не обвалился) +
индексируемость (noindex не просочился, robots не Disallow:/, sitemap-дрейф).

State (для «алерт только на смену») хранится в state/watchdog_state.json и переживает
запуски через actions/cache. Кэш-мисс = максимум один лишний алерт — не критично.
"""
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request

SITE = "https://batumi-expert.ru"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "watchdog_state.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CRITICAL_PATHS = [
    "/",
    "/kupit-kvartiru-batumi/",
    "/novostroyki-batumi/",
    "/apartamenty-batumi/",
    "/dohodnost-kvartiry-batumi/",
    "/rayony-batumi/",
    "/doma-batumi/",
    "/o-nas/",
    "/rayony-batumi/gonio-vs-kvariati/",
]
CRASH_MARKERS = [
    "критическая ошибка",
    "there has been a critical error",
    "error establishing a database connection",
    "fatal error",
    "call to undefined",
    "parse error",
]
MIN_BODY = 8000
EXTERNAL_REF = "https://ya.ru"


def fetch(url, timeout=25):
    """(status, body:str). status=0 при сетевой ошибке."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def check_page(path):
    code, body = fetch(SITE + path)
    if code == 0:
        return False, "нет ответа (сеть/таймаут)"
    if code != 200:
        return False, f"HTTP {code}"
    low = body.lower()
    for m in CRASH_MARKERS:
        if m in low:
            return False, f"в теле: «{m}»"
    if len(body) < MIN_BODY:
        return False, f"тело {len(body)}б — обрыв/белый экран"
    if "</html>" not in low:
        return False, "нет </html> — страница оборвана"
    mm = re.search(r'<meta[^>]+name=["\']robots["\'][^>]*>', body, re.I)
    if mm and "noindex" in mm.group(0).lower():
        return False, "на странице появился noindex"
    return True, ""


def check_robots():
    code, body = fetch(SITE + "/robots.txt", timeout=20)
    if code != 200:
        return False, f"robots.txt HTTP {code}"
    if re.search(r'(?im)^\s*disallow:\s*/\s*$', body) and \
       re.search(r'(?im)^\s*user-agent:\s*\*', body):
        return False, "robots.txt закрыл сайт (Disallow: /)"
    return True, ""


def check_sitemap(prev):
    code, body = fetch(SITE + "/sitemap_index.xml", timeout=20)
    if code != 200:
        return False, f"sitemap HTTP {code}", prev
    total = 0
    for sm in re.findall(r"<loc>([^<]+)</loc>", body):
        c2, b2 = fetch(sm, timeout=20)
        if c2 == 200:
            total += len(re.findall(r"<loc>", b2))
    if total == 0:
        return False, "sitemap пуст (0 URL)", 0
    if prev and total < prev * 0.7:
        return False, f"sitemap просел {prev}→{total} URL", total
    return True, "", total


def tg_send(text):
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not token or not chat:
        print("WARN: TG_TOKEN/TG_CHAT не заданы — алерт не отправлен")
        return
    data = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("WARN: Telegram send failed:", str(e)[:120])


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "ok", "since": time.time(), "sitemap_count": 0}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def human_dur(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{sec // 60} мин"
    if sec < 86400:
        return f"{sec // 3600} ч {sec % 3600 // 60} мин"
    return f"{sec // 86400} дн {sec % 86400 // 3600} ч"


def main():
    state = load_state()
    fails = []

    results = [(p,) + check_page(p) for p in CRITICAL_PATHS]
    for p, ok, reason in results:
        if not ok:
            fails.append(f"{p} — {reason}")

    # защита от ложняков: всё недоступно по сети → проверь внешний референс
    if all(r[2].startswith("нет ответа") for r in results):
        if fetch(EXTERNAL_REF, timeout=15)[0] == 0:
            print("Внешняя сеть недоступна — пропуск (не вина сайта).")
            return

    rob_ok, rob_reason = check_robots()
    if not rob_ok:
        fails.append(f"robots — {rob_reason}")

    sm_ok, sm_reason, sm_count = check_sitemap(state.get("sitemap_count", 0))
    if not sm_ok:
        fails.append(f"sitemap — {sm_reason}")

    now_status = "down" if fails else "ok"
    prev = state.get("status", "ok")
    now = time.time()
    if sm_count:
        state["sitemap_count"] = sm_count

    if now_status == "down" and prev == "ok":
        tg_send("🔴 batumi-expert.ru ЛЁГ (облачный сторож)\n"
                + "\n".join(f"• {f}" for f in fails))
        state["since"] = now
    elif now_status == "ok" and prev == "down":
        tg_send(f"✅ batumi-expert.ru ПОДНЯЛСЯ (лежал ~{human_dur(now - state.get('since', now))})")
        state["since"] = now

    state["status"] = now_status
    state["last_check"] = now
    state["last_fails"] = fails
    save_state(state)

    print(f"status={now_status}" + ("" if not fails else " | " + "; ".join(fails)))


if __name__ == "__main__":
    main()
