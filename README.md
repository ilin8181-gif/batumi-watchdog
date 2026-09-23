# batumi-watchdog

[![watchdog](https://github.com/ilin8181-gif/batumi-watchdog/actions/workflows/watchdog.yml/badge.svg)](https://github.com/ilin8181-gif/batumi-watchdog/actions/workflows/watchdog.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Zero-dependency website watchdog that runs on GitHub Actions — free, 24/7, and independent of your machine.**

It checks your site every 30 minutes on **three layers** and pings Telegram **only when the status actually changes** (down / back up):

1. **Availability** — HTTP status code *and* body sanity: `</html>` present, no crash markers, size hasn't collapsed;
2. **Indexability** — no leaked `noindex` meta tag, `robots.txt` not `Disallow: /`;
3. **Drift** — sitemap URL count hasn't dropped more than 30% against the stored baseline.

## Why HTTP 200 is not enough

A WordPress fatal error served behind a cache still returns **HTTP 200 with a "critical error" body**. Naive uptime pingers mark that as *up* — one real outage like this went unnoticed for ~3 days. This watchdog catches it:

- crash markers (`critical error`, `error establishing a database connection`, …) in the body;
- truncated/empty pages (minimum body size + `</html>` check);
- accidental `noindex` leakage that silently kills organic traffic while the site "works".

## Quick start

1. Fork this repo (or copy `watchdog_ci.py` + `.github/workflows/watchdog.yml` into your own).
2. Add secrets under **Settings → Secrets and Actions**:
   - `TG_TOKEN` — Telegram bot token;
   - `TG_CHAT` — your chat id.
3. Point it at your site: set the `WATCHDOG_SITE` environment variable in the workflow (defaults to `https://batumi-expert.ru`), and edit `CRITICAL_PATHS` / `CRASH_MARKERS` / `MIN_BODY` in `watchdog_ci.py` for your site.
4. (Optional) Pin your own sitemap baseline in `state/watchdog_state.json` as `sitemap_count` to enable the drift check.
5. Done — or run it manually: **Actions → watchdog → Run workflow**.

## How alerting works

State lives in `state/watchdog_state.json` and is carried between runs with `actions/cache` (rolling restore). The bot notifies **only on transitions**:

- `🔴 <host> ЛЁГ …` — first run of a failure, with the list of failed checks;
- `✅ <host> ПОДНЯЛСЯ …` — recovery, with downtime duration.

A cache miss costs at most one duplicate alert.

## False-positive guards

- If *every* path is unreachable, the watchdog probes an external reference (`ya.ru`); if the reference is also down, the run is skipped — it's the CI network, not your site.
- Fail reasons are collected, not fail-fast: you get the full picture in one message.

## Files

| File | Purpose |
|---|---|
| `watchdog_ci.py` | All checks + Telegram notify, Python 3 stdlib only |
| `.github/workflows/watchdog.yml` | Schedule (`*/30 * * * *`), state cache, secrets wiring |
| `state/` | Rolling state between runs |

## Complements

Designed to run alongside a deeper local monitor (e.g. a `watchdog.py` in an SEO pipeline on your own machine) and a simple external pinger (UptimeRobot etc.) — this one is the free cloud layer that never sleeps.

## License

[MIT](LICENSE)

Русское описание: [README.ru.md](README.ru.md)

