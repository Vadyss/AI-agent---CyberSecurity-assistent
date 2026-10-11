# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import pytest
import yaml
from gatekeeper.policy import load_policy

def test_frozen_and_digest(gk_policy):
    with pytest.raises(Exception):
        gk_policy.skills.max_code_bytes = 999999
    assert len(gk_policy.sha256) == 64
    digest = gk_policy.digest_for_llm()
    assert 'thresholds' not in digest and 'seed' not in str(digest)
    assert 'open' in digest['forbidden_calls']

@pytest.mark.parametrize('mutation', ['imports', 'network', 'threshold', 'identity', 'attempts', 'extra', 'introspection'])
def test_policy_rejects_invalid(tmp_path, gk_policy, mutation):
    raw = gk_policy.model_dump(exclude={'sha256'})
    if mutation == 'imports': raw['skills']['allowed_imports'] += ('os',)
    if mutation == 'network': raw['skills']['permissions']['network'] = 'allow'
    if mutation == 'threshold': raw['thresholds']['min_recall'] = 2
    if mutation == 'identity': raw['recipe']['identity_fields'] = []
    if mutation == 'attempts': raw['attempts']['plan'] = 9
    if mutation == 'extra': raw['unknown'] = 1
    if mutation == 'introspection': raw['skills']['forbidden_attributes'] = ('format_map',)
    path = tmp_path / 'policy.yaml'
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(Exception): load_policy(path)
