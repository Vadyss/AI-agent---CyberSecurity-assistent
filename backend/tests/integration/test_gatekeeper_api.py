# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Authority checks through the public facade with the real isolated runner."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest
from gatekeeper.api import Gatekeeper
from gatekeeper.registry import IntegrityError, recipe_sha256
from gatekeeper.types import GatekeeperConfig, Metrics, ParseFailure, PlanSkill, SandboxError
from orchestrator.llm_mock import manifest as mock_manifest
from tests.fakes import InProcessSandbox

BASE = Path(__file__).resolve().parents[2]
RUN = 'run_aaaa'


class TrackingSandbox(InProcessSandbox):
    def __init__(self):
        self.calls=[]
        self.after_run=None
        self.delay=False
        self.fail=False
    async def test(self,*args,**kwargs):
        self.calls.append('test')
        return await super().test(*args,**kwargs)
    async def run(self,*args,**kwargs):
        self.calls.append('run')
        if self.fail:
            raise SandboxError('Sandbox unavailable.')
        if self.delay:
            await asyncio.sleep(.01)
        result=await super().run(*args,**kwargs)
        if self.after_run is not None:
            self.after_run(result)
        return result


@pytest.fixture
def gk(tmp_path):
    cfg=GatekeeperConfig(tmp_path/'data',BASE/'datasets',BASE/'policy/policy.yaml',BASE/'seed_skills')
    return Gatekeeper(cfg,TrackingSandbox())


def count_plan():
    return {'intent':'detection_rule','log_source':'ssh','attack_type':'ssh_bruteforce','skills':[
        {'name':'ssh_parser','role':'parser','status':'existing'},
        {'name':'count_window','role':'aggregation','status':'existing'}]}


def missing_plan():
    raw=count_plan(); raw['attack_type']='ssh_password_spraying'
    manifest=mock_manifest('distinct_count_window')
    raw['skills'][1]={'name':manifest['name'],'role':'aggregation','status':'missing','description':manifest['description'],
        'spec':{'inputs':'events','params':{name:p['type'] for name,p in manifest['params'].items()},'outputs':manifest['outputs']}}
    return raw


def draft():
    return {'manifest':mock_manifest('distinct_count_window'), 'code':(BASE/'orchestrator/mock_fixtures/distinct_count_window.py').read_text(), 'tests':(BASE/'orchestrator/mock_fixtures/distinct_tests.py').read_text()}


def recipe(missing=False):
    return {'name':'ssh_password_spraying' if missing else 'ssh_bruteforce','attack_type':'ssh_password_spraying' if missing else 'ssh_bruteforce',
        'parser':'ssh_parser','filter':[{'field':'outcome','op':'eq','value':'failure'}],
        'aggregation':{'skill':'distinct_count_window' if missing else 'count_window','params':{'group_by':'src_ip','window_s':300,**({'distinct_field':'user'} if missing else {})}},
        'condition':{'field':'distinct_count' if missing else 'count','op':'gte','value':5 if missing else 10}}


async def checked_tuned(gk,missing=False):
    plan=gk.check_plan(RUN,missing_plan() if missing else count_plan()).plan
    if missing:
        assert (await gk.submit_skill(RUN,plan.missing_skills[0],draft())).kind=='candidate'
    checked=gk.check_recipe(RUN,plan,recipe(missing)).recipe
    result=await gk.evaluate_tuning(RUN,checked)
    assert result.metrics.passed
    return plan,checked


@pytest.mark.asyncio
async def test_requires_authoritative_plan_spec(gk):
    spec=PlanSkill.model_validate(missing_plan()['skills'][1])
    with pytest.raises(IntegrityError): await gk.submit_skill(RUN,spec,draft())
    plan=gk.check_plan(RUN,missing_plan()).plan
    invalid=plan.missing_skills[0].model_copy(deep=True); invalid.spec['outputs']=['different']
    with pytest.raises(IntegrityError): await gk.submit_skill(RUN,invalid,draft())
    assert not gk.sandbox.calls and not gk.candidates_info(RUN)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',[ParseFailure('invalid JSON'),None,{'manifest':ParseFailure('error'),'code':'','tests':''}])
