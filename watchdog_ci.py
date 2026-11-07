#!/usr/bin/env python3
"""Cloud website watchdog for GitHub Actions (stdlib only).

Self-contained, dependency-free monitor. Secrets come from env
(TG_TOKEN, TG_CHAT); the monitored site comes from WATCHDOG_SITE
(default: https://batumi-expert.ru). Runs in GitHub Actions 24/7
independently of any local machine and messages Telegram only on a
status *change* (down / back up).

Three layers of checks (HTTP 200 alone is not enough — a cached
WordPress fatal error still returns 200 with a "critical error" body):
status code + body (has </html>, no crash markers, size not collapsed),
indexability (no leaked noindex, robots not Disallow:/, sitemap drift).

State (for "alert only on change") lives in state/watchdog_state.json
and survives runs via actions/cache. A cache miss costs at most one
duplicate alert — not critical.
"""
import gzip
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

SITE = (os.environ.get("WATCHDOG_SITE") or "https://batumi-expert.ru").rstrip("/")
SITE_HOST = SITE.split("//", 1)[-1]
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
PAGES = CRITICAL_PATHS

DEFAULT_TIMEOUT = 2.5
ROBOTS_TIMEOUT = 2.5
SITEMAP_TIMEOUT = 3.5
TG_TIMEOUT = 4.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.5
MAX_RETRY_AFTER = 5.0
RECHECK_DELAY = 35
TRANSIENT_HTTP_CODES = {0, 408, 429, 500, 502, 503, 504}

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
_IN_RECHECK = False


def _parse_retry_after(headers=None, raw_body=b""):
    """Извлекает `Retry-After` (сек) из HTTP-заголовков, HTTPError или JSON-ответа Telegram API."""
    if isinstance(headers, urllib.error.HTTPError):
        err = headers
        headers = getattr(err, "headers", None)
        if not raw_body:
            try:
                raw_body = err.read()
            except Exception:
                raw_body = b""
    if headers and hasattr(headers, "get"):
        val = headers.get("Retry-After") or headers.get("retry-after")
        if val is not None:
            try:
                return max(0.0, float(str(val).strip()))
            except ValueError:
                pass
    if raw_body:
        try:
            obj = json.loads(raw_body.decode("utf-8", "replace"))
            ra = ((obj.get("parameters") or {}).get("retry_after")) if isinstance(obj, dict) else None
            if ra is not None:
                return max(0.0, float(ra))
        except Exception:
            pass
    return None


