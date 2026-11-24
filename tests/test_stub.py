"""ASGI-тесты reference stub: auth, идемпотентность, 404/409/422.

Сеть не нужна: httpx ходит в приложение через ASGITransport, состояние
создаётся заново на каждый тест.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from stub.app import create_app

API_KEY = 'test-key'


def call(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url='http://stub') as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_run())


def headers(**extra: str) -> dict[str, str]:
    return {'X-Api-Key': API_KEY, **extra}


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def in_days(days: float) -> str:
    return rfc3339(datetime.now(timezone.utc) + timedelta(days=days))


@pytest.fixture()
def app() -> FastAPI:
    return create_app(API_KEY)


def make_payload(request_id: str = 'req_t01', *,
                 deadline_days: float = 2, **extra) -> dict:
    payload = {
        'request_id': request_id,
        'origin': 'moscow',
        'destination': 'vladivostok',
        'allowed_modes': ['rail', 'road'],
        'weight_t': 1.5,
        'cargo': {'description': 'Тестовый груз.'},
        'response_deadline': in_days(deadline_days),
        'points_reward': 10,
    }
    payload.update(extra)
    return payload


def make_request(app: FastAPI, request_id: str = 'req_t01', *,
                 deadline_days: float = 2, **extra) -> dict:
    payload = make_payload(request_id, deadline_days=deadline_days, **extra)
    response = call(app, 'post', '/v1/requests', json=payload,
                    headers=headers(**{'Idempotency-Key':
                                       f'{request_id}:test-team'}))
    assert response.status_code == 201, response.text
    return payload


def quote_variant(quote_id: str, variant: str = 'balanced', **overrides) -> dict:
    body = {
        'quote_id': quote_id,
        'variant': variant,
        'price_rub': 100000,
        'duration_hours': 24,
        'route_id': 'rail:moscow-vladivostok',
        'leg_ids': ['rail:moscow-vladivostok'],
        'valid_until': in_days(3),
    }
    body.update(overrides)
    return body


def quote_body(*quote_ids: str, **overrides) -> dict:
    ids = list(quote_ids) or ['quo_t01c', 'quo_t01f', 'quo_t01b']
    labels = ['cheapest', 'fastest', 'balanced']
    return {'variants': [quote_variant(quote_id, labels[index], **overrides)
                         for index, quote_id in enumerate(ids)]}


# ------------------------------------------------------------------ health

def test_healthz_without_key(app):
    response = call(app, 'get', '/healthz')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ok'
    assert payload['service'] == 'team-service'
    assert payload['version'] == '2.4.0'
    assert payload['time'].endswith('Z')


# -------------------------------------------------------------------- auth

@pytest.mark.parametrize(('method', 'path', 'body'), [
    ('get', '/v1/requests', None),
    ('post', '/v1/requests', {'request_id': 'req_auth01'}),
    ('get', '/v1/requests/req_5001/quotes', None),
    ('post', '/v1/requests/req_5001/quotes',
     {'variants': [{'quote_id': 'quo_auth01'}]}),
    ('get', '/v1/orders/ord_6003', None),
    ('post', '/v1/orders/ord_6003/notify', {'order_id': 'ord_6003'}),
    ('get', '/v1/points', None),
])
def test_protected_operations_require_key(app, method, path, body):
    response = call(app, method, path, json=body) if body else call(
        app, method, path)
    assert response.status_code == 401
    assert set(response.json()) <= {'code', 'message', 'details'}
    assert response.json()['code'] == 'unauthorized'

    wrong = {'X-Api-Key': 'not-the-key'}
    response = call(app, method, path, headers=wrong,
                    **({'json': body} if body else {}))
    assert response.status_code == 401


def test_healthz_is_the_only_unprotected_operation(app):
    assert call(app, 'get', '/healthz').status_code == 200
    assert call(app, 'get', '/v1/healthz').status_code == 401


def test_api_key_comes_from_environment(monkeypatch):
    monkeypatch.setenv('TEAM_API_KEY', 'env-key')
    app = create_app()
    assert call(app, 'get', '/v1/requests').status_code == 401
    response = call(app, 'get', '/v1/requests',
                    headers={'X-Api-Key': 'env-key'})
    assert response.status_code == 200


# -------------------------------------------------------------------- сид

def test_seed_is_small_and_cross_linked(app):
    requests = call(app, 'get', '/v1/requests', headers=headers()).json()
    ids = {item['request_id'] for item in requests}
    assert ids == {'req_5001', 'req_5002'}
    assert call(app, 'get', '/v1/requests/req_5001/quotes',
                headers=headers()).json() == []
    quotes = call(app, 'get', '/v1/requests/req_5002/quotes',
                  headers=headers()).json()
    assert [item['quote_id'] for item in quotes] == ['quo_7007']

    order = call(app, 'get', '/v1/orders/ord_6003', headers=headers()).json()
    assert order['quote_id'] == 'quo_7007'
    assert order['status'] == 'delivered'

    points = call(app, 'get', '/v1/points', headers=headers()).json()
    assert {item['id'] for item in points} == {'pts_9001', 'pts_9002'}
    assert {item['kind'] for item in points} == {'order_delivery',
                                                 'subcontract_delivery'}


# ----------------------------------------------------------------- requests

def test_create_request_is_idempotent(app):
    payload = make_request(app)
    first = call(app, 'post', '/v1/requests', json=payload,
                 headers=headers(**{'Idempotency-Key': 'req_t01:test-team'}))
    assert first.status_code == 200
    assert first.json()['received_at'].endswith('Z')

    same = call(app, 'post', '/v1/requests', json=payload,
                headers=headers(**{'Idempotency-Key': 'req_t01:test-team'}))
    assert same.status_code == 200
    assert same.json() == first.json()

    changed = dict(payload, weight_t=2.5)
    conflict = call(app, 'post', '/v1/requests', json=changed,
                    headers=headers(**{'Idempotency-Key': 'req_t01:test-team'}))
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'request_conflict'


def test_create_request_accepts_optional_ceilings(app):
    """Practice RFQ: max_price_rub/max_duration_hours не должны давать 422."""
    payload = make_request(
        app, 'req_ceil01', max_price_rub=1000, max_duration_hours=5.0
    )
    response = call(app, 'get', '/v1/requests', headers=headers())
    assert response.status_code == 200
    stored = next(item for item in response.json()
                  if item['request_id'] == payload['request_id'])
    assert stored['max_price_rub'] == 1000
    assert stored['max_duration_hours'] == 5.0


def test_create_request_without_idempotency_key(app):
    payload = make_request(app, 'req_t02')
    response = call(app, 'post', '/v1/requests', json=payload,
                    headers=headers())
    assert response.status_code == 400
    assert response.json()['code'] == 'bad_request'


def test_create_request_accepts_canonical_idempotency_key(app):
    payload = make_payload('req_t07')
    request_headers = headers(**{'Idempotency-Key': 'req_t07:tri-kota'})
    first = call(app, 'post', '/v1/requests', json=payload,
                 headers=request_headers)
    assert first.status_code == 201, first.text
    repeat = call(app, 'post', '/v1/requests', json=payload,
                  headers=request_headers)
    assert repeat.status_code == 200
    assert repeat.json() == first.json()


def test_create_request_with_broken_idempotency_key(app):
    payload = make_request(app, 'req_t03')
    response = call(app, 'post', '/v1/requests', json=payload,
                    headers=headers(**{'Idempotency-Key': 'not-a-key'}))
    assert response.status_code == 422


def test_create_request_rejects_broken_body(app):
    response = call(app, 'post', '/v1/requests',
                    headers=headers(**{'Idempotency-Key': 'req_t04:test-team',
                                       'Content-Type': 'application/json'}),
                    content=b'{"request_id": ')
    assert response.status_code == 400
    assert response.json()['code'] == 'bad_request'

    invalid = make_request(app, 'req_t05')
    invalid.pop('cargo')
    response = call(app, 'post', '/v1/requests', json=invalid,
                    headers=headers(**{'Idempotency-Key': 'req_t05:test-team'}))
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_request_with_canonical_route_hints(app):
    hints = [
        {'route_id': 'road:moscow-tula', 'leg_ids': ['road:moscow-tula']},
        {'route_id': 'rail:moscow-saint-petersburg',
         'leg_ids': ['rail:moscow-saint-petersburg']},
    ]
    payload = make_request(app, 'req_t08', route_hints=hints)
    assert payload['route_hints'] == hints
    stored = {item['request_id']: item
              for item in call(app, 'get', '/v1/requests',
                               headers=headers()).json()}
    assert stored['req_t08']['route_hints'] == hints


def test_request_rejects_legacy_route_hint(app):
    payload = make_payload('req_t09', route_hints=[{'route_id': 'rte_5001',
                                                    'leg_ids': ['leg_5001_1']}])
    response = call(app, 'post', '/v1/requests', json=payload,
                    headers=headers(**{'Idempotency-Key': 'req_t09:test-team'}))
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


# ------------------------------------------------------------------- quotes

def test_quote_batch_is_created_once(app):
    make_request(app)
    body = quote_body()
    created = call(app, 'post', '/v1/requests/req_t01/quotes', json=body,
                   headers=headers())
    assert created.status_code == 201
    batch = created.json()
    assert batch['request_id'] == 'req_t01'
    variants = batch['variants']
    assert [item['quote_id'] for item in variants] == [
        'quo_t01c', 'quo_t01f', 'quo_t01b']
    seqs = [item['received_seq'] for item in variants]
    assert all(isinstance(seq, int) for seq in seqs)
    assert len(set(seqs)) == 3
    for item in variants:
        assert item['request_id'] == 'req_t01'
        assert item['status'] == 'submitted'
        assert 'cost_rub' not in item

    repeat = call(app, 'post', '/v1/requests/req_t01/quotes', json=body,
                  headers=headers())
    assert repeat.status_code == 409
    assert repeat.json()['code'] == 'quote_exists'

    listed = call(app, 'get', '/v1/requests/req_t01/quotes', headers=headers())
    assert [item['quote_id'] for item in listed.json()] == [
        'quo_t01c', 'quo_t01f', 'quo_t01b']
    assert {item['request_id'] for item in listed.json()} == {'req_t01'}


def test_quote_batch_of_four_is_rejected(app):
    make_request(app)
    variants = [
        quote_variant('quo_t04a', 'cheapest'),
        quote_variant('quo_t04b', 'fastest'),
        quote_variant('quo_t04c', 'balanced'),
        quote_variant('quo_t04d', 'balanced'),
    ]
    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json={'variants': variants}, headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_quote_batch_rejects_duplicate_variant(app):
    make_request(app)
    variants = [
        quote_variant('quo_t05a', 'cheapest'),
        quote_variant('quo_t05b', 'cheapest'),
    ]
    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json={'variants': variants}, headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_quote_batch_rejects_duplicate_quote_id(app):
    make_request(app)
    variants = [
        quote_variant('quo_t06', 'cheapest'),
        quote_variant('quo_t06', 'fastest'),
    ]
    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json={'variants': variants}, headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_quote_rejects_legacy_route_id(app):
    make_request(app)
    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json=quote_body(route_id='rte_5001'), headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'

    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json=quote_body(leg_ids=['leg_5001_1']),
                    headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_quote_requires_known_request(app):
    response = call(app, 'post', '/v1/requests/req_zzzz/quotes',
                    json=quote_body(), headers=headers())
    assert response.status_code == 404
    assert response.json()['code'] == 'not_found'


def test_quote_after_deadline_is_rejected(app):
    make_request(app, 'req_t06', deadline_days=-1)
    response = call(app, 'post', '/v1/requests/req_t06/quotes',
                    json=quote_body('quo_t06'), headers=headers())
    assert response.status_code == 409
    assert response.json()['code'] == 'request_closed'


def test_quote_valid_until_must_cover_deadline(app):
    make_request(app)
    response = call(app, 'post', '/v1/requests/req_t01/quotes',
                    json=quote_body(valid_until=in_days(1)), headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


# ------------------------------------------------------------------- orders

def test_order_snapshot_is_read_only(app):
    snapshot = call(app, 'get', '/v1/orders/ord_6003', headers=headers())
    assert snapshot.status_code == 200
    assert snapshot.json()['status'] == 'delivered'
    assert snapshot.json()['delivered_at'].endswith('Z')

    patched = call(app, 'patch', '/v1/orders/ord_6003',
                   json={'status': 'failed'}, headers=headers())
    assert patched.status_code == 405
    assert patched.json()['code'] == 'method_not_allowed'

    after = call(app, 'get', '/v1/orders/ord_6003', headers=headers())
    assert after.json() == snapshot.json()


def test_unknown_order_is_404(app):
    response = call(app, 'get', '/v1/orders/ord_zzzz', headers=headers())
    assert response.status_code == 404
    assert response.json()['code'] == 'not_found'


# ------------------------------------------------------------------- notify

def notify_body(**overrides) -> dict:
    body = {
        'order_id': 'ord_6003',
        'status': 'delivered',
        'delivered_at': '2026-11-17T03:40:00Z',
        'route_id': 'rail:saint-petersburg-novosibirsk',
        'leg_ids': ['rail:saint-petersburg-novosibirsk'],
        'points_awarded': 80,
    }
    body.update(overrides)
    return body


def test_notify_is_idempotent_and_does_not_touch_order(app):
    before = call(app, 'get', '/v1/orders/ord_6003', headers=headers()).json()
    first = call(app, 'post', '/v1/orders/ord_6003/notify', json=notify_body(),
                 headers=headers())
    assert first.status_code == 204
    assert first.content == b''

    repeat = call(app, 'post', '/v1/orders/ord_6003/notify',
                  json=notify_body(), headers=headers())
    assert repeat.status_code == 204

    conflict = call(app, 'post', '/v1/orders/ord_6003/notify',
                    json=notify_body(points_awarded=81), headers=headers())
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'notification_conflict'

    after = call(app, 'get', '/v1/orders/ord_6003', headers=headers()).json()
    assert after == before
    assert after['status'] == 'delivered'


def test_notify_unknown_order_is_404(app):
    response = call(app, 'post', '/v1/orders/ord_zzzz/notify',
                    json=notify_body(order_id='ord_zzzz'), headers=headers())
    assert response.status_code == 404


def test_notify_rejects_broken_body(app):
    response = call(app, 'post', '/v1/orders/ord_6003/notify',
                    json=notify_body(status='cancelled'), headers=headers())
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'

    mismatch = call(app, 'post', '/v1/orders/ord_6003/notify',
                    json=notify_body(order_id='ord_6001'), headers=headers())
    assert mismatch.status_code == 422


def test_subcontract_notification_is_idempotent_by_key(app):
    body = notify_body(**{'status': 'subcontract_delivered',
                          'subcontract_id': 'sub_t01',
                          'order_id': 'ord_6003',
                          'route_id': 'rail:saint-petersburg-novosibirsk'})
    first = call(app, 'post', '/v1/orders/ord_6003/notify', json=body,
                 headers=headers())
    assert first.status_code == 204
    repeat = call(app, 'post', '/v1/orders/ord_6003/notify', json=body,
                  headers=headers())
    assert repeat.status_code == 204
    changed = dict(body, points_awarded=41)
    conflict = call(app, 'post', '/v1/orders/ord_6003/notify', json=changed,
                    headers=headers())
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'notification_conflict'


# ------------------------------------------------- события order_won/lost

def won_body(**overrides) -> dict:
    body = {
        'event': 'order_won',
        'order_id': 'ord_6001',
        'request_id': 'req_5001',
        'team_id': 'team-1',
        'route_id': 'rail:moscow-vladivostok',
        'leg_ids': ['rail:moscow-vladivostok'],
        'price_rub': 1880000,
        'duration_hours': 227,
        'points_reward': 100,
        'message': 'Заявка выиграна. Заказ ещё не доставлен.',
    }
    body.update(overrides)
    return body


def lost_body(**overrides) -> dict:
    body = {
        'event': 'order_lost',
        'order_id': 'ord_6010',
        'request_id': 'req_5004',
        'team_id': 'team-1',
        'route_id': 'road:novosibirsk-tomsk',
        'leg_ids': ['road:novosibirsk-tomsk'],
        'reason': 'not_selected',
        'message': 'Заявка закрыта: выбрана другая котировка.',
    }
    body.update(overrides)
    return body


def test_order_won_is_idempotent_by_idempotency_key(app):
    key = {'Idempotency-Key': 'exchange:order:ord_6001:won:v1'}
    before = call(app, 'get', '/v1/orders/ord_6001', headers=headers()).json()

    first = call(app, 'post', '/v1/orders/ord_6001/notify', json=won_body(),
                 headers=headers(**key))
    assert first.status_code == 204
    assert first.content == b''

    repeat = call(app, 'post', '/v1/orders/ord_6001/notify', json=won_body(),
                  headers=headers(**key))
    assert repeat.status_code == 204

    conflict = call(app, 'post', '/v1/orders/ord_6001/notify',
                    json=won_body(price_rub=1880001), headers=headers(**key))
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'notification_conflict'

    after = call(app, 'get', '/v1/orders/ord_6001', headers=headers()).json()
    assert after == before


def test_order_won_requires_idempotency_key(app):
    response = call(app, 'post', '/v1/orders/ord_6001/notify',
                    json=won_body(), headers=headers())
    assert response.status_code == 400
    assert response.json()['code'] == 'bad_request'


def test_order_won_accepts_order_unknown_to_team(app):
    # Событие о выборе приходит раньше снимка заказа: 404 не нужен.
    response = call(app, 'post', '/v1/orders/ord_6099/notify',
                    json=won_body(order_id='ord_6099'),
                    headers=headers(**{'Idempotency-Key':
                                       'exchange:order:ord_6099:won:v1'}))
    assert response.status_code == 204


def test_order_lost_accepts_both_reasons(app):
    for reason in ('not_selected', 'no_quote'):
        response = call(
            app, 'post', '/v1/orders/ord_6010/notify',
            json=lost_body(reason=reason),
            headers=headers(**{'Idempotency-Key':
                               f'exchange:order:ord_6010:lost:{reason}:v1'}))
        assert response.status_code == 204, reason


def test_order_lost_rejects_unknown_reason(app):
    response = call(app, 'post', '/v1/orders/ord_6010/notify',
                    json=lost_body(reason='cancelled'),
                    headers=headers(**{'Idempotency-Key':
                                       'exchange:order:ord_6010:lost:team-1:v1'}))
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_order_lost_rejects_unknown_event(app):
    response = call(app, 'post', '/v1/orders/ord_6010/notify',
                    json=lost_body(event='order_cancelled'),
                    headers=headers(**{'Idempotency-Key':
                                       'exchange:order:ord_6010:lost:team-1:v1'}))
    assert response.status_code == 422


# ------------------------------------------------------------------- points

def test_points_are_read_only_and_own(app):
    points = call(app, 'get', '/v1/points', headers=headers()).json()
    assert len(points) == 2
    for entry in points:
        assert 'cost_rub' not in json.dumps(entry)
        assert 'team_id' not in entry
        if entry['kind'] == 'order_delivery':
            order = call(app, 'get', f"/v1/orders/{entry['order_id']}",
                         headers=headers())
            assert order.status_code == 200

    created = call(app, 'post', '/v1/points', json={}, headers=headers())
    assert created.status_code == 405


# --------------------------------------------------------------- запреты

@pytest.mark.parametrize('path', ['/award', '/dictionaries', '/routes',
                                  '/company'])
def test_no_extra_endpoints(app, path):
    assert call(app, 'get', path, headers=headers()).status_code == 404
    assert call(app, 'post', path, json={}, headers=headers()).status_code == 404
