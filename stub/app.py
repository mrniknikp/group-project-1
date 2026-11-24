"""Reference stub сервиса команды по ``api/openapi.yaml``.

8 операций, ``X-Api-Key``, in-memory состояние и детерминированный сид.
Запуск::

    TEAM_API_KEY=test-key uvicorn stub.app:app --port 8080

Stub — учебный эталон контракта, а не продакшен: состояние живёт в памяти
процесса, при перезапуске возвращается сид. Команда только котирует;
доставку и баллы ведёт система, поэтому снимок заказа read-only, а
``/v1/points`` — только чтение. ``notify`` принимает четыре тела:
``delivered``/``subcontract_delivered``, ``order_won`` и экспериментальное
``order_lost``.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cache import Cache
from .routing import DEFAULT_TIME_LIMIT_S, find_routes_iterative_dfs

SERVICE_NAME = 'team-service'
SERVICE_VERSION = '2.4.0'
DEFAULT_API_KEY = 'test-key'

REQUEST_ID = r'^req_[A-Za-z0-9]{2,32}$'
QUOTE_ID = r'^quo_[A-Za-z0-9]{2,32}$'
ORDER_ID = r'^ord_[A-Za-z0-9]{2,32}$'
SUBCONTRACT_ID = r'^sub_[A-Za-z0-9]{2,32}$'
TEAM_ID = r'^[a-z0-9-]{1,64}$'
# Канон портала: id маршрута и плеча — id сегмента справочника `<mode>:<from>-<to>`.
ROUTE_ID = r'^[a-z][a-z0-9_]*:[a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]+(?:-[a-z0-9]+)*$'
# В v1 плечо = сегмент, поэтому шаблон тот же, что у маршрута.
LEG_ID = ROUTE_ID
LOCATION_ID = r'^[a-z0-9]+(?:-[a-z0-9]+)*$'
IDEMPOTENCY_KEY = r'^req_[A-Za-z0-9]{2,32}:[a-z0-9-]{1,64}$'
NOTIFY_IDEMPOTENCY_KEY = r'^[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}$'
MODE = Literal['road', 'rail', 'air', 'sea']
CARGO_TYPE = Literal['general', 'fragile', 'refrigerated', 'hazardous', 'oversized']
QUOTE_VARIANT = Literal['cheapest', 'fastest', 'balanced']

VALID_UNTIL_MARGIN = timedelta(seconds=60)


# --------------------------------------------------------------- время и сид

def utc_now() -> str:
    """Текущее время в RFC 3339 UTC (``Z``), как требует контракт."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_utc(value: str) -> datetime:
    """Разобрать RFC 3339 и привести к UTC; наивное время — ошибка."""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('нужен RFC 3339 с часовым поясом (UTC, Z)')
    return parsed.astimezone(timezone.utc)


def normalize_utc(value: str) -> str:
    return parse_utc(value).strftime('%Y-%m-%dT%H:%M:%SZ')


