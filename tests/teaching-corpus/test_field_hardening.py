#!/usr/bin/env python3
"""Regression: field-hardening preserves verdict language and seals completed workspaces."""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CTL=ROOT/'runtime-feature-falsifier'/'scripts'/'auditctl.py'
def run(*a): return subprocess.run([sys.executable,str(CTL),*a],text=True,capture_output=True)
def main()->int:
  with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    bad=root/'bad.md'; bad.write_text('# Run 2 (Verified)\nGate: PASSED (Verified Reality)\n')
    chk=run('terminology-check','--input',str(bad))
    assert chk.returncode!=0 and 'verified' in chk.stdout.lower(), chk.stdout
    good=root/'good.md'; good.write_text('Run 2: NOT_FALSIFIED under the declared 14-probe matrix.\n')
    chk2=run('terminology-check','--input',str(good))
    assert chk2.returncode==0, chk2.stdout

    project=root/'project'; project.mkdir(); audit=project/'.runtime-feature-audit'; audit.mkdir()
    (audit/'.complete.json').write_text(json.dumps({'audit_id':'sealed','sealed':True}))
    sealed=run('init','--audit-dir',str(audit),'--target-root',str(project),'--force')
    assert sealed.returncode!=0 and 'sealed' in sealed.stdout.lower() and 'new audit directory' in sealed.stdout.lower(), sealed.stdout
  print('PASS: reporting terminology is scoped correctly and sealed audit workspaces cannot be reused for post-remediation runs')
  return 0
if __name__=='__main__': raise SystemExit(main())