def _fetch_once(url, timeout):
    """Одиночный запрос -> (status, body:str, retry_after:float|None). status=0 при сетевой ошибке/таймауте."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode(r.headers.get_content_charset() or "utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        ra = _parse_retry_after(getattr(e, "headers", None))
        return e.code, "", ra
    except Exception:
        return 0, "", None


def fetch(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, backoff_base=DEFAULT_BACKOFF):
    """(status, body:str) с retry, exponential backoff и поддержкой Retry-After (429)."""
    code, body = 0, ""
    effective_retries = 0 if (_IN_RECHECK and retries == DEFAULT_RETRIES) else max(0, int(retries))
    for attempt in range(effective_retries + 1):
        code, body, retry_after = _fetch_once(url, timeout)
        if code not in TRANSIENT_HTTP_CODES or attempt == effective_retries:
            return code, body
        delay = backoff_base * (2 ** attempt)
        if code == 429 and retry_after is not None:
            delay = min(max(delay, retry_after), MAX_RETRY_AFTER)
        time.sleep(delay)
    return code, body


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
    code, body = fetch(SITE + "/robots.txt", timeout=ROBOTS_TIMEOUT)
    if code != 200:
        return False, f"robots.txt HTTP {code}"
    if re.search(r'(?im)^\s*disallow:\s*/\s*$', body) and \
       re.search(r'(?im)^\s*user-agent:\s*\*', body):
        return False, "robots.txt закрыл сайт (Disallow: /)"
    return True, ""


def check_sitemap(prev):
    code, body = fetch(SITE + "/sitemap_index.xml", timeout=SITEMAP_TIMEOUT)
    if code != 200:
        return False, f"sitemap HTTP {code}", prev
    body_low = body.lower()
    if "<sitemapindex" in body_low and "</sitemapindex>" not in body_low:
        return False, "sitemap_index.xml оборван XML", prev
    child_urls = re.findall(r"<loc>([^<]+)</loc>", body)
    if not child_urls:
        return False, "sitemap пуст (нет дочерних sitemap)", prev
    total = 0
    bad_children = []
    multi_child = len(child_urls) > 1
    for sm in child_urls:
        sm_name = sm.rstrip("/").rsplit("/", 1)[-1] or sm
        c2, b2 = fetch(sm, timeout=SITEMAP_TIMEOUT)
        if c2 != 200:
            label = f"{sm_name} HTTP {c2}" if c2 else f"{sm_name} нет ответа"
            bad_children.append(label)
            break
        b2_low = b2.lower()
        if "<urlset" in b2_low and "</urlset>" not in b2_low:
            bad_children.append(f"{sm_name} оборван XML")
            break
        if "<urlset" in b2_low or "<url>" in b2_low or "</loc>" in b2_low:
            open_locs = len(re.findall(r"<loc>", b2_low))
            close_locs = len(re.findall(r"</loc>", b2_low))
            open_urls = len(re.findall(r"<url>", b2_low))
            close_urls = len(re.findall(r"</url>", b2_low))
            if open_locs != close_locs or open_urls != close_urls:
                bad_children.append(f"{sm_name} повреждён XML (незакрытые теги)")
                break
            cnt = len(re.findall(r"<loc>[^<]+</loc>", b2))
        else:
            cnt = len(re.findall(r"<loc>", b2))
        if multi_child and cnt == 0:
            bad_children.append(f"{sm_name} пуст (0 URL)")
            break
        total += cnt
    if bad_children:
        return False, f"недоступен дочерний sitemap ({', '.join(bad_children)})", prev
    if total == 0:
        return False, "sitemap пуст (0 URL)", prev
    if prev and total < prev * 0.7:
        return False, f"sitemap просел {prev}→{total} URL", total
    return True, "", total


def tg_send(text):
    token = os.environ.get("TG_TOKEN") or TG_TOKEN
    chat = os.environ.get("TG_CHAT") or TG_CHAT
    if not token or not chat:
        print("WARN: TG_TOKEN/TG_CHAT не заданы — алерт не отправлен")
        return
    data = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            urllib.request.urlopen(req, timeout=TG_TIMEOUT).read()
            return
        except urllib.error.HTTPError as e:
            if e.code not in TRANSIENT_HTTP_CODES or attempt == DEFAULT_RETRIES:
                print("WARN: Telegram send failed:", str(e)[:120])
                return
            delay = DEFAULT_BACKOFF * (2 ** attempt)
            ra = _parse_retry_after(e)
            if e.code == 429 and ra is not None:
                delay = min(max(delay, ra), MAX_RETRY_AFTER)
            time.sleep(delay)
        except (TimeoutError, socket.timeout) as e:
            # Read/socket timeout on non-idempotent POST /sendMessage:
            # Telegram server may have already accepted and delivered the message.
            # Do NOT retry to prevent duplicate notifications.
            print("WARN: Telegram send timeout (no retry to avoid duplicate):", str(e)[:120])
            return
        except Exception as e:
            # Check if a wrapped timeout (e.g. URLError(socket.timeout)) occurred
            reason = getattr(e, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                print("WARN: Telegram send timeout (no retry to avoid duplicate):", str(e)[:120])
                return
            if attempt == DEFAULT_RETRIES:
                print("WARN: Telegram send failed:", str(e)[:120])
                return
            time.sleep(DEFAULT_BACKOFF * (2 ** attempt))


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


SITEMAP_WINDOW = 14 * 24 * 3600   # O2: база sitemap = максимум за 14 дней


def sitemap_baseline(state, now):
    """O2: база для сравнения — максимум за окно, а не прошлый прогон.

    Раньше sitemap_count перезаписывался каждым прогоном, и плавное
    падение (100→80→64→…) ни разу не давало разницы ≥30%.
    """
    hist = [(t, c) for t, c in state.get("sitemap_hist", [])
            if now - t <= SITEMAP_WINDOW]
    if hist:
        return max(c for _, c in hist)
    return state.get("sitemap_count", 0)


def remember_sitemap(state, now, count):
    hist = [(t, c) for t, c in state.get("sitemap_hist", [])
            if now - t <= SITEMAP_WINDOW]
    hist.append((now, count))
    state["sitemap_hist"] = [list(x) for x in hist]
    state["sitemap_count"] = count      # совместимость со старым state


def confirm_down(fails, attempts=2, pause=RECHECK_DELAY, recheck=None):
    """Перепроверка перед алертом.

    Инцидент 2026-10-02 16:31 UTC: единственный прогон из 8 показал
    "нет </html>" на трёх страницах ровно в момент пересборки кэша.
    Сайт был жив — сбой длился меньше минуты, а владелец получил
    тревогу "САЙТ ЛЁГ". Скрипт gzip распаковывает корректно, винова
    была кратковременная (транзиентная) поломка.

    Отсюда правило: алерт шлём только после подтверждения. Если при
    повторной проверке сайт ожил — молчим, а факт фиксируем в state.

    Возвращает (итоговый список падений, подтверждено_ли_падение).
    """
    global _IN_RECHECK
    last = fails
    prev_flag = _IN_RECHECK
    _IN_RECHECK = True
    try:
        for i in range(attempts):
            time.sleep(pause)
            if recheck is not None:
                # O1: перепроверяем ВСЕ упавшие слои (страницы + robots + sitemap),
                # иначе падение только robots/sitemap «оживало» и алерт глушился.
                still = recheck()
            else:
                retry = [(p,) + check_page(p) for p in CRITICAL_PATHS]
                still = ["%s — %s" % (p, r) for p, ok, r in retry if not ok]
            if not still:
                return [], False          # ожил — тревога не нужна
            last = still
        return last, True
    finally:
        _IN_RECHECK = prev_flag


def main():
    state = load_state()
    fails = []

    results = [(p,) + check_page(p) for p in CRITICAL_PATHS]
    for p, ok, reason in results:
        if not ok:
            fails.append(f"{p} — {reason}")

    # false-positive guard: if everything is unreachable by network,
    # probe an external reference — if it fails too, it's not the site's fault
    if all(r[2].startswith("нет ответа") for r in results):
        if fetch(EXTERNAL_REF, timeout=DEFAULT_TIMEOUT)[0] == 0:
            print("Внешняя сеть недоступна — пропуск (не вина сайта).")
            return

    rob_ok, rob_reason = check_robots()
    if not rob_ok:
        fails.append(f"robots — {rob_reason}")

    now = time.time()
    baseline = sitemap_baseline(state, now)
    sm_ok, sm_reason, sm_count = check_sitemap(baseline)
    if not sm_ok:
        fails.append(f"sitemap — {sm_reason}")

    now_status = "down" if fails else "ok"
    prev = state.get("status", "ok")
    if sm_ok and sm_count:
        remember_sitemap(state, now, sm_count)

    def recheck():
        still = ["%s — %s" % (p, r) for p, ok, r in
                 [(p,) + check_page(p) for p in CRITICAL_PATHS] if not ok]
        r_ok, r_why = check_robots()
        if not r_ok:
            still.append(f"robots — {r_why}")
        s_ok, s_why, _ = check_sitemap(baseline)
        if not s_ok:
            still.append(f"sitemap — {s_why}")
        return still

    if now_status == "down" and prev == "ok":
        # Не кричим сразу: подтверждаем падение повторной проверкой.
        original_fails = list(fails)
        fails, confirmed = confirm_down(fails, recheck=recheck)
        if not confirmed:
            state["last_transient"] = {
                "at": time.time(),
                "was": original_fails,
                "note": "сайт ожил при перепроверке, алерт не отправлен",
            }
            print("⚠️  Транзиентный сбой: при перепроверке сайт жив, алерт НЕ отправлен")
            now_status = "ok"
        else:
            tg_send(f"🔴 {SITE_HOST} ЛЁГ (облачный сторож)\n"
                    + "\n".join(f"• {f}" for f in fails))
            state["since"] = now
    elif now_status == "ok" and prev == "down":
        tg_send(f"✅ {SITE_HOST} ПОДНЯЛСЯ (лежал ~{human_dur(now - state.get('since', now))})")
        state["since"] = now

    state["status"] = now_status
    state["last_check"] = now
    state["last_fails"] = fails
    save_state(state)

    print(f"status={now_status}" + ("" if not fails else " | " + "; ".join(fails)))


if __name__ == "__main__":
    main()
