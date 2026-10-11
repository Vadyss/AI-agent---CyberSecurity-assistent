# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from pathlib import Path
import pytest
from gatekeeper.policy import load_policy
from gatekeeper.types import Plan

@pytest.fixture
def gk_policy():
    return load_policy(Path(__file__).resolve().parents[2] / 'policy/policy.yaml')

@pytest.fixture
def gk_manifests():
    base = {'version': 1, 'description': 'Pure skill.', 'entrypoint': 'run', 'imports': [], 'permissions': {'network': False, 'filesystem': False, 'subprocess': False}}
    return {
        'ssh_parser': {**base, 'name': 'ssh_parser', 'kind': 'parser', 'inputs': 'lines', 'outputs': ['src_ip', 'user', 'host', 'outcome'], 'params': {}, 'log_sources': ['ssh']},
        'count_window': {**base, 'name': 'count_window', 'kind': 'aggregation', 'inputs': 'events', 'outputs': ['count'], 'params': {'group_by': {'type': 'string|string[]', 'required': True}, 'window_s': {'type': 'int', 'required': True, 'min': 10, 'max': 86400}, 'ts_field': {'type': 'string', 'required': False, 'default': 'ts'}}},
    }

@pytest.fixture
def gk_catalog(gk_manifests):
    return {'skills': list(gk_manifests.values()), 'log_sources': {'ssh': {'description': 'SSH'}}, 'attack_types': {'ssh_bruteforce': {'log_source': 'ssh'}}}

@pytest.fixture
def gk_plan():
    return Plan(intent='detection_rule', log_source='ssh', attack_type='ssh_bruteforce', skills=[{'name': 'ssh_parser', 'role': 'parser', 'status': 'existing'}, {'name': 'count_window', 'role': 'aggregation', 'status': 'existing'}])

@pytest.fixture
def gk_recipe():
    return {'name': 'ssh_bruteforce', 'attack_type': 'ssh_bruteforce', 'parser': 'ssh_parser', 'filter': [{'field': 'outcome', 'op': 'eq', 'value': 'failure'}], 'aggregation': {'skill': 'count_window', 'params': {'group_by': 'src_ip', 'window_s': 300}}, 'condition': {'field': 'count', 'op': 'gte', 'value': 5}}
