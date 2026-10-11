# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from gatekeeper.lessons import Lessons


def test_bounded_identity_free_shape_and_persistence(tmp_path,gk_recipe):
    store = Lessons(tmp_path)
    gk_recipe['filter'].append({'field':'src_ip','op':'neq','value':'203.0.113.7'})
    store.record('run_aaaa','ssh_bruteforce','rejected','A'*350,gk_recipe)
    store.record('run_bbbb','other_attack','failed','Another lesson.',None)
    saved = Lessons(tmp_path).read(5,'ssh_bruteforce')
    assert len(saved) == 1 and len(saved[0]['text']) == 300
    assert '203.0.113.7' not in str(saved[0]['recipe_shape'])
    assert store.read(0) == [] and store.read(1)[0]['run_id'] == 'run_bbbb'
