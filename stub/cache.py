"""Опциональный Redis-кэш для reference stub.

Кэш выключен по умолчанию: если ``REDIS_URL`` не задан или Redis недоступен,
приложение работает как раньше на in-memory состоянии.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - зависимость может отсутствовать в окружении
    Redis = None  # type: ignore[assignment]


class Cache:
    """Минимальный async-кэш с graceful fallback."""

    def __init__(self, url: str | None = None, ttl: int | None = None) -> None:
        self.url = (url if url is not None else os.getenv('REDIS_URL', '')).strip()
        raw_ttl = os.getenv('REDIS_TTL', '' if ttl is None else str(ttl)).strip()
        self.ttl = int(raw_ttl) if raw_ttl else 60
        self._client: Redis | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def connect(self) -> None:
        if not self.url or Redis is None:
            self._client = None
            return
        client = Redis.from_url(self.url, decode_responses=True)
        try:
            await client.ping()
        except Exception:
            await client.aclose()
            self._client = None
            return
        self._client = client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            value = await self._client.get(key)
        except Exception:
            return None
        if value is None:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        if self._client is None:
            return
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            await self._client.set(key, payload, ex=ttl or self.ttl)
        except Exception:
            return

    async def delete(self, *keys: str) -> None:
        if self._client is None or not keys:
            return
        try:
            await self._client.delete(*keys)
        except Exception:
            return

    async def delete_prefix(self, prefix: str) -> None:
        """Удалить ключи по префиксу. Использует SCAN, не блокирует Redis."""
        if self._client is None:
            return
        try:
            batch: list[str] = []
            async for key in self._client.scan_iter(match=f'{prefix}*', count=100):
                batch.append(key)
                if len(batch) >= 100:
                    await self._client.delete(*batch)
                    batch.clear()
            if batch:
                await self._client.delete(*batch)
        except Exception:
            return
