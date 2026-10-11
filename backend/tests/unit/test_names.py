# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import pytest
from gatekeeper.names import valid_name, valid_run_id, valid_field, safe_join

@pytest.mark.parametrize('name', ['abc', 'ssh_parser', 'a' * 50])
def test_names_valid(name):
    assert valid_name(name)

@pytest.mark.parametrize('name', ['ab', 'a' * 51, '../ssh_parser', 'foo/bar', 'RUN_ABC', '_abc', 'abc\n', None])
def test_names_invalid(name):
    assert not valid_name(name)

@pytest.mark.parametrize('name', ['run_', 'RUN_ABC', 'run_../x', 'run_' + 'a' * 33])
def test_run_id_invalid(name):
    assert not valid_run_id(name)

def test_safe_join(tmp_path):
    assert safe_join(tmp_path, 'valid') == tmp_path / 'valid'
    for name in ('../escape', '/etc/passwd', '.'):
        with pytest.raises(ValueError):
            safe_join(tmp_path, name)
    (tmp_path / 'link').symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        safe_join(tmp_path, 'link/outside')
    assert valid_field('_line')
