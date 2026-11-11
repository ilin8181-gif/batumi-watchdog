---
trigger: always_on
---

# Межрепозиторный мост координатора Batumi Expert (`batumi-watchdog` ↔ `governance/state.json`)

Репозиторий `/Users/aleksandrilin/AI-Projects/batumi-watchdog` является контуром эксплуатационного мониторинга (`contour: watchdog`) единого проекта **Batumi Expert**.
Единый машиночитаемый источник истины (Single Source of Truth) находится в:
- **База состояния:** `/Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/state.json`
- **CLI координатора:** `/Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/coordinator.py`
- **Автосводка:** `/Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/VIEWS-AUTO.md`

## 1. Холодный старт и планирование работ
При любом вопросе владельца («Что делаем?», «Какой статус мониторинга?») или перед изменением проверок `watchdog.py` первым делом вызови:
```bash
python3 /Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/coordinator.py status --contour watchdog
```

## 2. Обязательный 7-шаговый Post-Audit Gate
При проведении любого аудита инфраструктуры, SSL/DNS, endpoint-проверок или алертинга все находки регистрируются поштучно (`N = N`) в `/Users/aleksandrilin/AI-Projects/batumi-expert-project/governance/state.json` через `coordinator.py` (`register-audit` → `add-finding` → привязка `TSK`/`DEC` → `scan-overlaps` → сверка полноты → `validate` с `exit code 0` → `status`).