def seed_store() -> dict[str, Any]:
    """Сид: 2 заявки, 1 котировка-набор из одного варианта, 1 заказ, 2 начисления.

    ID и данные детерминированы, а времена относительны ``utc_now()``: сид не
    протухает и остаётся валидным для живых прогонов в любой день. ID взяты из
    ``api/mocks``, но набор намеренно маленький; кросс-ссылки замкнуты внутри
    сида. Котировка одна (один вариант ``balanced``), поэтому ``req_5001``
    остаётся свободной для живой публикации набора котировок.
    """
    now = datetime.now(timezone.utc)

    def at(**delta: float) -> str:
        return (now + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')

    request_1 = {
        'request_id': 'req_5001',
        'origin': 'moscow',
        'destination': 'kazan',
        'allowed_modes': ['rail', 'road'],
        'weight_t': 18.5,
        'cargo': {'description': 'Сборный груз, паллеты.',
                  'cargo_type': 'general'},
        'response_deadline': at(days=2),
        'points_reward': 100,
        'route_hints': [{'route_id': 'road:moscow-kazan',
                         'leg_ids': ['road:moscow-kazan']}],
        'comment': 'Погрузка на терминале заказчика.',
        'received_at': at(hours=-1),
        'status': 'open',
    }
    request_2 = {
        'request_id': 'req_5002',
        'origin': 'saint-petersburg',
        'destination': 'moscow',
        'allowed_modes': ['rail'],
        'weight_t': 12.0,
        'cargo': {'description': 'Промышленное оборудование в ящиках.',
                  'cargo_type': 'general'},
        'response_deadline': at(days=1),
        'points_reward': 80,
        'route_hints': [{'route_id': 'rail:saint-petersburg-arkhangelsk',
                         'leg_ids': ['rail:saint-petersburg-arkhangelsk',
                                     'rail:arkhangelsk-moscow']}],
        'received_at': at(hours=-2),
        'status': 'quoted',
    }
    quote_1 = {
        'quote_id': 'quo_7007',
        'request_id': 'req_5002',
        'variant': 'balanced',
        'status': 'submitted',
        'price_rub': 760000,
        'duration_hours': 90.0,
        'route_id': 'rail:saint-petersburg-arkhangelsk',
        'leg_ids': ['rail:saint-petersburg-arkhangelsk',
                    'rail:arkhangelsk-moscow'],
        'valid_until': at(days=4),
        'received_seq': 1007,
        'received_at': at(hours=-1),
        'comment': 'Ж/д через Архангельск.',
    }
    order_1 = {
        'order_id': 'ord_6003',
        'request_id': 'req_5002',
        'quote_id': 'quo_7007',
        'route_id': 'rail:saint-petersburg-arkhangelsk',
        'status': 'delivered',
        'price_rub': 760000,
        'created_at': at(days=-1),
        'delivered_at': at(hours=-2),
        'points_awarded': 80,
    }
    point_1 = {
        'id': 'pts_9001',
        'kind': 'order_delivery',
        'order_id': 'ord_6003',
        'amount_points': 80,
        'created_at': at(hours=-2),
    }
    point_2 = {
        'id': 'pts_9002',
        'kind': 'subcontract_delivery',
        'subcontract_id': 'sub_9001',
        'amount_points': 40,
        'created_at': at(hours=-1),
    }
    return {
        'requests': {request_1['request_id']: request_1,
                     request_2['request_id']: request_2},
        'request_order': ['req_5001', 'req_5002'],
        'quotes': {quote_1['request_id']: [quote_1]},
        'orders': {order_1['order_id']: order_1},
        'points': [point_1, point_2],
        'idempotency': {},
        'notifications': {},
        'next_seq': 1008,
    }


# --------------------------------------------------------------------- схемы

class CargoIn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    description: str = Field(min_length=1, max_length=500)
    cargo_type: CARGO_TYPE = 'general'


class RouteHintIn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    route_id: str = Field(pattern=ROUTE_ID)
    leg_ids: list[str] = Field(default_factory=list)

    @field_validator('leg_ids')
    @classmethod
    def _legs_are_valid(cls, value: list[str]) -> list[str]:
        for leg_id in value:
            if not re.fullmatch(LEG_ID, leg_id):
                raise ValueError(f'leg_id {leg_id!r} вне шаблона')
        return value


class RequestIn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str = Field(pattern=REQUEST_ID)
    origin: str = Field(pattern=LOCATION_ID)
    destination: str = Field(pattern=LOCATION_ID)
    allowed_modes: list[MODE] | None = None
    weight_t: float = Field(ge=0.001)
    cargo: CargoIn
    response_deadline: str
    points_reward: int = Field(ge=1, le=10000)
    route_hints: list[RouteHintIn] | None = Field(default=None, max_length=6)
    max_price_rub: int | None = Field(default=None, ge=1)
    max_duration_hours: float | None = Field(default=None, ge=0.01, le=100000)
    comment: str | None = Field(default=None, max_length=1000)
    # readOnly по контракту: принимаем, но не сохраняем и не сравниваем.
    status: str | None = None
    received_at: str | None = None

    @field_validator('allowed_modes')
    @classmethod
    def _modes_are_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if not value:
                raise ValueError('allowed_modes не может быть пустым')
            if len(set(value)) != len(value):
                raise ValueError('allowed_modes содержит повторы')
        return value

    @field_validator('response_deadline')
    @classmethod
    def _deadline_is_rfc3339(cls, value: str) -> str:
        return normalize_utc(value)


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    quote_id: str = Field(pattern=QUOTE_ID)
    variant: QUOTE_VARIANT
    price_rub: int = Field(ge=1)
    duration_hours: float = Field(ge=0.01)
    route_id: str = Field(pattern=ROUTE_ID)
    leg_ids: list[str] = Field(min_length=1)
    valid_until: str
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator('leg_ids')
    @classmethod
    def _legs_are_valid(cls, value: list[str]) -> list[str]:
        for leg_id in value:
            if not re.fullmatch(LEG_ID, leg_id):
                raise ValueError(f'leg_id {leg_id!r} вне шаблона')
        return value

    @field_validator('valid_until')
    @classmethod
    def _valid_until_is_rfc3339(cls, value: str) -> str:
        return normalize_utc(value)


class QuoteBatchIn(BaseModel):
    """Набор вариантов: 1–3, ``variant`` и ``quote_id`` уникальны."""

    model_config = ConfigDict(extra='forbid')

    variants: list[QuoteIn] = Field(min_length=1, max_length=3)

    @field_validator('variants')
    @classmethod
    def _variants_are_unique(cls, value: list[QuoteIn]) -> list[QuoteIn]:
        quote_ids = [item.quote_id for item in value]
        if len(set(quote_ids)) != len(quote_ids):
            raise ValueError('quote_id повторяется в наборе')
        variants = [item.variant for item in value]
        if len(set(variants)) != len(variants):
            raise ValueError('variant повторяется в наборе')
        return value


class NotifyIn(BaseModel):
    """Событие системной доставки: ``delivered`` | ``subcontract_delivered``."""

    model_config = ConfigDict(extra='forbid')

    order_id: str = Field(pattern=ORDER_ID)
    subcontract_id: str | None = Field(default=None, pattern=SUBCONTRACT_ID)
    status: Literal['delivered', 'subcontract_delivered']
    delivered_at: str
    route_id: str = Field(pattern=ROUTE_ID)
    leg_ids: list[str] = Field(min_length=1)
    points_awarded: int = Field(ge=0)

    @field_validator('leg_ids')
    @classmethod
    def _legs_are_valid(cls, value: list[str]) -> list[str]:
        for leg_id in value:
            if not re.fullmatch(LEG_ID, leg_id):
                raise ValueError(f'leg_id {leg_id!r} вне шаблона')
        return value

    @field_validator('delivered_at')
    @classmethod
    def _delivered_at_is_rfc3339(cls, value: str) -> str:
        return normalize_utc(value)


class OrderWonIn(BaseModel):
    """Событие ``order_won``: заявка выиграна, заказ ещё не доставлен."""

    model_config = ConfigDict(extra='forbid')

    event: Literal['order_won']
    order_id: str = Field(pattern=ORDER_ID)
    request_id: str = Field(pattern=REQUEST_ID)
    team_id: str = Field(pattern=TEAM_ID)
    route_id: str = Field(pattern=ROUTE_ID)
    leg_ids: list[str] = Field(min_length=1)
    price_rub: int = Field(ge=1)
    duration_hours: float = Field(ge=0.01)
    points_reward: int = Field(ge=1, le=10000)
    message: str = Field(min_length=1, max_length=500)

    @field_validator('leg_ids')
    @classmethod
    def _legs_are_valid(cls, value: list[str]) -> list[str]:
        for leg_id in value:
            if not re.fullmatch(LEG_ID, leg_id):
                raise ValueError(f'leg_id {leg_id!r} вне шаблона')
        return value


class OrderLostIn(BaseModel):
    """Экспериментальное событие ``order_lost``: заявка закрыта без победы."""

    model_config = ConfigDict(extra='forbid')

    event: Literal['order_lost']
    order_id: str = Field(pattern=ORDER_ID)
    request_id: str = Field(pattern=REQUEST_ID)
    team_id: str = Field(pattern=TEAM_ID)
    route_id: str = Field(pattern=ROUTE_ID)
    leg_ids: list[str] = Field(min_length=1)
    reason: Literal['not_selected', 'no_quote']
    message: str = Field(min_length=1, max_length=500)

    @field_validator('leg_ids')
    @classmethod
    def _legs_are_valid(cls, value: list[str]) -> list[str]:
        for leg_id in value:
            if not re.fullmatch(LEG_ID, leg_id):
                raise ValueError(f'leg_id {leg_id!r} вне шаблона')
        return value


# ---------------------------------------------------------------- приложение

def notify_event(value: Any) -> str:
    """Дискриминатор тела notify: событие из поля ``event`` или доставка."""
    if isinstance(value, dict) and value.get('event'):
        return str(value['event'])
    return 'delivery'


# Одно тело — три формы: событие доставки без `event` и события `order_won`
# / `order_lost` с `event`. Дискриминатор даёт точные 422 без шума ветвей.
NotifyBody = Annotated[
    Annotated[NotifyIn, Tag('delivery')]
    | Annotated[OrderWonIn, Tag('order_won')]
    | Annotated[OrderLostIn, Tag('order_lost')],
    Discriminator(notify_event),
]


def error_response(status: int, code: str, message: str,
                   details: dict[str, Any] | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """Единый конверт ошибок контракта: ``{code, message, details?}``."""
    body: dict[str, Any] = {'code': code, 'message': message}
    if details:
        body['details'] = details
    return JSONResponse(body, status_code=status, headers=headers)


def canonical(payload: dict[str, Any]) -> str:
    """Стабильное представление тела для сравнения идемпотентных повторов."""
    import json

    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'))


def create_app(api_key: str | None = None) -> FastAPI:
    """Собрать приложение с чистым in-memory состоянием.

    ``api_key`` — ключ команды; по умолчанию ``TEAM_API_KEY`` из окружения,
    иначе ``test-key`` (только для локального запуска и CI).
    """
    key = api_key or os.environ.get('TEAM_API_KEY') or DEFAULT_API_KEY
    store = seed_store()
    cache = Cache()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await cache.connect()
        try:
            yield
        finally:
            await cache.close()

    app = FastAPI(
        title='Team Service API (reference stub)',
        version=SERVICE_VERSION,
        description='Учебный stub контракта api/openapi.yaml.',
        lifespan=lifespan,
    )
    app.state.api_key = key
    app.state.store = store
    app.state.cache = cache

    @app.middleware('http')
    async def api_key_guard(request: Request, call_next):
        # /healthz — единственная операция без ключа (так в контракте).
        if request.url.path == '/healthz':
            return await call_next(request)
        supplied = request.headers.get('X-Api-Key')
        expected = app.state.api_key
        if supplied is None or not hmac.compare_digest(
                supplied.encode('utf-8'), expected.encode('utf-8')):
            return error_response(
                401, 'unauthorized', 'Ключ API отсутствует или неверен.',
                {'header': 'X-Api-Key'})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        if any(item.get('type') == 'json_invalid' for item in errors):
            return error_response(
                400, 'bad_request', 'Тело запроса не является корректным JSON.')
        fields = [
            {'loc': '.'.join(str(part) for part in item.get('loc', ())
                             if part != 'body'),
             'message': item.get('msg', 'некорректное значение')}
            for item in errors
        ]
        return error_response(
            422, 'validation_error', 'Тело или параметры не прошли проверку.',
            {'fields': fields})

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return error_response(404, 'not_found', 'Объект не найден.')
        if exc.status_code == 405:
            return error_response(
                405, 'method_not_allowed', 'Метод не поддерживается.',
                headers=dict(exc.headers or {}))
        return error_response(
            exc.status_code, 'http_error', 'Запрос отклонён.',
            headers=dict(exc.headers or {}))

    # ------------------------------------------------------------- health

    @app.get('/healthz')
    async def healthz() -> dict[str, Any]:
        return {
            'status': 'ok',
            'service': SERVICE_NAME,
            'version': SERVICE_VERSION,
            'time': utc_now(),
        }

    # ----------------------------------------------------------- requests

    @app.get('/v1/requests')
    async def list_requests() -> list[dict[str, Any]]:
        state = app.state.store
        cache_key = 'requests:list'
        cached = await cache.get_json(cache_key)
        if isinstance(cached, list):
            return cached
        items = [dict(state['requests'][request_id])
                 for request_id in state['request_order']]
        await cache.set_json(cache_key, items)
        return items

    @app.post('/v1/requests', status_code=201)
    async def create_request(body: RequestIn, request: Request,
                             response: Response):
        state = app.state.store
        idem = (request.headers.get('Idempotency-Key') or '').strip()
        if not idem:
            return error_response(
                400, 'bad_request', 'Обязательный заголовок Idempotency-Key отсутствует.',
                {'header': 'Idempotency-Key'})
        if not re.fullmatch(IDEMPOTENCY_KEY, idem):
            return error_response(
                422, 'validation_error', 'Idempotency-Key не соответствует шаблону.',
                {'header': 'Idempotency-Key',
                 'pattern': '<request_id>:<team_slug>'})

        payload = body.model_dump(exclude={'status', 'received_at'}, exclude_none=True)
        fingerprint = canonical(payload)
        seen = state['idempotency'].get(idem)
        if seen is not None:
            if seen['fingerprint'] == fingerprint:
                response.status_code = 200
                return dict(state['requests'][seen['request_id']])
            return error_response(
                409, 'request_conflict',
                'Ключ идемпотентности уже использован с другим телом.',
                {'idempotency_key': idem})
        if body.request_id in state['requests']:
            return error_response(
                409, 'request_conflict',
                'Заявка с таким request_id уже принята.',
                {'request_id': body.request_id})

        stored = dict(payload)
        stored['received_at'] = utc_now()
        stored['status'] = 'open'
        state['requests'][body.request_id] = stored
        state['request_order'].append(body.request_id)
        state['idempotency'][idem] = {
            'fingerprint': fingerprint,
            'request_id': body.request_id,
        }
        await cache.delete('requests:list')
        response.status_code = 201
        return dict(stored)

    # ------------------------------------------------------------- quotes

    @app.get('/v1/requests/{request_id}/quotes')
    async def list_quotes(request_id: str) -> list[dict[str, Any]]:
        state = app.state.store
        if request_id not in state['requests']:
            return error_response(404, 'not_found', 'Заявка не найдена.',
                                  {'resource': 'request', 'id': request_id})
        cache_key = f'quotes:{request_id}'
        cached = await cache.get_json(cache_key)
        if isinstance(cached, list):
            return cached
        items = [dict(quote) for quote in state['quotes'].get(request_id, [])]
        await cache.set_json(cache_key, items)
        return items

    @app.post('/v1/requests/{request_id}/quotes', status_code=201)
    async def create_quote(request_id: str, body: QuoteBatchIn,
                           response: Response):
        state = app.state.store
        record = state['requests'].get(request_id)
        if record is None:
            return error_response(404, 'not_found', 'Заявка не найдена.',
                                  {'resource': 'request', 'id': request_id})
        if record['status'] in {'closed', 'expired', 'cancelled'}:
            return error_response(
                409, 'request_closed', 'Заявка больше не принимает котировки.',
                {'request_id': request_id, 'status': record['status']})
        deadline = parse_utc(record['response_deadline'])
        now = datetime.now(timezone.utc)
        if now > deadline:
            return error_response(
                409, 'request_closed', 'Срок ответа на заявку истёк.',
                {'request_id': request_id,
                 'response_deadline': record['response_deadline']})
        for index, variant in enumerate(body.variants):
            if parse_utc(variant.valid_until) < deadline + VALID_UNTIL_MARGIN:
                return error_response(
                    422, 'validation_error',
                    'valid_until раньше response_deadline + 60 s.',
                    {'field': f'variants[{index}].valid_until',
                     'min': (deadline + VALID_UNTIL_MARGIN).strftime(
                         '%Y-%m-%dT%H:%M:%SZ')})
        existing = state['quotes'].get(request_id)
        if existing:
            return error_response(
                409, 'quote_exists', 'Активный набор котировок по заявке уже существует.',
                {'request_id': request_id,
                 'quote_id': existing[0]['quote_id']})

        received_at = utc_now()
        stored: list[dict[str, Any]] = []
        for variant in body.variants:
            quote = {
                'quote_id': variant.quote_id,
                'request_id': request_id,
                'variant': variant.variant,
                'status': 'submitted',
                'price_rub': variant.price_rub,
                'duration_hours': variant.duration_hours,
                'route_id': variant.route_id,
                'leg_ids': list(variant.leg_ids),
                'valid_until': variant.valid_until,
                'received_seq': state['next_seq'],
                'received_at': received_at,
            }
            if variant.comment is not None:
                quote['comment'] = variant.comment
            state['next_seq'] += 1
            stored.append(quote)
        state['quotes'][request_id] = stored
        record['status'] = 'quoted'
        await cache.delete(f'quotes:{request_id}', 'requests:list')
        response.status_code = 201
        return {'request_id': request_id,
                'variants': [dict(quote) for quote in stored]}

    # ------------------------------------------------------------- orders

    @app.get('/v1/orders/{order_id}')
    async def get_order(order_id: str) -> dict[str, Any]:
        state = app.state.store
        order = state['orders'].get(order_id)
        if order is None:
            return error_response(404, 'not_found', 'Заказ не найден.',
                                  {'resource': 'order', 'id': order_id})
        cache_key = f'orders:{order_id}'
        cached = await cache.get_json(cache_key)
        if isinstance(cached, dict):
            return cached
        payload = dict(order)
        await cache.set_json(cache_key, payload)
        return payload

    @app.post('/v1/orders/{order_id}/notify', status_code=204)
    async def notify_order(order_id: str, request: Request, body: NotifyBody):
        state = app.state.store
        if body.order_id != order_id:
            return error_response(
                422, 'validation_error',
                'order_id в теле не совпадает с order_id в пути.',
                {'path': order_id, 'body': body.order_id})

        event = getattr(body, 'event', None)
        key: tuple[Any, ...]
        if event is None:
            # Событие доставки: идемпотентность — (order_id, subcontract_id).
            if order_id not in state['orders']:
                return error_response(404, 'not_found', 'Заказ не найден.',
                                      {'resource': 'order', 'id': order_id})
            key = ('delivery', order_id, body.subcontract_id)
        else:
            # order_won/order_lost: идемпотентность — по Idempotency-Key,
            # канон ключа задаёт портал; заказ команде может быть ещё неизвестен.
            idem = (request.headers.get('Idempotency-Key') or '').strip()
            if not idem:
                return error_response(
                    400, 'bad_request',
                    'Для события order_won/order_lost обязателен '
                    'заголовок Idempotency-Key.',
                    {'header': 'Idempotency-Key'})
            if not re.fullmatch(NOTIFY_IDEMPOTENCY_KEY, idem):
                return error_response(
                    422, 'validation_error',
                    'Idempotency-Key не соответствует шаблону.',
                    {'header': 'Idempotency-Key',
                     'pattern': 'exchange:order:<order_id>:<event>:v1'})
            key = ('event', order_id, idem)

        fingerprint = canonical(body.model_dump(exclude_none=True))
        seen = state['notifications'].get(key)
        if seen is not None:
            if seen == fingerprint:
                return Response(status_code=204)
            return error_response(
                409, 'notification_conflict',
                'Уведомление по этому ключу уже принято с другим телом.',
                {'order_id': order_id,
                 **({'subcontract_id': body.subcontract_id}
                    if getattr(body, 'subcontract_id', None) else {})})
        # Событие фиксируем, но статус/доставку/баллы не меняем: они системные.
        state['notifications'][key] = fingerprint
        return Response(status_code=204)

    # ------------------------------------------------------------- points

    @app.get('/v1/points')
    async def list_points() -> list[dict[str, Any]]:
        state = app.state.store
        cache_key = 'points:list'
        cached = await cache.get_json(cache_key)
        if isinstance(cached, list):
            return cached
        items = [dict(entry) for entry in state['points']]
        await cache.set_json(cache_key, items)
        return items

    # ------------------------------------------------------------- routing

    @app.get('/v1/routing/search')
    async def routing_search(
            origin: str, destination: str,
            allowed_modes: str | None = None,
            max_legs: int = 3,
            max_routes: int = 10):
        """Служебный поиск маршрута по справочникам (итеративный DFS).

        Ограничение времени работы: 25 секунд.
        """
        modes = ([m.strip() for m in allowed_modes.split(',') if m.strip()]
                 if allowed_modes else None)
        max_legs = max(1, min(int(max_legs), 3))
        max_routes = max(1, min(int(max_routes), 20))
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    find_routes_iterative_dfs,
                    origin, destination, modes,
                    max_legs=max_legs,
                    time_limit_s=DEFAULT_TIME_LIMIT_S,
                    max_routes=max_routes,
                ),
                timeout=DEFAULT_TIME_LIMIT_S,
            )
        except asyncio.TimeoutError:
            return error_response(
                503, 'service_unavailable',
                f'Поиск маршрута превысил лимит {int(DEFAULT_TIME_LIMIT_S)} с.',
                {'origin': origin, 'destination': destination})

        routes = []
        for item in result['routes']:
            routes.append({
                'route_id': item['route_id'],
                'leg_ids': item['leg_ids'],
                'mode': item['mode'],
                'total_duration_h': item['total_duration_h'],
                'total_cost_per_ton_rub': item['total_cost_per_ton_rub'],
            })
        return {
            'origin': origin,
            'destination': destination,
            'allowed_modes': modes,
            'timed_out': result['timed_out'],
            'visited_states': result['visited_states'],
            'routes': routes,
        }

    return app


app = create_app()
