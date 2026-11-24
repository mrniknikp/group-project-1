"""Компактная проверка справочников ``dictionaries/``.

Инварианты: схема и типы, уникальность id и пар, ожидаемые размеры,
физическая осмысленность чисел, формулы из описания и связность графов.
Запуск: ``python3 scripts/check_dictionaries.py`` (код выхода 1 при ошибке).
Только стандартная библиотека и PyYAML.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DICTIONARIES = ROOT / 'dictionaries'
ROUTES_DIR = DICTIONARIES / 'routes'
MODE_FILES = {'road': 'routes_road.yaml', 'rail': 'routes_rail.yaml',
              'air': 'routes_air.yaml', 'sea': 'routes_sea.yaml'}
MODE_FLAG = {'road': 'road_access', 'rail': 'railway',
             'air': 'airport', 'sea': 'port'}
EXPECTED_SEGMENTS = {'road': 119, 'rail': 106, 'air': 200, 'sea': 112}
TOTAL_SEGMENTS = 537
EXPECTED_LOCATIONS = 100
EXPECTED_MODES = 4
DISTANCE_TOLERANCE_KM = 1.0       # distance_km — целые км
DURATION_TOLERANCE_H = 0.06       # duration_h — точность 0.1 ч
COST_TOLERANCE_RUB = 0.5          # cost_per_ton_rub — целые рубли
MAX_DISTANCE_KM = 20_000
MAX_DURATION_H = 1_000
MAX_COST_RUB = 2_000_000
LOCATION_FIELDS = {'id', 'name_ru', 'name_en', 'country_code', 'country_ru',
                   'lat', 'lon', 'port', 'airport', 'railway', 'road_access'}
MODE_FIELDS = {'id', 'name_ru', 'name_en', 'description'}
ROUTE_FIELDS = {'from', 'to', 'distance_km', 'duration_h', 'cost_per_ton_rub'}
#: «Время: distance/65 + 2 ч. Стоимость: 2000 + 11 RUB за тонно-км.»
FORMULA_RE = re.compile(
    r'Время:\s*distance/(?P<speed>\d+(?:[.,]\d+)?)\s*\+\s*(?P<fixed>\d+(?:[.,]\d+)?)'
    r'.*?Стоимость:\s*(?P<base>\d+(?:[.,]\d+)?)\s*\+\s*(?P<rate>\d+(?:[.,]\d+)?)\s*RUB',
    re.DOTALL)

def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding='utf-8'))

def load_locations() -> list[dict[str, Any]]:
    return list(load_yaml(DICTIONARIES / 'locations.yaml')['locations'])

def load_modes() -> list[dict[str, Any]]:
    return list(load_yaml(DICTIONARIES / 'modes.yaml')['modes'])

def load_routes(mode: str) -> dict[str, Any]:
    return load_yaml(ROUTES_DIR / MODE_FILES[mode])

def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def great_circle(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Расстояние по большой окружности между локациями, км."""
    p1, p2 = math.radians(a['lat']), math.radians(b['lat'])
    dphi, dlambda = p2 - p1, math.radians(b['lon'] - a['lon'])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))

