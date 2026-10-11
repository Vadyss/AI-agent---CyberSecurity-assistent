# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Untrusted generator verification uses the actual isolated runner."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest

from gatekeeper.audit import AuditLog
from gatekeeper.datasets import DatasetStore
from gatekeeper.examiner_data import prepare_datasets, _checked_generator
from gatekeeper.registry import IntegrityError, Registry
from tests.fakes import InProcessSandbox

BASE=Path(__file__).resolve().parents[2]
GENERATOR=(BASE/'examiner/fixtures/ssh_generator.py').read_text()


@pytest.fixture
def baseline():
    data=DatasetStore(BASE/'datasets')
    return {name:data.load('ssh',name) for name in ('tuning','validation')}


@pytest.fixture
def skill_loader():
    code=(BASE/'seed_skills/ssh_parser/skill.py').read_text()
    manifest=json.loads((BASE/'seed_skills/ssh_parser/manifest.json').read_text())
    meta={'origin':'seed','sha256':hashlib.sha256(code.encode()).hexdigest()}
    return lambda name:(code,deepcopy(manifest),meta.copy())


class RecordingSandbox(InProcessSandbox):
    def __init__(self,mutate=None):
        self.generations=[]
        self.mutate=mutate
        self.jobs=[]
    async def run(self,code,inputs,params,**kwargs):
        self.jobs.append((code,deepcopy(inputs),params.copy()))
        answer=await super().run(code,inputs,params,**kwargs)
        if 'def generate(' in code:
            self.generations.append(deepcopy(answer['result'][0]))
            if self.mutate is not None:
                self.mutate(answer,len(self.generations))
        return answer


@pytest.mark.asyncio
async def test_verified_deterministic_generator_and_fixed_benign_merge(baseline,skill_loader,gk_policy):
    sandbox=RecordingSandbox()
    prepared=await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,sandbox,gk_policy,skill_loader)
    assert set(prepared)=={'tuning','validation'} and len(sandbox.jobs)==4
    for name,generated in zip(('tuning','validation'),sandbox.generations):
        target_indices={index for instance in generated['instances'] for index in instance['lines']}
        fixed_attack_indices={index for instance in baseline[name].labels['instances'] for index in instance['lines']}
        fixed_benign=[line for index,line in enumerate(baseline[name].lines) if index not in fixed_attack_indices]
        dataset=prepared[name]
        assert len(dataset.lines)==len(fixed_benign)+len(target_indices)
        assert all(line in dataset.lines for line in fixed_benign)
        assert all(line not in dataset.lines for index,line in enumerate(generated['lines']) if index not in target_indices)
        assert dataset.labels['counts']=={'ssh_custom_attack':6}
        assert 'seed' not in dataset.labels and dataset.labels['line_count']==len(dataset.lines)
        assert all(instance['attack_type']=='ssh_custom_attack' for instance in dataset.labels['instances'])
        all_label_indices=[index for instance in dataset.labels['instances'] for index in instance['lines']]
        assert len(all_label_indices)==len(set(all_label_indices))==len(target_indices)
        parsed=await sandbox.run(skill_loader('ssh_parser')[0],dataset.lines,{},allowed_imports=list(gk_policy.skills.allowed_imports))
        assert [event['ts'] for event in parsed['result']]==sorted(event['ts'] for event in parsed['result'])
    for key in ('src_ips','users'):
        tuning={v for instance in prepared['tuning'].labels['instances'] for v in instance[key]}
        validation={v for instance in prepared['validation'].labels['instances'] for v in instance[key]}
        assert not tuning.intersection(validation)
    wrapped=_checked_generator(GENERATOR,gk_policy)
    one=await sandbox.run(wrapped,[],{'seed':12345},allowed_imports=list(gk_policy.skills.allowed_imports))
    two=await sandbox.run(wrapped,[],{'seed':12345},allowed_imports=list(gk_policy.skills.allowed_imports))
    assert one['result']==two['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('code',[
    'import socket\ndef generate(seed):\n    return {}',
    'def generate(seed, extra):\n    return {}',
    'def generate(other):\n    return {}',
    'def generate(seed=1):\n    return {}',
    'def generate(seed, **kwargs):\n    return {}',
    'def something(seed):\n    return {}',
    'def generate(seed):\n    return ["\ud800"]',
    'def generate(',
])
async def test_bad_code_rejected_without_execution(code,baseline,skill_loader,gk_policy):
    sandbox=RecordingSandbox()
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',code,baseline,sandbox,gk_policy,skill_loader)
    assert not sandbox.jobs


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['not_object','not_list','empty_lines','line_type','long_line','newline','few_instances','empty_labels','duplicate_index','shared_index','invalid_index','boolean_index','invalid_parse','invalid_ip','unicode'])
async def test_invalid_generator_outputs_rejected(mutation,baseline,skill_loader,gk_policy):
    def mutate(answer,_):
        payload=answer['result'][0]
        if mutation=='not_object': answer['result']=[None]
        if mutation=='not_list': answer['result']=payload
        if mutation=='empty_lines': payload['lines']=[]
        if mutation=='line_type': payload['lines'][0]=1
        if mutation=='long_line': payload['lines'][0]='A'*8193
        if mutation=='newline': payload['lines'][0]+='\n'
        if mutation=='few_instances': payload['instances']=payload['instances'][:5]
        if mutation=='empty_labels': payload['instances'][0]['lines']=[]
        if mutation=='duplicate_index': payload['instances'][0]['lines']*=2
        if mutation=='shared_index': payload['instances'][1]['lines']+=payload['instances'][0]['lines'][:1]
        if mutation=='invalid_index': payload['instances'][0]['lines']=[len(payload['lines'])]
        if mutation=='boolean_index': payload['instances'][0]['lines']=[True]
        if mutation=='invalid_parse': payload['lines']=['invalid']*len(payload['lines'])
        if mutation=='invalid_ip': payload['lines']=[line.replace(' from ',' from invalid_') for line in payload['lines']]
        if mutation=='unicode': payload['lines'][0]='\ud800'
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(mutate),gk_policy,skill_loader)


