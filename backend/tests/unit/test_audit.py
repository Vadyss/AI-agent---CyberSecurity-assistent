# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from gatekeeper.audit import AuditLog, verify_audit


def test_chain_restart_and_threads(tmp_path):
    audit = AuditLog(tmp_path)
    audit.append('startup', detail={'code':'sensitive code', 'api_key':'secret'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: audit.append('rule_evaluated', 'run_aaaa', {'number':n}), range(30)))
    assert verify_audit(audit.path) == (True, [])
    restarted = AuditLog(tmp_path); restarted.append('startup')
    assert restarted.n == 32 and verify_audit(audit.path)[0]
    assert 'sensitive code' not in audit.path.read_text() and 'secret' not in audit.path.read_text()


def test_tampering_and_deletion(tmp_path):
    audit = AuditLog(tmp_path)
    for n in range(3): audit.append('startup', detail={'n':n})
    original = audit.path.read_text()
    lines = original.splitlines()
    changed = json.loads(lines[1]); changed['detail']['n'] = 100; lines[1] = json.dumps(changed)
    audit.path.write_text('\n'.join(lines) + '\n')
    assert not verify_audit(audit.path)[0]
    recovered = AuditLog(tmp_path)
    assert json.loads(recovered.path.read_text().splitlines()[-1])['kind'] == 'audit_chain_broken'
    audit.path.write_text('\n'.join(original.splitlines()[1:]) + '\n')
    assert not verify_audit(audit.path)[0]


def test_cli(tmp_path):
    audit = AuditLog(tmp_path); audit.append('startup')
    script = Path(__file__).resolve().parents[2] / 'scripts/verify_audit.py'
    command = [sys.executable, str(script), str(audit.path)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    audit.path.write_text('{invalid}\n')
    assert subprocess.run(command, capture_output=True).returncode == 1


def test_violation_codes_retained_without_source_or_secrets(tmp_path):
    audit=AuditLog(tmp_path)
    record=audit.append('skill_rejected',detail={'violations':[{'code':'FORBIDDEN_IMPORT','detail':'Forbidden import: socket.'}], 'code':'import socket', 'api_key':'secret'})
    assert record['detail']['violations'][0]['code']=='FORBIDDEN_IMPORT'
    assert record['detail']['code']=='[redacted]' and record['detail']['api_key']=='[redacted]'
    assert verify_audit(audit.path)[0]
