#!/usr/bin/env python3
"""Install the verified Inventure host in the active Klipper checkout."""
import argparse
from collections import defaultdict
import configparser
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
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


def nozzle_limit_updates(target, config):
    reader_spec = importlib.util.spec_from_file_location(
        'inventure_klipper_configfile', target/'klippy/configfile.py')
    module = importlib.util.module_from_spec(reader_spec)
    reader_spec.loader.exec_module(module)
    source_reader = module.ConfigFileReader()
    sources = {}

    class TracingReader(module.ConfigFileReader):
        def append_fileconfig(self, fileconfig, data, filename):
            parsed = source_reader.build_fileconfig(data, filename)
            for section in ('extruder', 'extruder1'):
                if parsed.has_option(section, 'max_temp'):
                    sources[section] = Path(filename).resolve()
            super().append_fileconfig(fileconfig, data, filename)

    reader = TracingReader()
    active = reader.build_fileconfig_with_includes(reader.read_config_file(config), str(config))
    by_file = defaultdict(set)
    matched = False
    for tool, section in enumerate(('extruder', 'extruder1')):
        if (active.get(section, 'heater_pin', fallback='') != 'inventure_r5:HEATER_T%d'%tool
                or active.get(section, 'sensor_type', fallback='') != 'Inventure R5 ADC'
                or active.getint(section, 'sensor_channel', fallback=-1) != tool):
            continue
        matched = True
        if active.getfloat(section, 'max_temp') == 220:
            by_file[sources[section]].add(section)
    if not matched:
        raise ValueError('The active configuration has no stock Inventure R5 nozzle heater')

    updates = {}
    for source, sections in by_file.items():
        original = source.read_bytes()
        lines = original.decode('utf-8').splitlines(keepends=True)
        section = None
        found = set()
        for index, line in enumerate(lines):
            header = configparser.RawConfigParser.SECTCRE.match(line.strip())
            if header is not None:
                section = header.group('header')
            if section not in sections:
                continue
            option = re.match(r'^(\s*max_temp\s*[:=]\s*)220(?:\.0+)?(?=\s|[#;]|$)', line, re.I)
            if option is not None:
                if section in found:
                    raise ValueError('Multiple legacy max_temp definitions in '+str(source))
                lines[index] = option.group(1)+'310'+line[option.end():]
                found.add(section)
        if found != sections:
            raise ValueError('Cannot locate the active legacy max_temp in '+str(source))
        updates[source] = (original, ''.join(lines).encode())
    return updates


def atomic_config_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            shutil.copymode(path, staged)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def update(check_only=False, upgrade_nozzle_limits=False):
    # Resolve the running stage before stopping it; ~/klipper can be another
    # checkout. Config migration is enabled by --upgrade-nozzle-limits.
    target=core.running_klipper_target()
    config=core.running_printer_config()
    plan=core.install(target)
    config_updates=nozzle_limit_updates(target,config) if upgrade_nozzle_limits else {}
    identity={'host_revision':plan['host_revision'],'klipper_root':str(target),
              'printer_config':str(config),'MCU_changed':False,'config_changed':False}
    check_print_job()
    if check_only:
        print(json.dumps(dict(identity,status='CHECK_OK',
            planned_config_updates=list(map(str,config_updates))),indent=2))
        return
    if os.geteuid()!=0:
        subprocess.run(['sudo','-v'],check=True,timeout=120)
    service('stop')
    result=None
    config_backups={}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    try:
        # Recheck module hashes after the service is stopped. The core owns
        # exact backups, atomic staging and rollback of a partial copy.
        result=core.install(target,apply=True)
        for path,(before,after) in config_updates.items():
            if path.read_bytes()!=before:
                raise ValueError('The active configuration changed during the update: '+str(path))
            backup=path.with_name(path.name+'.pre-nozzle-limit-'+stamp+'.bak')
            shutil.copy2(path,backup)
            config_backups[path]=backup
            atomic_config_write(path,after)
        service('start')
        subprocess.run(['systemctl','is-active','--quiet','klipper'],check=True,timeout=10)
    except BaseException:
        if config_backups or result and result.get('status')=='INSTALLED':
            service('stop')
            for path,backup in config_backups.items():
                atomic_config_write(path,backup.read_bytes())
            if result and result.get('status')=='INSTALLED':
                core.install(target,apply=True,rollback=Path(result['backup']))
        service('start')
        raise
    status='HOST_RESTARTED' if result['status']=='ALREADY_INSTALLED' else 'HOST_UPDATED'
    identity.update(config_changed=bool(config_backups),
                    config_backups={str(path):str(backup) for path,backup in config_backups.items()})
    print(json.dumps(dict(identity,status=status,backup=result.get('backup')),
                     indent=2))
    print('In Klipper console: INVENTURE_VERSION')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Read-only validation')
    parser.add_argument('--upgrade-nozzle-limits',action='store_true',
                        help='Migrate the legacy 220 C stock R5 nozzle limit to 310 C')
    args=parser.parse_args()
    try:update(args.check,args.upgrade_nozzle_limits)
    except (OSError,ValueError,AssertionError,configparser.Error,subprocess.SubprocessError) as exc:
        raise SystemExit('UPDATE FAILED: '+str(exc))


if __name__=='__main__':
    main()
