# Conformance-проверки сервиса команды

`check_service.py` проверяет живой сервис снаружи по контракту
[`api/openapi.yaml`](../api/openapi.yaml): все 8 операций, `X-Api-Key`,
коды ошибок, схемы ответов, набор котировок (1–3 варианта),
идемпотентность событий `notify` и запрет мутаций заказа.

```bash
# против локального stub
python -m contract.check_service \
  --url http://127.0.0.1:8080 --api-key test-key --order-id ord_6003

# против своего сервиса
python -m contract.check_service \
  --url https://team-<id>.codenotes.ru --api-key "$TEAM_API_KEY"

# без проверки notify
python -m contract.check_service --url ... --api-key ... --skip-notify
```

Код выхода `1` — хотя бы одна проверка провалилась; отчёт печатается в
человекочитаемом виде. Ключ не логируется и не попадает в отчёт.

## Что проверяется

| Проверка      | Смысл                                                                                                                                                                                                                                                                                                                                                     |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `spec`        | в `api/openapi.yaml` ровно 8 операций/6 path, нет мутирующих методов, в `Quote`/`QuoteBatch` нет `cost_rub`, `OrderStatus` — только `confirmed/in_transit/delivered/failed`; `notify` принимает `oneOf` из `OrderNotification`/`OrderWonNotification`/`OrderLostNotification`, у событий `event` — `const`, у `OrderLostReason` — `not_selected/no_quote` |
| `healthz`     | `GET /healthz` без ключа отвечает `200` и схемой `Health`                                                                                                                                                                                                                                                                                                 |
| `auth`        | каждая защищённая операция без ключа и с неверным ключом — `401` + `Error`                                                                                                                                                                                                                                                                                |
| `bad_request` | битый JSON и отсутствие `Idempotency-Key` — `400` + `Error`                                                                                                                                                                                                                                                                                               |
| `not_found`   | неизвестная заявка/заказ — `404` + `Error`                                                                                                                                                                                                                                                                                                                |
| `requests`    | `POST` → `201`, повтор с тем же ключом и телом → `200`, другое тело → `409`; список — массив `Request`                                                                                                                                                                                                                                                    |
| `validation`  | тело без обязательного поля, `price_rub=0`, чужой статус в notify — `422` + `Error`                                                                                                                                                                                                                                                                       |
| `quotes`      | `POST` → `201` (`QuoteBatch` из 1–3 вариантов: все `quote_id`, уникальные `variant`, числовой `received_seq` у каждого, без себестоимости), повтор → `409`, `GET` — массив всех вариантов набора по заявке                                                                                                                                                |
| `order`       | `GET` — read-only `OrderSnapshot`, статус из `OrderStatus`, условные поля `delivered_at`/`failed_at`+`failure_code`                                                                                                                                                                                                                                       |
| `notify`      | доставка `204/204/409`; `order_won` `204/204/409` (с `Idempotency-Key`); `order_lost` `reason=not_selected` → `204`, неизвестный `reason` → `422`; `204` без тела; события не меняют снимок заказа                                                                                                                                                        |
| `read_only`   | `PATCH/PUT/DELETE` на заказы/заявки/котировки/баллы и ручки `/award`, `/dictionaries`, `/routes`, `/company` отсутствуют (`404/405`)                                                                                                                                                                                                                      |
| `points`      | только свои `PointsEntry`: `order_delivery` ссылается на доступный заказ, `subcontract_delivery` несёт `subcontract_id`, нет чужих/денежных полей                                                                                                                                                                                                         |

`--order-id` нужен для `notify`; без него checker берёт первый `order_delivery`
из `GET /v1/points`. `--skip-notify` отключает проверки уведомления (полезно,
если у сервиса ещё нет заказа).

## События notify

| Тело                                                                               | Ожидание                                                                                    |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `delivered` / `subcontract_delivered`                                              | `204`, повтор — `204`, другое тело — `409`; идемпотентность по `(order_id, subcontract_id)` |
| `order_won` (`Idempotency-Key: exchange:order:<id>:won:v1`)                        | `204`, повтор — `204`, другое тело с тем же ключом — `409`                                  |
| `order_lost` (`reason=not_selected`, ключ `exchange:order:<id>:lost:<team_id>:v1`) | `204`; неизвестный `reason` — `422`                                                         |

`team_id` в событиях — id команды-получателя; checker передаёт
`team_id: conformance` и не требует сверять его с ключом. `order_won` может
прийти раньше, чем сервис узнал заказ: `404` на это событие не ожидается.

`order_lost` — экспериментальное событие, не входящее в базовый контракт.
В экспериментальном режиме портал отправляет `order_lost` командам, получившим
RFQ, но не выигравшим. Поведение можно выключить на стороне портала без
изменения контракта; команда должна отвечать `204` и не падать, если событие
не приходит.
