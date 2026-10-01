"""Bounded local orchestration of explicitly approved existing-session CLI jobs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

PLAN_SCHEMA = 'colab-calculation-plan-v1'
RECEIPT_SCHEMA = 'colab-calculation-receipt-v2'

# The official CLI's `exec` returns exit code 0 even when the remote code raises or calls sys.exit(n)
# (measured live against google-colab-cli 0.7.4, see docs/FINDINGS.md), so a CLI exit code alone cannot establish remote success.
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
REMOTE_FAILURE_SIGNALS = (
    ('traceback', re.compile(r'Traceback \(most recent call last\)')),
    ('ipython-exception-banner', re.compile(r'An exception has occurred')),
    ('exception-line', re.compile(r'^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\s*:', re.M)),
)
# `colab sessions` prints `[name] machine-id | Hardware: CPU | Shape: ... | Variant: ...` per session.
SESSION_LINE = re.compile(r'^\[(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{0,79})\]\s+(?P<machine>\S+)\s+\|\s+Hardware:\s*(?P<hardware>[^|\n]+?)\s*(?:\||$)', re.M)
# Name-level screen only: it cannot see contents. Do not rename secrets to bypass it.
SECRET_PATH = re.compile(
    r'(^|/)(id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|\.ssh|\.aws|\.gnupg|\.netrc|\.npmrc|\.pypirc|\.env(\.[^/]*)?|\.config|Keychains?'
    r'|[^/]*\.(pem|key|p12|pfx|kdbx)|[^/]*(credential|secret|token|password)[^/]*)(/|$)', re.I)
RESERVED_PREFIXES = ('UPLOAD-', 'DOWNLOAD-', 'EXEC.', 'PRECHECK.', 'POSTCHECK.')

def resolve_cli(value=None):
    """Return the Colab CLI executable: explicit value, else $COLAB_CLI. A bare name is looked up on PATH."""
    value = value or os.environ.get('COLAB_CLI')
    if not value: raise ValueError('Colab CLI not configured: set COLAB_CLI or pass --cli')
    path = Path(value)
    if path.parent == Path('.') and not str(value).startswith('.'):
        found = shutil.which(str(value))
        if found is None: raise ValueError('Colab CLI not found on PATH')
        path = Path(found)
    path = path.absolute()
    if not path.is_file() or not os.access(path, os.X_OK): raise ValueError('Colab CLI not found or not executable')
    return path

def stamp_zone():
    """Timezone for folder names and START records: $COLAB_CALC_TZ (IANA name), default UTC."""
    return ZoneInfo(os.environ.get('COLAB_CALC_TZ') or 'UTC')

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def strict_load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Plan must be a bounded regular file')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

def regular(value):
    p = Path(value)
    if not p.is_absolute() or p.is_symlink() or any(q.is_symlink() for q in p.parents) or not p.is_file():
        raise ValueError('Expected absolute regular local file reached without symlinks')
    if SECRET_PATH.search(str(p)):
        raise ValueError('Credential-like path refused; this orchestrator never uploads secrets')
    return p

def validate(plan):
    keys = {'schema','purpose','session','execution_approved','script','timeout_seconds','output_parent','uploads','downloads'}
    if not isinstance(plan, dict) or not keys <= set(plan) <= keys | {'success_marker'}: raise ValueError('Exact plan fields required')
    if plan['schema'] != PLAN_SCHEMA or plan['execution_approved'] is not True:
        raise ValueError('Schema/approval mismatch')
    for name in ('purpose','session'):
        if not isinstance(plan[name], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', plan[name]):
            raise ValueError('Invalid purpose/session')
    if 'success_marker' in plan and (not isinstance(plan['success_marker'], str) or not re.fullmatch(r'[\x20-\x7e]{8,200}', plan['success_marker'])):
        raise ValueError('success_marker must be 8..200 printable ASCII characters')
    if type(plan['timeout_seconds']) is not int or not 1 <= plan['timeout_seconds'] <= 3600:
        raise ValueError('Timeout outside 1..3600')
    if regular(plan['script']).stat().st_size>1024*1024: raise ValueError('Script exceeds 1 MiB')
    if not isinstance(plan['output_parent'],str) or not Path(plan['output_parent']).is_absolute():
        raise ValueError('Absolute output parent required')
    for field in ('uploads','downloads'):
        if not isinstance(plan[field],list) or len(plan[field])>64: raise ValueError('Invalid artifact list')
        seen=set()
        for item in plan[field]:
            expected={'local','remote'} if field=='uploads' else {'remote','name','sha256'}
            if not isinstance(item,dict) or set(item)!=expected: raise ValueError('Artifact fields mismatch')
            remote=item['remote']
            if not isinstance(remote,str) or not remote.startswith('/content/') or '..' in Path(remote).parts or any(ord(c)<32 for c in remote):
                raise ValueError('Invalid remote path')
            if remote in seen: raise ValueError('Duplicate remote artifact')
            seen.add(remote)
            if field=='uploads':
                if regular(item['local']).stat().st_size>64*1024*1024: raise ValueError('Upload exceeds 64 MiB; design batching separately')
            elif not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}',item['name']) or not re.fullmatch(r'[0-9a-f]{64}',item['sha256']):
                raise ValueError('Invalid download name/digest')
            if field=='downloads' and (item['name'] in {'PLAN.json','EXECUTED_SCRIPT.py','RUN_RECEIPT.json'} or item['name'].startswith(RESERVED_PREFIXES)):
                raise ValueError('Download name reserved for orchestration evidence')
        if field=='downloads' and len({x['name'] for x in plan[field]})!=len(plan[field]):
            raise ValueError('Duplicate download name')
    return plan

def write_json(path,value):
    with path.open('x') as f: json.dump(value,f,indent=2,allow_nan=False); f.write('\n')

def remote_failure_signals(stderr_path):
    text=ANSI.sub('',stderr_path.read_text(errors='replace'))
    return [name for name,pattern in REMOTE_FAILURE_SIGNALS if pattern.search(text)]

def run(plan, cli=None):
    validate(plan)
    cli=resolve_cli(cli)
    stamp=datetime.now(stamp_zone()).strftime('%Y%m%d-%H%M%S')
    root=Path(plan['output_parent'])/(stamp+'__'+plan['purpose'])
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    stage='SNAPSHOT'
    receipt={'schema':RECEIPT_SCHEMA,'session':plan['session'],
             'new_vm_allocated':None,'numeric_validation':'NOT_EVALUATED','scientific_claim_effect':'NONE'}
    try:
        write_json(root/'PLAN.json',plan)
        script=regular(plan['script'])
        frozen=root/'EXECUTED_SCRIPT.py'
        frozen.write_bytes(script.read_bytes())
        receipt['script_sha256']=digest(frozen)
        def call(label,args):
            nonlocal stage
            stage=label
            write_json(root/(label+'.START.json'),{'stage':label,'started':datetime.now(stamp_zone()).isoformat()})
            with (root/(label+'.stdout')).open('xb') as out, (root/(label+'.stderr')).open('xb') as err:
                result=subprocess.run([str(cli),*args],stdout=out,stderr=err,timeout=plan['timeout_seconds']+30,check=False)
            if result.returncode: raise RuntimeError(f'{label} CLI exit {result.returncode}; inspect logs before retry')
        def locate(label):
            call(label,['sessions'])
            text=ANSI.sub('',(root/(label+'.stdout')).read_text(errors='replace'))
            for found in SESSION_LINE.finditer(text):
                if found['name']==plan['session']: return found['machine'],found['hardware']
            return None
        before=locate('PRECHECK')
        if before is None: raise ValueError('Named session is not live on the server; this orchestrator never creates one')
        receipt.update(session_machine_before=before[0],session_hardware=before[1])
        receipt['uploads']=[]
        for i,item in enumerate(plan['uploads']):
            src=regular(item['local']); snap=root/f'UPLOAD-{i:03d}'
            snap.write_bytes(src.read_bytes())
            receipt['uploads'].append({'remote':item['remote'],'sha256':digest(snap),'bytes':snap.stat().st_size})
            call(f'UPLOAD-{i:03d}',['upload','-s',plan['session'],str(snap),item['remote']])
        call('EXEC',['exec','-s',plan['session'],'-f',str(frozen),'--timeout',str(plan['timeout_seconds'])])
        signals=remote_failure_signals(root/'EXEC.stderr')
        receipt['remote_failure_signals']=signals
        if signals: raise RuntimeError('Remote code reported failure although the CLI exited 0: '+', '.join(signals))
        marker=plan.get('success_marker')
        if marker is not None:
            lines=ANSI.sub('',(root/'EXEC.stdout').read_text(errors='replace')).splitlines()
            receipt['success_marker_found']=marker in lines
            if marker not in lines: raise RuntimeError('Required success marker line absent from EXEC.stdout')
        for i,item in enumerate(plan['downloads']):
            target=root/item['name']
            if target.exists(): raise ValueError('Download collides with evidence file')
            call(f'DOWNLOAD-{i:03d}',['download','-s',plan['session'],item['remote'],str(target)])
            if target.is_symlink() or digest(target)!=item['sha256']: raise ValueError('Downloaded digest mismatch')
        after=locate('POSTCHECK')
        receipt['session_machine_after']=after[0] if after else None
        receipt['same_machine_observed']=after is not None and after[0]==before[0]
        receipt['new_vm_allocated']=False if receipt['same_machine_observed'] else None
        if not receipt['same_machine_observed']: raise ValueError('Session machine changed or vanished; allocation history is unknown')
        warnings=[]
        if marker is None and not plan['downloads']: warnings.append('NO_SUCCESS_MARKER_AND_NO_DOWNLOAD_DIGEST: remote success rests on the stderr screen only')
        receipt.update(status='PASS_EXECUTION',download_count=len(plan['downloads']),result_root=str(root),warnings=warnings,
                       pass_scope='CLI exit codes, remote-failure stderr screen, optional success marker, declared download digests, same machine before/after')
    except Exception as exc:
        receipt.update(status='FAIL_STOP',failed_stage=stage,exception_type=type(exc).__name__,message=str(exc),remote_termination_confirmed=False,retry_authorized=False)
        write_json(root/'RUN_RECEIPT.json',receipt)
        raise
    write_json(root/'RUN_RECEIPT.json',receipt)
    return root

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--cli',help='Colab CLI executable (default: $COLAB_CLI)')
    args=parser.parse_args()
    print(run(strict_load(args.plan),cli=args.cli))

if __name__=='__main__': main()
