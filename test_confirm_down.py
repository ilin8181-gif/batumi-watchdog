#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест confirm_down(): алерт должен уходить только при УСТОЙЧИВОМ падении.

Три сценария:
  A) сайт падает всегда           -> тревога ДОЛЖНА уйти
  B) сайт упал и сразу ожил      -> тревога НЕ должна уйти (транзиентный сбой)
  C) confirm_down при живом сайте -> пустой результат, без задержек
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_ci as w

REAL_PATHS = w.CRITICAL_PATHS


def run_case(name, fail_attempts, expect_confirmed):
    state = {"call": 0}
    n_paths = max(1, len(REAL_PATHS))

    def fake_check(path):
        idx = state["call"]
        state["call"] += 1
        attempt = idx // n_paths + 1        # какой это ПРОХОД по страницам
        if attempt <= fail_attempts:
            return False, "нет </html> — страница оборвана"
        return True, ""

    w.check_page = fake_check
    fails, confirmed = w.confirm_down(["/ — нет </html> — страница оборвана"],
                                      attempts=2, pause=0.01)
    ok = confirmed == expect_confirmed
    print(f"  {name}: попыток с падением={fail_attempts}, confirmed={confirmed} "
          f"(ожидался {expect_confirmed}) -> {'OK' if ok else 'ПРОВАЛ'}")
    print(f"      вернулось падений: {len(fails)}")
    return ok


print("=== Тест confirm_down (считаем ПОПЫТКИ, не вызовы) ===")
print(f"  страниц в CRITICAL_PATHS: {len(REAL_PATHS)}")
a = run_case("A) падение устойчивое", fail_attempts=99, expect_confirmed=True)
b = run_case("B) упал и сразу ожил", fail_attempts=1, expect_confirmed=False)
c = run_case("C) падает обе попытки", fail_attempts=2, expect_confirmed=True)

w.check_page = lambda p: (True, "")
import time
t0 = time.time()
d, conf = w.confirm_down(["/ — x"], attempts=2, pause=0.01)
print(f"  D) сайт здоров на первой же попытке: падений={len(d)}, "
      f"подтверждено={conf} (ожидалось 0/False) -> "
      f"{'OK' if (len(d) == 0 and not conf) else 'ПРОВАЛ'}")
fast = time.time() - t0 < 0.5
print(f"  D) без лишних пауз: {'OK' if fast else 'МЕДЛЕННО'}")

w.check_page = lambda p: (False, "нет </html>")  # восстановим контекст
ok = all([a, b, c]) and len(d) == 0 and not conf and fast
print("\nИТОГ:", "все сценарии пройдены" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)