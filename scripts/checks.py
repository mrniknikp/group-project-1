"""Валидатор контракта api/openapi.yaml, моков и примеров.

Запуск: ``python3 scripts/checks.py`` (код выхода 1 при любой ошибке).
Только стандартная библиотека и PyYAML; без jsonschema и сетевых вызовов.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[1]
API_SPEC = ROOT / 'api' / 'openapi.yaml'
MOCKS_DIR = ROOT / 'api' / 'mocks'
EXAMPLES_DIR = ROOT / 'api' / 'examples'

MOCK_FILES = ('requests', 'quotes', 'orders', 'points', 'notifications', 'errors')
EXAMPLE_FILES = ('request.json', 'quote.json', 'order-notify.json', 'error.json')

# События notify: `event` отличает уведомление от события доставки.
NOTIFY_EVENT_SCHEMAS = {
    'order_won': 'OrderWonNotification',
    'order_lost': 'OrderLostNotification',
}

OPERATIONS = {
    ('get', '/healthz'): 'getHealth',
    ('get', '/v1/requests'): 'listRequests',
    ('post', '/v1/requests'): 'createRequest',
    ('get', '/v1/requests/{request_id}/quotes'): 'listQuotes',
    ('post', '/v1/requests/{request_id}/quotes'): 'createQuote',
    ('get', '/v1/orders/{order_id}'): 'getOrder',
    ('post', '/v1/orders/{order_id}/notify'): 'notifyOrder',
    ('get', '/v1/points'): 'listPoints',
}
METHODS = {'get', 'post', 'put', 'patch', 'delete'}
FORBIDDEN_WORDS = (
    'cost_rub', 'cost', 'carriers', 'team_ids', 'owner_team_id',
    'payment', 'payout', 'invoice', 'advance', 'balance',
)
ERROR_STATUSES = {400, 401, 404, 409, 422, 503}
DATE_FIELDS = ('received_at', 'valid_until', 'response_deadline',
               'delivered_at', 'failed_at', 'created_at')


# ------------------------------------------------------------------ загрузка

def load_yaml(path: Path) -> Any:
    with path.open(encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def load_api_spec() -> dict[str, Any]:
    return load_yaml(API_SPEC)


def load_mocks() -> dict[str, Any]:
    return {name: load_yaml(MOCKS_DIR / f'{name}.yaml') for name in MOCK_FILES}


def load_examples() -> dict[str, Any]:
    return {name: json.loads((EXAMPLES_DIR / name).read_text(encoding='utf-8'))
            for name in EXAMPLE_FILES}


# ------------------------------------------------------------------ утилиты

def iter_operations(spec: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in spec.get('paths', {}).items():
        for method, operation in item.items():
            if method in METHODS:
                yield method, path, operation


def iter_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == '$ref' and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_refs(value)


def resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith('#/'):
        raise ValueError(f'внешняя ссылка не поддерживается: {ref}')
    node: Any = spec
    for part in ref[2:].split('/'):
        node = node[part.replace('~1', '/').replace('~0', '~')]
    return node


def enum(spec: dict[str, Any], schema: str, field: str | None = None) -> set[str]:
    node = spec['components']['schemas'][schema]
    if field is not None:
        node = node['properties'][field]
    while '$ref' in node:
        node = resolve_ref(spec, node['$ref'])
    return set(node['enum'])


def iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith('Z'):
        return False
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return True


def required_and_allowed(spec: dict[str, Any], name: str) -> tuple[set[str], set[str]]:
    schema = spec['components']['schemas'][name]
    return set(schema.get('required', ())), set(schema.get('properties', {}))


def check_item(item: Any, required: set[str], allowed: set[str],
               where: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f'{where}: ожидался объект')
        return
    for field in sorted(required - item.keys()):
        errors.append(f'{where}: нет обязательного поля {field}')
    for field in sorted(item.keys() - allowed):
        errors.append(f'{where}: поле {field} не описано схемой')
    for field in DATE_FIELDS:
        if field in item and not iso(item[field]):
            errors.append(f'{where}: {field} не RFC 3339 UTC')


def unique(items: list[dict[str, Any]], key: str, where: str,
           errors: list[str]) -> None:
    seen: set[Any] = set()
    for item in items:
        value = item.get(key)
        if value in seen:
            errors.append(f'{where}: повтор {key}={value!r}')
        seen.add(value)


# ------------------------------------------------------------------ контракт

def check_contract(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if spec.get('openapi') != '3.1.0':
        errors.append('openapi: ожидается 3.1.0')
    if (spec.get('servers') or [])[:1] != [{'url': 'http://localhost:8080'}]:
        errors.append('servers: ожидается [{"url": "http://localhost:8080"}]')
    scheme = spec.get('components', {}).get('securitySchemes', {}).get('ApiKeyAuth', {})
    if (scheme.get('type'), scheme.get('in'), scheme.get('name')) != (
            'apiKey', 'header', 'X-Api-Key'):
        errors.append('securitySchemes.ApiKeyAuth: ожидается apiKey/header/X-Api-Key')
    if spec.get('security') != [{'ApiKeyAuth': []}]:
        errors.append('security: ожидается требование ApiKeyAuth')
    health = spec.get('paths', {}).get('/healthz', {}).get('get', {})
    if health.get('security') != []:
        errors.append('/healthz: ожидается security: []')
    return errors


def check_operations(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    found = {(method, path): operation.get('operationId')
             for method, path, operation in iter_operations(spec)}
    for method, path in sorted(found.keys() - OPERATIONS.keys()):
        errors.append(f'лишняя операция: {method.upper()} {path}')
    for method, path in sorted(OPERATIONS.keys() - found.keys()):
        errors.append(f'нет операции: {method.upper()} {path}')
    expected_paths = {path for _, path in OPERATIONS}
    if set(spec.get('paths', {})) != expected_paths:
        errors.append(f'paths: ожидается ровно {len(expected_paths)} path-объектов')
    for (method, path), expected in OPERATIONS.items():
        actual = found.get((method, path))
        if actual != expected:
            errors.append(f'{method.upper()} {path}: operationId {actual!r}, '
                          f'ожидается {expected!r}')
    operation_ids = [value for value in found.values() if value]
    for operation_id in sorted(set(operation_ids)):
        if operation_ids.count(operation_id) > 1:
            errors.append(f'operationId не уникален: {operation_id}')
    return errors


def check_refs(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for ref in iter_refs(spec):
        try:
            resolve_ref(spec, ref)
        except (KeyError, ValueError) as exc:
            errors.append(f'$ref не резолвится: {ref} ({exc})')
    return errors


def check_forbidden(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    files = [API_SPEC]
    files += sorted(MOCKS_DIR.glob('*.yaml'))
    files += sorted(EXAMPLES_DIR.glob('*.json'))
    # Слова ищем по границам латинских букв: `balance` не должен срабатывать
    # на легитимный `variant: balanced`, а `cost` в `cost_rub` — срабатывать.
    patterns = [(word, re.compile(rf'(?<![a-z]){re.escape(word)}(?![a-z])'))
                for word in FORBIDDEN_WORDS]
    for path in files:
        lowered = path.read_text(encoding='utf-8').lower()
        for word, pattern in patterns:
            if pattern.search(lowered):
                errors.append(f'{path.relative_to(ROOT)}: запрещённое слово {word!r}')
    if re.search(r'\b(PATCH|PUT|DELETE)\b', API_SPEC.read_text(encoding='utf-8')):
        errors.append('в спеке есть PATCH/PUT/DELETE')
    for method, path, _ in iter_operations(spec):
        if method not in {'get', 'post'}:
            errors.append(f'мутирующий метод: {method.upper()} {path}')
    return errors


# --------------------------------------------------------------------- моки

def check_requests(items: list[dict[str, Any]], spec: dict[str, Any],
                   errors: list[str]) -> None:
    required, allowed = required_and_allowed(spec, 'Request')
    statuses = enum(spec, 'RequestStatus')
    for item in items:
        where = f'request {item.get("request_id")}'
        check_item(item, required, allowed, where, errors)
        if item.get('status') not in statuses:
            errors.append(f'{where}: статус {item.get("status")!r}')
        if not isinstance(item.get('points_reward'), int) or item['points_reward'] < 1:
            errors.append(f'{where}: points_reward < 1')
        for hint in item.get('route_hints', ()):
            if not isinstance(hint, dict) or 'route_id' not in hint:
                errors.append(f'{where}: битый route_hint')


def check_quotes(items: list[dict[str, Any]], spec: dict[str, Any],
                 request_ids: set[str], errors: list[str]) -> None:
    required, allowed = required_and_allowed(spec, 'Quote')
    statuses = enum(spec, 'QuoteStatus')
    variants = enum(spec, 'Quote', 'variant')
    per_request: dict[Any, list[dict[str, Any]]] = {}
    for item in items:
        where = f'quote {item.get("quote_id")}'
        check_item(item, required, allowed, where, errors)
        if item.get('request_id') not in request_ids:
            errors.append(f'{where}: request_id {item.get("request_id")!r} не найден')
        if item.get('status') not in statuses:
            errors.append(f'{where}: статус {item.get("status")!r}')
        if item.get('variant') not in variants:
            errors.append(f'{where}: variant {item.get("variant")!r}')
        if not isinstance(item.get('price_rub'), int) or item['price_rub'] < 1:
            errors.append(f'{where}: price_rub < 1')
        if not isinstance(item.get('duration_hours'), (int, float)) or item['duration_hours'] <= 0:
            errors.append(f'{where}: duration_hours <= 0')
        if not item.get('leg_ids'):
            errors.append(f'{where}: пустой leg_ids')
        per_request.setdefault(item.get('request_id'), []).append(item)

    for request_id, group in per_request.items():
        where = f'набор котировок {request_id}'
        if len(group) > 3:
            errors.append(f'{where}: вариантов {len(group)}, допустимо не более 3')
        labels = [item.get('variant') for item in group]
        if len(set(labels)) != len(labels):
            errors.append(f'{where}: повтор variant {labels}')


def check_orders(items: list[dict[str, Any]], spec: dict[str, Any],
                 quotes: dict[str, dict[str, Any]], errors: list[str]) -> None:
    required, allowed = required_and_allowed(spec, 'OrderSnapshot')
    statuses = enum(spec, 'OrderStatus')
    for item in items:
        where = f'order {item.get("order_id")}'
        check_item(item, required, allowed, where, errors)
        quote = quotes.get(item.get('quote_id'))
        if quote is None:
            errors.append(f'{where}: quote_id {item.get("quote_id")!r} не найден')
        elif (item.get('request_id'), item.get('route_id')) != (
                quote['request_id'], quote['route_id']):
            errors.append(f'{where}: request_id/route_id не совпадают с котировкой')
        if item.get('status') not in statuses:
            errors.append(f'{where}: статус {item.get("status")!r}')
        if 'delivered_at' in item and item['status'] != 'delivered':
            errors.append(f'{where}: delivered_at только у delivered')
        if item.get('status') == 'delivered' and 'delivered_at' not in item:
            errors.append(f'{where}: нет delivered_at')
        if {'failed_at', 'failure_code'} & item.keys() and item['status'] != 'failed':
            errors.append(f'{where}: failed_at/failure_code только у failed')
        if item.get('status') == 'failed' and not {'failed_at', 'failure_code'} <= item.keys():
            errors.append(f'{where}: у failed нужны failed_at и failure_code')


def check_notifications(items: list[dict[str, Any]], spec: dict[str, Any],
                        orders: dict[str, dict[str, Any]],
                        requests: dict[str, dict[str, Any]],
                        errors: list[str]) -> None:
    required, allowed = required_and_allowed(spec, 'OrderNotification')
    allowed |= {'id', 'idempotency_key'}  # метаданные мока, не тело запроса
    statuses = enum(spec, 'OrderNotification', 'status')
    reasons = enum(spec, 'OrderLostReason')
    keys: set[tuple[Any, Any]] = set()
    idempotency_keys: set[Any] = set()
    for item in items:
        where = f'notification {item.get("id")}'
        event = item.get('event')
        if event is None:
            # Событие доставки: delivered | subcontract_delivered.
            check_item(item, required, allowed, where, errors)
            order = orders.get(item.get('order_id'))
            if order is None:
                errors.append(f'{where}: order_id {item.get("order_id")!r} не найден')
            elif item.get('route_id') != order.get('route_id'):
                errors.append(f'{where}: route_id не совпадает с заказом')
            status = item.get('status')
            if status not in statuses:
                errors.append(f'{where}: статус {status!r}')
            elif status == 'subcontract_delivered' and not item.get('subcontract_id'):
                errors.append(f'{where}: subcontract_delivered без subcontract_id')
            elif status == 'delivered' and item.get('subcontract_id'):
                errors.append(f'{where}: delivered не несёт subcontract_id')
            key = (item.get('order_id'), item.get('subcontract_id'))
            if key in keys:
                errors.append(f'{where}: повтор ключа идемпотентности {key}')
            keys.add(key)
            continue

        schema = NOTIFY_EVENT_SCHEMAS.get(event)
        if schema is None:
            errors.append(f'{where}: неизвестное событие {event!r}')
            continue
        event_required, event_allowed = required_and_allowed(spec, schema)
        check_item(item, event_required, event_allowed | {'id', 'idempotency_key'},
                   where, errors)
        request = requests.get(item.get('request_id'))
        if request is None:
            errors.append(f'{where}: request_id {item.get("request_id")!r} не найден')
        elif (event == 'order_won'
              and item.get('points_reward') != request.get('points_reward')):
            errors.append(f'{where}: points_reward не совпадает с заявкой')

        if event == 'order_won':
            order = orders.get(item.get('order_id'))
            if order is None:
                errors.append(f'{where}: order_won без известного заказа '
                              f'{item.get("order_id")!r}')
            else:
                if item.get('route_id') != order.get('route_id'):
                    errors.append(f'{where}: route_id не совпадает с заказом')
                if item.get('price_rub') != order.get('price_rub'):
                    errors.append(f'{where}: price_rub не совпадает с заказом')
        elif item.get('reason') not in reasons:
            errors.append(f'{where}: reason {item.get("reason")!r}')

        expected_key = (f'exchange:order:{item.get("order_id")}:won:v1'
                        if event == 'order_won' else
                        f'exchange:order:{item.get("order_id")}:'
                        f'lost:{item.get("team_id")}:v1')
        if item.get('idempotency_key') != expected_key:
            errors.append(f'{where}: idempotency_key '
                          f'{item.get("idempotency_key")!r}, ожидается '
                          f'{expected_key!r}')
        if item.get('idempotency_key') in idempotency_keys:
            errors.append(f'{where}: повтор ключа идемпотентности '
                          f'{item.get("idempotency_key")!r}')
        idempotency_keys.add(item.get('idempotency_key'))


def check_points(items: list[dict[str, Any]], spec: dict[str, Any],
                 orders: dict[str, dict[str, Any]], errors: list[str]) -> None:
    required, allowed = required_and_allowed(spec, 'PointsEntry')
    kinds = enum(spec, 'PointsEntry', 'kind')
    for item in items:
        where = f'point {item.get("id")}'
        check_item(item, required, allowed, where, errors)
        kind = item.get('kind')
        if kind not in kinds:
            errors.append(f'{where}: kind {kind!r}')
        if not isinstance(item.get('amount_points'), int) or item['amount_points'] < 0:
            errors.append(f'{where}: amount_points < 0')
        if kind == 'order_delivery':
            order = orders.get(item.get('order_id'))
            if order is None:
                errors.append(f'{where}: order_id {item.get("order_id")!r} не найден')
            elif order.get('status') != 'delivered':
                errors.append(f'{where}: order_delivery только по delivered-заказу')
        elif not item.get('subcontract_id'):
            errors.append(f'{where}: subcontract_delivery без subcontract_id')


def check_errors(items: list[dict[str, Any]], errors: list[str]) -> None:
    unique(items, 'id', 'errors', errors)
    statuses = {item.get('http_status') for item in items}
    if statuses != ERROR_STATUSES:
        errors.append(f'errors: статусы {sorted(statuses)}, ожидается {sorted(ERROR_STATUSES)}')
    for item in items:
        response = item.get('response')
        if not isinstance(response, dict):
            errors.append(f'error {item.get("id")}: response не объект')
            continue
        extra = set(response) - {'code', 'message', 'details'}
        if extra:
            errors.append(f'error {item.get("id")}: лишние поля {sorted(extra)}')
        if not {'code', 'message'} <= response.keys():
            errors.append(f'error {item.get("id")}: нужны code и message')
        if 'details' in response and not isinstance(response['details'], dict):
            errors.append(f'error {item.get("id")}: details должен быть объектом')


def check_mock_dataset(mocks: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requests = mocks['requests']['requests']
    quotes = mocks['quotes']['quotes']
    orders = mocks['orders']['orders']
    points = mocks['points']['points']
    notifications = mocks['notifications']['notifications']

    unique(requests, 'request_id', 'requests', errors)
    unique(quotes, 'quote_id', 'quotes', errors)
    unique(quotes, 'received_seq', 'quotes', errors)
    unique(orders, 'order_id', 'orders', errors)
    unique(points, 'id', 'points', errors)
    unique(notifications, 'id', 'notifications', errors)

    request_ids = {item['request_id'] for item in requests}
    quotes_by_id = {item['quote_id']: item for item in quotes}
    orders_by_id = {item['order_id']: item for item in orders}

    check_requests(requests, spec, errors)
    check_quotes(quotes, spec, request_ids, errors)
    check_orders(orders, spec, quotes_by_id, errors)
    check_notifications(notifications, spec, orders_by_id,
                        {item['request_id']: item for item in requests}, errors)
    check_points(points, spec, orders_by_id, errors)
    check_errors(mocks['errors']['errors'], errors)
    return errors


def check_examples(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    names = {path.name for path in EXAMPLES_DIR.glob('*.json')}
    if names != set(EXAMPLE_FILES):
        errors.append(f'examples: {sorted(names)}, ожидается {sorted(EXAMPLE_FILES)}')
    try:
        examples = load_examples()
    except (json.JSONDecodeError, OSError) as exc:
        return errors + [f'examples: не читаются как JSON ({exc})']
    for name, value in examples.items():
        if not isinstance(value, dict):
            errors.append(f'examples/{name}: ожидался объект')

    batch = examples.get('quote.json')
    if isinstance(batch, dict):
        if set(batch) != {'variants'}:
            errors.append('examples/quote.json: ожидается объект с одним полем '
                          f'variants, есть {sorted(batch)}')
        variants = batch.get('variants')
        if not isinstance(variants, list):
            errors.append('examples/quote.json: variants должен быть массивом')
            return errors
        if not 1 <= len(variants) <= 3:
            errors.append(f'examples/quote.json: вариантов {len(variants)}, '
                          'допустимо 1–3')
        required, allowed = required_and_allowed(spec, 'QuoteInput')
        labels = enum(spec, 'QuoteInput', 'variant')
        for index, item in enumerate(variants):
            where = f'examples/quote.json variants[{index}]'
            check_item(item, required, allowed, where, errors)
            if isinstance(item, dict) and item.get('variant') not in labels:
                errors.append(f'{where}: variant {item.get("variant")!r}')
        if all(isinstance(item, dict) for item in variants):
            unique(variants, 'variant', 'examples/quote.json', errors)
            unique(variants, 'quote_id', 'examples/quote.json', errors)
    return errors


# --------------------------------------------------------------------- CLI

# --------------------------------------------------------------------- канон id

# Канон портала: id маршрута и плеча — id сегмента справочника
# `<mode>:<from>-<to>` (например `road:moscow-tula`). `leg_ids` —
# упорядоченная цепочка 1–3 сегментов от origin до destination,
# `route_id` — её первый сегмент.
ROUTE_ID_PATTERN = (r'^[a-z][a-z0-9_]*:[a-z0-9]+(?:-[a-z0-9]+)*'
                    r'-[a-z0-9]+(?:-[a-z0-9]+)*$')
ROUTE_ID = re.compile(ROUTE_ID_PATTERN)


def check_route_ids(spec: dict[str, Any], mocks: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schemas = spec.get('components', {}).get('schemas', {})
    for name in ('RouteId', 'LegId'):
        schema = schemas.get(name, {})
        if schema.get('pattern') != ROUTE_ID_PATTERN:
            errors.append(f'{name}: шаблон не совпадает с каноном портала '
                          f'{ROUTE_ID_PATTERN!r}')
        for value in schema.get('examples', ()):
            if not ROUTE_ID.fullmatch(str(value)):
                errors.append(f'{name}: пример {value!r} вне канона')

    def check_leg_chain(route_id: Any, legs: Any, where: str) -> None:
        if not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
            errors.append(f'{where}: route_id {route_id!r} вне канона')
            return
        if not isinstance(legs, list) or not legs:
            errors.append(f'{where}: leg_ids должен быть непустым массивом')
            return
        if len(legs) > 3:
            errors.append(f'{where}: leg_ids содержит {len(legs)} сегментов, '
                          'допустимо 1–3')
        for leg_id in legs:
            if not isinstance(leg_id, str) or not ROUTE_ID.fullmatch(leg_id):
                errors.append(f'{where}: leg_id {leg_id!r} вне канона')
        if legs[0] != route_id:
            errors.append(f'{where}: route_id {route_id!r} должен совпадать '
                          f'с первым leg_id {legs[0]!r}')

    def check_hint(hint: Any, where: str) -> None:
        if not isinstance(hint, dict):
            errors.append(f'{where}: route_hint не объект')
            return
        check_leg_chain(hint.get('route_id'), hint.get('leg_ids'), where)

    def check_body(body: dict[str, Any], where: str) -> None:
        check_leg_chain(body.get('route_id'), body.get('leg_ids'), where)

    for item in mocks['requests']['requests']:
        for hint in item.get('route_hints') or ():
            check_hint(hint, f'request {item.get("request_id")}')
    for item in mocks['quotes']['quotes']:
        check_body(item, f'quote {item.get("quote_id")}')
    for item in mocks['orders']['orders']:
        route_id = item.get('route_id')
        if not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
            errors.append(f'order {item.get("order_id")}: '
                          f'route_id {route_id!r} вне канона')
    for item in mocks['notifications']['notifications']:
        check_body(item, f'notification {item.get("id")}')

    examples = load_examples()
    for hint in examples['request.json'].get('route_hints') or ():
        check_hint(hint, 'examples/request.json')
    for index, item in enumerate(examples['quote.json'].get('variants') or ()):
        check_body(item, f'examples/quote.json variants[{index}]')
    check_body(examples['order-notify.json'], 'examples/order-notify.json')
    return errors


def all_checks() -> list[tuple[str, list[str]]]:
    spec = load_api_spec()
    mocks = load_mocks()
    return [
        ('contract', check_contract(spec)),
        ('operations', check_operations(spec)),
        ('refs', check_refs(spec)),
        ('forbidden', check_forbidden(spec)),
        ('mocks', check_mock_dataset(mocks, spec)),
        ('examples', check_examples(spec)),
        ('route_ids', check_route_ids(spec, mocks)),
    ]


def main() -> int:
    failed = False
    for name, errors in all_checks():
        if errors:
            failed = True
            print(f'[{name}] ошибок: {len(errors)}')
            for error in errors:
                print(f'  - {error}')
        else:
            print(f'[{name}] OK')
    print('Все проверки пройдены.' if not failed else 'Проверки не пройдены.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
