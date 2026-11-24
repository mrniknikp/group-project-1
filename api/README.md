# API сервиса команды

`openapi.yaml` — единственный контракт сервиса команды в групповом проекте
(exchange profile v2.4.0): 6 path-объектов и 8 операций. Команда только
котирует: по заявке публикуется набор из 1–3 вариантов котировки, выбор
делает портал; доставку выполняет портал, баллы начисляет система, у команды
нет операций изменения заказа.

Канон имён и семантики — `docs/exchange/integration/team-service-for-portal.openapi.yaml`
в общем репозитории проекта. Сокращать можно только ответы (меньше полей в
ответе), но не приём `Request`: все поля, которые присылает портал, включая
необязательные `max_price_rub`/`max_duration_hours`, сервис обязан принять.

## Операции

| Метод  | Путь                               | operationId     | Назначение                                                   |
| ------ | ---------------------------------- | --------------- | ------------------------------------------------------------ |
| `GET`  | `/healthz`                         | `getHealth`     | Живость и готовность, без ключа и БД                         |
| `GET`  | `/v1/requests`                     | `listRequests`  | Заявки, переданные команде (массив)                          |
| `POST` | `/v1/requests`                     | `createRequest` | Принять RFQ от портала (`Idempotency-Key`)                   |
| `GET`  | `/v1/requests/{request_id}/quotes` | `listQuotes`    | Свои котировки: до трёх вариантов набора (массив)            |
| `POST` | `/v1/requests/{request_id}/quotes` | `createQuote`   | Опубликовать набор котировок (`QuoteBatch`)                  |
| `GET`  | `/v1/orders/{order_id}`            | `getOrder`      | Read-only снимок заказа                                      |
| `POST` | `/v1/orders/{order_id}/notify`     | `notifyOrder`   | Принять событие портала: `order_won`, `order_lost`, доставка |
| `GET`  | `/v1/points`                       | `listPoints`    | Баллы команды (массив)                                       |

## Аутентификация и ошибки

- `X-Api-Key` — ключ команды; `/healthz` работает без ключа.
- Ошибки — единый конверт `{"code": string, "message": string, "details": object?}`.
- В операциях описаны `400`, `401`, `404`, `409`, `422`, `503`; у `notify` ещё
  `405` и `501` — «ручка не поддерживается» для портала. Отдельного `500` в
  контракте нет — общий конверт ошибок тот же.

## Что держит контракт

- **Нет мутаций заказа.** Только `GET` и `POST`; никаких операций смены
  статуса, отмены, приёмки или подтверждения доставки.
- **Системные поля `readOnly`.** `status`, `delivered_at`, `failed_at`,
  `failure_code`, `points_awarded` ставит портал; `delivered` и `failed`
  терминальны.
- **Котировка без коммерции.** `QuoteInput` — `quote_id`, `variant`,
  `price_rub`, `duration_hours`, `route_id`, `leg_ids`, `valid_until`,
  `comment`; `variant` — `cheapest | fastest | balanced`, уникален внутри
  набора. `POST` принимает `QuoteBatchInput` (1–3 варианта) и отвечает
  `QuoteBatch`; один активный набор на команду и заявку, повтор —
  `409 quote_exists`. `route_id`/`leg_ids` — id сегмента справочника
  (`<mode>:<from>-<to>`, например `road:moscow-tula`); `leg_ids` —
  упорядоченная цепочка 1–3 сегментов, `route_id` — её первый сегмент.
  Необязательные `max_price_rub`/`max_duration_hours` — потолки заявки,
  сервис обязан их принимать. Отдельных мок-таблиц маршрутов нет.
- **Notify — событие, не команда.** Одна ручка, четыре тела:
  `OrderNotification` (`status` — `delivered | subcontract_delivered`),
  `OrderWonNotification` (`event: order_won`) и экспериментальное
  `OrderLostNotification` (`event: order_lost`, `reason` —
  `not_selected | no_quote`). Схема тела — `oneOf` из трёх; ответ — `204` без
  тела, в том числе идемпотентный повтор. `409` — «уже обработано» (портал
  считает это успехом), `404`/`405`/`501` — ручка не поддерживается (портал
  помечает доставку `failed`, награда не откатывается), битое тело — `422`.
- **Идемпотентность notify.** Для `order_won`/`order_lost` портал всегда
  передаёт заголовок `Idempotency-Key`
  (`exchange:order:<order_id>:won:v1`,
  `exchange:order:<order_id>:lost:<team_id>:v1`), и повтор с тем же ключом и
  телом — `204`, другое тело с тем же ключом — `409`. Для событий доставки
  ключ не обязателен: идемпотентность по паре `(order_id, subcontract_id)`
  не изменилась.
