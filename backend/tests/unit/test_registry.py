# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from copy import deepcopy
import ast
import json
from pathlib import Path
import shutil
import pytest
from gatekeeper.audit import AuditLog, verify_audit
from gatekeeper.registry import Registry, IntegrityError, recipe_sha256
from gatekeeper.types import Metrics, Thresholds

SEEDS = Path(__file__).resolve().parents[2] / 'seed_skills'
CODE = 'def run(inputs, params):\n    return []\n'
TESTS = 'from skill import run\ndef test_empty():\n    assert run([], {}) == []\n'


def new_registry(tmp_path):
    audit = AuditLog(tmp_path)
    registry = Registry(tmp_path,audit); registry.initialize(SEEDS)
    return registry


def candidate(registry,gk_manifests,name='new_counter'):
    manifest = deepcopy(gk_manifests['count_window']); manifest['name'] = name
    return registry.save_candidate('run_aaaa',manifest,CODE,TESTS,2)


def validated(recipe):
    metrics = Metrics(true_positives=1,false_positives=0,false_negatives=0,precision=1.,recall=1.,thresholds=Thresholds(min_precision=.9,min_recall=.9),passed=True)
    return {'recipe_sha256':recipe_sha256(recipe),'attack_type':recipe['attack_type'],'metrics_tuning':metrics,'metrics_validation':metrics}


def test_seed_install_reuse_restart_and_candidate_cleanup(tmp_path,gk_manifests):
    registry = new_registry(tmp_path)
    assert [s.name for s in registry.installed_skills()] == ['count_window','ssh_parser']
    registry.note_reuse('run_aaaa',['ssh_parser','ssh_parser'])
    assert registry.index['skills']['ssh_parser']['times_used'] == 1
    info = candidate(registry,gk_manifests)
    assert info.status == 'candidate' and info.created_by_run == 'run_aaaa'
    restarted = new_registry(tmp_path)
    assert restarted.index['skills']['ssh_parser']['times_used'] == 1
    assert restarted.candidate_infos('run_aaaa') == []
    assert verify_audit(registry.audit.path)[0]
    assert not list((tmp_path/'registry').glob('.tmp-*'))


def test_seed_update(tmp_path):
    registry = new_registry(tmp_path)
    seed_copy = tmp_path/'seed_copy'; shutil.copytree(SEEDS,seed_copy)
    path = seed_copy/'count_window/skill.py'; path.write_text(path.read_text()+'\n# new version\n')
    registry.initialize(seed_copy)
    assert registry.skill_info('count_window').version == 2
    assert registry.read_skill(None,'count_window')[1]['version'] == 2


