#!/usr/bin/env python3
"""Install the verified Inventure host in the active Klipper checkout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inventure_host_installer',
    ROOT/'orange_pi/install_thermal_test.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def service(action):
    prefix = [] if os.geteuid() == 0 else ['sudo']
    subprocess.run(prefix+['systemctl',action,'klipper'],check=True,timeout=45)


def check_print_job():
    """Do not stop an active/paused print when Moonraker reports one."""
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:7125/printer/objects/query?print_stats',timeout=3) as r:
            value=json.load(r)
    except (OSError,ValueError,urllib.error.URLError):
        # The diagnostic skeleton may have no virtual_sdcard/print_stats.
        return
    state=value.get('result',{}).get('status',{}).get('print_stats',{}).get('state')
    if state in ('printing','paused'):
        raise ValueError('Finish or cancel the current print before updating the host')


def update(check_only=False):
    # Resolve the running stage before stopping it; ~/klipper can be another
    # checkout. No UART/config/bootloader/MCU write is part of this installer.
    target=core.running_klipper_target()
    config=core.running_printer_config()
    plan=core.install(target)
    identity={'host_revision':plan['host_revision'],'klipper_root':str(target),
              'printer_config':str(config),'MCU_changed':False,'config_changed':False}
    check_print_job()
    if check_only:
        print(json.dumps(dict(identity,status='CHECK_OK'),indent=2))
        return
    if os.geteuid()!=0:
        subprocess.run(['sudo','-v'],check=True,timeout=120)
    service('stop')
    result=None
    try:
        # Recheck module hashes after the service is stopped. The core owns
        # exact backups, atomic staging and rollback of a partial copy.
        result=core.install(target,apply=True)
        service('start')
        subprocess.run(['systemctl','is-active','--quiet','klipper'],check=True,timeout=10)
    except BaseException:
        if result and result.get('status')=='INSTALLED':
            service('stop')
            core.install(target,apply=True,rollback=Path(result['backup']))
        service('start')
        raise
    status='HOST_RESTARTED' if result['status']=='ALREADY_INSTALLED' else 'HOST_UPDATED'
    print(json.dumps(dict(identity,status=status,backup=result.get('backup')),
                     indent=2))
    print('In Klipper console: INVENTURE_VERSION')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Read-only validation')
    args=parser.parse_args()
    try:update(args.check)
    except (OSError,ValueError,AssertionError,subprocess.SubprocessError) as exc:
        raise SystemExit('UPDATE FAILED: '+str(exc))


if __name__=='__main__':
    main()
