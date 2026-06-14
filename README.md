# batumi-watchdog

Облачный сторож **batumi-expert.ru** на GitHub Actions (бесплатно, 24/7, независимо от Mac).

Каждые ~10 мин проверяет 3 слоя и шлёт в Telegram **только на смену статуса** (лёг/поднялся):
1. доступность — HTTP-код + тело (есть `</html>`, нет маркеров краха, размер не обвалился);
2. индексируемость — не просочился `noindex`, robots не `Disallow:/`;
3. дрейф — sitemap не просел против бейзлайна.

Зачем: WP fatal под кэшем отдаёт **HTTP 200 с телом «критическая ошибка»** — простой пинг
«200=живой» это пропускает (так одна поломка прожила ~3 дня).

Дополняет: локальный `seo_machine/watchdog.py` (Mac, глубже) + UptimeRobot (e-mail).

## Секреты (Settings → Secrets → Actions)
- `TG_TOKEN` — токен Telegram-бота
- `TG_CHAT` — chat id владельца

State между запусками — через `actions/cache` (rolling). Ручной запуск — вкладка Actions → Run workflow.
