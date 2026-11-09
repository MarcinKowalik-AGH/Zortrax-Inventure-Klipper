#!/usr/bin/env python3
"""Install the two verified thermal/UART host modules with exact backups."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parent
def sha(data):return hashlib.sha256(data).hexdigest()

def running_service_script(service='klipper', proc_root=Path('/proc')):
    """Resolve the script of the running service, before stopping it.

    A configured ~/klipper may be a different checkout. Read the actual
    process arguments instead of guessing a repository from that name.
    """
    result=subprocess.run(['systemctl','show',service,'--property=MainPID','--value'],
                          capture_output=True,text=True,check=False,timeout=10)
    pid=result.stdout.strip()
    if result.returncode or not pid.isdecimal() or int(pid)<=0:
        raise ValueError('Cannot identify the running '+service+' service. No files changed; start the service and retry.')
    process=Path(proc_root)/pid
    argv=[value.decode(errors='surrogateescape') for value in
          (process/'cmdline').read_bytes().split(b'\0') if value]
    indices=[i for i,value in enumerate(argv[1:],1) if Path(value).name=='klippy.py']
    if len(indices)!=1:
        raise ValueError('The running service does not identify one klippy.py script. No files changed.')
    index=indices[0];script=Path(argv[index])
    if not script.is_absolute():script=process/'cwd'/script
    script=script.resolve(strict=True)
    if not script.is_file() or script.parent.name!='klippy':
        raise ValueError('Unexpected running Klipper script path. No files changed.')
    return process,argv,index,script

def running_klipper_target(service='klipper',proc_root=Path('/proc')):
    return running_service_script(service,proc_root)[3].parent.parent

def running_printer_config(service='klipper',proc_root=Path('/proc')):
    process,argv,index,script=running_service_script(service,proc_root)
    if len(argv)<=index+1 or argv[index+1].startswith('-'):
        raise ValueError('Cannot identify the running printer config; no files changed')
    config=Path(argv[index+1])
    if not config.is_absolute():config=process/'cwd'/config
    config=config.resolve(strict=True)
    if not config.is_file():raise ValueError('Running config is not a file')
    return config

def install(target,apply=False,rollback=None,service='klipper'):
    target=Path(target).expanduser().resolve(strict=True)
    extras=target/'klippy/extras'
    meta=json.loads((ROOT/'THERMAL_HOST_MANIFEST.json').read_text())
    names=('inventure_mcu.py','inventure_oled.py','serialhdl.py')
    def dest_path(name):return target/'klippy/serialhdl.py' if name=='serialhdl.py' else extras/name
    incoming={name:(ROOT/name).read_bytes() for name in names}
    expected={'inventure_mcu.py':meta['module_sha256'],
              'inventure_oled.py':meta['oled_module_sha256'],'serialhdl.py':meta['serialhdl_sha256']}
    accepted={'inventure_mcu.py':{meta['baseline_sha256'],*meta.get('allowed_existing_sha256',[]),expected['inventure_mcu.py']},
              'inventure_oled.py':{meta['oled_baseline_sha256'],expected['inventure_oled.py'],*meta.get('oled_allowed_existing_sha256',[])},
              'serialhdl.py':{*meta['serialhdl_allowed_sha256'],expected['serialhdl.py']}}
    before={}
    for name in names:
        dest=dest_path(name)
        if not dest.is_file():raise ValueError('Missing verified Inventure module: '+str(dest))
        if sha(incoming[name])!=expected[name]:raise ValueError('Package module checksum differs: '+name)
        compile(incoming[name],str(dest),'exec')
        before[name]=dest.read_bytes()
        if sha(before[name]) not in accepted[name]:
            raise ValueError('Current '+name+' differs from verified R6/R1/R2. No files changed.')
    backups=target/'.inventure-thermal-test-r2-backups'
    if rollback:
        backup=Path(rollback).expanduser().resolve(strict=True)
        if not backup.is_relative_to(backups.resolve()):raise ValueError('Use a backup from this installation')
        if any(sha(before[name])!=expected[name] for name in names):raise ValueError('Current adapter differs from this release')
        incoming={name:(backup.parent/name).read_bytes() for name in names}
        if any(sha(incoming[name]) not in accepted[name] for name in names):raise ValueError('Unexpected rollback source')
    elif all(sha(before[name])==expected[name] for name in names):
        result={'status':'ALREADY_INSTALLED','path':str(extras),'host_revision':meta['host_revision']}
        print(json.dumps(result));return result
    plan={'status':'READY_TO_APPLY' if not apply else 'INSTALLING','path':str(extras),
          'host_revision':meta['host_revision'],
          'before_sha256':{name:sha(before[name]) for name in names},
          'after_sha256':{name:sha(incoming[name]) for name in names}}
    if not apply:print(json.dumps(plan,indent=2));return plan
    active=subprocess.run(['systemctl','is-active','--quiet',service],check=False)
    if active.returncode==0:raise ValueError('Stop the Klipper service before applying')
    if active.returncode!=3:raise ValueError('Cannot verify Klipper service state')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    saved=backups/stamp;saved.mkdir(parents=True)
    for name in names:shutil.copy2(dest_path(name),saved/name)
    staged={name:dest_path(name).with_name('.'+name+'.thermal-r2.new') for name in names}
    try:
        for name in names:
            staged[name].write_bytes(incoming[name])
            staged[name].chmod(dest_path(name).stat().st_mode&0o777)
        for name in names:staged[name].replace(dest_path(name))
        assert all(sha(dest_path(name).read_bytes())==sha(incoming[name]) for name in names)
    except BaseException:
        for name in names:
            shutil.copy2(saved/name,dest_path(name))
            staged[name].unlink(missing_ok=True)
        raise
    result=dict(plan,status='INSTALLED',backup=str(saved/'inventure_mcu.py'))
    print(json.dumps(result,indent=2));return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--klipper',default=str(Path.home()/'klipper'))
    p.add_argument('--apply',action='store_true');p.add_argument('--rollback',type=Path)
    p.add_argument('--running-path',action='store_true',
                   help='Print the active service repository path without changing any file')
    p.add_argument('--running-config',action='store_true',help='Print the active printer config path')
    p.add_argument('--service',default='klipper');a=p.parse_args()
    try:
        if a.running_path or a.running_config:
            if a.apply or a.rollback:raise ValueError('--running-path is read-only')
            if a.running_path and a.running_config:raise ValueError('Select one running path')
            print(running_printer_config(a.service) if a.running_config else running_klipper_target(a.service))
        else:install(a.klipper,a.apply,a.rollback,a.service)
    except (OSError,ValueError,AssertionError,subprocess.SubprocessError) as exc:
        raise SystemExit('REFUSED: '+str(exc))
if __name__=='__main__':main()