async def test_parse_failure_is_policy_rejection_without_execution(gk,bad):
    plan=gk.check_plan(RUN,missing_plan()).plan
    result=await gk.submit_skill(RUN,plan.missing_skills[0],bad)
    assert result.kind=='policy_rejected' and result.violations[0].code=='INVALID_OUTPUT'
    assert not gk.sandbox.calls


@pytest.mark.asyncio
async def test_minimum_four_llm_tests_enforced(gk):
    plan=gk.check_plan(RUN,missing_plan()).plan
    proposal=draft(); proposal['tests']='from skill import run\ndef test_empty():\n    assert run([], {"group_by":"src_ip","distinct_field":"user","window_s":60}) == []\n'
    result=await gk.submit_skill(RUN,plan.missing_skills[0],proposal)
    assert result.kind=='tests_failed' and result.tests_failed==1 and result.tests_total==7
    assert result.failures==[{'name':'test_count','error':'The skill contains too few tests.'}]
    assert not gk.candidates_info(RUN)


@pytest.mark.asyncio
async def test_candidate_requires_static_policy_before_test(gk):
    plan=gk.check_plan(RUN,missing_plan()).plan
    proposal=draft(); proposal['code']='import socket\n'+proposal['code']
    for _ in range(3):
        result=await gk.submit_skill(RUN,plan.missing_skills[0],proposal)
        assert result.kind=='policy_rejected' and 'FORBIDDEN_IMPORT' in {v.code for v in result.violations}
    with pytest.raises(IntegrityError): await gk.submit_skill(RUN,plan.missing_skills[0],proposal)
    assert not gk.sandbox.calls


@pytest.mark.asyncio
async def test_draft_snapshot_during_hidden_tests(gk):
    plan=gk.check_plan(RUN,missing_plan()).plan
    proposal=draft(); original=proposal['code']
    def mutate(_):
        # The last hidden sandbox call has already received the tested code.
        if gk.sandbox.calls.count('run')==4:
            proposal['code']='import socket\n'+original
    gk.sandbox.after_run=mutate
    result=await gk.submit_skill(RUN,plan.missing_skills[0],proposal)
    assert result.kind=='candidate'
    assert gk.registry.read_skill(RUN,plan.missing_skills[0].name)[0]==original


@pytest.mark.asyncio
async def test_recipe_requires_exact_checked_plan_and_successful_tuning(gk):
    raw=recipe()
    with pytest.raises(IntegrityError): await gk.evaluate_tuning(RUN,raw)
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,raw)
    plan=gk.check_plan(RUN,count_plan()).plan
    forged=plan.model_copy(deep=True); forged.attack_type='ssh_password_spraying'
    with pytest.raises(IntegrityError): gk.check_recipe(RUN,forged,raw)
    checked=gk.check_recipe(RUN,plan,raw).recipe
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,checked)
    changed=deepcopy(checked); changed['condition']['value']=11
    with pytest.raises(IntegrityError): await gk.evaluate_tuning(RUN,changed)
    checked['condition']['value']=9999
    assert gk.check_recipe(RUN,plan,checked).ok
    assert not (await gk.evaluate_tuning(RUN,checked)).metrics.passed
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,checked)


@pytest.mark.asyncio
async def test_recipe_snapshot_during_tuning(gk):
    plan=gk.check_plan(RUN,count_plan()).plan
    checked=gk.check_recipe(RUN,plan,recipe()).recipe
    original_hash=recipe_sha256(checked)
    def mutate(_):
        if gk.sandbox.calls.count('run')==1:
            checked['filter'].append({'field':'src_ip','op':'neq','value':'198.51.100.23'})
    gk.sandbox.after_run=mutate
    await gk.evaluate_tuning(RUN,checked)
    assert gk._tuning[RUN]['recipe_sha256']==original_hash
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,checked)


@pytest.mark.asyncio
async def test_validation_exactly_once_including_concurrency(gk):
    _,checked=await checked_tuned(gk)
    gk.sandbox.delay=True
    answers=await asyncio.gather(gk.evaluate_validation(RUN,checked),gk.evaluate_validation(RUN,checked),return_exceptions=True)
    assert sum(isinstance(answer,Metrics) for answer in answers)==1
    assert sum(isinstance(answer,IntegrityError) for answer in answers)==1
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,checked)
    assert gk.sandbox.calls.count('run')==4  # parser+aggregation once per data set


