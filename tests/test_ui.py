"""Тесты веб-панели API: страница, прокси вызовов и ограничения пути."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api import ui
from api.ui import CallRequest


def call(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://console') as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_run())


def test_index_serves_console_page():
    response = call(ui.app, 'GET', '/')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'Соборный коммит' in response.text
    assert '/api/call' in response.text
    assert 'price_rub' in response.text
    assert 'duration_hours' in response.text


def test_proxy_returns_status_time_and_body(monkeypatch: pytest.MonkeyPatch):
    expected = {'status': 200, 'time_ms': 12.5, 'url': 'http://x/v1/requests', 'body': []}

    def fake_perform(payload: CallRequest) -> dict:
        assert payload.path == '/v1/requests'
        assert payload.api_key == 'test-key'
        return expected

    monkeypatch.setattr(ui, 'perform_call', fake_perform)
    response = call(ui.app, 'POST', '/api/call',
                    json={'method': 'GET', 'path': '/v1/requests'})
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize('method,path', [
    ('PATCH', '/v1/requests'),
    ('GET', '/admin/secrets'),
    ('GET', 'http://example.com/v1/requests'),
])
def test_proxy_rejects_unsafe_requests(method: str, path: str):
    response = call(ui.app, 'POST', '/api/call', json={'method': method, 'path': path})
    assert response.status_code == 400
    assert response.json()['detail']


@pytest.mark.parametrize('path', [
    '/healthz',
    '/v1/requests',
    '/v1/requests/req_5001/quotes',
    '/v1/routing/search?origin=moscow&destination=kazan',
])
def test_validate_allows_contract_paths(path: str):
    request = CallRequest(method='GET', path=path)
    assert ui._validate(request).endswith(path)


def test_generated_ids_have_required_underscore():
    """Регрессия: без подчёркивания API возвращает 422 на req_/quo_."""
    import re

    from api.ui import PAGE_PATH

    html = PAGE_PATH.read_text(encoding='utf-8')
    definition = re.search(r'const randomId = .*?;', html).group(0)
    assert "prefix + '_'" in definition
    assert 'details' in html and 'fields' in html  # ошибки валидации видны в панели