def build_graph(doc: dict[str, Any]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for route in doc['routes']:
        graph[route['from']].add(route['to'])
        graph[route['to']].add(route['from'])
    return dict(graph)

def reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen, stack = {start}, [start]
    while stack:
        for neighbour in graph.get(stack.pop(), ()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen

def graph_components(graph: dict[str, set[str]]) -> list[set[str]]:
    parts, unseen = [], set(graph)
    while unseen:
        part = reachable(graph, min(unseen))
        parts.append(part)
        unseen -= part
    return parts

def parse_formula(description: str) -> tuple[float, float, float, float] | None:
    """Разобрать формулы из описания: (скорость, фикс. ч, база ₽, ₽/км)."""
    match = FORMULA_RE.search(description or '')
    if match is None:
        return None
    speed, fixed, base, rate = (float(match.group(name).replace(',', '.'))
                                for name in ('speed', 'fixed', 'base', 'rate'))
    return speed, fixed, base, rate

def check_version(doc: Any, label: str) -> list[str]:
    ok = isinstance(doc, dict) and doc.get('schema_version') == 3
    return [] if ok else [f'{label}: ожидался объект со schema_version=3']

def check_locations(locations: list[dict[str, Any]]) -> list[str]:
    errors = [] if len(locations) == EXPECTED_LOCATIONS else [
        f'locations.yaml: {len(locations)} локаций, ожидалось {EXPECTED_LOCATIONS}']
    ids = [loc.get('id') for loc in locations if isinstance(loc, dict)]
    errors += [f'locations.yaml: дубль id {key!r} ({count})'
               for key, count in Counter(ids).items() if count > 1]
    for loc in locations:
        if not isinstance(loc, dict):
            errors.append('locations.yaml: элемент не объект')
            continue
        name = loc.get('id', '<без id>')
        if LOCATION_FIELDS - set(loc):
            errors.append(f'{name}: нет полей {sorted(LOCATION_FIELDS - set(loc))}')
        errors += [f'{name}: {field} должен быть непустой строкой'
                   for field in ('id', 'name_ru', 'name_en', 'country_ru')
                   if not isinstance(loc.get(field), str) or not loc[field].strip()]
        code = loc.get('country_code')
        if not (isinstance(code, str) and len(code) == 2 and code.isalpha() and code.isupper()):
            errors.append(f'{name}: country_code={code!r}, нужен код из двух заглавных букв')
        for field, low, high in (('lat', -90, 90), ('lon', -180, 180)):
            value = loc.get(field)
            if not is_number(value) or not low <= value <= high:
                errors.append(f'{name}: {field}={value!r} вне диапазона')
        errors += [f'{name}: {flag} должен быть bool'
                   for flag in MODE_FLAG.values() if not isinstance(loc.get(flag), bool)]
    return errors

def check_modes(modes: list[dict[str, Any]]) -> list[str]:
    errors = [] if len(modes) == EXPECTED_MODES else [
        f'modes.yaml: {len(modes)} режимов, ожидалось {EXPECTED_MODES}']
    ids = [mode.get('id') for mode in modes if isinstance(mode, dict)]
    if set(ids) != set(MODE_FILES):
        errors.append(f'modes.yaml: id {sorted(ids)} не совпадают с маршрутными файлами')
    for mode in modes:
        if not isinstance(mode, dict):
            errors.append('modes.yaml: элемент не объект')
            continue
        errors += [f'{mode.get("id")}: {field} должен быть непустой строкой'
                   for field in MODE_FIELDS
                   if not isinstance(mode.get(field), str) or not mode[field].strip()]
    return errors

def check_route_document(
    mode: str, doc: dict[str, Any], locations: dict[str, dict[str, Any]]
) -> list[str]:
    label = MODE_FILES[mode]
    errors = check_version(doc, label)
    if not isinstance(doc, dict):
        return errors
    meta = {'mode': mode, 'bidirectional': True, 'distance_unit': 'km',
            'duration_unit': 'h', 'cost_unit': 'RUB/ton'}
    errors += [f'{label}: {field}={doc.get(field)!r}, ожидалось {want!r}'
               for field, want in meta.items() if doc.get(field) != want]
    routes = doc.get('routes')
    if not isinstance(routes, list):
        return errors + [f'{label}: routes должен быть списком']
    if len(routes) != EXPECTED_SEGMENTS[mode]:
        errors.append(f'{label}: {len(routes)} сегментов, ожидалось {EXPECTED_SEGMENTS[mode]}')
    formula, seen = parse_formula(doc.get('description', '')), set()
    for route in routes:
        tag = f'{label}: {route.get("from")}–{route.get("to")}' if isinstance(route, dict) else label
        if not isinstance(route, dict) or set(route) != ROUTE_FIELDS:
            errors.append(f'{tag}: ожидался объект с полями {sorted(ROUTE_FIELDS)}')
            continue
        start, end = route['from'], route['to']
        if not isinstance(start, str) or not isinstance(end, str):
            errors.append(f'{tag}: from/to должны быть строками')
            continue
        if start == end:
            errors.append(f'{tag}: сегмент в ту же локацию')
        if start not in locations or end not in locations:
            errors.append(f'{tag}: неизвестная локация')
            continue
        pair = tuple(sorted((start, end)))
        if pair in seen:
            errors.append(f'{tag}: дубль пары (в любом направлении)')
        seen.add(pair)
        if not all(is_number(route[field]) for field in
                   ('distance_km', 'duration_h', 'cost_per_ton_rub')):
            errors.append(f'{tag}: distance_km/duration_h/cost_per_ton_rub — не числа')
            continue
        for field, cap in (('distance_km', MAX_DISTANCE_KM),
                           ('duration_h', MAX_DURATION_H),
                           ('cost_per_ton_rub', MAX_COST_RUB)):
            if not 0 < route[field] <= cap:
                errors.append(f'{tag}: {field}={route[field]!r} вне (0, {cap}]')
        direct = great_circle(locations[start], locations[end])
        if route['distance_km'] < direct - DISTANCE_TOLERANCE_KM:
            errors.append(f'{tag}: distance_km={route["distance_km"]} короче прямой {direct:.0f} км')
        if formula:
            speed, fixed, base, rate = formula
            for field, want, tolerance in (
                ('duration_h', route['distance_km'] / speed + fixed, DURATION_TOLERANCE_H),
                ('cost_per_ton_rub', base + rate * route['distance_km'], COST_TOLERANCE_RUB),
            ):
                if abs(route[field] - want) > tolerance:
                    errors.append(f'{tag}: {field}={route[field]}, по формуле {want:.1f}')
    return errors

def check_connectivity(
    all_routes: dict[str, dict[str, Any]], locations: dict[str, dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    combined: dict[str, set[str]] = defaultdict(set)
    covered: set[str] = set()
    for mode, doc in all_routes.items():
        label = MODE_FILES[mode]
        graph = build_graph(doc)
        covered |= set(graph)
        if any(not graph[node] for node in graph):
            errors.append(f'{label}: есть изолированные узлы')
        parts = graph_components(graph)
        if mode in ('air', 'sea') and len(parts) != 1:
            errors.append(f'{label}: ожидалась одна компонента, получено {len(parts)}')
        for route in doc['routes']:
            combined[route['from']].add(route['to'])
            combined[route['to']].add(route['from'])
    if covered != set(locations):
        errors.append(f'локации без единого сегмента: {sorted(set(locations) - covered)}')
    unreachable = sorted(set(locations) - reachable(combined, 'moscow'))
    if unreachable:
        errors.append(f'никаким видом транспорта не достижимы из Москвы: {unreachable}')
    return errors

def all_checks() -> list[tuple[str, list[str]]]:
    locations_doc = load_yaml(DICTIONARIES / 'locations.yaml')
    modes_doc = load_yaml(DICTIONARIES / 'modes.yaml')
    locations, modes = list(locations_doc['locations']), list(modes_doc['modes'])
    routes = {mode: load_routes(mode) for mode in MODE_FILES}
    by_id = {item['id']: item for item in locations}
    results = [
        ('locations', check_version(locations_doc, 'locations.yaml') + check_locations(locations)),
        ('modes', check_version(modes_doc, 'modes.yaml') + check_modes(modes)),
    ]
    results += [(f'routes:{mode}', check_route_document(mode, routes[mode], by_id))
                for mode in MODE_FILES] + [('connectivity', check_connectivity(routes, by_id))]
    total = sum(len(doc.get('routes', [])) for doc in routes.values())
    results.append(('total', [] if total == TOTAL_SEGMENTS
                    else [f'всего {total} сегментов, ожидалось {TOTAL_SEGMENTS}']))
    return results

def main() -> int:
    try:
        results = all_checks()
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f'Не удалось прочитать справочники: {exc}', file=sys.stderr)
        return 1
    failed = any(errors for _, errors in results)
    for name, errors in results:
        if errors:
            print(f'[{name}] ошибок: {len(errors)}')
            for error in errors:
                print(f'  - {error}')
        else:
            print(f'[{name}] OK')
    print('Справочники в порядке.' if not failed else 'Проверки справочников не пройдены.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
