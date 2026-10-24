#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регрессии O1/O2 (найдены аудитом 2026-10-24).

O1: robots.txt закрыл сайт, страницы живы -> алерт ДОЛЖЕН уйти
    (раньше confirm_down перепроверял только страницы и глушил тревогу).
O2: sitemap плавно тает 100->80->64->52 -> алерт ДОЛЖЕН уйти, когда
    падение от максимума за 14 дней >= 30% (раньше база сползала вниз).
Плюс защита от ложных тревог: транзиентный сбой robots -> молчим.
Запуск: python3 test_regressions_o1_o2.py  (или pytest).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_ci as w

_REAL_TG_SEND = w.tg_send          # настоящая отправка — до любых подмен


def _harness(state, robots, sitemap_counts):
    """Подменяет сеть/state; robots и sitemap_counts — итераторы ответов."""
    sent = []
    w.time.sleep = lambda s: None
    w.check_page = lambda p: (True, "")
    w.check_robots = lambda: next(robots)
    w.load_state = lambda: state
    w.save_state = lambda s: None
    w.tg_send = lambda m: sent.append(m)

    def fake_fetch(url, timeout=0):
        if "sitemap_index" in url:
            return 200, "<loc>https://x/s.xml</loc>"
        return 200, "<loc>" * next(sitemap_counts)
    w.fetch = fake_fetch
    return sent


def test_o1_robots_closed_alerts():
    st = {"status": "ok", "sitemap_count": 100}
    closed = (False, "robots.txt закрыл сайт (Disallow: /)")
    sent = _harness(st, iter([closed] * 10), iter([100] * 10))
    w.main()
    assert st["status"] == "down", st
    assert len(sent) == 1 and "robots" in sent[0], sent


def test_o1_transient_robots_stays_silent():
    st = {"status": "ok", "sitemap_count": 100}
    seq = iter([(False, "robots.txt HTTP 503")] + [(True, "")] * 10)
    sent = _harness(st, seq, iter([100] * 10))
    w.main()
    assert st["status"] == "ok" and sent == [], (st, sent)


def test_o2_gradual_sitemap_decline_alerts():
    st = {"status": "ok", "sitemap_count": 100}
    alerts_at = []
    for n in [100, 80, 64, 52, 42]:
        sent = _harness(st, iter([(True, "")] * 10), iter([n] * 10))
        w.main()
        if sent:
            alerts_at.append(n)
    assert alerts_at and alerts_at[0] == 64, alerts_at   # 64 < 0.7*100
    assert st["status"] == "down"


def test_full_path_robots_closed_reaches_telegram_api():
    """Событие -> формирование текста -> настоящий tg_send -> HTTP-запрос в Bot API.

    Сеть не трогаем: подменяем только urllib.request.urlopen и проверяем,
    что реальный tg_send собрал корректный POST с chat_id и текстом.
    """
    import json
    st = {"status": "ok", "sitemap_count": 100}
    closed = (False, "robots.txt закрыл сайт (Disallow: /)")
    _harness(st, iter([closed] * 10), iter([100] * 10))
    w.tg_send = _REAL_TG_SEND
    captured = []

    class _Resp:
        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout=0):
        captured.append(req)
        return _Resp()
    os.environ["TG_TOKEN"], os.environ["TG_CHAT"] = "TEST_TOKEN", "12345"
    real_urlopen = w.urllib.request.urlopen
    w.urllib.request.urlopen = fake_urlopen
    try:
        w.main()
    finally:
        w.urllib.request.urlopen = real_urlopen
        os.environ.pop("TG_TOKEN"), os.environ.pop("TG_CHAT")
    assert len(captured) == 1, captured
    req = captured[0]
    assert req.full_url == "https://api.telegram.org/botTEST_TOKEN/sendMessage"
    body = json.loads(req.data.decode())
    assert body["chat_id"] == "12345"
    assert "ЛЁГ" in body["text"] and "robots" in body["text"], body["text"]


def test_o2_window_forgets_old_peak():
    now = w.time.time()
    st = {"sitemap_hist": [[now - 20 * 86400, 1000], [now - 86400, 100]]}
    assert w.sitemap_baseline(st, now) == 100


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("OK  ", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, e)
    print("ИТОГ:", "все сценарии пройдены" if not failed else f"провалов: {failed}")
    sys.exit(1 if failed else 0)
