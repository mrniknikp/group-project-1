"""Удобная веб-панель для ручного вызова API команды «Соборный коммит».

    python3 api/ui.py            # http://localhost:8001
    python3 api/ui.py 9001       # другой порт

Панель не знает «правильных» ответов: она отправляет введённые данные в API
и показывает то, что реально вернул сервер — статус, время ответа и тело,
в том числе цену (`price_rub`) и срок (`duration_hours`) из котировок.

Вызовы идут через серверную часть (`POST /api/call`), поэтому:
- не нужна поддержка CORS на целевом API;
- ключ команды не светится в адресной строке;
- можно дернуть любой адрес, разрешённый настройками (по умолчанию
  `http://31.172.73.114:8080`).
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
PAGE_PATH = ROOT / 'ui.html'

DEFAULT_BASE_URL = 'http://31.172.73.114:8080'
DEFAULT_API_KEY = 'test-key'
DEFAULT_TIMEOUT_S = 25.0
ALLOWED_METHODS = frozenset({'GET', 'POST', 'PUT', 'DELETE'})
# Панель работает только с контрактными путями и служебным поиском маршрута.
SAFE_PATH = re.compile(
    r'^/(?:healthz|db-check|v1(?:/|$))'
)


class CallRequest(BaseModel):
    method: str = Field(default='GET', description='HTTP-метод')
    path: str = Field(description='Путь и query-строка, например /v1/requests')
    base_url: str = Field(default=DEFAULT_BASE_URL, description='Адрес API')
    api_key: str = Field(default=DEFAULT_API_KEY, description='Значение X-Api-Key')
    body: dict[str, Any] | None = Field(default=None, description='Тело запроса JSON')
    headers: dict[str, str] | None = Field(default=None, description='Дополнительные заголовки')
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, ge=1, le=120)


def _validate(call: CallRequest) -> str:
    """Проверить параметры вызова и вернуть итоговый URL."""
    method = call.method.strip().upper()
    if method not in ALLOWED_METHODS:
        raise ValueError(f'метод {method!r} не разрешён, используйте GET/POST')

    base = call.base_url.strip().rstrip('/')
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in {'http', 'https'} or not parts.netloc:
        raise ValueError(f'base_url {base!r} должен быть http(s)-адресом')

    path = call.path.strip()
    if not path.startswith('/'):
        raise ValueError(f'путь {path!r} должен начинаться со слеша')
    if not SAFE_PATH.match(urllib.parse.urlsplit(path).path):
        raise ValueError(f'путь {path!r} не разрешён панелью')

    return f'{base}{path}'


def perform_call(call: CallRequest) -> dict[str, Any]:
    """Синхронно выполнить HTTP-вызов и вернуть статус, время и тело."""
    url = _validate(call)
    method = call.method.strip().upper()

    headers = {'Accept': 'application/json'}
    if call.api_key:
        headers['X-Api-Key'] = call.api_key
    for name, value in (call.headers or {}).items():
        if name.lower() not in {'host', 'content-length', 'connection'}:
            headers[name] = value

    data: bytes | None = None
    if call.body is not None:
        data = json.dumps(call.body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=float(call.timeout_s)) as response:
            raw = response.read().decode('utf-8', errors='replace')
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            'status': 0,
            'time_ms': elapsed_ms,
            'url': url,
            'body': {'code': 'network_error', 'message': str(exc)},
        }

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    try:
        body: Any = json.loads(raw) if raw else None
    except ValueError:
        body = raw
    return {'status': status, 'time_ms': elapsed_ms, 'url': url, 'body': body}


app = FastAPI(
    title='Team Service API — панель',
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get('/', response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE_PATH.read_text(encoding='utf-8'))


@app.post('/api/call', include_in_schema=False)
def api_call(payload: CallRequest) -> JSONResponse:
    try:
        return JSONResponse(perform_call(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f'Панель API: http://localhost:{port}  (Ctrl+C — остановить)')
    print(f'Целевой API по умолчанию: {DEFAULT_BASE_URL}')
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