- **`order_lost` — эксперимент.** Событие не входит в базовый контракт:
  портал может выключить его без изменения контракта. В экспериментальном
  режиме портал отправляет `order_lost` командам, получившим RFQ, но не
  выигравшим; команда должна отвечать `204` и не падать, если событие не
  приходит.
- **Только баллы.** `order_delivery` и `subcontract_delivery`;
  `subcontract_delivery` может ссылаться на opaque-`subcontract_id` без
  `order_id`.

## Структура

```
api/
├── openapi.yaml   # контракт
├── examples/      # 4 тела: request, quote, order-notify, error
├── mocks/         # 6 согласованных наборов
├── viewer.py      # Swagger UI и Redoc
└── ui.py, ui.html # удобная панель для вызова API (console)
```

## Панель API (`api-console`)

`api/ui.py` поднимает веб-панель на `http://localhost:8001`:

```bash
api-console              # либо python3 api/ui.py 8001
```

В панели можно:

- задать адрес API (по умолчанию `http://31.172.73.114:8080`), `X-Api-Key`
  (по умолчанию `test-key`) и таймаут (по умолчанию 25 с);
- пройти полный цикл «создать заявку → найти маршруты итеративным DFS →
  опубликовать 1–3 варианта → получить котировки»;
- увидеть **реальные** `price_rub` и `duration_hours` из ответа сервера, статус
  и время каждого вызова;
- вызвать любую из 8 операций вручную или через кнопки-пресеты.

Запросы идут через серверную часть панели (`POST /api/call`), поэтому CORS
на целевом API не нужен. Допущены только методы `GET`/`POST` и пути
`/healthz`, `/db-check`, `/v1/...`. Swagger UI и Redoc остаются на порту 8000
(`api-viewer`), ссылка на них есть в шапке панели.

## Моки

| Файл                       | Записей | Что внутри                                                                             |
| -------------------------- | ------: | -------------------------------------------------------------------------------------- |
| `mocks/requests.yaml`      |       5 | по одной на `RequestStatus`                                                            |
| `mocks/quotes.yaml`        |       8 | `submitted`, `accepted`, `lost`, `expired`; `variant` и `price_rub`, без себестоимости |
| `mocks/orders.yaml`        |       4 | по одной на `OrderStatus`; `delivered_at` только у `delivered`                         |
| `mocks/points.yaml`        |       4 | 1 `order_delivery` + 3 `subcontract_delivery`                                          |
| `mocks/notifications.yaml` |       4 | 2 события доставки + `order_won` (по `ord_6001`) и `order_lost` (`not_selected`)       |
| `mocks/errors.yaml`        |       6 | по одному на `400/401/404/409/422/503`                                                 |

Все ID синтетические, секретов и персональных данных нет. Моки собираются
строго по схемам контракта; кросс-ссылки request → quote → order →
notification/point проверяются. `order_lost` ссылается на заявку, а не на
заказ: заказа у проигравшей команды нет.

## Примеры

`examples/request.json`, `examples/quote.json`, `examples/order-notify.json`,
`examples/error.json` — ровно четыре файла, согласованные со схемами контракта
(иллюстративные тела, не записи мок-наборов). Набор фиксирован проверками,
поэтому тела `order_won`/`order_lost` живут не отдельными файлами, а
встроенными примерами операции `notifyOrder` в `openapi.yaml` (вместе с
`delivered` и `subcontract_delivered`).
`quote.json` — тело набора: `{"variants": [...]}` из 1–3 вариантов с
уникальными `variant` и `quote_id`.

## Проверки

```bash
python3 scripts/checks.py              # контракт, $ref, операции, моки, примеры
python3 scripts/check_dictionaries.py  # справочники dictionaries/
ruff check . && python3 -m pytest -q   # линт и тесты (включая conformance)
npx --yes @redocly/cli@1 lint api/openapi.yaml   # если доступен npx
api-viewer                             # Swagger UI на http://localhost:8000
api-console                            # панель вызовов на http://localhost:8001

# исполняемая версия контракта и проверка любого URL
uvicorn stub.app:app --port 8080 &
python -m contract.check_service --url http://127.0.0.1:8080 \
  --api-key test-key --order-id ord_6003
```

`stub/README.md` — сид и поведение reference stub; `contract/README.md` — что
проверяет conformance и как читать отчёт.
