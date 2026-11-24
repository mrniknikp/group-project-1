"""Поиск маршрутов по справочникам dictionaries/routes.

Требования: итеративный DFS, ограничение времени работы API — 25 секунд.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = ROOT / 'dictionaries' / 'routes'
MODES = ('road', 'rail', 'air', 'sea')
DEFAULT_TIME_LIMIT_S = 25.0
MAX_LEGS = 3

_GRAPH_CACHE: dict[str, dict[str, list[dict[str, Any]]]] = {}


def _load_mode_graph(mode: str) -> dict[str, list[dict[str, Any]]]:
    """Загрузить граф сегментов для mode: from -> [{to, route_id, ...}]."""
    cached = _GRAPH_CACHE.get(mode)
    if cached is not None:
        return cached
    path = ROUTES_DIR / f'routes_{mode}.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    graph: dict[str, list[dict[str, Any]]] = {}
    for seg in data.get('routes', []):
        src = str(seg['from'])
        dst = str(seg['to'])
        edge = {
            'from': src,
            'to': dst,
            'route_id': f'{mode}:{src}-{dst}',
            'distance_km': seg.get('distance_km'),
            'duration_h': seg.get('duration_h'),
            'cost_per_ton_rub': seg.get('cost_per_ton_rub'),
        }
        reverse = {
            'from': dst,
            'to': src,
            'route_id': f'{mode}:{dst}-{src}',
            'distance_km': seg.get('distance_km'),
            'duration_h': seg.get('duration_h'),
            'cost_per_ton_rub': seg.get('cost_per_ton_rub'),
        }
        graph.setdefault(src, []).append(edge)
        graph.setdefault(dst, []).append(reverse)
    # Детерминированный порядок обхода.
    for neighbors in graph.values():
        neighbors.sort(key=lambda item: (item['to'], item['route_id']))
    _GRAPH_CACHE[mode] = graph
    return graph


def clear_graph_cache() -> None:
    _GRAPH_CACHE.clear()


def find_routes_iterative_dfs(
    origin: str,
    destination: str,
    allowed_modes: list[str] | None = None,
    *,
    max_legs: int = MAX_LEGS,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    max_routes: int = 10,
) -> dict[str, Any]:
    """Найти маршруты итеративным DFS.

    Возвращает dict с ключами:
    - routes: список маршрутов [{route_id, leg_ids, mode, legs, total_duration_h, total_cost_per_ton_rub}]
    - timed_out: достигнут ли лимит времени
    - visited_states: число раскрытых состояний (для диагностики)
    """
    if origin == destination:
        return {'routes': [], 'timed_out': False, 'visited_states': 0}

    modes = [m for m in (allowed_modes or list(MODES)) if m in MODES]
    deadline = time.monotonic() + max(0.0, float(time_limit_s))
    found: list[dict[str, Any]] = []
    visited_states = 0

    for mode in modes:
        if time.monotonic() >= deadline:
            return {'routes': found[:max_routes], 'timed_out': True,
                    'visited_states': visited_states}
        graph = _load_mode_graph(mode)
        if origin not in graph:
            continue

        # Стек содержит состояние: (node, path_edges, visited_nodes, next_idx)
        # path_edges - список ребер в текущем пути.
        stack: list[dict[str, Any]] = [{
            'node': origin,
            'path': [],
            'visited': {origin},
            'next_idx': 0,
        }]

        while stack:
            if time.monotonic() >= deadline:
                return {'routes': found[:max_routes], 'timed_out': True,
                        'visited_states': visited_states}

            state = stack[-1]
            node = state['node']
            path = state['path']
            visited = state['visited']
            idx = state['next_idx']
            neighbors = graph.get(node, [])

            if idx >= len(neighbors) or len(path) >= max_legs:
                stack.pop()
                continue

            state['next_idx'] = idx + 1
            edge = neighbors[idx]
            nxt = edge['to']
            if nxt in visited:
                continue

            visited_states += 1
            new_path = [*path, edge]

            if nxt == destination:
                total_duration = round(sum(float(e.get('duration_h') or 0) for e in new_path), 2)
                total_cost = sum(int(e.get('cost_per_ton_rub') or 0) for e in new_path)
                route = {
                    'route_id': new_path[0]['route_id'],
                    'leg_ids': [e['route_id'] for e in new_path],
                    'mode': mode,
                    'legs': new_path,
                    'total_duration_h': total_duration,
                    'total_cost_per_ton_rub': total_cost,
                }
                found.append(route)
                if len(found) >= max_routes:
                    return {'routes': found[:max_routes], 'timed_out': False,
                            'visited_states': visited_states}
                continue

            if len(new_path) < max_legs:
                stack.append({
                    'node': nxt,
                    'path': new_path,
                    'visited': visited | {nxt},
                    'next_idx': 0,
                })

    return {'routes': found[:max_routes], 'timed_out': False,
            'visited_states': visited_states}
