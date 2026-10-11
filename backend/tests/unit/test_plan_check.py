# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from copy import deepcopy
import pytest
from gatekeeper.plan_check import check_plan

@pytest.mark.parametrize('mutation,code', [
('source', 'UNKNOWN_LOG_SOURCE'), ('attack', 'UNKNOWN_ATTACK_TYPE'), ('roles', 'PLAN_STRUCTURE'), ('name', 'INVALID_NAME'), ('unknown', 'UNKNOWN_SKILL'), ('kind', 'KIND_MISMATCH'), ('exists', 'SKILL_EXISTS'), ('many', 'TOO_MANY_MISSING'), ('parser', 'PARSER_SOURCE_MISMATCH'), ('spec', 'INVALID_SPEC'), ('forbidden', 'FORBIDDEN_PARAM'), ('duplicate', 'PLAN_STRUCTURE')])
def test_plan_violations(mutation, code, gk_policy, gk_catalog, gk_plan):
    raw = gk_plan.model_dump(); catalog = deepcopy(gk_catalog)
    missing = {'name': 'new_skill', 'role': 'enrichment', 'status': 'missing', 'spec': {'inputs': 'events', 'outputs': ['added'], 'params': {}}}
    if mutation == 'source': raw['log_source'] = 'unknown'
    if mutation == 'attack': raw['attack_type'] = 'unknown'
    if mutation == 'roles': raw['skills'] = raw['skills'][:1]
    if mutation == 'name': raw['skills'][0]['name'] = '../escape'
    if mutation == 'unknown': raw['skills'][0]['name'] = 'unknown'
    if mutation == 'kind': raw['skills'][0]['role'] = 'aggregation'
    if mutation == 'exists': raw['skills'][0].update(status='missing', role='enrichment')
    if mutation == 'many': raw['skills'] += [missing, {**missing, 'name': 'new_second'}, {**missing, 'name': 'new_third'}]
    if mutation == 'parser': catalog['skills'][0]['log_sources'] = ['web']
    if mutation == 'spec': raw['skills'].append({**missing, 'spec': {}})
    if mutation == 'forbidden': missing['spec']['params'] = {'exclude_ip': 'string'}; raw['skills'].append(missing)
    if mutation == 'duplicate': raw['skills'].append(raw['skills'][0])
    assert code in {v.code for v in check_plan(raw, catalog, gk_policy).violations}

def test_plan_normalization_and_rejection(gk_policy, gk_catalog, gk_plan):
    raw = gk_plan.model_dump(); raw['skills'][0]['status'] = 'missing'; raw['goal'] = 'A' * 300; raw['steps'] = ['B' * 300] * 20
    result = check_plan(raw, gk_catalog, gk_policy)
    assert result.ok and result.plan.skills[0].status == 'existing'
    assert len(result.plan.goal) == 200 and len(result.plan.steps) == 10 and len(result.plan.steps[0]) == 200
    assert check_plan(None, gk_catalog, gk_policy).violations[0].code == 'INVALID_OUTPUT'
    assert check_plan({'intent': 'out_of_scope'}, gk_catalog, gk_policy).request_rejected
    assert check_plan({'attack_type': 'unsupported'}, gk_catalog, gk_policy).request_rejected
