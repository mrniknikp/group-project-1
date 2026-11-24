"""Ключевые инварианты справочников dictionaries/.

Логика — в ``scripts/check_dictionaries.py``; здесь только проверки,
которые должны падать при порче данных, без дублирования валидатора.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_dictionaries as cd  # noqa: E402

LOCATIONS = cd.load_locations()
MODES = cd.load_modes()
ROUTES = {mode: cd.load_routes(mode) for mode in cd.MODE_FILES}


def assert_ok(errors: list[str]) -> None:
    assert errors == [], '\n'.join(errors)


def test_dictionary_files_are_present():
    expected = [
        'dictionaries/README.md',
        'dictionaries/locations.yaml',
        'dictionaries/modes.yaml',
        'dictionaries/routes/README.md',
        *[f'dictionaries/routes/{name}' for name in cd.MODE_FILES.values()],
    ]
    assert len(expected) == 8
    assert [path for path in expected if not (ROOT / path).is_file()] == []


def test_locations_count_and_unique_ids():
    assert len(LOCATIONS) == cd.EXPECTED_LOCATIONS
    assert len({loc['id'] for loc in LOCATIONS}) == len(LOCATIONS)
    assert_ok(cd.check_locations(LOCATIONS))


def test_modes_are_exactly_the_route_networks():
    assert len(MODES) == cd.EXPECTED_MODES
    assert {mode['id'] for mode in MODES} == set(cd.MODE_FILES)
    assert_ok(cd.check_modes(MODES))


def test_segment_counts_and_total():
    assert {mode: len(doc['routes']) for mode, doc in ROUTES.items()} == cd.EXPECTED_SEGMENTS
    assert sum(len(doc['routes']) for doc in ROUTES.values()) == cd.TOTAL_SEGMENTS


@pytest.mark.parametrize('mode', sorted(cd.MODE_FILES))
def test_route_invariants(mode):
    by_id = {loc['id']: loc for loc in LOCATIONS}
    assert_ok(cd.check_route_document(mode, ROUTES[mode], by_id))


def test_reversed_duplicate_pair_is_rejected():
    by_id = {loc['id']: loc for loc in LOCATIONS}
    doc = copy.deepcopy(ROUTES['road'])
    route = doc['routes'][0]
    doc['routes'].append({**route, 'from': route['to'], 'to': route['from']})
    errors = cd.check_route_document('road', doc, by_id)
    assert any('дубль пары' in error for error in errors)


def test_formula_mismatch_is_rejected():
    by_id = {loc['id']: loc for loc in LOCATIONS}
    doc = copy.deepcopy(ROUTES['sea'])
    doc['routes'][0]['duration_h'] = round(doc['routes'][0]['duration_h'] + 1, 1)
    errors = cd.check_route_document('sea', doc, by_id)
    assert any('по формуле' in error for error in errors)


def test_wrong_schema_version_is_rejected():
    doc = copy.deepcopy(ROUTES['rail'])
    doc['schema_version'] = 2
    assert cd.check_version(doc, 'routes_rail.yaml')


def test_connectivity_and_component_counts():
    by_id = {loc['id']: loc for loc in LOCATIONS}
    assert_ok(cd.check_connectivity(ROUTES, by_id))
    for mode in ('air', 'sea'):
        assert len(cd.graph_components(cd.build_graph(ROUTES[mode]))) == 1
    graph = cd.build_graph({'routes': [r for doc in ROUTES.values() for r in doc['routes']]})
    assert len(cd.reachable(graph, 'moscow')) == len(LOCATIONS)
