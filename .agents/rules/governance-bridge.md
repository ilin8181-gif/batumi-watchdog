---
trigger: always_on
---

# Актуальный межконтурный регламент (`batumi-watchdog` ↔ `batumi-expert-project`)

Репозиторий `/Users/aleksandrilin/AI-Projects/batumi-watchdog` является контуром эксплуатационного мониторинга (`contour: watchdog`) боевого домена `batumi-expert.ru` проекта **Batumi Expert**.

## 1. Отмена старого координатора (`read-only` архив)
- Архивный каталог `/Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/` (`coordinator.py`, `state.json`, старые реестры) сохраняется **строго в режиме `read-only` архива**.
- Обязательный вызов `coordinator.py` и старый 7-шаговый Post-Audit Gate **отменены** — не запускать `coordinator.py` и не записывать находки в `state.json`.

## 2. Межконтурный read-only контракт и изоляция
- При вопросе владельца «Что делаем?» или при разборе инцидентов доступности разрешён ограниченный `read-only` доступ для сверки состояния сторожа с оперативной панелью [`/Users/aleksandrilin/AI-Projects/batumi-expert-project/STATUS.md`](/Users/aleksandrilin/AI-Projects/batumi-expert-project/STATUS.md) (а по доменам и контактам — с `FACTS.md`), **без модификации файлов соседнего репозитория** без прямого поручения владельца.
- Результаты аудитов и проверок фиксируются в локальном отчёте/задаче (и переносятся в `STATUS.md` основного проекта только по прямому поручению владельца).
- Чужие проекты (`woodroot-batumi`, `woodroot-ads`, `primeera-fitness`) полностью изолированы — обращение к ним запрещено.