@pytest.mark.asyncio
async def test_overlapping_attack_identities_rejected(baseline,skill_loader,gk_policy):
    first={}
    def same(answer,number):
        if number==1: first['result']=deepcopy(answer['result'])
        else: answer['result']=deepcopy(first['result'])
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(same),gk_policy,skill_loader)


@pytest.mark.asyncio
async def test_labelled_unparsed_line_rejected_even_at_95percent(baseline,skill_loader,gk_policy):
    def corrupt_one(answer,_): answer['result'][0]['lines'][0]='invalid'
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(corrupt_one),gk_policy,skill_loader)


@pytest.mark.asyncio
async def test_input_limit_and_parser_integrity(baseline,skill_loader,gk_policy):
    tiny=gk_policy.model_copy(update={'sandbox':gk_policy.sandbox.model_copy(update={'max_input_bytes':1000})})
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(),tiny,skill_loader)
    def untrusted_loader(name):
        code,manifest,meta=skill_loader(name); meta['origin']='agent'
        return code,manifest,meta
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(),gk_policy,untrusted_loader)


@pytest.mark.asyncio
async def test_private_persistence_atomic_hashes_and_reserved_name(tmp_path,baseline,skill_loader,gk_policy,gk_manifests):
    data=await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(),gk_policy,skill_loader)
    audit=AuditLog(tmp_path); registry=Registry(tmp_path,audit)
    digests=registry.save_examiner_data('run_aaaa',data)
    assert len(digests)==4 and registry.candidate_infos('run_aaaa')==[]
    for relative,digest in digests.items():
        assert hashlib.sha256((tmp_path/'candidates/run_aaaa'/relative).read_bytes()).hexdigest()==digest
    assert not list((tmp_path/'candidates/run_aaaa').glob('.tmp-*'))
    labels=json.loads((tmp_path/'candidates/run_aaaa/data/tuning/labels.json').read_text())
    assert 'seed' not in labels
    with pytest.raises(IntegrityError): registry.save_examiner_data('run_aaaa',data)
    manifest=deepcopy(gk_manifests['count_window']); manifest['name']='data'
    with pytest.raises(ValueError): registry.save_candidate('run_aaaa',manifest,'def run(inputs, params):\n    return []','')
    registry.discard('run_aaaa')
    assert not (tmp_path/'candidates/run_aaaa').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('identity',['ip','user'])
async def test_generated_identity_cannot_match_other_set_fixed_benign(identity,baseline,skill_loader,gk_policy):
    import re
    attack_indices={i for instance in baseline['validation'].labels['instances'] for i in instance['lines']}
    benign=[line for index,line in enumerate(baseline['validation'].lines) if index not in attack_indices]
    parsed=await InProcessSandbox().run(skill_loader('ssh_parser')[0],benign,{},allowed_imports=list(gk_policy.skills.allowed_imports))
    normal=parsed['result'][0]
    def reuse(answer,number):
        if number != 1: return
        payload=answer['result'][0]
        index=payload['instances'][0]['lines'][0]
        if identity=='ip':
            payload['lines'][index]=re.sub(r' from \S+ port ',f" from {normal['src_ip']} port ",payload['lines'][index])
        else:
            payload['lines'][index]=re.sub(r' for invalid user \S+ from ',f" for invalid user {normal['user']} from ",payload['lines'][index])
    with pytest.raises(ValueError):
        await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(reuse),gk_policy,skill_loader)


@pytest.mark.asyncio
async def test_unparsed_generated_benign_below_coverage_limit_is_discarded(baseline,skill_loader,gk_policy):
    def nonsense(answer,_):
        payload=answer['result'][0]
        labelled={i for instance in payload['instances'] for i in instance['lines']}
        index=next(i for i in range(len(payload['lines'])) if i not in labelled)
        payload['lines'][index]='unrecognizable benign'
    prepared=await prepare_datasets('run_aaaa','ssh_custom_attack',GENERATOR,baseline,RecordingSandbox(nonsense),gk_policy,skill_loader)
    assert all('unrecognizable benign' not in data.lines for data in prepared.values())
