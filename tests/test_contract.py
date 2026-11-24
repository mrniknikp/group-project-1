"""Conformance-тесты: checker проходит stub и ловит нарушения контракта.

Stub поднимается через httpx ASGI transport — сеть и порт не нужны. Отдельный
тест проверяет, что checker действительно падает на лишнем поле, а не
«зеленеет» всегда.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import FastAPI
from starlette.responses import Response

from contract import check_service
from contract.check_service import Report, run_checks
from stub.app import create_app

API_KEY = 'test-key'


def run(app: FastAPI, **kwargs) -> Report:
    async def _run() -> Report:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url='http://stub') as client:
            return await run_checks(api_key=API_KEY, client=client, **kwargs)

    return asyncio.run(_run())


def add_json_middleware(app: FastAPI, predicate, mutate) -> FastAPI:
    """Подменить JSON-ответ, чтобы сымитировать нарушение контракта."""

    @app.middleware('http')
    async def middleware(request, call_next):
        response = await call_next(request)
        if not predicate(request):
            return response
        body = b''.join([chunk async for chunk in response.body_iterator])
        try:
            data = json.loads(body)
        except ValueError:
            return response
        mutate(data)
        payload = json.dumps(data).encode()
        headers = dict(response.headers)
        headers['content-length'] = str(len(payload))
        return Response(payload, status_code=response.status_code,
                        headers=headers, media_type='application/json')

    return app


# ------------------------------------------------------------ stub проходит

def test_stub_passes_conformance():
    report = run(create_app(API_KEY), order_id='ord_6003')
    assert report.ok, report.render()
    names = {result.name for result in report.results}
    assert names == {'spec', 'healthz', 'auth', 'bad_request', 'not_found',
                     'requests', 'validation', 'quotes', 'order', 'notify',
                     'read_only', 'points'}


def test_order_id_is_discovered_from_points():
    report = run(create_app(API_KEY))
    assert report.ok, report.render()


def test_skip_notify_skips_only_notify():
    report = run(create_app(API_KEY), order_id='ord_6003', skip_notify=True)
    assert report.ok, report.render()
    notify = [item for item in report.results if item.name == 'notify']
    assert len(notify) == 1
    assert 'пропущено' in notify[0].detail


# -------------------------------------------------------- нарушения ловятся

def test_checker_detects_extra_cost_field():
    app = create_app(API_KEY)
    add_json_middleware(
        app,
        lambda request: (request.method == 'GET'
                         and request.url.path.endswith('/quotes')),
        lambda data: [item.__setitem__('cost_rub', 1) for item in data
                      if isinstance(item, dict)],
    )
    report = run(app, order_id='ord_6003')
    assert not report.ok
    errors = '\n'.join(report.errors)
    assert 'cost_rub' in errors
    assert any(item.name == 'quotes' and not item.ok
               for item in report.results)


def test_checker_detects_wrong_order_status():
    app = create_app(API_KEY)
    add_json_middleware(
        app,
        lambda request: (request.method == 'GET'
                         and request.url.path.startswith('/v1/orders/')),
        lambda data: data.__setitem__('status', 'cancelled')
        if isinstance(data, dict) else None,
    )
    report = run(app, order_id='ord_6003')
    assert not report.ok
    assert 'OrderStatus' in '\n'.join(report.errors)


def test_checker_detects_mutating_endpoint():
    app = create_app(API_KEY)

    @app.patch('/v1/orders/{order_id}')
    async def mutate_order(order_id: str) -> dict:
        return {'order_id': order_id, 'status': 'delivered'}

    report = run(app, order_id='ord_6003')
    assert not report.ok
    assert any(item.name == 'read_only' and not item.ok
               for item in report.results)
    assert 'PATCH /v1/orders' in '\n'.join(report.errors)


def test_checker_detects_unsupported_order_won():
    app = create_app(API_KEY)

    @app.middleware('http')
    async def middleware(request, call_next):
        key = request.headers.get('Idempotency-Key', '')
        if request.url.path.endswith('/notify') and ':won:v1' in key:
            return Response('{"code":"not_implemented","message":"нет"}',
                            status_code=501, media_type='application/json')
        return await call_next(request)

    report = run(app, order_id='ord_6003')
    assert not report.ok
    notify = [item for item in report.results if item.name == 'notify']
    assert notify and not notify[0].ok
    assert 'order_won' in notify[0].detail


# ---------------------------------------------------------------------- CLI

def test_cli_exit_code_follows_report(monkeypatch):
    async def failing(**_kwargs):
        report = Report()
        report.add('spec', False, 'сломано')
        return report

    async def passing(**_kwargs):
        report = Report()
        report.add('spec', True)
        return report

    argv = ['--url', 'http://127.0.0.1:9', '--api-key', 'k']
    monkeypatch.setattr(check_service, 'run_checks', failing)
    assert check_service.main(argv) == 1
    monkeypatch.setattr(check_service, 'run_checks', passing)
    assert check_service.main(argv) == 0


def test_runner_entry_point_points_to_main():
    from scripts.check_service import main as wrapper_main

    assert wrapper_main is check_service.main
