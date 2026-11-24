# AGENTS.md

Факты репозитория и команды проверок. Контракт — `api/openapi.yaml`
(OpenAPI 3.1, exchange profile v2.4.0).

## Установка и проверки

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .                           # линт
python3 -m pytest -q                   # тесты (stub, контракт, conformance)
python3 scripts/checks.py              # контракт, $ref, операции, моки, примеры
python3 scripts/check_dictionaries.py  # справочники dictionaries/
api-viewer                             # Swagger UI: http://localhost:8000

# reference stub (8 операций, X-Api-Key, сид, ключ test-key)
uvicorn stub.app:app --host 127.0.0.1 --port 8080 &
# conformance любого URL по схемам api/openapi.yaml
python -m contract.check_service \
  --url http://127.0.0.1:8080 --api-key test-key --order-id ord_6003
```

`validate-api`, `validate-dictionaries`, `api-viewer` — консольные скрипты
из `pyproject.toml`; `python3 scripts/checks.py`,
`python3 scripts/check_dictionaries.py` и `python3 api/viewer.py`
эквивалентны им.
Все команды проверок должны проходить до и после правки. Если проверка
падает — чинить нужно правку, а не тест.

## Карта репозитория

| Путь                                     | Роль                                                             | Можно ли менять                 |
| ---------------------------------------- | ---------------------------------------------------------------- | ------------------------------- |
| `api/openapi.yaml`                       | Единственный контракт: пути, схемы, ответы                       | да, согласованно                |
| `api/mocks/*.yaml`                       | Согласованные моки 6 наборов                                     | только данные, строго по схемам |
| `api/examples/*.json`                    | Ровно 4 тела запросов                                            | да, вслед за контрактом         |
| `api/viewer.py`                          | Swagger UI / Redoc                                               | только обвязку                  |
| `stub/`                                  | Reference stub контракта: 8 операций, in-memory сид, `X-Api-Key` | да, с сохранением контракта     |
| `contract/`                              | Conformance-проверки живого сервиса (`check_service.py`, README) | да                              |
| `scripts/check_service.py`               | Обёртка CLI `check-service`                                      | только обвязку                  |
| `tests/test_stub.py`                     | ASGI-тесты stub: auth, идемпотентность, 404/409/422              | да                              |
| `tests/test_contract.py`                 | Conformance против stub и проверка, что нарушения ловятся        | да                              |
| `.forgejo/workflows/ci.yml`              | CI: ruff, pytest, stub, conformance                              | да                              |
| `dictionaries/`                          | Данные задания: локации, режимы, 537 сегментов                   | только по задаче                |
| `scripts/checks.py`                      | Проверки контракта и моков                                       | да                              |
| `scripts/check_dictionaries.py`          | Проверки справочников                                            | да                              |
| `tests/test_mocks.py`                    | Pytest-инварианты контракта, без дублирования логики             | да                              |
| `tests/test_dictionaries.py`             | Pytest-инварианты справочников                                   | да                              |
| `pyproject.toml`                         | Зависимости и entry points                                       | да                              |
| `task.md`, `groups.md`, `team-platform/` | Условие, команды, окружение                                      | нет                             |

Правило: логика живёт в `scripts/checks.py` и
`scripts/check_dictionaries.py`, тесты только вызывают её.

## Инварианты контракта

- Набор операций фиксирован: 6 path-объектов, 8 операций; `operationId`
  уникальны. Мутирующих методов нет.
- Ошибка — ровно `{"code", "message", "details"?}`, `details` — объект.
- `status`, `delivered_at`, `failed_at`, `failure_code`, `points_awarded` —
  `readOnly`; `delivered` и `failed` терминальны, повторного решения нет.
- В `QuoteInput` только котировочные поля; никакой коммерции конкурентов.
  `variant` (`cheapest`/`fastest`/`balanced`) уникален внутри набора; `POST`
  принимает 1–3 варианта и отвечает `QuoteBatch`, один активный набор на
  команду и заявку.
- `route_id`/`leg_ids` — id сегмента справочника (`<mode>:<from>-<to>`,
  например `road:moscow-tula`); `leg_ids` — упорядоченная цепочка 1–3
  сегментов, `route_id` — её первый сегмент; мок-таблицы маршрутов нет.
- `OrderStatus` — `confirmed | in_transit | delivered | failed`;
  `cancelled` — только у `RequestStatus`.
- `notify` — одна ручка, четыре тела: `OrderNotification` (`delivered` /
  `subcontract_delivered`), `OrderWonNotification` (`event: order_won`) и
  экспериментальное `OrderLostNotification` (`event: order_lost`, `reason` —
  `not_selected` / `no_quote`). Успех и идемпотентный повтор — `204`, `409` —
  «уже обработано», `404`/`405`/`501` — ручка не поддерживается. События
  идемпотентны по `Idempotency-Key` (`exchange:order:<id>:won:v1`,
  `exchange:order:<id>:lost:<team_id>:v1`), доставка — по
  `(order_id, subcontract_id)`; статус заказа, доставку и баллы notify не меняет.
- Баллы — только `order_delivery` и `subcontract_delivery`; начисление
  идемпотентно по `exchange:*`, `failed` баллов не начисляет.
- `stub/` — исполняемая версия контракта: те же 8 операций, `X-Api-Key`
  (по умолчанию `test-key`), `notify` не меняет статус/баллы (в т.ч. для
  `order_won`/`order_lost`), `POST` набора
  котировок (1–3 варианта, сид — один `balanced`) повторно —
  `409 quote_exists` (в контракте нет `200` для этой операции).
- `contract/check_service.py` проверяет сервис снаружи по
  `components.schemas`; `--url`, `--api-key`, `--order-id`, `--skip-notify`,
  exit code 1 при ошибке. Нарушения должны ловиться тестом
  `tests/test_contract.py`, а не только глазами.
- Эталон имён и семантики — `docs/exchange/integration/team-service-for-portal.openapi.yaml`
  в общем репозитории проекта. Сокращать можно только ответы; приём `Request`
  обязан включать все поля, которые присылает портал (включая необязательные
  `max_price_rub`/`max_duration_hours`).

## Правила данных

- Все даты — RFC 3339 UTC (`Z`), суммы — целые рубли (`price_rub`).
- ID синтетические, уникальные; кросс-ссылки request → quote → order →
  notification/point обязаны сходиться.
- `delivered_at` — только у `delivered`; `failed_at` + `failure_code` —
  только у `failed`; `delivered_at` несовместим с `failed_at`.
- Моки не фиксируют размеры наборов в тестах и не раздуваются «для объёма».
- Секреты, токены, персональные данные в репозиторий не коммитятся.

## Стиль

- Python 3.10+, `from __future__ import annotations`, современные аннотации.
- Комментарии и сообщения проверок — по-русски, с указанием места ошибки.
- Функции проверок чистые: принимают данные, возвращают `list[str]`;
  печать — только в `main()`.
- Новые зависимости не добавлять без необходимости: PyYAML, FastAPI,
  uvicorn, pytest.

## Перед коммитом

1. `ruff check .` — чисто.
2. `python3 -m pytest -q` — зелёный (stub, контракт, conformance).
3. `python3 scripts/checks.py` — «Все проверки пройдены».
4. Если правили `api/openapi.yaml` — `npx --yes @redocly/cli@1 lint api/openapi.yaml`
   (если доступен npx; иначе отметьте в PR).
5. Если правили `api/examples/*.json` — это валидный JSON.
6. Если правили `stub/` или `contract/` — прогоните связку stub + `check-service`
   (или дождитесь CI: он делает то же самое).
7. README, `api/README.md`, `stub/README.md` и `contract/README.md` отражают
   фактический набор операций, моков и проверок.
