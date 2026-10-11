# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from copy import deepcopy
import pytest
from gatekeeper.manifest import validate_manifest
from gatekeeper.types import PlanSkill

@pytest.mark.parametrize('field,value,code', [
('name', '../bad', 'INVALID_NAME'), ('kind', 'parser', 'KIND_MISMATCH'), ('version', 2, 'INVALID_MANIFEST'), ('entrypoint', 'other', 'INVALID_MANIFEST'), ('inputs', 'lines', 'INVALID_MANIFEST'), ('outputs', [], 'INVALID_MANIFEST'), ('outputs', ['Bad'], 'INVALID_MANIFEST'), ('imports', ['os'], 'INVALID_MANIFEST'), ('permissions', {'network': True, 'filesystem': False, 'subprocess': False}, 'NETWORK_NOT_ALLOWED'), ('params', {'exclude_ip': {'type': 'string', 'required': False}}, 'FORBIDDEN_PARAM'), ('params', {'x': {'type': 'object', 'required': True}}, 'INVALID_MANIFEST'), ('params', {'x': {'type': 'int', 'required': True, 'min': 20, 'max': 10}}, 'INVALID_MANIFEST'), ('description', None, 'INVALID_MANIFEST')])
def test_manifest_violations(field, value, code, gk_policy, gk_manifests):
    raw = deepcopy(gk_manifests['count_window'])
    raw[field] = value
    spec = PlanSkill(name='count_window', role='aggregation', status='missing', spec={'outputs': ['count'], 'params': {}})
    assert code in {v.code for v in validate_manifest(raw, spec, gk_policy)[1]}

def test_manifest_normalized(gk_policy, gk_manifests):
    raw = deepcopy(gk_manifests['count_window']); raw['description'] = 'A' * 350
    result, errors = validate_manifest(raw, None, gk_policy)
    assert not errors and len(result['description']) == 300
    assert raw['description'] != result['description']

def test_parser_source_required(gk_policy, gk_manifests):
    raw = deepcopy(gk_manifests['ssh_parser']); raw.pop('log_sources')
    assert validate_manifest(raw, None, gk_policy)[1][0].code == 'INVALID_MANIFEST'

@pytest.mark.parametrize('schema', [
    {'type': [], 'required': True},
    {'type': 'int', 'required': False, 'default': 'bad', 'min': 10, 'max': 20},
    {'type': 'float', 'required': False, 'default': float('nan')},
])
def test_malformed_parameter_schema_is_verdict(schema,gk_policy,gk_manifests):
    raw = deepcopy(gk_manifests['count_window']); raw['params'] = {'value':schema}
    assert validate_manifest(raw,None,gk_policy)[1]


def test_lone_surrogate_manifest_rejected(gk_manifests,gk_policy):
    raw=deepcopy(gk_manifests['count_window']); raw['description']='\ud800'
    assert validate_manifest(raw,None,gk_policy)[1][0].code=='INVALID_MANIFEST'