@pytest.mark.parametrize('name', ['count_window', 'ssh_parser'])
def test_english_seed_upgrade_preserves_usage_and_integrity(tmp_path, name):
    legacy_seeds = tmp_path / 'legacy_seeds'
    shutil.copytree(SEEDS, legacy_seeds)
    legacy_descriptions = {
        'count_window': 'Počítá události v posuvném časovém okně pro každou skupinu.',
        'ssh_parser': 'Převede řádky auth.log z OpenSSH (RFC 3339) na události.',
    }
    for seed_name, description in legacy_descriptions.items():
        skill_path = legacy_seeds / seed_name / 'skill.py'
        source = skill_path.read_text(encoding='utf-8')
        module = ast.parse(source)
        assert ast.get_docstring(module)
        docstring = module.body.pop(0)
        legacy_source = ''.join(source.splitlines(keepends=True)[docstring.end_lineno:])
        assert ast.dump(module, include_attributes=False) == ast.dump(ast.parse(legacy_source), include_attributes=False)
        skill_path.write_text(legacy_source, encoding='utf-8')
        manifest_path = legacy_seeds / seed_name / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['description'] = description
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    state = tmp_path / 'state'
    registry = Registry(state, AuditLog(state))
    registry.initialize(legacy_seeds)
    registry.note_reuse('run_aaaa', [name])
    registry.note_reuse('run_bbbb', [name])
    old = deepcopy(registry.index['skills'][name])
    assert registry.skill_info(name).version == 1
    assert registry.skill_info(name).description == legacy_descriptions[name]

    # The existing writer publishes the updated seed with matching digests.
    restarted = Registry(state, registry.audit)
    restarted.initialize(SEEDS)
    code, manifest, upgraded = restarted.read_skill(None, name)
    expected = json.loads((SEEDS / name / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['description'] == expected['description']
    assert restarted.skill_info(name).description == expected['description']
    assert upgraded['version'] == manifest['version'] == 2
    assert upgraded['sha256'] != old['sha256']
    assert upgraded['manifest_sha256'] != old['manifest_sha256']
    assert upgraded['tests_sha256'] == old['tests_sha256']
    assert upgraded['times_used'] == old['times_used'] == 2
    assert upgraded['last_used_at'] == old['last_used_at']
    assert upgraded['origin'] == 'seed' and upgraded['created_by_run'] is None
    assert code == (SEEDS / name / 'skill.py').read_text(encoding='utf-8')
    assert restarted.verify_integrity() == []
    assert not list((state / 'quarantine').iterdir())
    assert not list((state / 'registry').glob('.tmp-*'))
    assert not list((state / 'registry').glob('.old-*'))
    assert verify_audit(registry.audit.path)[0]

    # The same source is idempotent on every later restart.
    same_source = deepcopy(restarted.index)
    restarted.initialize(SEEDS)
    assert restarted.index == same_source


@pytest.mark.parametrize('filename',['skill.py','manifest.json','test_skill.py'])
def test_candidate_integrity_checked(tmp_path,gk_manifests,filename):
    registry = new_registry(tmp_path); candidate(registry,gk_manifests)
    path = tmp_path/'candidates/run_aaaa/new_counter'/filename
    path.write_text(path.read_text()+'x')
    with pytest.raises(IntegrityError): registry.read_skill('run_aaaa','new_counter')


@pytest.mark.parametrize('filename',['skill.py','manifest.json','test_skill.py'])
def test_registry_quarantine(tmp_path,gk_manifests,gk_recipe,filename):
    registry = new_registry(tmp_path); info = candidate(registry,gk_manifests)
    recipe = deepcopy(gk_recipe); recipe['aggregation']['skill'] = 'new_counter'
    registry.promote('run_aaaa',recipe,[info],validated(recipe))
    path = tmp_path/'registry/new_counter'/filename; path.write_text(path.read_text()+'x')
    with pytest.raises(IntegrityError): registry.read_skill(None,'new_counter')
    restarted = new_registry(tmp_path)
    assert 'new_counter' not in restarted.index['skills']
    assert list((tmp_path/'quarantine').glob('new_counter__*'))


def test_promotion_exact_validated_recipe_and_candidates(tmp_path,gk_manifests,gk_recipe):
    registry = new_registry(tmp_path); info = candidate(registry,gk_manifests)
    recipe = deepcopy(gk_recipe); recipe['aggregation']['skill'] = 'new_counter'
    valid = validated(recipe)
    changed = deepcopy(recipe); changed['condition']['value'] = 2
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',changed,[info],valid)
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[],valid)
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[info,info],valid)
    valid['metrics_validation'] = valid['metrics_validation'].model_copy(update={'passed':False})
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[info],valid)
    installed = registry.promote('run_aaaa',recipe,[info],validated(recipe),'Looks good.')
    assert installed[0].status == 'installed'
    assert registry.candidate_infos('run_aaaa') == []
    assert registry.approved_rule('ssh_bruteforce') == recipe
    assert (tmp_path/'rules/history/ssh_bruteforce__run_aaaa.json').is_file()
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[],validated(recipe))


def test_no_partial_promotion_on_integrity_failure(tmp_path,gk_manifests,gk_recipe):
    registry = new_registry(tmp_path)
    first = candidate(registry,gk_manifests,'new_first'); second = candidate(registry,gk_manifests,'new_second')
    recipe = deepcopy(gk_recipe); recipe['aggregation']['skill'] = 'new_first'
    (tmp_path/'candidates/run_aaaa/new_second/skill.py').write_text('tampered')
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[first,second],validated(recipe))
    assert not (tmp_path/'registry/new_first').exists() and not (tmp_path/'registry/new_second').exists()
    assert registry.approved_rule('ssh_bruteforce') is None


def test_promotion_rollback_on_write_error(tmp_path,gk_manifests,gk_recipe,monkeypatch):
    registry = new_registry(tmp_path); info = candidate(registry,gk_manifests)
    recipe = deepcopy(gk_recipe); recipe['aggregation']['skill'] = 'new_counter'
    import gatekeeper.registry as module
    actual = module.atomic_json
    def fail_history(path,value):
        if path.parent.name == 'history': raise OSError('disk full')
        actual(path,value)
    monkeypatch.setattr(module,'atomic_json',fail_history)
    with pytest.raises(OSError): registry.promote('run_aaaa',recipe,[info],validated(recipe))
    assert not (tmp_path/'registry/new_counter').exists()
    assert 'new_counter' not in registry.index['skills']
    assert registry.candidate_infos('run_aaaa') == [info]
    assert Registry(tmp_path,registry.audit).installed_skills() == registry.installed_skills()


@pytest.mark.parametrize('run,name',[('run_../x','new_counter'),('run_aaaa','../../escape')])
def test_path_traversal(tmp_path,gk_manifests,run,name):
    registry = new_registry(tmp_path)
    manifest = deepcopy(gk_manifests['count_window']); manifest['name'] = name
    with pytest.raises(ValueError): registry.save_candidate(run,manifest,CODE,TESTS)


