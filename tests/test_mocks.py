"""Тесты контракта api/openapi.yaml, моков и примеров.

Сеть и БД не нужны: только YAML, JSON и стандартная библиотека.
Правила живут в ``scripts/checks.py``; здесь — инварианты контракта.
Размеры наборов намеренно не фиксируются.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import checks  # noqa: E402

SPEC = checks.load_api_spec()
MOCKS = checks.load_mocks()
REQUESTS = MOCKS['requests']['requests']
QUOTES = MOCKS['quotes']['quotes']
ORDERS = MOCKS['orders']['orders']
POINTS = MOCKS['points']['points']
NOTIFICATIONS = MOCKS['notifications']['notifications']
ERRORS = MOCKS['errors']['errors']


def assert_ok(errors: list[str]) -> None:
    assert errors == [], '\n'.join(errors)


# ------------------------------------------------------------------ контракт

def test_operations_are_exactly_eight():
    found = {(method, path): operation['operationId']
             for method, path, operation in checks.iter_operations(SPEC)}
    assert set(found) == set(checks.OPERATIONS)
    assert found == checks.OPERATIONS
    assert len(SPEC['paths']) == 6


def test_no_mutating_methods():
    methods = {method for method, _, _ in checks.iter_operations(SPEC)}
    assert methods <= {'get', 'post'}
    text = checks.API_SPEC.read_text(encoding='utf-8')
    for token in ('PATCH', 'PUT', 'DELETE'):
        assert token not in text


def test_servers_and_auth():
    assert SPEC['servers'] == [{'url': 'http://localhost:8080'}]
    scheme = SPEC['components']['securitySchemes']['ApiKeyAuth']
    assert (scheme['type'], scheme['in'], scheme['name']) == (
        'apiKey', 'header', 'X-Api-Key')
    assert SPEC['security'] == [{'ApiKeyAuth': []}]
    assert SPEC['paths']['/healthz']['get']['security'] == []
    assert '/v1/healthz' not in SPEC['paths']


def test_forbidden_words_absent():
    assert_ok(checks.check_forbidden(SPEC))


def test_quote_input_has_no_commerce():
    schema = SPEC['components']['schemas']['QuoteInput']
    assert set(schema['required']) == {
        'quote_id', 'variant', 'price_rub', 'duration_hours', 'route_id',
        'leg_ids', 'valid_until'}
    assert not {'cost', 'cost_rub', 'price', 'company_id'} & set(schema['properties'])
    assert schema['properties']['price_rub']['minimum'] == 1
    example = json.loads((checks.EXAMPLES_DIR / 'quote.json').read_text(encoding='utf-8'))
    assert set(example) == {'variants'}
    assert 1 <= len(example['variants']) <= 3
    labels = [item['variant'] for item in example['variants']]
    assert len(set(labels)) == len(labels)
    text = json.dumps(example)
    assert 'cost' not in text and 'cost_rub' not in text


def test_order_projection_is_read_only():
    schema = SPEC['components']['schemas']['OrderSnapshot']
    for field in ('status', 'delivered_at', 'failed_at', 'failure_code',
                  'points_awarded'):
        assert schema['properties'][field].get('readOnly') is True, field
    assert schema['properties']['status']['allOf'][0]['$ref'].endswith('/OrderStatus')
    assert checks.enum(SPEC, 'OrderStatus') == {
        'confirmed', 'in_transit', 'delivered', 'failed'}
    forbidden = {'cancelled', 'cancel', 'accept', 'confirm'}
    assert not forbidden & set(schema['properties'])


def test_notify_accepts_event_only():
    operation = SPEC['paths']['/v1/orders/{order_id}/notify']['post']
    assert {'204', '409', '422'} <= set(operation['responses'])
    branches = {
        item.get('$ref', '').rsplit('/', 1)[-1]
        for item in operation['requestBody']['content']['application/json']
        ['schema']['oneOf']
    }
    assert branches == {'OrderNotification', 'OrderWonNotification',
                        'OrderLostNotification'}
    headers = [param.get('$ref', '') for param in operation['parameters']]
    assert '#/components/parameters/NotifyIdempotencyKey' in headers

    schema = SPEC['components']['schemas']['OrderNotification']
    assert schema['properties']['status']['enum'] == ['delivered', 'subcontract_delivered']
    assert set(schema['required']) == {
        'order_id', 'status', 'delivered_at', 'route_id', 'leg_ids', 'points_awarded'}
    assert {'subcontract_id', 'points_awarded'} <= set(schema['properties'])
    assert not {'new_status', 'cancel', 'confirm', 'accept',
                'failed_at', 'failure_code'} & set(schema['properties'])


def test_notify_event_schemas():
    won = SPEC['components']['schemas']['OrderWonNotification']
    lost = SPEC['components']['schemas']['OrderLostNotification']
    assert won['properties']['event']['const'] == 'order_won'
    assert lost['properties']['event']['const'] == 'order_lost'
    assert set(won['required']) == {
        'event', 'order_id', 'request_id', 'team_id', 'route_id', 'leg_ids',
        'price_rub', 'duration_hours', 'points_reward', 'message'}
    assert set(lost['required']) == {
        'event', 'order_id', 'request_id', 'team_id', 'route_id', 'leg_ids',
        'reason', 'message'}
    assert checks.enum(SPEC, 'OrderLostReason') == {'not_selected', 'no_quote'}
    # События не меняют заказ: полей статуса и доставки в них нет.
    for schema in (won, lost):
        assert not {'status', 'delivered_at', 'points_awarded'} & set(schema['properties'])


def test_lists_are_arrays_without_pagination():
    for path in ('/v1/requests', '/v1/requests/{request_id}/quotes', '/v1/points'):
        operation = SPEC['paths'][path]['get']
        schema = operation['responses']['200']['content']['application/json']['schema']
        assert schema['type'] == 'array', path
        query = [param for param in operation.get('parameters', [])
                 if param.get('in') == 'query']
        assert query == [], path


def test_error_envelope_is_fixed():
    schema = SPEC['components']['schemas']['Error']
    assert set(schema['properties']) == {'code', 'message', 'details'}
    assert set(schema['required']) == {'code', 'message'}
    assert schema['properties']['details']['type'] == 'object'
    for item in ERRORS:
        assert set(item['response']) <= {'code', 'message', 'details'}
        assert isinstance(item['response'].get('details', {}), dict)


# --------------------------------------------------------------------- моки

def test_mock_dataset_is_valid():
    assert_ok(checks.check_mock_dataset(MOCKS, SPEC))


def test_mock_cross_links():
    request_ids = {item['request_id'] for item in REQUESTS}
    quotes = {item['quote_id']: item for item in QUOTES}
    orders = {item['order_id']: item for item in ORDERS}
    subcontracts = {item['subcontract_id'] for item in POINTS if 'subcontract_id' in item}

    for quote in QUOTES:
        assert quote['request_id'] in request_ids
    for order in ORDERS:
        quote = quotes[order['quote_id']]
        assert order['request_id'] == quote['request_id']
        assert order['route_id'] == quote['route_id']
    for notification in NOTIFICATIONS:
        if notification.get('event') == 'order_lost':
            # Проигранная заявка: заказа у команды нет, ссылка на заявку.
            assert notification['request_id'] in request_ids
            continue
        order = orders[notification['order_id']]
        assert notification['route_id'] == order['route_id']
        if 'subcontract_id' in notification:
            assert notification['subcontract_id'] in subcontracts
    for point in POINTS:
        if point['kind'] == 'order_delivery':
            assert orders[point['order_id']]['status'] == 'delivered'
        else:
            assert point['subcontract_id'] in subcontracts

    assert {item['status'] for item in REQUESTS} == checks.enum(SPEC, 'RequestStatus')
    assert {item['status'] for item in QUOTES} == checks.enum(SPEC, 'QuoteStatus')
    assert {item['status'] for item in ORDERS} == checks.enum(SPEC, 'OrderStatus')


def test_examples_are_four_and_parse():
    names = {path.name for path in checks.EXAMPLES_DIR.glob('*.json')}
    assert names == set(checks.EXAMPLE_FILES)
    examples = checks.load_examples()
    request = examples['request.json']
    assert {'request_id', 'origin', 'destination', 'weight_t',
            'cargo', 'response_deadline', 'points_reward'} <= set(request)
    assert 'status' not in request
    notify = examples['order-notify.json']
    assert notify['status'] in checks.enum(SPEC, 'OrderNotification', 'status')
    assert set(examples['error.json']) <= {'code', 'message', 'details'}


def test_all_checks_pass():
    failed = {name: errors for name, errors in checks.all_checks() if errors}
    assert failed == {}, failed


def test_route_ids_follow_portal_canon():
    assert_ok(checks.check_route_ids(SPEC, MOCKS))
    for name in ('RouteId', 'LegId'):
        schema = SPEC['components']['schemas'][name]
        assert schema['pattern'] == checks.ROUTE_ID_PATTERN
        assert all(checks.ROUTE_ID.fullmatch(str(value))
                   for value in schema['examples'])
    # Старый формат (`rte_`/`leg_`) и неполный `<mode>:<city>` — вне канона.
    for value in ('rte_5001', 'leg_5001_1', 'road:moscow',
                  'road:Moscow-Tula', 'road:moscow-tula-voronezh-'):
        assert not checks.ROUTE_ID.fullmatch(value), value
