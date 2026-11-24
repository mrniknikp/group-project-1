#!/usr/bin/env python3
"""Веб-просмотрщик API на Swagger UI и Redoc.

    python3 api/viewer.py            # http://localhost:8000
    python3 api/viewer.py 9000       # другой порт

Поднимает FastAPI-приложение, которое отдаёт:
  /              — Swagger UI со списком методов;
  /redoc         — Redoc с тем же контрактом;
  /openapi.json  — спецификация из api/openapi.yaml.

Спецификация читается при каждом запросе /openapi.json, поэтому правки YAML
подхватываются перезагрузкой страницы без перезапуска сервера.
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / 'openapi.yaml'


def load_spec() -> dict:
    with SPEC_PATH.open(encoding='utf-8') as fh:
        return yaml.safe_load(fh)


app = FastAPI(
    title='Team Service API',
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get('/openapi.json', include_in_schema=False)
def openapi_json() -> JSONResponse:
    return JSONResponse(load_spec())


@app.get('/', include_in_schema=False)
def docs():
    return get_swagger_ui_html(
        openapi_url='/openapi.json',
        title='Team Service API — документация',
        swagger_ui_parameters={
            'defaultModelsExpandDepth': 1,
            'docExpansion': 'list',
            'filter': True,
            'tryItOutEnabled': True,
        },
    )


@app.get('/redoc', include_in_schema=False)
def redoc():
    return get_redoc_html(
        openapi_url='/openapi.json',
        title='Team Service API — Redoc',
    )


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f'Swagger UI: http://localhost:{port}/  Redoc: http://localhost:{port}/redoc  (Ctrl+C — остановить)')
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
