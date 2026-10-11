# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from copy import deepcopy
import pytest
from gatekeeper.recipe import check_recipe, display_recipe

@pytest.mark.parametrize('mutation,code', [('unknown_key','RECIPE_INVALID'), ('name','INVALID_NAME'), ('attack','RECIPE_INVALID'), ('skill','UNKNOWN_SKILL'), ('field','UNKNOWN_FIELD'), ('condition_field','UNKNOWN_FIELD'), ('operator','RECIPE_INVALID'), ('param','PARAM_INVALID'), ('window','PARAM_INVALID'), ('bool_window','PARAM_INVALID'), ('missing_param','PARAM_INVALID'), ('group','UNKNOWN_FIELD'), ('exclude','RECIPE_EXCEPTION'), ('user','RECIPE_EXCEPTION'), ('cidr','RECIPE_EXCEPTION'), ('overfit','RECIPE_OVERFIT'), ('forbidden','RECIPE_EXCEPTION'), ('big','RECIPE_INVALID'), ('nonfinite','RECIPE_INVALID')])
def test_recipe_violations(mutation, code, gk_recipe, gk_plan, gk_manifests, gk_policy):
    raw = deepcopy(gk_recipe)
    if mutation == 'unknown_key': raw['extra'] = 1
    if mutation == 'name': raw['name'] = '../bad'
    if mutation == 'attack': raw['attack_type'] = 'other_attack'
    if mutation == 'skill': raw['parser'] = 'unknown_parser'
    if mutation == 'field': raw['filter'][0]['field'] = 'unknown'
    if mutation == 'condition_field': raw['condition']['field'] = 'unknown'
    if mutation == 'operator': raw['filter'][0]['op'] = 'regex'
    if mutation == 'param': raw['aggregation']['params']['window_s'] = '300'
    if mutation == 'window': raw['aggregation']['params']['window_s'] = 1
    if mutation == 'bool_window': raw['aggregation']['params']['window_s'] = True
    if mutation == 'missing_param': raw['aggregation']['params'].pop('group_by')
    if mutation == 'group': raw['aggregation']['params']['group_by'] = ['unknown']
    if mutation == 'exclude': raw['filter'] = [{'field':'src_ip','op':'neq','value':'10.0.0.1'}]
    if mutation == 'user': raw['filter'] = [{'field':'user','op':'not_in','value':['root']}]
    if mutation == 'cidr': raw['filter'] = [{'field':'outcome','op':'neq','value':'192.0.2.0/24'}]
    if mutation == 'overfit': raw['filter'] = [{'field':'src_ip','op':'in','value':['10.0.0.1']}]
    if mutation == 'forbidden': raw['aggregation']['params']['exclude_ip'] = '10.0.0.1'
    if mutation == 'big': raw['description'] = 'a' * 5000
    if mutation == 'nonfinite': raw['condition']['value'] = float('nan')
    verdict = check_recipe(raw,gk_plan,gk_manifests,gk_policy)
    assert not verdict.ok and code in {v.code for v in verdict.violations}
    if mutation == 'exclude': assert len([v for v in verdict.violations if v.code == 'RECIPE_EXCEPTION']) == 1

def test_valid_recipe_and_display(gk_recipe,gk_plan,gk_manifests,gk_policy):
    assert check_recipe(gk_recipe,gk_plan,gk_manifests,gk_policy).ok
    assert check_recipe(None,gk_plan,gk_manifests,gk_policy).violations[0].code == 'INVALID_OUTPUT'
    assert display_recipe(None) == {'name':'invalid_draft'}
    assert display_recipe({'name':'../bad'}) == {'name':'invalid_draft'}
    assert len(str(display_recipe({'name':'valid_name','blob':'A'*5000}))) < 4096

@pytest.mark.parametrize('field,value', [('field', []), ('op', []), ('value', {}), ('op', {})])
def test_malformed_filter_is_verdict(field,value,gk_recipe,gk_plan,gk_manifests,gk_policy):
    raw=deepcopy(gk_recipe); raw['filter'][0][field]=value
    assert not check_recipe(raw,gk_plan,gk_manifests,gk_policy).ok

def test_nonstring_nested_key_is_verdict(gk_recipe,gk_plan,gk_manifests,gk_policy):
    raw=deepcopy(gk_recipe); raw['aggregation']['params']={2:'bad'}
    assert check_recipe(raw,gk_plan,gk_manifests,gk_policy).violations[0].code == 'RECIPE_INVALID'


def test_lone_surrogate_json_rejected(gk_recipe,gk_plan,gk_manifests,gk_policy):
    raw=deepcopy(gk_recipe); raw['filter'][0]['value']='\ud800'
    assert check_recipe(raw,gk_plan,gk_manifests,gk_policy).violations[0].code=='RECIPE_INVALID'
    assert display_recipe(raw)=={'name':raw['name']}
