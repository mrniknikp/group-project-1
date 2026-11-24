"""Тесты поиска маршрута: итеративный DFS и лимит 25 секунд."""

from __future__ import annotations

import time

from stub.routing import find_routes_iterative_dfs


def test_iterative_dfs_finds_road_moscow_kazan():
    result = find_routes_iterative_dfs('moscow', 'kazan', allowed_modes=['road'])
    assert result['timed_out'] is False
    assert result['routes']
    first = result['routes'][0]
    assert first['mode'] == 'road'
    assert first['route_id'] == first['leg_ids'][0]
    assert first['leg_ids'][0].startswith('road:')
    assert first['leg_ids'][-1].endswith('-kazan') or 'kazan' in first['leg_ids'][-1]


def test_iterative_dfs_respects_max_legs():
    result = find_routes_iterative_dfs(
        'moscow', 'kazan', allowed_modes=['road'], max_legs=1, max_routes=10)
    assert result['timed_out'] is False
    # Должен найти только прямой сегмент, если он есть.
    assert any(r['leg_ids'] == ['road:moscow-kazan'] for r in result['routes'])
    assert all(len(r['leg_ids']) <= 1 for r in result['routes'])


def test_iterative_dfs_respects_time_limit():
    start = time.monotonic()
    result = find_routes_iterative_dfs(
        'moscow', 'vladivostok', allowed_modes=['road', 'rail', 'air', 'sea'],
        time_limit_s=0.001, max_routes=100)
    elapsed = time.monotonic() - start
    assert elapsed < 5
    assert result['timed_out'] is True
    assert isinstance(result['routes'], list)


def test_iterative_dfs_returns_empty_for_same_point():
    result = find_routes_iterative_dfs('moscow', 'moscow', allowed_modes=['road'])
    assert result == {'routes': [], 'timed_out': False, 'visited_states': 0}
