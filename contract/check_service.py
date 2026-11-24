"""Conformance-проверки сервиса команды по ``api/openapi.yaml``.

Checker не зависит от реализации: ему дают URL и ключ команды, он прогоняет
контракт снаружи. Ответы валидируются по ``components.schemas`` через
``jsonschema`` (Draft 2020-12, внутренние ``$ref`` разворачиваются), поэтому
лишнее поле, неверный enum или нарушенная идемпотентность видны сразу.

Запуск::

    python -m contract.check_service --url http://127.0.0.1:8080 \
        --api-key test-key --order-id ord_6003

Код выхода 1, если хотя бы одна проверка провалилась. Ключ не печатается и
не попадает в отчёт.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / 'api' / 'openapi.yaml'

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
RESPONSE_SCHEMAS = {
    ('get', '/healthz'): ('single', 'Health'),
    ('get', '/v1/requests'): ('array', 'Request'),
    ('post', '/v1/requests'): ('single', 'Request'),
    ('get', '/v1/requests/{request_id}/quotes'): ('array', 'Quote'),
    ('post', '/v1/requests/{request_id}/quotes'): ('single', 'QuoteBatch'),
    ('get', '/v1/orders/{order_id}'): ('single', 'OrderSnapshot'),
    ('get', '/v1/points'): ('array', 'PointsEntry'),
}
ORDER_STATUS = {'confirmed', 'in_transit', 'delivered', 'failed'}
QUOTE_STATUS = {'submitted', 'accepted', 'lost', 'expired'}
POINTS_KIND = {'order_delivery', 'subcontract_delivery'}
# События notify: одна ручка, четыре тела. `order_lost` — экспериментальное.
NOTIFY_SCHEMAS = {'OrderNotification', 'OrderWonNotification',
                  'OrderLostNotification'}
LOST_REASONS = {'not_selected', 'no_quote'}
# Поля, которых в контракте нет и быть не должно: себестоимость и чужие
# владельцы. Проверяем и схемой (additionalProperties: false), и явно —
# чтобы отчёт называл причину, а не «additional properties are not allowed».
QUOTE_FORBIDDEN_KEYS = {'cost', 'cost_rub'}
FOREIGN_KEYS = {'team_id', 'team_ids', 'owner_team_id', 'company_id', 'carriers',
                'payment', 'payout', 'invoice', 'advance', 'balance'}
FORBIDDEN_PATHS = ('/award', '/dictionaries', '/routes', '/company',
                   '/v1/award', '/v1/dictionaries', '/v1/routes', '/v1/company',
                   '/v1/healthz')
EXPECTED_PATHS = {path for _, path in OPERATIONS}
DEFAULT_ORDER_ID = 'ord_6003'
NOTIFY_FALLBACK_TIME = '2026-11-17T03:40:00Z'


# ------------------------------------------------------------------- отчёт

@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ''


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)
    url: str = '<ASGI>'

    def add(self, name: str, ok: bool, detail: str = '') -> None:
        self.results.append(CheckResult(name=name, ok=ok, detail=detail))

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    @property
    def errors(self) -> list[str]:
        return [f'{r.name}: {r.detail}' for r in self.results if not r.ok]

    def render(self) -> str:
        lines = [f'Conformance: {self.url}']
        for result in self.results:
            mark = 'OK  ' if result.ok else 'FAIL'
            line = f'[{mark}] {result.name}'
            if result.detail:
                line += f' — {result.detail}'
            lines.append(line)
        failed = sum(1 for result in self.results if not result.ok)
        lines.append(f'Итог: {len(self.results) - failed}/{len(self.results)} '
                     f'проверок пройдено, ошибок {failed}.')
        return '\n'.join(lines)


# ------------------------------------------------------------------ схема

def load_spec(path: Path | str = DEFAULT_SPEC) -> dict[str, Any]:
    with Path(path).open(encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def resolve_pointer(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith('#/'):
        raise ValueError(f'внешняя ссылка не поддерживается: {ref}')
    node: Any = spec
    for part in ref[2:].split('/'):
        node = node[part.replace('~1', '/').replace('~0', '~')]
    return node


def inline_refs(node: Any, spec: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    """Развернуть внутренние ``$ref`` в самодостаточную JSON Schema."""
    if isinstance(node, dict):
        ref = node.get('$ref')
        if isinstance(ref, str):
            if ref in stack:
                raise ValueError(f'циклическая ссылка: {ref}')
            resolved = inline_refs(resolve_pointer(spec, ref), spec, (*stack, ref))
            siblings = {key: value for key, value in node.items() if key != '$ref'}
            if siblings:
                return {'allOf': [resolved,
                                  inline_refs(siblings, spec, (*stack, ref))]}
            return resolved
        return {key: inline_refs(value, spec, stack) for key, value in node.items()}
    if isinstance(node, list):
        return [inline_refs(value, spec, stack) for value in node]
    return node


def build_format_checker() -> FormatChecker:
    # Своя проверка date-time: не зависим от extras jsonschema (rfc3339-validator)
    # и принимаем как Z, так и числовой offset.
    checker = FormatChecker()

    @checker.checks('date-time', raises=ValueError)
    def _date_time(value: Any) -> None:
        if not isinstance(value, str):
            raise ValueError('date-time: ожидалась строка')
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True

    return checker


def build_validators(spec: dict[str, Any]) -> dict[str, Draft202012Validator]:
    formats = build_format_checker()
    validators = {}
    for name, schema in spec['components']['schemas'].items():
        inlined = inline_refs(schema, spec)
        validators[name] = Draft202012Validator(inlined, format_checker=formats)
        validators[f'{name}[]'] = Draft202012Validator(
            {'type': 'array', 'items': inlined}, format_checker=formats)
    return validators


# ---------------------------------------------------------------- утилиты

def deep_find_keys(node: Any, forbidden: set[str], path: str = '') -> list[str]:
    """Найти запрещённые ключи на любой глубине ответа."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            current = f'{path}.{key}' if path else key
            if key in forbidden:
                found.append(current)
            found.extend(deep_find_keys(value, forbidden, current))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(deep_find_keys(value, forbidden, f'{path}[{index}]'))
    return found


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def body_snippet(response: httpx.Response, limit: int = 240) -> str:
    text = ' '.join(response.text.split())
    return text[:limit]