@pytest.mark.asyncio
async def test_validation_transport_failure_cannot_retry(gk):
    _,checked=await checked_tuned(gk)
    gk.sandbox.fail=True
    with pytest.raises(SandboxError): await gk.evaluate_validation(RUN,checked)
    with pytest.raises(IntegrityError): await gk.evaluate_validation(RUN,checked)


@pytest.mark.asyncio
async def test_promotion_requires_validation_and_rejects_recipe_and_disk_tampering(gk):
    _,checked=await checked_tuned(gk,missing=True)
    candidates=gk.candidates_info(RUN)
    with pytest.raises(IntegrityError): await gk.promote(RUN,checked,candidates,None)
    validation=await gk.evaluate_validation(RUN,checked)
    assert validation.passed
    changed=deepcopy(checked); changed['condition']['value']=6
    with pytest.raises(IntegrityError): await gk.promote(RUN,changed,candidates,None)
    path=gk.registry.candidates_dir/RUN/'distinct_count_window/skill.py'; path.write_text(path.read_text()+'\n# tampering\n')
    with pytest.raises(IntegrityError): await gk.promote(RUN,checked,candidates,None)
    assert 'distinct_count_window' not in {skill.name for skill in gk.installed_skills()}


@pytest.mark.asyncio
async def test_success_promotes_exact_recipe_and_forgets_run(gk):
    _,checked=await checked_tuned(gk,missing=True)
    await gk.evaluate_validation(RUN,checked)
    promoted=await gk.promote(RUN,checked,gk.candidates_info(RUN),'Approved.')
    assert [s.name for s in promoted]==['distinct_count_window'] and promoted[0].status=='installed'
    assert gk.approved_rule('ssh_password_spraying')==checked
    assert not gk.candidates_info(RUN) and RUN not in gk._plans


@pytest.mark.asyncio
async def test_failures_rejections_lessons_and_discard(gk):
    _,checked=await checked_tuned(gk,missing=True)
    checked['condition']['value']=9999
    plan=gk._plans[RUN].model_copy(deep=True)
    gk.check_recipe(RUN,plan,checked)
    await gk.evaluate_tuning(RUN,checked)
    gk.record_failure(RUN,'RULE_FAILED','Rule nesplnilo hranice.',checked)
    lesson=gk.lessons(5,'ssh_password_spraying')[-1]
    assert lesson['kind']=='RULE_FAILED' and lesson['recipe_shape']['condition']['value']==9999
    assert 'recall' in lesson['text'].lower() and 'precision' in lesson['text'].lower()
    gk.record_rejection(RUN,checked,'ssh_password_spraying','Lower the threshold.')
    assert gk.lessons(1)[0]['kind']=='analyst_rejected'
    await gk.discard(RUN)
    assert not gk.candidates_info(RUN) and RUN not in gk._plans
    # Deadline and infrastructure failures are still audited and cleanly forgotten.
    gk.check_plan('run_bbbb',count_plan())
    gk.record_failure('run_bbbb','INTERNAL_ERROR','The run exceeded its time limit.')
    await gk.discard('run_bbbb')
    assert 'run_bbbb' not in gk._plans
    assert json.loads(gk.audit.path.read_text().splitlines()[-1])['kind']=='run_failed'


@pytest.mark.asyncio
async def test_hidden_failures_do_not_expose_fixture_identities(gk):
    plan=gk.check_plan(RUN,missing_plan()).plan
    proposal=draft(); proposal['code']='def run(inputs, params):\n    return []\n'
    # LLM tests know only their supplied empty input; hidden errors are templates.
    proposal['tests']='from skill import run\n'+'\n'.join(f'def test_{i}():\n    assert run([], {{}}) == []\n' for i in range(4))
    result=await gk.submit_skill(RUN,plan.missing_skills[0],proposal)
    assert result.kind=='tests_failed'
    feedback=str(result.failures)+result.error_excerpt
    for identity in ['192.0.2.1','192.0.2.2','192.0.2.3','user_0','user_1','user_2','user_3']:
        assert identity not in feedback
