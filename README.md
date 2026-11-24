# Групповой проект: контракт сервиса команды

Учебный проект по `task.md`: пять команд — транспортные компании. Здесь
лежит portal-facing контракт сервиса команды (exchange profile v2.4.0):
заявки, котировки, уведомления (`order_won`, экспериментальное `order_lost`,
системная доставка) и баллы.

Команда только котирует: по заявке публикуется набор из 1–3 вариантов
(`cheapest` / `fastest` / `balanced`), а выбирает из них портал по своей
политике. Доставку выполняет портал, он же ставит статусы,
время доставки и срыва; у команды нет операций изменения заказа, отмены,
приёмки или подтверждения. Баллы — только `order_delivery` и
`subcontract_delivery`.

## Наша команда

**Соборный коммит** (`soborny-kommit`)
- Репозиторий: https://git.codenotes.ru/team-soborny-kommit/team-service
- Публичный хост: https://team-soborny-kommit.codenotes.ru

## Команды и доступы

Доступы (логины, БД, логи сервиса) — в студенческом портале:
https://student.codenotes.ru/access. Статистика команд —
https://group.codenotes.ru/.

Пять команд проекта, у каждой свой репозиторий и публичный хост:

| Команда (`team.id`)                | Репозиторий                                               | Публичный хост                           |
| ---------------------------------- | --------------------------------------------------------- | ---------------------------------------- |
| Три кота (`tri-kota`)              | https://git.codenotes.ru/team-tri-kota/team-service       | https://team-tri-kota.codenotes.ru       |
| ArJuNis (`arjunis`)                | https://git.codenotes.ru/team-arjunis/team-service        | https://team-arjunis.codenotes.ru        |
| Соборный коммит (`soborny-kommit`) | https://git.codenotes.ru/team-soborny-kommit/team-service | https://team-soborny-kommit.codenotes.ru |
| Deploy Knee (`deploy-knee`)        | https://git.codenotes.ru/team-deploy-knee/team-service    | https://team-deploy-knee.codenotes.ru    |
| Полтора админа (`poltora-admina`)  | https://git.codenotes.ru/team-poltora-admina/team-service | https://team-poltora-admina.codenotes.ru |

## Стек

- Python 3.11+, PyYAML — проверки контракта, моков и справочников;
- FastAPI + uvicorn — reference stub сервиса и просмотрщик Swagger UI / Redoc;
- httpx + jsonschema — conformance-проверки живого сервиса;
- pytest + ruff — тесты и линт (локально и в CI);
- OpenAPI 3.1 — единственный источник истины.

## Структура

| Путь                            | Что внутри                                                                     |
| ------------------------------- | ------------------------------------------------------------------------------ |
| `api/openapi.yaml`              | Контракт: 6 path-объектов, 8 операций                                          |
| `api/mocks/`                    | 6 согласованных наборов: заявки, котировки, заказы, баллы, уведомления, ошибки |
| `api/examples/`                 | 4 тела запросов: request, quote, order-notify, error                           |
| `api/viewer.py`                 | Swagger UI и Redoc поверх контракта                                            |
| `bin/kubectl`                   | `kubectl` для учебных машин (Linux x86-64)                                     |
| `stub/`                         | Reference stub: 8 операций, `X-Api-Key`, in-memory сид                         |
| `contract/`                     | Conformance-проверки любого URL по схемам контракта                            |
| `dictionaries/`                 | Справочники задания: 100 локаций, 4 режима, 537 сегментов                      |
| `scripts/checks.py`             | Валидатор контракта, `$ref`, операций, моков и примеров                        |
| `scripts/check_dictionaries.py` | Компактный валидатор справочников                                              |
| `scripts/check_service.py`      | Обёртка CLI `check-service`                                                    |
| `tests/test_mocks.py`           | Те же инварианты в виде pytest-тестов                                          |
| `tests/test_dictionaries.py`    | Pytest-инварианты справочников                                                 |
| `tests/test_stub.py`            | ASGI-тесты stub: auth, идемпотентность, 404/409/422                            |
| `tests/test_contract.py`        | Conformance против stub и проверка, что нарушения ловятся                      |
| `.forgejo/workflows/ci.yml`     | CI: ruff, pytest, stub, conformance                                            |
| `task.md`, `groups.md`          | Условие задания и состав команд                                                |
| `team-platform/README.md`       | Рабочее окружение команды: сайт, БД, деплой                                    |

## Проверки

```bash
ruff check .                           # линт
python3 -m pytest -q                   # тесты (stub + контракт + conformance)
python3 scripts/checks.py              # контракт, моки, примеры
python3 scripts/check_dictionaries.py  # справочники dictionaries/
npx --yes @redocly/cli@1 lint api/openapi.yaml   # если доступен npx
```

Reference stub и conformance:

```bash
uvicorn stub.app:app --host 127.0.0.1 --port 8080 &
python -m contract.check_service \
  --url http://127.0.0.1:8080 --api-key test-key --order-id ord_6003
```

Тот же сценарий выполняет CI на каждый push в `main` (см.
`.forgejo/workflows/ci.yml`): `ruff check .`, `pytest`, запуск stub, ожидание
`/healthz`, `check-service` против stub. Подробности — `stub/README.md` и
`contract/README.md`.

`api-viewer` (или `python3 api/viewer.py`) поднимает Swagger UI на
http://localhost:8000 и Redoc на `/redoc`.

## kubectl

На учебных машинах с Linux Mint готовый бинарник уже лежит в репозитории:

```bash
./bin/kubectl version --client
```

Чтобы вызывать его без пути, установите в `PATH`:

```bash
sudo install -m 0755 bin/kubectl /usr/local/bin/kubectl
kubectl version --client
```

Бинарник собран под Linux x86-64. На других системах (macOS, Windows, ARM)
ставьте `kubectl` по официальной инструкции:
https://kubernetes.io/ru/docs/tasks/tools/install-kubectl/

## Документация

- `api/README.md` — операции, ошибки, моки и примеры.
- `stub/README.md` — запуск reference stub, тестовый ключ и сидовые id.
- `contract/README.md` — что именно проверяет `check-service` и его CLI.
- `task.md` — условие задания, критерии оценки и правила раундов.
- Эталон имён и семантики — `docs/exchange/integration/team-service-for-portal.openapi.yaml`
  в общем репозитории проекта. Сокращать можно только **ответы** (меньше полей
  в ответе), но не приём `Request`: все поля, которые присылает портал, включая
  необязательные `max_price_rub`/`max_duration_hours`, сервис обязан принять и
  не отвечать `422` на незнакомые необязательные поля.
- `task.md` — условие группового задания, `groups.md` — состав команд.

`dictionaries/` — данные задания (локации, режимы, сегменты маршрутов);
в контракте API `route_id`/`leg_ids` — id сегмента справочника
(`<mode>:<from>-<to>`, например `road:moscow-tula`); `leg_ids` — упорядоченная
цепочка 1–3 сегментов от `origin` до `destination`, `route_id` — её первый
сегмент. Необязательные `max_price_rub`/`max_duration_hours` — потолки заявки;
сервис обязан их принимать. Отдельных мок-таблиц маршрутов нет.