def test_symlink_artifact_rejected(tmp_path):
    registry = new_registry(tmp_path)
    target = tmp_path/'registry/count_window/skill.py'; content=target.read_text(); target.unlink()
    outside=tmp_path/'outside.py'; outside.write_text(content); target.symlink_to(outside)
    with pytest.raises(IntegrityError): registry.read_skill(None,'count_window')


def test_validated_skill_digest_prevents_replacement(tmp_path,gk_manifests,gk_recipe):
    registry = new_registry(tmp_path); info = candidate(registry,gk_manifests)
    recipe = deepcopy(gk_recipe); recipe['aggregation']['skill'] = 'new_counter'
    record = validated(recipe); record['skill_digests']={'ssh_parser':registry.read_skill(None,'ssh_parser')[2]['sha256'],'new_counter':'0'*64}
    with pytest.raises(IntegrityError): registry.promote('run_aaaa',recipe,[info],record)
    assert not (tmp_path/'registry/new_counter').exists()


def test_promotion_uses_verified_artifact_snapshot(tmp_path,gk_manifests,gk_recipe,monkeypatch):
    registry=new_registry(tmp_path)
    first=candidate(registry,gk_manifests,'new_first'); second=candidate(registry,gk_manifests,'new_second')
    recipe=deepcopy(gk_recipe); recipe['aggregation']['skill']='new_first'
    import gatekeeper.registry as module
    actual=module.atomic_write
    def mutate_after_staging_started(path,payload):
        if path.parent.name.startswith('.tmp-new_first-'):
            (tmp_path/'candidates/run_aaaa/new_second/skill.py').write_text('import socket\n'+CODE)
        actual(path,payload)
    monkeypatch.setattr(module,'atomic_write',mutate_after_staging_started)
    result=registry.promote('run_aaaa',recipe,[first,second],validated(recipe))
    assert {s.name for s in result}=={'new_first','new_second'}
    assert registry.read_skill(None,'new_second')[0]==CODE


def test_audit_failure_rolls_back_promotion(tmp_path,gk_manifests,gk_recipe,monkeypatch):
    registry=new_registry(tmp_path); info=candidate(registry,gk_manifests)
    recipe=deepcopy(gk_recipe); recipe['aggregation']['skill']='new_counter'
    original=registry.audit.append
    def broken_audit(kind,*args,**kwargs):
        if kind=='rule_approved': raise OSError('audit disk full')
        return original(kind,*args,**kwargs)
    monkeypatch.setattr(registry.audit,'append',broken_audit)
    with pytest.raises(OSError): registry.promote('run_aaaa',recipe,[info],validated(recipe))
    assert 'new_counter' not in registry.index['skills']
    assert not (tmp_path/'registry/new_counter').exists()
    assert registry.approved_rule('ssh_bruteforce') is None
    assert registry.candidate_infos('run_aaaa')==[info]


def test_postcommit_cleanup_failure_preserves_approval(tmp_path,gk_manifests,gk_recipe,monkeypatch):
    registry=new_registry(tmp_path); info=candidate(registry,gk_manifests)
    recipe=deepcopy(gk_recipe); recipe['aggregation']['skill']='new_counter'
    def broken_cleanup(run_id): raise OSError('cleanup denied')
    monkeypatch.setattr(registry,'discard',broken_cleanup)
    result=registry.promote('run_aaaa',recipe,[info],validated(recipe))
    assert result[0].status=='installed'
    assert registry.approved_rule('ssh_bruteforce')==recipe
    assert registry.read_skill(None,'new_counter')[0]==CODE


def test_postcommit_stage_cleanup_failure_preserves_approval(tmp_path,gk_manifests,gk_recipe,monkeypatch):
    registry=new_registry(tmp_path); info=candidate(registry,gk_manifests)
    recipe=deepcopy(gk_recipe); recipe['aggregation']['skill']='new_counter'
    import gatekeeper.registry as module
    actual_exists=Path.exists
    actual_remove=module.shutil.rmtree
    def leftover(path):
        return True if path.name.startswith('.tmp-new_counter-') else actual_exists(path)
    def denied(path,*args,**kwargs):
        if Path(path).name.startswith('.tmp-new_counter-'): raise OSError('stage cleanup denied')
        return actual_remove(path,*args,**kwargs)
    monkeypatch.setattr(Path,'exists',leftover)
    monkeypatch.setattr(module.shutil,'rmtree',denied)
    result=registry.promote('run_aaaa',recipe,[info],validated(recipe))
    assert result[0].status=='installed'
    assert registry.approved_rule('ssh_bruteforce')==recipe
