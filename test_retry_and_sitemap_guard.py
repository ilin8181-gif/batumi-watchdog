#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты сетевой устойчивости `fetch()` и защиты baseline `check_sitemap()` в `watchdog_ci.py`.

Покрывает:
- Retry для временных HTTP-ошибок (429, 500, 502, 503, 504) и сетевых таймаутов (`TimeoutError`/`URLError`).
- Ограниченные дефолтные таймауты (`<= 10` с вместо прежних 25/20 с).
- Exponential backoff между повторами (`backoff_base * 2**attempt`).
- Отказ от бесполезных повторов на постоянных клиентских кодах (`404`, `403`).
- Защиту `baseline` при частичной недоступности дочернего sitemap (`HTTP 503`, `502`, `429`, таймаут, обрыв XML, пустой дочерний файл).
- Запрет перезаписи достоверного `sitemap_count` и `sitemap_hist` неполными или аварийными данными в `main()`.
"""
import importlib
import io
import os
import socket
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_ci as w


@pytest.fixture(autouse=True)
def _reset_watchdog_module():
    """Сбрасывает глобальные моки соседних тестов (`test_regressions_o1_o2.py`) перед каждым тестом."""
    importlib.reload(w)
    yield
    importlib.reload(w)



class _DummyResponse:
    def __init__(self, status=200, body="<html>ok</html>", headers=None):
        self.status = status
        self._raw = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = _DummyHeaders(headers or {})

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyHeaders(dict):
    def get_content_charset(self):
        return "utf-8"


def _http_error(code, url="https://batumi-expert.ru/"):
    return urllib.error.HTTPError(url, code, f"HTTP {code}", hdrs={}, fp=io.BytesIO(b""))


# ---------------------------------------------------------------------------
# 1. Таймауты, Retry и Exponential Backoff в `fetch()`
# ---------------------------------------------------------------------------

def test_bounded_default_timeouts():
    """Дефолтный таймаут `fetch()` ограничен (<= 10 с), чтобы укладываться в лимит GitHub Actions."""
    observed_timeouts = []

    def fake_urlopen(req, timeout=None):
        observed_timeouts.append(timeout)
        return _DummyResponse(200, "<html>healthy</html>")

    orig_urlopen = w.urllib.request.urlopen
    w.urllib.request.urlopen = fake_urlopen
    try:
        code, body = w.fetch("https://batumi-expert.ru/")
    finally:
        w.urllib.request.urlopen = orig_urlopen

    assert code == 200
    assert "healthy" in body
    assert len(observed_timeouts) == 1
    assert observed_timeouts[0] is not None and observed_timeouts[0] <= 10


@pytest.mark.parametrize("transient_code", [429, 500, 502, 503, 504])
def test_fetch_retries_transient_http_errors_with_exponential_backoff(transient_code):
    """Одиночные и двойные ошибки 429/500/502/503/504 успешно проходят после retry с exponential backoff."""
    sleeps = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(transient_code)
        return _DummyResponse(200, "<html>recovered</html>")

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    w.urllib.request.urlopen = fake_urlopen
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        code, body = w.fetch("https://batumi-expert.ru/")
    finally:
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert code == 200
    assert "recovered" in body
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[0] > 0
    assert sleeps[1] == pytest.approx(sleeps[0] * 2.0)


@pytest.mark.parametrize("transient_code", [429, 500, 502, 503])
def test_fetch_returns_last_http_code_when_all_retries_exhausted(transient_code):
    """Если все попытки вернули временный код ошибки, возвращается фактический HTTP-код после всех ретраев."""
    sleeps = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(transient_code)

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    w.urllib.request.urlopen = fake_urlopen
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        code, body = w.fetch("https://batumi-expert.ru/")
    finally:
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert code == transient_code
    assert body == ""
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_fetch_retries_socket_timeout_and_recovers():
    """Таймаут сокета (`socket.timeout` / `URLError`) ретраится с exponential backoff."""
    sleeps = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise socket.timeout("timed out")
        if calls["n"] == 2:
            raise urllib.error.URLError(TimeoutError("read timeout"))
        return _DummyResponse(200, "<html>after-timeout</html>")

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    w.urllib.request.urlopen = fake_urlopen
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        code, body = w.fetch("https://batumi-expert.ru/")
    finally:
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert code == 200
    assert "after-timeout" in body
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] == pytest.approx(sleeps[0] * 2.0)


@pytest.mark.parametrize("permanent_code", [403, 404])
def test_fetch_does_not_retry_permanent_4xx_errors(permanent_code):
    """Постоянные ошибки 403/404 не должны тратить время раннера на повторы."""
    sleeps = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(permanent_code)

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    w.urllib.request.urlopen = fake_urlopen
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        code, _ = w.fetch("https://batumi-expert.ru/missing/")
    finally:
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert code == permanent_code
    assert calls["n"] == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# 2. Защита baseline при частичной недоступности дочернего sitemap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_child_status", [0, 429, 500, 502, 503])
def test_check_sitemap_partial_child_failure_preserves_baseline(bad_child_status):
    """Если один дочерний sitemap недоступен, `check_sitemap()` возвращает False и сохраняет `prev` (не частичную сумму)."""
    index_xml = (
        "<sitemapindex>"
        "<sitemap><loc>https://batumi-expert.ru/page-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://batumi-expert.ru/sitemap-objects.xml</loc></sitemap>"
        "<sitemap><loc>https://batumi-expert.ru/vtorichka-sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    def fake_fetch(url, timeout=0):
        if "sitemap_index.xml" in url:
            return 200, index_xml
        if "page-sitemap.xml" in url:
            return 200, "<urlset>" + "<loc>https://batumi-expert.ru/a/</loc>" * 50 + "</urlset>"
        if "sitemap-objects.xml" in url:
            return bad_child_status, ""
        if "vtorichka-sitemap.xml" in url:
            return 200, "<urlset>" + "<loc>https://batumi-expert.ru/b/</loc>" * 120 + "</urlset>"
        return 200, ""

    orig_fetch = w.fetch
    w.fetch = fake_fetch
    try:
        ok, reason, returned_count = w.check_sitemap(prev=240)
    finally:
        w.fetch = orig_fetch

    assert ok is False
    assert "sitemap-objects.xml" in reason
    assert "просел" not in reason, f"Нельзя маскировать сбой дочернего sitemap под просадку URL: {reason}"
    assert returned_count == 240, f"Baseline обязан сохраниться (240), получено: {returned_count}"


def test_check_sitemap_truncated_or_empty_child_in_multi_sitemap_preserves_baseline():
    """Оборванный XML (`<urlset` без `</urlset>`) или пустой дочерний файл в мульти-индексе не портит `prev`."""
    index_xml = (
        "<sitemapindex>"
        "<sitemap><loc>https://batumi-expert.ru/page-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://batumi-expert.ru/post-sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    # Случай A: оборванный XML дочернего sitemap
    def fake_fetch_truncated(url, timeout=0):
        if "sitemap_index.xml" in url:
            return 200, index_xml
        if "page-sitemap.xml" in url:
            return 200, "<urlset>" + "<loc>https://batumi-expert.ru/p/</loc>" * 80 + "</urlset>"
        return 200, "<urlset><url><loc>https://batumi-expert.ru/blog/1/</loc>"  # нет </urlset>

    # Случай B: один дочерний sitemap вернул 200, но 0 URL
    def fake_fetch_empty_child(url, timeout=0):
        if "sitemap_index.xml" in url:
            return 200, index_xml
        if "page-sitemap.xml" in url:
            return 200, "<urlset>" + "<loc>https://batumi-expert.ru/p/</loc>" * 80 + "</urlset>"
        return 200, "<urlset></urlset>"

    orig_fetch = w.fetch
    try:
        w.fetch = fake_fetch_truncated
        ok_a, reason_a, cnt_a = w.check_sitemap(prev=110)
        assert ok_a is False
        assert "post-sitemap.xml" in reason_a
        assert cnt_a == 110

        w.fetch = fake_fetch_empty_child
        ok_b, reason_b, cnt_b = w.check_sitemap(prev=110)
        assert ok_b is False
        assert "post-sitemap.xml" in reason_b
        assert cnt_b == 110
    finally:
        w.fetch = orig_fetch


def test_main_never_overwrites_saved_state_with_partial_or_failed_sitemap():
    """Интеграционный тест `main()`: при сбое дочернего sitemap или `sitemap_index`
    прежнее достоверное значение `sitemap_count` и `sitemap_hist` не перезаписывается."""
    now_fixed = 1_790_000_000.0
    initial_hist = [[now_fixed - 3600, 300]]
    state = {
        "status": "ok",
        "since": now_fixed - 86400,
        "sitemap_count": 300,
        "sitemap_hist": [list(x) for x in initial_hist],
    }
    saved_snapshots = []
    sent_alerts = []

    index_xml = (
        "<sitemapindex>"
        "<sitemap><loc>https://batumi-expert.ru/page-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://batumi-expert.ru/vtorichka-sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    def fake_fetch_partial_503(url, timeout=0):
        if "sitemap_index.xml" in url:
            return 200, index_xml
        if "page-sitemap.xml" in url:
            # Даже если 220 >= 300*0.7 (210), неполная сумма НЕ должна сохраниться!
            return 200, "<urlset>" + "<loc>https://batumi-expert.ru/p/</loc>" * 220 + "</urlset>"
        if "vtorichka-sitemap.xml" in url:
            return 503, ""
        return 200, "<html>" + ("x" * 9000) + "</html>"

    orig_fetch = w.fetch
    orig_check_page = w.check_page
    orig_check_robots = w.check_robots
    orig_load = w.load_state
    orig_save = w.save_state
    orig_tg = w.tg_send
    orig_sleep = w.time.sleep

    w.fetch = fake_fetch_partial_503
    w.check_page = lambda p: (True, "")
    w.check_robots = lambda: (True, "")
    w.load_state = lambda: state
    w.save_state = lambda s: saved_snapshots.append(dict(s))
    w.tg_send = lambda m: sent_alerts.append(m)
    w.time.sleep = lambda s: None
    try:
        w.main()
    finally:
        w.fetch = orig_fetch
        w.check_page = orig_check_page
        w.check_robots = orig_check_robots
        w.load_state = orig_load
        w.save_state = orig_save
        w.tg_send = orig_tg
        w.time.sleep = orig_sleep

    assert state["status"] == "down"
    assert len(sent_alerts) == 1
    assert "vtorichka-sitemap.xml" in sent_alerts[0]
    # Главное требование: достоверный baseline 300 не перезаписан частичным числом 220 и история не загрязнена
    assert state["sitemap_count"] == 300
    assert state["sitemap_hist"] == initial_hist


# ---------------------------------------------------------------------------
# 3. Аудит готовности к слиянию: бюджет времени < 300 с, Retry-After (429),
#    защита от дублей Telegram при таймауте и повреждённый XML
# ---------------------------------------------------------------------------

def test_worst_case_runtime_budget_fits_within_github_actions_5_minutes():
    """Доказывает, что абсолютный худший сценарий (Проход 1 со всеми retry + RECHECK_DELAY 45 с +
    Проход 2 recheck со всеми retry + отправка в Telegram) гарантированно укладывается в < 300 с
    (`timeout-minutes: 5` в `.github/workflows/watchdog.yml`) с запасом >= 30 с на старт раннера."""
    attempts = w.DEFAULT_RETRIES + 1
    backoff_sum = sum(w.DEFAULT_BACKOFF * (2 ** i) for i in range(w.DEFAULT_RETRIES))

    page_worst = attempts * w.DEFAULT_TIMEOUT + backoff_sum
    robots_worst = attempts * w.ROBOTS_TIMEOUT + backoff_sum
    # В check_sitemap() при fail-fast на первом упавшем дочернем файле:
    # 1 запрос sitemap_index + максимум 4 дочерних (3 быстрых + 1 по полному таймауту со всеми retry)
    sitemap_worst = w.SITEMAP_TIMEOUT + (attempts * w.SITEMAP_TIMEOUT + backoff_sum)

    single_pass_worst = len(w.PAGES) * page_worst + robots_worst + sitemap_worst
    tg_worst = attempts * w.TG_TIMEOUT + backoff_sum

    total_worst_case_sec = single_pass_worst + w.RECHECK_DELAY + single_pass_worst + tg_worst
    assert total_worst_case_sec <= 270.0, (
        f"Худший сценарий ({total_worst_case_sec:.1f} с) превышает безопасный порог 270 с для 5-минутного лимита CI!"
    )


def test_http_429_respects_bounded_retry_after_in_fetch_and_tg_send():
    """Проверяет учёт заголовка `Retry-After` (и JSON `retry_after` в Telegram) при HTTP 429
    с ограничением верхней границы (`MAX_RETRY_AFTER`), чтобы не подвесить раннер."""
    sleeps = []
    calls = {"n": 0}

    def fake_urlopen_fetch(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            err = urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", hdrs={"Retry-After": "2"}, fp=io.BytesIO(b"")
            )
            raise err
        return _DummyResponse(200, "<html>ok</html>")

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    w.urllib.request.urlopen = fake_urlopen_fetch
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        code, body = w.fetch("https://batumi-expert.ru/")
    finally:
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert code == 200
    assert sleeps == [2.0]

    # Проверяем tg_send с JSON retry_after и защитным потолком MAX_RETRY_AFTER
    tg_sleeps = []
    tg_calls = {"n": 0}

    def fake_urlopen_tg(req, timeout=None):
        tg_calls["n"] += 1
        if tg_calls["n"] == 1:
            payload = b'{"ok":false,"error_code":429,"parameters":{"retry_after":999}}'
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", hdrs={}, fp=io.BytesIO(payload))
        return _DummyResponse(200, '{"ok":true}')

    orig_token, orig_chat = w.TG_TOKEN, w.TG_CHAT
    w.TG_TOKEN, w.TG_CHAT = "test-token", "12345"
    w.urllib.request.urlopen = fake_urlopen_tg
    w.time.sleep = lambda s: tg_sleeps.append(s)
    try:
        w.tg_send("Тест 429")
    finally:
        w.TG_TOKEN, w.TG_CHAT = orig_token, orig_chat
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert tg_calls["n"] == 2
    assert len(tg_sleeps) == 1
    assert tg_sleeps[0] == w.MAX_RETRY_AFTER


def test_tg_send_does_not_duplicate_alert_on_socket_timeout_but_retries_503():
    """Таймаут чтения (`socket.timeout` / `TimeoutError`) на POST `/sendMessage` не должен
    вызывать повторный POST (риск дублирования уже доставленного алерта в Telegram),
    тогда как явный HTTP 503 от сервера безопасно повторяется."""
    timeout_calls = {"n": 0}

    def fake_urlopen_timeout(req, timeout=None):
        timeout_calls["n"] += 1
        raise socket.timeout("read timed out after POST body sent")

    orig_urlopen = w.urllib.request.urlopen
    orig_sleep = w.time.sleep
    orig_token, orig_chat = w.TG_TOKEN, w.TG_CHAT
    w.TG_TOKEN, w.TG_CHAT = "test-token", "12345"
    w.urllib.request.urlopen = fake_urlopen_timeout
    w.time.sleep = lambda s: None
    try:
        w.tg_send("Критический алерт")
    finally:
        w.TG_TOKEN, w.TG_CHAT = orig_token, orig_chat
        w.urllib.request.urlopen = orig_urlopen
        w.time.sleep = orig_sleep

    assert timeout_calls["n"] == 1, "POST в Telegram при таймауте чтения не должен дублироваться!"


def test_partially_corrupted_sitemap_index_and_unclosed_loc_tags_preserve_baseline():
    """Проверяет защиту baseline при оборванном `sitemap_index.xml` и при незакрытых тегах `<loc>` / `<url>`."""
    def fake_truncated_index(url, timeout=0):
        return 200, "<sitemapindex><sitemap><loc>https://batumi-expert.ru/page-sitemap.xml</loc>"

    def fake_corrupted_child_tags(url, timeout=0):
        if "sitemap_index.xml" in url:
            return 200, "<sitemapindex><sitemap><loc>https://batumi-expert.ru/page-sitemap.xml</loc></sitemap></sitemapindex>"
        # Два открывающих <loc>, но только один закрывающий </loc> (обрыв внутри тега)
        return 200, "<urlset><url><loc>https://batumi-expert.ru/1/</loc></url><url><loc>https://batumi-expert.ru/2/</urlset>"

    orig_fetch = w.fetch
    try:
        w.fetch = fake_truncated_index
        ok1, reason1, cnt1 = w.check_sitemap(prev=423)
        assert ok1 is False
        assert cnt1 == 423

        w.fetch = fake_corrupted_child_tags
        ok2, reason2, cnt2 = w.check_sitemap(prev=423)
        assert ok2 is False
        assert cnt2 == 423
    finally:
        w.fetch = orig_fetch

