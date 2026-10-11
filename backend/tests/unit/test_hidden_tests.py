# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import json
from pathlib import Path
import pytest
from gatekeeper.datasets import DatasetStore
from gatekeeper.hidden_tests import run_hidden_tests
from tests.fakes import InProcessSandbox

BASE=Path(__file__).resolve().parents[2]

@pytest.mark.asyncio
@pytest.mark.parametrize('name,source,total',[('ssh_parser','ssh',6),('count_window',None,5)])
async def test_seeds_pass_hidden_tests(name,source,total,gk_policy):
    folder=BASE/'seed_skills'/name
    manifest=json.loads((folder/'manifest.json').read_text())
    inputs=DatasetStore(BASE/'datasets').parser_lines(source,50) if source else None
    got,failures=await run_hidden_tests(manifest,(folder/'skill.py').read_text(),InProcessSandbox(),gk_policy,parser_lines=inputs)
    assert got==total and failures==[]

@pytest.mark.asyncio
async def test_bad_aggregation_boundary_detected(gk_policy):
    folder=BASE/'seed_skills/count_window'
    manifest=json.loads((folder/'manifest.json').read_text())
    code=(folder/'skill.py').read_text().replace('< end - window','<= end - window')
    total,failures=await run_hidden_tests(manifest,code,InProcessSandbox(),gk_policy)
    assert total==5 and 'hidden_aggregation_values' in [f['name'] for f in failures]

@pytest.mark.asyncio
async def test_enrichment_and_preservation(gk_policy):
    manifest={'name':'ip_class','kind':'enrichment','params':{},'outputs':['local'],'imports':[]}
    code='def run(inputs, params):\n    return [dict(event, local=True) for event in inputs]\n'
    total,failures=await run_hidden_tests(manifest,code,InProcessSandbox(),gk_policy)
    assert total==5 and not failures
    bad='def run(inputs, params):\n    return [dict(event, local=True, user="changed") for event in inputs]\n'
    _,failures=await run_hidden_tests(manifest,bad,InProcessSandbox(),gk_policy)
    assert 'hidden_enrichment_preserves' in [f['name'] for f in failures]

@pytest.mark.asyncio
async def test_bad_parser_and_execution_error(gk_policy):
    manifest=json.loads((BASE/'seed_skills/ssh_parser/manifest.json').read_text())
    inputs=DatasetStore(BASE/'datasets').parser_lines('ssh',50)
    _,failures=await run_hidden_tests(manifest,'def run(inputs, params):\n    return []\n',InProcessSandbox(),gk_policy,parser_lines=inputs)
    assert {'hidden_parser_shape','hidden_parser_coverage','hidden_parser_outputs'} <= {f['name'] for f in failures}
