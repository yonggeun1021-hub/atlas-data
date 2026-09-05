"""Install an immutable local control bundle; preserve worker files and processes."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
FILES=('audit/ic_weekly_control.py','config/ic_weekly_control_packet.schema.json',
       'evidence/ic_weekly_control/IC6-TSM-20260904.json','services/ic_weekly_control/controller_hook.py')
TICK_ANCHOR="    child=subprocess.Popen([sys.executable,str(BASE/'cio_dispatcher.py')],cwd=BASE,start_new_session=True)\n"
TICK_HOOK="    import ic_control_bridge\n    ic_control_bridge.tick(BASE)\n"
STATUS_ANCHOR='    print(status.render(view,state))\n'
STATUS_HOOK='    import ic_control_bridge\n    view=ic_control_bridge.surface(BASE,view)\n'
SUMMARY_HOOK='    print(ic_control_bridge.summary(BASE))\n'

def install(base):
    base=Path(base)
    originals={n:(base/n).read_text() for n in ('dispatcher_tick.py','atlas-status')}
    for n,anchor in [('dispatcher_tick.py',TICK_ANCHOR),('atlas-status',STATUS_ANCHOR)]:
        if originals[n].count(anchor)!=1: raise ValueError('Unknown integration anchor: '+n)
    # Refuse foreign hooks, while making this exact installer rerunnable.
    if 'ic_control_bridge' in originals['dispatcher_tick.py'] and TICK_HOOK not in originals['dispatcher_tick.py']: raise ValueError('Existing hook differs')
    if 'ic_control_bridge' in originals['atlas-status'] and STATUS_HOOK not in originals['atlas-status']: raise ValueError('Existing status hook differs')
    sha=hashlib.sha256(b''.join((ROOT/p).read_bytes() for p in FILES)).hexdigest()
    release=base/'ic_control_releases'/sha
    for name in FILES:
        dest=release/name;dest.parent.mkdir(parents=True,exist_ok=True)
        payload=(ROOT/name).read_bytes()
        if dest.exists() and dest.read_bytes()!=payload: raise ValueError('Immutable release mismatch')
        dest.write_bytes(payload)
    bridge='''"""Local IC adapter. Failures are durable; money workers are never stopped."""
import importlib.util
from pathlib import Path
import runtime_ops as ops
RELEASE = __RELEASE__
spec=importlib.util.spec_from_file_location('atlas_ic_control_hook',str(Path(RELEASE)/'services/ic_weekly_control/controller_hook.py'))
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
def tick(base):
    try: return hook.tick(base,ops.now())
    except Exception as exc:
        ops.write(Path(base)/'state/ic_control_error.json',dict(at=ops.now(),error=str(exc)))
        return None
def summary(base):
    return hook.summary(base)
def surface(base,view):
    try: return hook.surface(base,view,ops.now())
    except Exception as exc:
        result=dict(view);result['user_action']=str(view.get('user_action',''))+'; IC_CONTROL_ERROR: '+str(exc)
        return result
'''.replace('__RELEASE__',repr(str(release)))
    backup=base/'backups'/('ic-control-'+sha[:12]);backup.mkdir(parents=True,exist_ok=True)
    replacements={
        'dispatcher_tick.py':originals['dispatcher_tick.py'] if TICK_HOOK in originals['dispatcher_tick.py'] else originals['dispatcher_tick.py'].replace(TICK_ANCHOR,TICK_ANCHOR+TICK_HOOK),
        'atlas-status':originals['atlas-status'] if STATUS_HOOK in originals['atlas-status'] else originals['atlas-status'].replace(STATUS_ANCHOR,STATUS_HOOK+STATUS_ANCHOR),
        'ic_control_bridge.py':bridge}
    if SUMMARY_HOOK not in replacements['atlas-status']:
        replacements['atlas-status']=replacements['atlas-status'].replace(STATUS_ANCHOR,STATUS_ANCHOR+SUMMARY_HOOK)
    for name,text in replacements.items():
        compile(text,name,'exec')
        path=base/name
        if path.exists() and not (backup/name).exists(): shutil.copy2(path,backup/name)
        # Exact observed-byte compare protects concurrent controller owners.
        if name in originals and path.read_text()!=originals[name]: raise ValueError('Concurrent update: '+name)
        temp=path.with_name(name+'.ic-tmp');temp.write_text(text)
        if path.exists(): shutil.copymode(path,temp)
        temp.replace(path)
    manifest=dict(release=str(release),bundle_sha256=sha,changed_local_files=list(replacements),backup=str(backup),worker_files_changed=[],process_signals_sent=0)
    (release/'install_receipt.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True)
    print(json.dumps(install(parser.parse_args().base),indent=2))