class Checker:
    def __init__(self, client: httpx.AsyncClient, spec: dict[str, Any],
                 api_key: str, order_id: str | None = None,
                 skip_notify: bool = False) -> None:
        self.client = client
        self.spec = spec
        self.api_key = api_key
        self.order_id = order_id
        self.skip_notify = skip_notify
        self.validators = build_validators(spec)
        self.headers = {'X-Api-Key': api_key}
        self.report = Report()
        self.context: dict[str, Any] = {}
        # Уникальный суффикс прогона: checker можно запускать повторно против
        # одного и того же процесса, не натыкаясь на собственные прошлые записи.
        self.run_id = secrets.token_hex(3)

    @property
    def request_id(self) -> str:
        return f'req_chk{self.run_id}'

    @property
    def quote_ids(self) -> list[str]:
        return [f'quo_chk{self.run_id}{suffix}' for suffix in ('c', 'f', 'b')]

    # ------------------------------------------------------------ запросы

    async def request(self, method: str, path: str, *, auth: bool = True,
                      headers: dict[str, str] | None = None,
                      **kwargs: Any) -> httpx.Response:
        merged = dict(headers or {})
        if auth:
            merged.update(self.headers)
        return await self.client.request(method, path, headers=merged, **kwargs)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request('get', path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request('post', path, **kwargs)

    # ------------------------------------------------------------ проверки

    def expect(self, response: httpx.Response, status: int, where: str,
               errors: list[str]) -> bool:
        if response.status_code != status:
            errors.append(f'{where}: ожидался {status}, получен '
                          f'{response.status_code} ({body_snippet(response)})')
            return False
        return True

    def validate(self, schema: str, payload: Any, where: str,
                 errors: list[str]) -> None:
        validator = self.validators.get(schema)
        if validator is None:
            errors.append(f'{where}: схема {schema} не найдена в api/openapi.yaml')
            return
        for error in validator.iter_errors(payload):
            path = '.'.join(str(part) for part in error.absolute_path) or '<root>'
            errors.append(f'{where}: {path}: {error.message}')

    def json(self, response: httpx.Response, where: str,
             errors: list[str]) -> Any:
        content_type = response.headers.get('content-type', '')
        if 'json' not in content_type:
            errors.append(f'{where}: content-type {content_type!r}, ожидался JSON')
        try:
            return response.json()
        except ValueError:
            errors.append(f'{where}: ответ не разбирается как JSON '
                          f'({body_snippet(response)})')
            return None

    def expect_error(self, response: httpx.Response, status: int, where: str,
                     errors: list[str]) -> None:
        if not self.expect(response, status, where, errors):
            return
        payload = self.json(response, where, errors)
        if payload is not None:
            self.validate('Error', payload, where, errors)

    def add(self, name: str, errors: list[str]) -> None:
        self.report.add(name, not errors, '; '.join(errors))

    # -------------------------------------------------------------- запуск

    async def run(self) -> Report:
        try:
            self.check_spec()
            await self.check_health()
            await self.check_auth()
            await self.check_bad_request()
            await self.check_not_found()
            await self.check_requests()
            await self.check_validation()
            await self.check_quotes()
            await self.check_orders()
            await self.check_forbidden()
            await self.check_points()
        except httpx.HTTPError as exc:
            self.report.add('транспорт', False,
                            f'{type(exc).__name__}: {exc}')
        return self.report

    # ----------------------------------------------------------- статика

    def check_spec(self) -> None:
        errors: list[str] = []
        found = {(method, path): operation.get('operationId')
                 for path, item in self.spec.get('paths', {}).items()
                 for method, operation in item.items()
                 if method in METHODS}
        if found != OPERATIONS:
            errors.append(f'набор операций в api/openapi.yaml не совпадает с '
                          f'контрактом: {sorted(found)}')
        if set(self.spec.get('paths', {})) != EXPECTED_PATHS:
            errors.append('в api/openapi.yaml должно быть ровно 6 path-объектов')
        methods = {method for method, _ in found}
        if methods - {'get', 'post'}:
            errors.append(f'в контракте появились мутирующие методы: '
                          f'{sorted(methods - {"get", "post"})}')
        quote_props = set(self.spec['components']['schemas']['Quote']
                          ['properties'])
        quote_input = set(self.spec['components']['schemas']['QuoteInput']
                          ['properties'])
        quote_schemas = {'Quote': quote_props, 'QuoteInput': quote_input}
        for name in ('QuoteBatch', 'QuoteBatchInput'):
            schema = self.spec['components']['schemas'].get(name)
            if schema is not None:
                quote_schemas[name] = set(schema.get('properties') or ())
        leaky = {name: sorted(schema_props & QUOTE_FORBIDDEN_KEYS)
                 for name, schema_props in quote_schemas.items()
                 if schema_props & QUOTE_FORBIDDEN_KEYS}
        if leaky:
            errors.append(f'в схемах котировок есть себестоимость ({leaky})')
        order_status = set(self.spec['components']['schemas']['OrderStatus']['enum'])
        if order_status != ORDER_STATUS:
            errors.append(f'OrderStatus {sorted(order_status)}, ожидается '
                          f'{sorted(ORDER_STATUS)}')

        schemas = self.spec['components']['schemas']
        notify = (self.spec.get('paths', {})
                  .get('/v1/orders/{order_id}/notify', {}).get('post', {}))
        body_schema = (notify.get('requestBody', {})
                       .get('content', {}).get('application/json', {})
                       .get('schema', {}))
        branches = {item.get('$ref', '').rsplit('/', 1)[-1]
                    for item in body_schema.get('oneOf') or ()}
        if branches != NOTIFY_SCHEMAS:
            errors.append(f'notify принимает {sorted(branches)}, ожидается '
                          f'{sorted(NOTIFY_SCHEMAS)} через oneOf')
        reasons = set(schemas.get('OrderLostReason', {}).get('enum') or ())
        if reasons != LOST_REASONS:
            errors.append(f'OrderLostReason {sorted(reasons)}, ожидается '
                          f'{sorted(LOST_REASONS)}')
        for name, event in (('OrderWonNotification', 'order_won'),
                            ('OrderLostNotification', 'order_lost')):
            const = ((schemas.get(name) or {}).get('properties', {})
                     .get('event', {}).get('const'))
            if const != event:
                errors.append(f'{name}.event {const!r}, ожидается const {event!r}')
        self.add('spec', errors)

    async def check_health(self) -> None:
        errors: list[str] = []
        response = await self.get('/healthz', auth=False)
        if self.expect(response, 200, 'GET /healthz', errors):
            payload = self.json(response, 'GET /healthz', errors)
            if payload is not None:
                self.validate('Health', payload, 'GET /healthz', errors)
                if payload.get('status') != 'ok':
                    errors.append('GET /healthz: status должен быть "ok"')
        self.add('healthz', errors)

    # -------------------------------------------------------------- auth

    def _auth_cases(self) -> list[tuple[str, str, dict[str, Any] | None, dict[str, str]]]:
        order = self.order_id or DEFAULT_ORDER_ID
        deadline = rfc3339(utc_now() + timedelta(days=2))
        auth_request = f'req_chk401{self.run_id}'
        request_body = {
            'request_id': auth_request,
            'origin': 'moscow',
            'destination': 'vladivostok',
            'allowed_modes': ['rail'],
            'weight_t': 1.5,
            'cargo': {'description': 'Conformance: проверка ключа.'},
            'response_deadline': deadline,
            'points_reward': 10,
        }
        quote_body = {
            'variants': [{
                'quote_id': f'quo_chk401{self.run_id}',
                'variant': 'balanced',
                'price_rub': 100000,
                'duration_hours': 24,
                'route_id': 'rail:moscow-vladivostok',
                'leg_ids': ['rail:moscow-vladivostok'],
                'valid_until': rfc3339(utc_now() + timedelta(days=3)),
            }],
        }
        notify_body = {
            'order_id': order,
            'status': 'delivered',
            'delivered_at': NOTIFY_FALLBACK_TIME,
            'route_id': 'rail:moscow-vladivostok',
            'leg_ids': ['rail:moscow-vladivostok'],
            'points_awarded': 0,
        }
        return [
            ('get', '/v1/requests', None, {}),
            ('post', '/v1/requests', request_body,
             {'Idempotency-Key': f'{auth_request}:conformance'}),
            ('get', f'/v1/requests/{auth_request}/quotes', None, {}),
            ('post', f'/v1/requests/{auth_request}/quotes', quote_body, {}),
            ('get', f'/v1/orders/{order}', None, {}),
            ('post', f'/v1/orders/{order}/notify', notify_body, {}),
            ('get', '/v1/points', None, {}),
        ]

    async def check_auth(self) -> None:
        errors: list[str] = []
        for method, path, body, headers in self._auth_cases():
            kwargs: dict[str, Any] = {'headers': headers, 'auth': False}
            if body is not None:
                kwargs['json'] = body
            where = f'{method.upper()} {path} без ключа'
            response = await self.request(method, path, **kwargs)
            self.expect_error(response, 401, where, errors)

            bad = dict(headers)
            bad['X-Api-Key'] = 'wrong-team-key'
            where = f'{method.upper()} {path} с неверным ключом'
            response = await self.request(method, path, auth=False,
                                          headers=bad, **({'json': body}
                                                          if body is not None
                                                          else {}))
            self.expect_error(response, 401, where, errors)
        self.add('auth', errors)

    # --------------------------------------------------------- 400 и 404

    async def check_bad_request(self) -> None:
        errors: list[str] = []
        bad_request_id = f'req_chk400{self.run_id}'
        headers = {'Idempotency-Key': f'{bad_request_id}:conformance',
                   'Content-Type': 'application/json'}
        response = await self.post('/v1/requests', headers=headers,
                                   content=b'{"request_id": ')
        self.expect_error(response, 400, 'POST /v1/requests с битым JSON', errors)

        body = {
            'request_id': bad_request_id,
            'origin': 'moscow',
            'destination': 'vladivostok',
            'weight_t': 1.5,
            'cargo': {'description': 'Conformance: без Idempotency-Key.'},
            'response_deadline': rfc3339(utc_now() + timedelta(days=2)),
            'points_reward': 10,
        }
        response = await self.post('/v1/requests', json=body)
        self.expect_error(response, 400, 'POST /v1/requests без Idempotency-Key',
                          errors)
        self.add('bad_request', errors)

    async def check_not_found(self) -> None:
        errors: list[str] = []
        response = await self.get('/v1/requests/req_zzzz/quotes')
        self.expect_error(response, 404, 'GET /v1/requests/req_zzzz/quotes', errors)

        response = await self.get('/v1/orders/ord_zzzz')
        self.expect_error(response, 404, 'GET /v1/orders/ord_zzzz', errors)

        notify = {
            'order_id': 'ord_zzzz',
            'status': 'delivered',
            'delivered_at': NOTIFY_FALLBACK_TIME,
            'route_id': 'rail:moscow-vladivostok',
            'leg_ids': ['rail:moscow-vladivostok'],
            'points_awarded': 0,
        }
        response = await self.post('/v1/orders/ord_zzzz/notify', json=notify)
        self.expect_error(response, 404, 'POST /v1/orders/ord_zzzz/notify', errors)
        self.add('not_found', errors)

    # ------------------------------------------------------------ requests

    def _request_payload(self, request_id: str, *, weight_t: float = 1.5,
                         deadline_days: int = 2) -> dict[str, Any]:
        return {
            'request_id': request_id,
            'origin': 'moscow',
            'destination': 'vladivostok',
            'allowed_modes': ['rail'],
            'weight_t': weight_t,
            'cargo': {'description': 'Conformance-проверка контракта.',
                      'cargo_type': 'general'},
            'response_deadline': rfc3339(utc_now() + timedelta(days=deadline_days)),
            'points_reward': 10,
            'route_hints': [{'route_id': 'rail:moscow-vladivostok',
                             'leg_ids': ['rail:moscow-vladivostok']}],
            'comment': 'Сгенерировано contract.check_service.',
        }

    async def check_requests(self) -> None:
        errors: list[str] = []
        body = self._request_payload(self.request_id)
        headers = {'Idempotency-Key': f'{self.request_id}:conformance'}

        response = await self.post('/v1/requests', json=body, headers=headers)
        if self.expect(response, 201, 'POST /v1/requests', errors):
            payload = self.json(response, 'POST /v1/requests', errors)
            if payload is not None:
                self.validate('Request', payload, 'POST /v1/requests', errors)
                self.context['request'] = payload
                if payload.get('request_id') != self.request_id:
                    errors.append('POST /v1/requests: request_id в ответе '
                                  f'{payload.get("request_id")!r}')
                if payload.get('status') != 'open':
                    errors.append('POST /v1/requests: новая заявка должна быть '
                                  f'"open", получено {payload.get("status")!r}')

        repeat = await self.post('/v1/requests', json=body, headers=headers)
        if self.expect(repeat, 200, 'повтор POST /v1/requests', errors):
            payload = self.json(repeat, 'повтор POST /v1/requests', errors)
            if payload is not None:
                self.validate('Request', payload, 'повтор POST /v1/requests',
                              errors)
                if payload.get('request_id') != self.request_id:
                    errors.append('повтор POST /v1/requests: вернулась другая '
                                  f'заявка {payload.get("request_id")!r}')

        conflict_body = self._request_payload(self.request_id, weight_t=2.5)
        conflict = await self.post('/v1/requests', json=conflict_body,
                                   headers=headers)
        self.expect_error(conflict, 409,
                          'POST /v1/requests: тот же ключ, другое тело', errors)

        listing = await self.get('/v1/requests')
        if self.expect(listing, 200, 'GET /v1/requests', errors):
            payload = self.json(listing, 'GET /v1/requests', errors)
            if payload is not None:
                self.validate('Request[]', payload, 'GET /v1/requests', errors)
                self.context['requests'] = payload
                ids = {item.get('request_id') for item in payload
                       if isinstance(item, dict)}
                if self.request_id not in ids:
                    errors.append(f'GET /v1/requests: созданной заявки '
                                  f'{self.request_id} нет в списке')
        self.add('requests', errors)

    async def check_validation(self) -> None:
        errors: list[str] = []
        invalid_id = f'req_chk422{self.run_id}'
        invalid = self._request_payload(invalid_id)
        invalid.pop('weight_t')
        response = await self.post(
            '/v1/requests', json=invalid,
            headers={'Idempotency-Key': f'{invalid_id}:conformance'})
        self.expect_error(response, 422,
                          'POST /v1/requests без weight_t', errors)

        quote = {
            'variants': [{
                'quote_id': f'quo_chk422{self.run_id}',
                'variant': 'balanced',
                'price_rub': 0,
                'duration_hours': 24,
                'route_id': 'rail:moscow-vladivostok',
                'leg_ids': ['rail:moscow-vladivostok'],
                'valid_until': rfc3339(utc_now() + timedelta(days=3)),
            }],
        }
        response = await self.post(f'/v1/requests/{self.request_id}/quotes',
                                   json=quote)
        self.expect_error(response, 422,
                          'POST quotes с price_rub=0', errors)

        order = self.context.get('order_id') or self.order_id or DEFAULT_ORDER_ID
        notify = {
            'order_id': order,
            'status': 'cancelled',
            'delivered_at': NOTIFY_FALLBACK_TIME,
            'route_id': 'rail:moscow-vladivostok',
            'leg_ids': ['rail:moscow-vladivostok'],
            'points_awarded': 0,
        }
        response = await self.post(f'/v1/orders/{order}/notify', json=notify)
        self.expect_error(response, 422,
                          'POST notify со статусом cancelled', errors)
        self.add('validation', errors)

    # -------------------------------------------------------------- quotes

    def _quote_payload(self) -> dict[str, Any]:
        valid_until = rfc3339(utc_now() + timedelta(days=3))
        specs = (
            ('c', 'cheapest', 100000, 24),
            ('f', 'fastest', 140000, 18),
            ('b', 'balanced', 120000, 21),
        )
        return {
            'variants': [{
                'quote_id': f'quo_chk{self.run_id}{suffix}',
                'variant': variant,
                'price_rub': price_rub,
                'duration_hours': duration_hours,
                'route_id': 'rail:moscow-vladivostok',
                'leg_ids': ['rail:moscow-vladivostok'],
                'valid_until': valid_until,
                'comment': 'Conformance-котировка.',
            } for suffix, variant, price_rub, duration_hours in specs],
        }

    async def check_quotes(self) -> None:
        errors: list[str] = []
        body = self._quote_payload()
        expected_ids = set(self.quote_ids)
        path = f'/v1/requests/{self.request_id}/quotes'

        response = await self.post(path, json=body)
        if self.expect(response, 201, f'POST {path}', errors):
            payload = self.json(response, f'POST {path}', errors)
            if payload is not None:
                self.validate('QuoteBatch', payload, f'POST {path}', errors)
                if payload.get('request_id') != self.request_id:
                    errors.append(f'POST {path}: request_id '
                                  f'{payload.get("request_id")!r}')
                variants = payload.get('variants')
                if not isinstance(variants, list):
                    errors.append(f'POST {path}: variants должен быть массивом')
                else:
                    items = [item for item in variants if isinstance(item, dict)]
                    quote_ids = [item.get('quote_id') for item in items]
                    if set(quote_ids) != expected_ids:
                        errors.append(f'POST {path}: quote_id {quote_ids}, '
                                      f'ожидались {sorted(expected_ids)}')
                    if len(set(quote_ids)) != len(quote_ids):
                        errors.append(f'POST {path}: quote_id повторяется в наборе '
                                      f'{quote_ids}')
                    labels = [item.get('variant') for item in items]
                    if len(set(labels)) != len(labels):
                        errors.append(f'POST {path}: variant повторяется в наборе '
                                      f'{labels}')
                    seqs = [item.get('received_seq') for item in items]
                    if not all(isinstance(seq, int) for seq in seqs):
                        errors.append(f'POST {path}: received_seq должен быть '
                                      f'числом у каждого варианта ({seqs})')
                    elif len(set(seqs)) != len(seqs):
                        errors.append(f'POST {path}: received_seq не уникальны '
                                      f'в наборе ({seqs})')
                    for item in items:
                        if item.get('status') not in QUOTE_STATUS:
                            errors.append(f'POST {path}: статус '
                                          f'{item.get("status")!r} вне QuoteStatus')
                    forbidden = deep_find_keys(payload, QUOTE_FORBIDDEN_KEYS)
                    if forbidden:
                        errors.append(f'POST {path}: поля себестоимости '
                                      f'{forbidden}')
                    self.context['quotes'] = payload

        repeat = await self.post(path, json=body)
        self.expect_error(repeat, 409, f'повтор POST {path}', errors)

        listing = await self.get(path)
        if self.expect(listing, 200, f'GET {path}', errors):
            payload = self.json(listing, f'GET {path}', errors)
            if isinstance(payload, list):
                self.validate('Quote[]', payload, f'GET {path}', errors)
                quote_ids = {item.get('quote_id') for item in payload
                             if isinstance(item, dict)}
                missing = expected_ids - quote_ids
                if missing:
                    errors.append(f'GET {path}: в списке нет котировок '
                                  f'{sorted(missing)}')
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    if item.get('request_id') != self.request_id:
                        errors.append(f'GET {path}: чужая котировка '
                                      f'{item.get("quote_id")!r} по заявке '
                                      f'{item.get("request_id")!r}')
                    forbidden = deep_find_keys(item, QUOTE_FORBIDDEN_KEYS)
                    if forbidden:
                        errors.append(f'GET {path}: поля себестоимости '
                                      f'{forbidden}')
            elif payload is not None:
                errors.append(f'GET {path}: ожидался массив')
        self.add('quotes', errors)

    # -------------------------------------------------------------- orders

    async def check_orders(self) -> None:
        errors: list[str] = []
        order_id = self.order_id
        if not order_id:
            order_id = await self._discover_order_id(errors)
        if not order_id:
            errors.append('не удалось определить order_id для проверки заказа: '
                          'передайте --order-id (например, ord_6003)')
            self.report.add('order', False, '; '.join(errors))
            return
        self.context['order_id'] = order_id

        response = await self.get(f'/v1/orders/{order_id}')
        snapshot: dict[str, Any] | None = None
        if self.expect(response, 200, f'GET /v1/orders/{order_id}', errors):
            payload = self.json(response, f'GET /v1/orders/{order_id}', errors)
            if payload is not None:
                snapshot = payload
                self.validate('OrderSnapshot', payload,
                              f'GET /v1/orders/{order_id}', errors)
                if payload.get('order_id') != order_id:
                    errors.append(f'GET /v1/orders/{order_id}: order_id '
                                  f'{payload.get("order_id")!r}')
                status = payload.get('status')
                if status not in ORDER_STATUS:
                    errors.append(f'GET /v1/orders/{order_id}: статус {status!r} '
                                  f'вне OrderStatus')
                if status == 'delivered' and 'delivered_at' not in payload:
                    errors.append(f'GET /v1/orders/{order_id}: у delivered нет '
                                  'delivered_at')
                if status == 'failed' and not {'failed_at', 'failure_code'} <= set(payload):
                    errors.append(f'GET /v1/orders/{order_id}: у failed нет '
                                  'failed_at/failure_code')
        self.add('order', errors)

        if self.skip_notify:
            self.report.add('notify', True, 'пропущено по --skip-notify')
            return
        if snapshot is None:
            self.report.add('notify', False,
                            'снимок заказа не получен, notify не проверен')
            return
        await self._check_notify(order_id, snapshot)

    async def _discover_order_id(self, errors: list[str]) -> str | None:
        response = await self.get('/v1/points')
        if response.status_code != 200:
            return None
        try:
            points = response.json()
        except ValueError:
            return None
        if not isinstance(points, list):
            return None
        for entry in points:
            if isinstance(entry, dict) and entry.get('kind') == 'order_delivery':
                value = entry.get('order_id')
                if isinstance(value, str):
                    return value
        return None

    async def _check_notify(self, order_id: str,
                            snapshot: dict[str, Any]) -> None:
        errors: list[str] = []
        status = ('subcontract_delivered'
                  if snapshot.get('status') == 'failed' else 'delivered')
        delivered_at = snapshot.get('delivered_at') or NOTIFY_FALLBACK_TIME
        body: dict[str, Any] = {
            'order_id': order_id,
            'status': status,
            'delivered_at': delivered_at,
            'route_id': snapshot.get('route_id') or 'rail:moscow-vladivostok',
            'leg_ids': ['rail:moscow-vladivostok'],
            'points_awarded': int(snapshot.get('points_awarded') or 0),
        }
        if status == 'subcontract_delivered':
            body['subcontract_id'] = 'sub_chk01'
        path = f'/v1/orders/{order_id}/notify'

        response = await self.post(path, json=body)
        if self.expect(response, 204, path, errors):
            if response.content:
                errors.append(f'{path}: у 204 не должно быть тела')

        repeat = await self.post(path, json=body)
        if self.expect(repeat, 204, f'повтор {path}', errors):
            if repeat.content:
                errors.append(f'повтор {path}: у 204 не должно быть тела')

        conflict_body = dict(body)
        conflict_body['points_awarded'] = body['points_awarded'] + 1
        conflict = await self.post(path, json=conflict_body)
        self.expect_error(conflict, 409, f'{path}: другое тело', errors)

        # События order_won / order_lost: идемпотентность по Idempotency-Key.
        route_id = snapshot.get('route_id') or 'rail:moscow-vladivostok'
        team_id = 'conformance'
        won = {
            'event': 'order_won',
            'order_id': order_id,
            'request_id': self.request_id,
            'team_id': team_id,
            'route_id': route_id,
            'leg_ids': [route_id],
            'price_rub': int(snapshot.get('price_rub') or 100000),
            'duration_hours': 24,
            'points_reward': 10,
            'message': 'Conformance: заявка выиграна.',
        }
        won_key = f'exchange:order:{order_id}:won:v1'
        response = await self.post(path, json=won,
                                   headers={'Idempotency-Key': won_key})
        if self.expect(response, 204, f'{path}: order_won', errors):
            if response.content:
                errors.append(f'{path}: order_won — у 204 не должно быть тела')
        repeat = await self.post(path, json=won,
                                 headers={'Idempotency-Key': won_key})
        if self.expect(repeat, 204, f'{path}: повтор order_won', errors):
            if repeat.content:
                errors.append(f'{path}: повтор order_won — у 204 не должно быть тела')
        changed = dict(won, price_rub=won['price_rub'] + 1)
        conflict = await self.post(path, json=changed,
                                   headers={'Idempotency-Key': won_key})
        self.expect_error(conflict, 409,
                          f'{path}: order_won с тем же ключом и другим телом',
                          errors)

        lost = {
            'event': 'order_lost',
            'order_id': order_id,
            'request_id': self.request_id,
            'team_id': team_id,
            'route_id': route_id,
            'leg_ids': [route_id],
            'reason': 'not_selected',
            'message': 'Conformance: заявка закрыта без победы.',
        }
        lost_key = f'exchange:order:{order_id}:lost:{team_id}:v1'
        response = await self.post(path, json=lost,
                                   headers={'Idempotency-Key': lost_key})
        if self.expect(response, 204, f'{path}: order_lost', errors):
            if response.content:
                errors.append(f'{path}: order_lost — у 204 не должно быть тела')

        invalid = dict(lost, reason='unknown_reason')
        response = await self.post(
            path, json=invalid,
            headers={'Idempotency-Key':
                     f'exchange:order:{order_id}:lost:{team_id}-bad:v1'})
        self.expect_error(response, 422,
                          f'{path}: order_lost с неизвестным reason', errors)

        after = await self.get(f'/v1/orders/{order_id}')
        if self.expect(after, 200, f'GET /v1/orders/{order_id} после notify',
                       errors):
            payload = self.json(after, f'GET /v1/orders/{order_id} после notify',
                                errors)
            if payload is not None and payload != snapshot:
                errors.append('notify изменил снимок заказа: команда не владеет '
                              'статусом/доставкой/баллами')
        self.add('notify', errors)

    # ------------------------------------------------------------ запреты

    async def check_forbidden(self) -> None:
        errors: list[str] = []
        for path in FORBIDDEN_PATHS:
            for method in ('get', 'post'):
                response = await self.request(method, path)
                if response.status_code not in {404, 405}:
                    errors.append(f'{method.upper()} {path}: ручки быть не должно, '
                                  f'получен {response.status_code}')

        order_id = self.context.get('order_id') or self.order_id or DEFAULT_ORDER_ID
        mutations = [
            ('patch', f'/v1/orders/{order_id}', {'status': 'delivered'}),
            ('put', f'/v1/orders/{order_id}', {'status': 'delivered'}),
            ('delete', f'/v1/orders/{order_id}', None),
            ('put', '/v1/requests', {}),
            ('delete', '/v1/requests', None),
            ('patch', f'/v1/requests/{self.request_id}/quotes', {'price_rub': 1}),
            ('put', f'/v1/requests/{self.request_id}/quotes', {}),
            ('delete', f'/v1/requests/{self.request_id}/quotes', None),
            ('post', '/v1/points', {}),
            ('patch', '/v1/points', {}),
            ('put', '/v1/points', {}),
            ('delete', '/v1/points', None),
        ]
        for method, path, body in mutations:
            kwargs: dict[str, Any] = {}
            if body is not None:
                kwargs['json'] = body
            response = await self.request(method, path, **kwargs)
            if response.status_code not in {404, 405}:
                errors.append(f'{method.upper()} {path}: мутирующей ручки быть '
                              f'не должно, получен {response.status_code}')
        self.add('read_only', errors)

    # -------------------------------------------------------------- points

    async def check_points(self) -> None:
        errors: list[str] = []
        response = await self.get('/v1/points')
        if not self.expect(response, 200, 'GET /v1/points', errors):
            self.add('points', errors)
            return
        payload = self.json(response, 'GET /v1/points', errors)
        if not isinstance(payload, list):
            if payload is not None:
                errors.append('GET /v1/points: ожидался массив')
            self.add('points', errors)
            return
        self.validate('PointsEntry[]', payload, 'GET /v1/points', errors)

        seen: set[Any] = set()
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            point_id = entry.get('id')
            if point_id in seen:
                errors.append(f'GET /v1/points: повтор id {point_id!r}')
            seen.add(point_id)
            foreign = deep_find_keys(entry, FOREIGN_KEYS)
            if foreign:
                errors.append(f'GET /v1/points: чужие/денежные поля {foreign}')
            kind = entry.get('kind')
            amount = entry.get('amount_points')
            if isinstance(amount, int) and amount < 0:
                errors.append(f'GET /v1/points: {point_id!r} отрицательные баллы')
            if kind == 'order_delivery':
                order_id = entry.get('order_id')
                if not order_id:
                    errors.append(f'GET /v1/points: {point_id!r} без order_id')
                    continue
                order = await self.get(f'/v1/orders/{order_id}')
                if order.status_code != 200:
                    errors.append(
                        f'GET /v1/points: {point_id!r} ссылается на недоступный '
                        f'заказ {order_id!r} ({order.status_code}) — чужие данные')
                else:
                    snapshot = order.json() if order.content else None
                    if isinstance(snapshot, dict):
                        self.validate('OrderSnapshot', snapshot,
                                      f'GET /v1/orders/{order_id} из points',
                                      errors)
            elif kind == 'subcontract_delivery':
                if not entry.get('subcontract_id'):
                    errors.append(
                        f'GET /v1/points: {point_id!r} subcontract_delivery без '
                        'subcontract_id')
        self.add('points', errors)


# ------------------------------------------------------------------- CLI

async def run_checks(*, api_key: str, order_id: str | None = None,
                     skip_notify: bool = False,
                     client: httpx.AsyncClient | None = None,
                     url: str | None = None,
                     spec: dict[str, Any] | None = None,
                     timeout: float = 10.0) -> Report:
    """Прогнать conformance против сервиса.

    ``client`` нужен тестам (httpx ASGI transport); в обычном запуске достаточно
    ``url``. Возвращает отчёт, не печатает и не завершает процесс.
    """
    document = spec if spec is not None else load_spec()
    if client is None:
        if not url:
            raise ValueError('нужен url или готовый httpx.AsyncClient')
        async with httpx.AsyncClient(base_url=url, timeout=timeout) as http:
            report = await Checker(http, document, api_key, order_id,
                                   skip_notify).run()
            report.url = url
            return report
    return await Checker(client, document, api_key, order_id, skip_notify).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='check-service',
        description='Conformance-проверки сервиса команды по api/openapi.yaml.')
    parser.add_argument('--url', required=True,
                        help='базовый URL сервиса, например http://127.0.0.1:8080')
    parser.add_argument('--api-key', required=True,
                        help='ключ команды (X-Api-Key); в отчёт не попадает')
    parser.add_argument('--order-id', default=None,
                        help='сидовый заказ для notify (иначе ищется в /v1/points)')
    parser.add_argument('--skip-notify', action='store_true',
                        help='не проверять POST /v1/orders/{id}/notify')
    parser.add_argument('--timeout', type=float, default=10.0,
                        help='таймаут одного запроса, секунды (по умолчанию 10)')
    parser.add_argument('--spec', default=str(DEFAULT_SPEC),
                        help='путь к api/openapi.yaml')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(run_checks(
            api_key=args.api_key,
            order_id=args.order_id,
            skip_notify=args.skip_notify,
            url=args.url,
            spec=load_spec(args.spec),
            timeout=args.timeout,
        ))
    except FileNotFoundError as exc:
        print(f'Не найден файл контракта: {exc}', file=sys.stderr)
        return 1
    except (ValueError, yaml.YAMLError) as exc:
        print(f'Не удалось загрузить контракт: {exc}', file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f'Ошибка транспорта: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(report.render())
    return 0 if report.ok else 1


if __name__ == '__main__':
    sys.exit(main())
