#!/usr/bin/env python3
"""Stage and activate the complete R38.6 host on an existing Inventure account.

The diagnostic RC8 skeleton is not an upgradeable native printer profile.
This entry point installs the pinned runtime separately, stages a complete
configuration, validates it without an MCU, and switches the actual service.
It never flashes firmware or issues movement, heating, or reset commands.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import inventure_bootstrap as bootstrap
from inventure_bootstrap_config import stage_config
import inventure_config_layout as layout
import fluidd_web
from inventure_configuration_audit_restore import RELEASE as LEVELING_UI_RELEASE
import inventure_accepted_home_restore as accepted_home
import inventure_oem_home_restore as oem_home
import inventure_operator_home_restore as operator_home
from inventure_information_restore import RELEASE as INFORMATION_RELEASE
from inventure_information_restore import COMMUNICATION_RELEASE

spec = importlib.util.spec_from_file_location('inventure_existing_installer', ROOT/'install-orange-pi.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
core, dashboard = updater.core, updater.dashboard

MCU_RELEASE = 'RC8-CONTROL-R7-HOMING-R16'
MCU_SERVICE_HANDOFF_RELEASE = 'RC8-CONTROL-R7-HOMING-R17'
MCU_NATIVE_PROFILE_RELEASE = 'RC8-CONTROL-R7-HOMING-R18'
MCU_SCHEDULER_RELEASE = 'RC8-CONTROL-R7-HOMING-R21'
MCU_SAFETY_POLICY_RELEASE = 'RC8-CONTROL-R7-HOMING-R22'
MCU_DISPLAY_RELEASE = 'RC8-CONTROL-R7-HOMING-R23-OLED'
MCU_XY_STEP_RELEASE = 'RC8-CONTROL-R7-HOME-R28-XY-TIMERS'
MCU_OEM_XY_STEP_RELEASE = 'RC8-CONTROL-R7-HOME-R29-XY-OEM'
MCU_XY_STEP_R30_RELEASE = 'RC8-CONTROL-R7-HOME-R30-XY-STEP-224'
MCU_CASSETTE_WINDOWS_RELEASE = 'RC8-CONTROL-R7-HOME-R31-CASSETTE-WINDOWS'
XY_STEP_HOST_PROFILES = {MCU_XY_STEP_RELEASE: True, MCU_OEM_XY_STEP_RELEASE: 'r29',
                         MCU_XY_STEP_R30_RELEASE: 'r30', MCU_CASSETTE_WINDOWS_RELEASE: 'r30'}
HOME_RELEASES = ('RC8-CONTROL-R7-HOME-R24-OLED', 'RC8-CONTROL-R7-HOME-R25-INFO-OLED',
                 'RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED', 'RC8-CONTROL-R7-HOME-R27-UART-OLED',
                 *XY_STEP_HOST_PROFILES)
DROPIN = Path('/etc/systemd/system/klipper.service.d/50-inventure-release.conf')
UNIT_MARKER = '# Managed by Inventure install-new-host.py\n'
INSTALLER_RELEASE = 'R31'
SHUTDOWN_LATCH = "Can not update MCU 'mcu' config as it is shutdown"
DB_KEYS = ('macros', 'uiSettings.general.locale')


def api(endpoint, data=None):
    return dashboard.request(dashboard.BASE, endpoint, data)


def regular_bytes(path):
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError('Refusing a symlink in an installation path: '+str(path))
    if not path.exists():
        return None
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Expected a regular file: '+str(path))
    return path.read_bytes()


def verify_snapshot(snapshot):
    for name, expected in snapshot.items():
        data = regular_bytes(Path(name))
        actual = None if data is None else sha256(data).hexdigest()
        if actual != expected:
            raise ValueError('Configuration changed during preparation: '+str(name))


def require_mcu(mcu):
    constants = mcu.get('mcu_constants', {})
    release = constants.get('INVENTURE_MCU_RELEASE')
    if release in HOME_RELEASES:
        if release == MCU_XY_STEP_RELEASE and (
            str(constants.get('INVENTURE_XY_STEP_TIMER_ABI')) != '1'
            or str(constants.get('INVENTURE_XY_STEP_MIN_TICKS')) != '1200'
        ):
            raise ValueError('R28 MCU lacks its CoreXY STEP timer capability')
        if release == MCU_OEM_XY_STEP_RELEASE and any(
            str(constants.get(key)) != value for key, value in (
                ('INVENTURE_XY_STEP_TIMER_ABI', '2'), ('INVENTURE_XY_STEP_MIN_TICKS', '660'),
                ('INVENTURE_XY_STEP_HIGH_TICKS', '294'))
        ):
            raise ValueError('R29 MCU lacks its matched 2.45 us CoreXY STEP timer capability')
        if release in (MCU_XY_STEP_R30_RELEASE, MCU_CASSETTE_WINDOWS_RELEASE) and any(
            str(constants.get(key)) != value for key, value in (
                ('INVENTURE_XY_STEP_TIMER_ABI', '3'), ('INVENTURE_XY_STEP_MIN_TICKS', '448'),
                ('INVENTURE_XY_STEP_HIGH_TICKS', '294'), ('INVENTURE_XY_STEP_LOW_FLOOR_TICKS', '90'),
                ('CLOCK_FREQ', '120000000'))
        ):
            raise ValueError('R30 MCU lacks its matched 224-tick CoreXY STEP timer capability')
        if release == MCU_CASSETTE_WINDOWS_RELEASE and str(constants.get('INVENTURE_FACTORY_FEED_WINDOW_ABI')) != '1':
            raise ValueError('R31 MCU lacks its cassette feed phase capability')
        if release in HOME_RELEASES[3:] and any(str(constants.get(key))!='1' for key in ('INVENTURE_UART_TIMER_WINDOW_ABI','INVENTURE_OLED_COEXISTENCE_ABI')):
            raise ValueError('R27 MCU lacks its sampler/UART priority-window capability')
        if release == 'RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED' and str(constants.get('INVENTURE_OLED_COEXISTENCE_ABI')) != '1':
            raise ValueError('R26 MCU lacks its cooperative DMA transport capability')
        if release in HOME_RELEASES[1:] and any(str(constants.get(key)) != '1' for key in ('INVENTURE_FACTORY_INFO_ABI','INVENTURE_FACTORY_COUNTER_ABI')):
            raise ValueError('R25 MCU lacks its factory information/counter capability')
        if str(constants.get('INVENTURE_NATIVE_HELD_SERVICE_ABI')) != '1':
            raise ValueError('R24 MCU lacks the held-service handoff capability')
        compatible=dict(mcu,mcu_constants=dict(constants,INVENTURE_MCU_RELEASE=MCU_DISPLAY_RELEASE))
        return dict(require_mcu(compatible),mcu_release=release,native_held_service_abi=1)
    expected = {'INVENTURE_NATIVE_XY_HANDOFF_ABI': 1,
                'INVENTURE_NATIVE_Z_CLEARANCE_ABI': 1}
    wrong = {key: constants.get(key) for key, value in expected.items()
             if str(constants.get(key)) != str(value)}
    if release not in (MCU_RELEASE, MCU_SERVICE_HANDOFF_RELEASE,
                       MCU_NATIVE_PROFILE_RELEASE, MCU_SCHEDULER_RELEASE,
                       MCU_SAFETY_POLICY_RELEASE, MCU_DISPLAY_RELEASE):
        wrong['INVENTURE_MCU_RELEASE'] = release
    if release in (MCU_SERVICE_HANDOFF_RELEASE, MCU_NATIVE_PROFILE_RELEASE,
                   MCU_SCHEDULER_RELEASE, MCU_SAFETY_POLICY_RELEASE, MCU_DISPLAY_RELEASE) and str(
            constants.get('INVENTURE_NATIVE_SERVICE_HANDOFF_ABI')) != '1':
        wrong['INVENTURE_NATIVE_SERVICE_HANDOFF_ABI'] = constants.get(
            'INVENTURE_NATIVE_SERVICE_HANDOFF_ABI')
    if release in (MCU_NATIVE_PROFILE_RELEASE, MCU_SCHEDULER_RELEASE,
                   MCU_SAFETY_POLICY_RELEASE, MCU_DISPLAY_RELEASE):
        for key in ('INVENTURE_NATIVE_THERMAL_PROFILE_ABI', 'INVENTURE_FAULT_REASON_ABI'):
            if str(constants.get(key)) != '1':
                wrong[key] = constants.get(key)
    if release in (MCU_SCHEDULER_RELEASE, MCU_SAFETY_POLICY_RELEASE, MCU_DISPLAY_RELEASE):
        for key,value in (('INVENTURE_SCHEDULER_ABI',1),
                          ('INVENTURE_SCHEDULER_NATIVE_HZ',10000),
                          ('INVENTURE_SCHEDULER_SERVICE_HZ',20000),
                          ('INVENTURE_SCHEDULER_GUARD_MS',10)):
            if str(constants.get(key)) != str(value):
                wrong[key] = constants.get(key)
    if release in (MCU_SAFETY_POLICY_RELEASE, MCU_DISPLAY_RELEASE):
        for key in ('INVENTURE_SERVICE_NATIVE_SPLIT_ABI','INVENTURE_POLICY_STATUS_ABI'):
            if str(constants.get(key))!='1':
                wrong[key]=constants.get(key)
    if release == MCU_DISPLAY_RELEASE:
        for key,value in (('INVENTURE_DISPLAY_ERROR_ABI',1),
                          ('INVENTURE_LOCAL_OLED_ANIMATION_ABI',1),
                          ('INVENTURE_LOCAL_OLED_NATIVE_GRAY4',1),
                          ('INVENTURE_LOCAL_SPLASH_ABI',1),
                          ('INVENTURE_OLED_HOSTLINK_TIMEOUT_MS',1500)):
            if str(constants.get(key))!=str(value):
                wrong[key]=constants.get(key)
    if wrong:
        raise ValueError('Install the compatible InventureBoot 1.7 MCU application. '
                         'R38.6 requires MCU R16+ with XY handoff and Z clearance; '
                         'Home All after separate Home X/Y requires R17. Observed: '
                         + json.dumps(wrong, sort_keys=True))
    return dict(mcu_version=mcu.get('mcu_version'), mcu_release=release,
                native_xy_handoff_abi=1, native_z_clearance_abi=1,
                native_service_handoff_abi=int(
                    str(constants.get('INVENTURE_NATIVE_SERVICE_HANDOFF_ABI', 0)) == '1'),
                native_thermal_profile_abi=int(
                    str(constants.get('INVENTURE_NATIVE_THERMAL_PROFILE_ABI', 0)) == '1'),
                fault_reason_abi=int(
                    str(constants.get('INVENTURE_FAULT_REASON_ABI', 0)) == '1'),
                service_native_split_abi=int(str(
                    constants.get('INVENTURE_SERVICE_NATIVE_SPLIT_ABI',0))=='1'),
                policy_status_abi=int(str(
                    constants.get('INVENTURE_POLICY_STATUS_ABI',0))=='1'),
                scheduler_abi=int(str(constants.get('INVENTURE_SCHEDULER_ABI',0)) == '1'),
                scheduler_native_hz=constants.get('INVENTURE_SCHEDULER_NATIVE_HZ'),
                scheduler_service_hz=constants.get('INVENTURE_SCHEDULER_SERVICE_HZ'))


def unit_text(argv, working_directory=None):
    def quote(value):
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Invalid control character in service argument')
        # systemd performs its own specifier/environment expansion, not shell parsing.
        return '"'+value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$')+'"'
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError('Use the absolute Python path of the running Klipper service')
    directory = ''
    if working_directory is not None:
        value = str(working_directory)
        if (not Path(value).is_absolute() or value != value.strip()
                or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value)):
            raise ValueError('Use an absolute Klipper working directory')
        # WorkingDirectory is one path, not an ExecStart word list. Quotes
        # remain literal and make an otherwise absolute path invalid.
        directory = 'WorkingDirectory='+value.replace('%','%%')+'\n'
    return (UNIT_MARKER+'[Service]\n'+directory+'ExecStart=\nExecStart='
            +' '.join(map(quote, argv))+'\n').encode()


def validate_service_unit(data, directory):
    """Use systemd's actual parser before any active service is stopped."""
    analyzer = shutil.which('systemd-analyze')
    if analyzer is None:
        raise ValueError('systemd-analyze is required to validate the Klipper service before activation')
    folder = directory/'systemd-validation'
    folder.mkdir(mode=0o700)
    candidate = folder/'inventure-validation.service'
    candidate.write_bytes(b'[Unit]\nDescription=Inventure startup validation\n'+data)
    result = subprocess.run([analyzer,'--man=no','verify',str(candidate)],
                            capture_output=True,text=True,check=False,timeout=30)
    (folder/'verification.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise ValueError('The proposed Klipper service failed systemd validation: '
                         +(result.stderr or result.stdout).strip())
    return candidate


def discover():
    process, argv, index, script = core.running_service_script()
    config = core.running_printer_config()
    if process.stat().st_uid != os.geteuid():
        raise ValueError('Run this installer as the account running klipper.service')
    if not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file():
        raise ValueError('Cannot preserve the service Python interpreter')
    pid = int(process.name)
    info = api('/printer/info')
    if (info.get('process_id') != pid
            or Path(info.get('klipper_path', '')).resolve() != script.parent.parent
            or Path(info.get('config_file', '')).resolve() != config):
        raise ValueError('Moonraker and systemd do not identify the same Klipper instance')
    if regular_bytes(config) is None:
        raise ValueError('Missing active printer configuration')
    return dict(pid=pid, argv=argv, script_index=index, host=script.parent.parent,
                config=config, info=info)


def idle_mcu():
    info = api('/printer/info')
    if info.get('state') != 'ready':
        raise ValueError('The current host must be Ready before switching: '
                         + str(info.get('state_message')))
    names = api('/printer/objects/list')['objects']
    wanted = [name for name in names if name in ('mcu', 'inventure_mcu', 'print_stats', 'idle_timeout',
                                               'inventure_native_z', 'inventure_oled')
              or name == 'extruder' or name.startswith('heater_generic ')]
    status = api('/printer/objects/query?'+ '&'.join(urllib.parse.quote(n, safe='') for n in wanted))['status']
    if status.get('print_stats', {}).get('state') in ('printing', 'paused'):
        raise ValueError('Finish or cancel the active print before installing')
    if status.get('idle_timeout', {}).get('state') == 'Printing':
        raise ValueError('Wait until motion and heater activity has finished before installing')
    if (status.get('inventure_native_z', {}).get('running')
            or (status.get('inventure_oled', {}).get('ui_transport') or {}).get('active_work')):
        raise ValueError('Finish or cancel the active Inventure service procedure before installing')
    for name in wanted:
        if float(status.get(name, {}).get('target', 0) or 0) > 0:
            raise ValueError('Turn off heater targets before installing: '+name)
    board = status.get('inventure_mcu', {})
    age = board.get('status_age')
    if (board.get('connected') is not True
            or type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= 1
            or type(board.get('armed')) is not int or board['armed'] not in (0, 1)
            or type(board.get('fault')) is not int or not 0 <= board['fault'] <= 255):
        raise ValueError('A fresh Inventure MCU status with armed/fault fields is required. '
                         'Check the MCU connection before installing; the active installation '
                         'has not been changed.')
    if board.get('fault'):
        raise ValueError('The Inventure MCU reports an active safety fault. '
                         'Inspect its cause before resetting or installing.')
    if board.get('armed'):
        raise ValueError('The Inventure MCU remains armed after a previous operation. '
                         'A service stop can expire its heartbeat. With the printer idle '
                         'and heater targets off, run FIRMWARE_RESTART once in Fluidd, '
                         'wait for Ready, then install before any Home or heating command. '
                         'The active installation has not been changed.')
    return status.get('mcu', {})


def recoverable_startup(info):
    """Only a failed configuration load may defer the live MCU check.

    This does not accept a shutdown, connection timeout or printer fault.
    The replacement is checked without serial I/O, then must become Ready
    and pass the normal fresh, idle, compatible-MCU checks inside rollback.
    """
    message = str(info.get('state_message', '')).splitlines()[0]
    return (info.get('state') == 'error'
            and message in ("Unknown pin chip name 'inventure_r5'",
                            "Unknown temperature sensor 'Inventure R5 ADC'"))


def db_read(key):
    try:
        value = api('/server/database/item?namespace=fluidd&key='+urllib.parse.quote(key))['value']
        return dict(exists=True, value=value)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return dict(exists=False)


def db_restore(key, saved):
    if db_read(key) == saved:
        return
    if saved['exists']:
        api('/server/database/item', dict(namespace='fluidd', key=key, value=saved['value']))
    else:
        headers = {}
        if os.environ.get('MOONRAKER_API_KEY'):
            headers['X-Api-Key'] = os.environ['MOONRAKER_API_KEY']
        url = dashboard.BASE+'/server/database/item?namespace=fluidd&key='+urllib.parse.quote(key)
        with urllib.request.urlopen(urllib.request.Request(url, method='DELETE', headers=headers), timeout=6):
            pass
    if db_read(key) != saved:
        raise ValueError('Could not restore Fluidd setting '+key)


def wait_api(seconds=30):
    deadline = time.monotonic()+seconds
    while True:
        try:
            return api('/server/info')
        except (OSError, ValueError, KeyError) as exc:
            if time.monotonic() >= deadline:
                raise ValueError('Moonraker did not return after restart') from exc
            time.sleep(.5)


def wait_ready(host, config, seconds=90, observation_path=None):
    """Never accept the old process's cached Ready response during reconnection."""
    deadline, reason = time.monotonic()+seconds, 'waiting for new Klipper'
    while True:
        try:
            process, _, _, script = core.running_service_script()
            info = api('/printer/info')
            expected=dict(service_script=str(host/'klippy/klippy.py'),
                          process_id=int(process.name),klipper_path=str(host),
                          config_file=str(config),software_version=bootstrap.VERSION)
            observed=dict(service_script=str(script),
                          **{key:info.get(key) for key in ('process_id','klipper_path','config_file','software_version')})
            normalized=dict(observed)
            for key in ('klipper_path','config_file'):
                normalized[key]=str(Path(observed.get(key) or '').resolve())
            mismatches={key:dict(expected=value,observed=observed.get(key))
                        for key,value in expected.items() if normalized.get(key)!=value}
            if observation_path is not None:
                json_write(observation_path,dict(expected=expected,observed=observed,
                           mismatches=mismatches,state=info.get('state'),
                           state_message=info.get('state_message')))
            if mismatches:
                raise ConnectionError('Startup identity mismatch: '+json.dumps(mismatches,sort_keys=True))
            state, message = info.get('state'), str(info.get('state_message', ''))
            if state in ('error', 'shutdown'):
                if message.partition('\n')[0].strip() == SHUTDOWN_LATCH:
                    return dict(info, firmware_restart_required=True)
                raise RuntimeError('New Klipper configuration failed: '+message)
            if state == 'ready':
                values = api('/printer/objects/query?inventure_mcu&configfile&mcu&inventure_oled&inventure_native_z&inventure_information')['status']
                board = values.get('inventure_mcu', {})
                if board.get('host_revision') != bootstrap.HOST_REVISION:
                    raise RuntimeError('Ready process has the wrong Inventure host revision')
                native = values.get('inventure_native_z', {})
                home_reference = (operator_home if bootstrap.current_procedure_profile() == operator_home.PROFILE
                                  else oem_home if bootstrap.current_procedure_profile() == oem_home.PROFILE
                                  else accepted_home)
                if (Path(board.get('host_module','')).resolve() != host/'klippy/extras/inventure_mcu.py'
                        or Path(native.get('host_module','')).resolve() != host/'klippy/extras/inventure_native_z.py'
                        or native.get('homing_safety_release') != home_reference.RELEASE):
                    raise RuntimeError('Ready process loaded a different Inventure MCU or Home module')
                if (native.get('procedure_restore_release') != home_reference.RELEASE
                        or native.get('procedure_baseline_blob') != home_reference.BASELINE_BLOB):
                    raise RuntimeError('Ready process did not load the selected recorded Home procedure')
                if (home_reference in (oem_home,operator_home)
                        and native.get('home_procedure_profile') != home_reference.PROFILE):
                    raise RuntimeError('Ready process did not activate the complete OEM Home profile')
                if values.get('inventure_oled', {}).get('procedure_ui_release') != LEVELING_UI_RELEASE:
                    raise RuntimeError('Ready Klipper did not load the matching OLED procedure UI: '
                                       + LEVELING_UI_RELEASE)
                panel=values.get('inventure_oled', {})
                if panel.get('operator_menu_root') != 'launcher' or panel.get('operator_menu_roots') != ['Klipper','Zortrax Inventure']:
                    raise RuntimeError('Ready Klipper did not activate the requested Klipper / Zortrax Inventure menu')
                if home_reference is operator_home:
                    information=values.get('inventure_information',{})
                    if (panel.get('communication_guard_release') != COMMUNICATION_RELEASE
                            or information.get('communication_guard_release') != COMMUNICATION_RELEASE):
                        raise RuntimeError('Ready Klipper did not load the matching communication guard')
                    if (str(values.get('mcu',{}).get('mcu_constants',{}).get('INVENTURE_UART_TIMER_WINDOW_ABI'))=='1'
                            and panel.get('oled_transport_mode')!='cooperative_dma'):
                        raise RuntimeError('Ready Klipper did not activate the paired cooperative DMA transport')
                    if (information.get('information_release')!=INFORMATION_RELEASE
                            or Path(information.get('host_module','')).resolve()!=host/'klippy/extras/inventure_information.py'
                            or 'inventure_information' not in values.get('configfile',{}).get('config',{})):
                        raise RuntimeError('Ready Klipper did not load the matching Inventure Information service')
                    if information.get('factory_counter_sync') in (None,'DISABLED','CHECKING'):
                        raise ConnectionError('Waiting for the first Inventure Information startup check')
                    help_data=api('/printer/gcode/help')
                    if 'INVENTURE_INFO' not in help_data.get('help',help_data):
                        raise RuntimeError('Ready Klipper did not register INVENTURE_INFO')
                require_mcu(values.get('mcu', {}))
                return dict(info, firmware_restart_required=False, objects=values)
            reason = message or state
        except (OSError, ValueError, KeyError) as exc:
            reason = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError('The new Klipper did not become ready: '+str(reason))
        time.sleep(.5)


def write_unit(data, run_directory, dropin=DROPIN):
    if data is None:
        subprocess.run(['sudo', '--', 'rm', '-f', '--', str(dropin)], check=True, timeout=15)
    else:
        local = run_directory/'service-dropin.conf'
        local.write_bytes(data)
        # Publish atomically: an interrupted install must not truncate the
        # active override and expose the old $KLIPPER_ARGS on next boot.
        temporary = dropin.with_name(dropin.name+'.new')
        try:
            subprocess.run(['sudo', '--', 'install', '-D', '-m', '0644', '--', str(local), str(temporary)],
                           check=True, timeout=15)
            subprocess.run(['sudo', '--', 'mv', '-f', '--', str(temporary), str(dropin)],
                           check=True, timeout=15)
            if regular_bytes(dropin) != data:
                raise OSError('The persisted Klipper service override did not verify')
        finally:
            subprocess.run(['sudo', '--', 'rm', '-f', '--', str(temporary)],
                           check=False, timeout=15)


def reload_units():
    subprocess.run(['sudo', '--', 'systemctl', 'daemon-reload'], check=True, timeout=30)
    result=subprocess.run(['systemctl','show','klipper.service',
                           '--property=NeedDaemonReload','--value'],
                          check=True,capture_output=True,text=True,timeout=10)
    if result.stdout.strip()!='no':
        raise ValueError('systemd has not accepted the Klipper service reload: '+result.stdout.strip())


def service_stopped():
    result = subprocess.run(['systemctl', 'show', 'klipper', '--property=MainPID',
                             '--property=ActiveState'], check=True, text=True,
                            capture_output=True, timeout=10)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return values.get('MainPID') == '0' and values.get('ActiveState') in ('inactive', 'failed')


def restore_web(root, previous):
    # No shell interpolation; only exchange the two known site directories.
    code = ('import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'from fluidd_web import exchange; exchange(Path(sys.argv[2]),Path(sys.argv[3]))')
    subprocess.run(['sudo', '--', sys.executable, '-c', code, str(ROOT), str(root), str(previous)],
                   check=True, timeout=30)


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str)+'\n')


@contextmanager
def timed_stage(directory, name):
    started=time.monotonic()
    try:
        yield
    finally:
        elapsed=round(time.monotonic()-started,3)
        print('Stage '+name+': %.3f s' % elapsed,flush=True)
        # Diagnostic timing must not change a successful activation or mask
        # its original failure if the report medium itself becomes full.
        try:
            path=directory/'installation-timings.json'
            data=json.loads(path.read_text()) if path.exists() else {}
            data[name]=elapsed
            json_write(path,data)
        except (OSError,ValueError):
            pass


class InstallTransaction:
    """Back up every affected file before stop; restore on any activation failure."""
    def __init__(self, directory, updates, unit, moon_changed, root, dropin=DROPIN,
                 before_start=None):
        self.directory, self.updates, self.unit = directory, updates, unit
        self.dropin, self.moon_changed, self.root = dropin, moon_changed, root
        self.before_start = before_start
        self.before_unit = regular_bytes(dropin)
        if self.before_unit is not None and self.before_unit.strip() and not self.before_unit.startswith(UNIT_MARKER.encode()):
            raise ValueError('An unmanaged service override already exists: '+str(dropin))
        self.settings = {key: db_read(key) for key in DB_KEYS}
        self.written, self.unit_written, self.stopped, self.web_previous = [], False, False, None
        self.settings_changed = False
        self.backups = {}
        files = directory/'before'
        files.mkdir(mode=0o700)
        for i, (path, (before, _)) in enumerate(updates.items()):
            if regular_bytes(path) != before:
                raise ValueError('File changed before backup: '+str(path))
            saved = None
            if before is not None:
                saved = files/str(i)
                shutil.copy2(path, saved)
            self.backups[path] = saved
        json_write(directory/'backup.json', dict(
            files={str(p): str(b) if b else None for p,b in self.backups.items()},
            service_dropin=str(dropin), service_dropin_existed=self.before_unit is not None,
            fluidd_settings=self.settings))
        if self.before_unit is not None:
            (directory/'previous-service.conf').write_bytes(self.before_unit)

    def activate_files(self):
        for path, (before, _) in self.updates.items():
            if regular_bytes(path) != before:
                raise ValueError('File changed immediately before activation: '+str(path))
        if regular_bytes(self.dropin) != self.before_unit:
            raise ValueError('The service override changed during preparation')
        for key, before in self.settings.items():
            if db_read(key) != before:
                raise ValueError('Fluidd settings changed during preparation: '+key)
        # A timeout may be reported after systemd already stopped the process.
        self.stopped = True
        updater.service('stop')
        # Include files first, then main files; Klipper is stopped for the whole copy.
        for path, (before, after) in sorted(self.updates.items(), key=lambda item: item[0].name in ('printer.cfg','moonraker.conf')):
            path.parent.mkdir(parents=True, exist_ok=True)
            if regular_bytes(path) != before:
                raise ValueError('File changed after stopping Klipper: '+str(path))
            self.written.append(path)
            try:
                updater.atomic_config_write(path, after, create=before is None)
            except FileExistsError:
                # A concurrent creator owns this file; our no-replace write
                # did not create it, so rollback must not remove it.
                if before is None:
                    self.written.remove(path)
                raise
        self.unit_written = True  # Restore even if install(1) fails after truncation.
        write_unit(self.unit, self.directory, self.dropin)
        reload_units()
        if self.moon_changed:
            updater.service('restart', 'moonraker')
            wait_api()
        if self.before_start is not None:
            self.before_start()
        updater.service('start')

    def rollback(self):
        errors = []
        def attempt(label, action):
            try:
                action()
            except BaseException as exc:
                errors.append(label+': '+str(exc))
        if self.stopped:
            try:
                updater.service('stop')
            except BaseException as exc:
                try:
                    confirmed = service_stopped()
                except BaseException:
                    confirmed = False
                if not confirmed:
                    errors.append('Could not confirm that Klipper stopped; configuration left intact: '+str(exc))
                    json_write(self.directory/'rollback.json', dict(
                        status='ROLLBACK_NEEDS_ATTENTION', errors=errors,
                        service_restart_attempted=False))
                    return errors
        if self.web_previous is not None:
            attempt('Fluidd web', lambda: restore_web(self.root, self.web_previous))
        for path in reversed(self.written):
            saved = self.backups[path]
            if saved is None:
                attempt(str(path), lambda p=path: p.unlink(missing_ok=True))
            else:
                def restore(p=path, b=saved):
                    updater.atomic_config_write(p, b.read_bytes(), create=not p.exists())
                    shutil.copymode(b, p)
                attempt(str(path), restore)
        if self.unit_written:
            attempt('service drop-in', lambda: write_unit(self.before_unit, self.directory, self.dropin))
            attempt('systemd reload', reload_units)
        if self.moon_changed and self.stopped:
            attempt('Moonraker restart', lambda: updater.service('restart', 'moonraker'))
            attempt('Moonraker API', wait_api)
        if self.settings_changed:
            for key, saved in self.settings.items():
                attempt('Fluidd '+key, lambda k=key,s=saved: db_restore(k,s))
        # Do not run a mixed configuration after a failed restore.
        restart_attempted = self.stopped and not errors
        if restart_attempted:
            attempt('old Klipper start', lambda: updater.service('start'))
        json_write(self.directory/'rollback.json', dict(
            status='ROLLED_BACK' if not errors else 'ROLLBACK_NEEDS_ATTENTION', errors=errors,
            service_restart_attempted=restart_attempted))
        return errors


def activate(transaction, host, config, web_source, macros, *, serial=None, baud=None,
             recover_startup=False):
    try:
        transaction.activate_files()
        live = wait_ready(host, config,observation_path=transaction.directory/'startup-observation.json')
        if recover_startup:
            if live.get('firmware_restart_required'):
                raise ValueError('Startup repair requires a Ready printer before accepting the new runtime')
            require_mcu(idle_mcu())
        transaction.settings_changed = True
        groups = dashboard.setup(dashboard.BASE, config.parent, apply=True, wait=60, english=True,
                                 installed_names=lambda: set(macros), expected_revision=bootstrap.HOST_REVISION,
                                 expected_pid=live['process_id'])
        transaction.web_previous = fluidd_web.activate(web_source, transaction.root)
        if not fluidd_web.same_build(web_source, transaction.root):
            raise ValueError('Installed Fluidd assets did not verify')
        # Recheck after the dashboard readback, accepting only the new process.
        final = wait_ready(host, config,observation_path=transaction.directory/'startup-final-observation.json')
        if recover_startup:
            final['recovered_mcu'] = require_mcu(idle_mcu())
        if not final.get('firmware_restart_required') and serial is not None:
            options = final['objects']['configfile']['config']['mcu']
            if options.get('serial') != serial or int(options.get('baud', 0)) != baud:
                raise ValueError('Active UART differs from the requested connection')
        return final, groups
    except BaseException as exc:
        errors = transaction.rollback()
        detail = '; rollback needs attention: '+ '; '.join(errors) if errors else '; previous host/configuration restored'
        raise RuntimeError(str(exc)+detail+'. Backup: '+str(transaction.directory)) from exc


def reuse_generated_helper(host, validated):
    """Reuse this host's compiled helper only for identical verified C inputs.

    This affects the isolated parser check, never the running installation.
    The caller authenticates both runtime manifests before calling here.
    """
    source=host/'klippy/chelper/c_helper.so'
    target=validated/'klippy/chelper/c_helper.so'
    if not source.exists() or host==validated or target.exists():
        return None
    old=json.loads(regular_bytes(host/bootstrap.MANIFEST_NAME))['files']
    new=json.loads(regular_bytes(validated/bootstrap.MANIFEST_NAME))['files']
    def inputs(files):
        return {p:r for p,r in files.items()
                if Path(p).suffix in ('.c','.h') or p=='klippy/chelper/__init__.py'}
    before,after=inputs(old),inputs(new)
    if not before or before!=after:
        return None
    for path,record in before.items():
        if any(sha256(regular_bytes(root/path)).hexdigest()!=record['sha256'] for root in (host,validated)):
            raise ValueError('C helper source changed while preparing validation: '+path)
    data=regular_bytes(source)
    if not data.startswith(b'\x7fELF'):
        return None
    target.write_bytes(data);target.chmod(0o644)
    # New staging mtimes alone would otherwise force recompilation of the
    # identical C sources on every Python-only update on the Orange Pi.
    stamp=max(time.time_ns(),max((validated/p).stat().st_mtime_ns for p in before))
    os.utime(target,ns=(stamp,stamp))
    return dict(source=str(source),sha256=sha256(data).hexdigest(),verified_source_files=len(before))


def stage_runtime(host, directory):
    """Validate the complete renderer without changing a running runtime.

    A recognized first-generation R38.6 bootstrap is the only in-place
    migration input. Its two runtime files join the configuration transaction
    after the service stops. Offline validation uses a complete isolated copy.
    """
    if host.exists() or host.is_symlink():
        before = bootstrap.verify_host(host, allow_legacy=True)
        if before.get('runtime_restoration_required'):
            update = bootstrap.prepare_runtime_update(host)
            validation_host = bootstrap.prepare_host(directory/'validated-runtime')
            helper = reuse_generated_helper(host, validation_host)
            provenance = bootstrap.verify_host(validation_host)
            provenance['generated_helper_reuse'] = helper
            if helper:
                print('Reusing this host\'s C helper for identical verified sources; no rebuild needed.',flush=True)
            return validation_host, provenance, update['files'], before
        return host, before, {}, before
    prepared = bootstrap.prepare_host(host)
    provenance = bootstrap.verify_host(prepared)
    return prepared, provenance, {}, None


def require_identical_configuration(host, original, staged):
    module = updater.klipper_config_module(host)
    before, _ = layout.active_klipper(module, original, {})
    after, _ = layout.active_klipper(module, staged, {})
    previous, proposed = layout.config_values(before), layout.config_values(after)
    # Allow only previously absent, exact canonical Info/UI additions.
    # Existing options, macro overrides, tuning and calibration stay identical.
    import configparser
    controls = configparser.RawConfigParser()
    controls.read([ROOT/'config/INVENTURE_DASHBOARD.cfg', ROOT/'config/INVENTURE_INFORMATION.cfg'])
    allowed = ('gcode_macro INVENTURE_NOZZLE_ALIGNMENT',
               'gcode_macro INVENTURE_PRINTER_INFORMATION',
               'gcode_macro INVENTURE_FIRMWARE_VERSION', 'inventure_information')
    canonical = layout.config_values(controls)
    proposed = dict(proposed)
    for addition in allowed:
        if addition not in previous and addition in canonical and proposed.get(addition) == canonical[addition]:
            proposed.pop(addition)
    if previous != proposed:
        raise ValueError('Accepted Home restoration refuses any effective printer configuration change')


def run(args):
    restore_oem = bool(getattr(args, 'restore_oem_home', False))
    repair = bool(getattr(args, 'repair_home_procedures', False))
    xy_timers = bool(getattr(args, 'with_xy_step_timers', False))
    if sum((restore_oem,repair,bool(getattr(args, 'restore_accepted_home', False)))) > 1:
        raise ValueError('Select exactly one Home recovery profile')
    if xy_timers and not repair:
        raise ValueError('--with-xy-step-timers requires --repair-home-procedures')
    with bootstrap.procedure_profile(operator_home.PROFILE if repair else oem_home.PROFILE if restore_oem else 'current'), bootstrap.xy_step_timers(xy_timers):
        return _run(args)


def _run(args):
    if os.geteuid() == 0:
        raise ValueError('Run as the Klipper user without sudo; privileged service operations request sudo separately')
    os.umask(0o077)
    live = discover()
    requested_recovery = bool(getattr(args, 'recover_startup', False))
    recovery = requested_recovery and live['info'].get('state') != 'ready'
    if recovery and not recoverable_startup(live['info']):
        raise ValueError('--recover-startup requires the exact missing Inventure pin/sensor configuration error')
    mcu = {} if recovery else idle_mcu()
    requirement = None
    try:
        mcu_identity = require_mcu(mcu)
    except ValueError as exc:
        mcu_identity, requirement = {'mcu_version': mcu.get('mcu_version')}, str(exc)
    if args.apply and requirement and not recovery:
        raise ValueError(requirement)
    xy_timers = bool(getattr(args, 'with_xy_step_timers', False))
    xy_profile = XY_STEP_HOST_PROFILES.get(mcu_identity.get('mcu_release')) if xy_timers else False
    if xy_timers and xy_profile is None:
        raise ValueError('--with-xy-step-timers requires a matched R28–R31 MCU')
    cassette_windows = mcu_identity.get('mcu_release') == MCU_CASSETTE_WINDOWS_RELEASE
    with bootstrap.xy_step_timers(xy_profile), bootstrap.cassette_windows(cassette_windows):
        return _install(args, live, mcu_identity, requirement, recovery)


def _install(args, live, mcu_identity, requirement, recovery):
    repair = bool(getattr(args, 'repair_home_procedures', False))
    if repair and (recovery or mcu_identity.get('mcu_release') not in HOME_RELEASES):
        raise ValueError('Install and verify the matching R24–R31 MCU before activating the shared Home/transport profile')
    restore_oem = bool(getattr(args, 'restore_oem_home', False))
    home_reference = operator_home if repair else oem_home if restore_oem else accepted_home
    restore_home = bool(getattr(args, 'restore_accepted_home', False) or restore_oem or repair)
    reference_backup = None
    if restore_home:
        if recovery or args.destination.expanduser().absolute() != live['host']:
            raise ValueError('Accepted Home recovery requires the currently running Ready runtime')
        reference_backup = home_reference.require_reference_backup(ROOT, Path.home(), live['host'])
    installer_commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = Path.home()/'inventure-installations'/stamp
    directory.mkdir(parents=True, mode=0o700)
    print('Preparing verified runtime and configuration. Report directory: '+str(directory), flush=True)
    host = args.destination.expanduser().absolute()
    print('Verifying the installed runtime and preparing an isolated update.', flush=True)
    with timed_stage(directory,'runtime_preparation'):
        validation_host, provenance, runtime_updates, runtime_before = stage_runtime(host, directory)
    config = live['config']
    gcodes = config.parent.parent/'gcodes'
    if not gcodes.is_dir():
        raise ValueError('Expected the existing G-code directory: '+str(gcodes))
    print('Staging active configuration includes and printer state.', flush=True)
    with timed_stage(directory,'configuration_preparation'):
        plan = stage_config(validation_host, directory/'config', config, serial=args.serial, baud=args.baud,
                            gcodes_path=gcodes, restore_field_profile=args.restore_field_profile)
    staged = Path(plan['config_path'])
    if restore_home:
        require_identical_configuration(validation_host, config, staged)
        plan['source_snapshot'][reference_backup['saved_file']] = home_reference.BASELINE_SHA256
        plan['accepted_home_backup'] = reference_backup
    if recovery:
        active, _ = layout.active_klipper(updater.klipper_config_module(validation_host), staged, {})
        for section in active.sections():
            if section.startswith('delayed_gcode ') and active.getfloat(section, 'initial_duration', fallback=0) > 0:
                raise ValueError('Startup repair refuses automatic delayed G-code: '+section)
    if staged.name != config.name:
        raise ValueError('Recovery requires the active main config to be named printer.cfg')
    print('Validating Klipper configuration offline; no MCU commands are sent.', flush=True)
    with timed_stage(directory,'offline_validation'):
        validation = subprocess.run([live['argv'][0], str(ROOT/'ci/validate_bootstrap_config.py'),
                                     '--klipper', str(validation_host), '--config', str(staged)],
                                    check=False, text=True, capture_output=True, timeout=120)
    (directory/'offline-validation.log').write_text(validation.stdout+validation.stderr)
    if validation.returncode:
        raise ValueError('Offline Klipper validation failed: '+validation.stdout.strip()
                         + '. Details: '+str(directory/'offline-validation.log'))
    argv = list(live['argv'])
    argv[live['script_index']] = str(host/'klippy/klippy.py')
    argv[live['script_index']+1] = str(config)
    unit = unit_text(argv, working_directory=host)
    validate_service_unit(unit, directory)
    json_write(directory/'plan.json', dict(runtime=provenance, runtime_before=runtime_before,
                                         runtime_target=str(host),
                                         runtime_files_to_restore=sorted(str(p) for p in runtime_updates),
                                         config=plan, mcu=mcu_identity,
                                         firmware_requirement=requirement, active_before=live))
    if not args.apply:
        result = dict(status='PREPARED_NOT_ACTIVATED', runtime=provenance, config=plan,
                      runtime_target=str(host),
                      runtime_files_to_restore=sorted(str(p) for p in runtime_updates),
                      mcu=mcu_identity, firmware_requirement=requirement,
                      report_directory=str(directory), mcu_flashed=False)
        json_write(directory/'result.json', result)
        return result

    subprocess.run(['sudo', '-v'], check=True, timeout=120)
    web_root = fluidd_web.web_root(args.fluidd_root)
    with timed_stage(directory,'fluidd_package'):
        web_source = fluidd_web.release_bundle(directory, args.fluidd_zip)
    updates = dict(runtime_updates)
    for relative in plan['files']:
        target = config.parent/relative
        before, after = regular_bytes(target), (staged.parent/relative).read_bytes()
        if before != after:
            updates[target] = before, after
    moon_config = updater.running_moonraker_config(config)
    if moon_config is None:
        raise ValueError('Cannot identify the active Moonraker configuration')
    moon_updates = {}
    retired = layout.organize_moonraker(moon_config,
        [config.parent/name for name in plan['loaded_configs']], web_root, moon_updates)
    if set(moon_updates).intersection(updates):
        raise ValueError('Klipper and Moonraker unexpectedly share a modified file')
    updates.update(moon_updates)
    # InstallTransaction retains every changed file, the service override and
    # Fluidd settings. Do not copy the whole config folder as a second backup:
    # it can contain large historical ZIPs and unrelated inactive files which
    # this transaction never writes. Existing backups remain untouched.
    print('Backing up changed files and service settings only.', flush=True)
    transaction = InstallTransaction(directory, updates, unit, bool(moon_updates), web_root,
                                     before_start=lambda: bootstrap.verify_host(host))
    verify_snapshot(plan['source_snapshot'])
    bootstrap.verify_host(host, allow_legacy=True)
    current = discover()
    if current['pid'] != live['pid'] or current['argv'] != live['argv']:
        raise ValueError('Klipper changed during preparation; no service switch performed')
    if recovery:
        if not recoverable_startup(current['info']):
            raise ValueError('The failed-startup state changed during preparation')
    else:
        mcu_identity = require_mcu(idle_mcu())
    print('Activating R38.6; original files and service override are backed up.', flush=True)
    with timed_stage(directory,'activation_and_live_checks'):
        final, groups = activate(transaction, host, config, web_source, plan['macros'],
                                serial=args.serial, baud=args.baud, recover_startup=recovery)
    if recovery:
        mcu_identity = final.pop('recovered_mcu')
        requirement = None
    pending = final.get('firmware_restart_required', False)
    result = dict(
        status='INSTALLED_MCU_RESTART_REQUIRED' if pending else 'INSTALLED_INVENTURE_R38_6',
        installer_release=INSTALLER_RELEASE,
        accepted_home_backup=reference_backup,
        oem_home_restoration=provenance.get('oem_home_restoration'),
        procedure_restore_release=final.get('objects',{}).get('inventure_native_z',{}).get('procedure_restore_release'),
        procedure_baseline_blob=home_reference.BASELINE_BLOB,
        home_procedure_profile=bootstrap.current_procedure_profile(),
        host_revision=bootstrap.HOST_REVISION, software_version=final['software_version'],
        source_commit=bootstrap.SOURCE_COMMIT, source_tag=bootstrap.SOURCE_TAG,
        installer_commit=installer_commit, runtime_manifest=str(host/bootstrap.MANIFEST_NAME),
        restored_runtime_files=sorted(str(p) for p in runtime_updates),
        oled_restoration=provenance.get('oled_restoration'),
        active_klipper_path=final['klipper_path'], active_config=final['config_file'],
        process_id=final['process_id'], state=final['state'], state_message=final['state_message'],
        mcu=mcu_identity, mcu_flashed=False, serial=args.serial, baud=args.baud,
        recovery_profile_used=plan['recovery_profile_used'],
        preserved_state_files=plan['copied_state_files'],
        restored_state_files=plan.get('restored_state_files', {}),
        missing_state_files=plan['missing_state_files'],
        fluidd_root=str(web_root), fluidd_build=fluidd_web.TAG, fluidd_groups=groups,
        retired_stock_web_updaters=retired, previous_fluidd_site=transaction.web_previous,
        report_directory=str(directory), firmware_restart_required=pending,
        hardware_acceptance='pending_operator', automatic_motion_or_heating=False)
    if not pending:
        result['mcu'] = require_mcu(final['objects']['mcu'])
    # This is informative: a source snapshot deliberately has no upstream git updater.
    try:
        result['moonraker_klipper_updater'] = api('/machine/update/status').get('version_info', {}).get('klipper')
    except (OSError, ValueError, KeyError) as exc:
        result['moonraker_updater_readback'] = str(exc)
    json_write(directory/'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Activate after all offline checks; default only stages')
    parser.add_argument('--restore-field-profile', action='store_true',
                        help='Recover the recorded machine profile and missing published, accepted pad calibration')
    parser.add_argument('--recover-startup', action='store_true',
                        help='Repair the exact missing Inventure pin/sensor startup error, validating MCU after Ready')
    parser.add_argument('--restore-accepted-home', action='store_true',
                        help='Require the retained pre-R23.5 Home backup and preserve every effective printer setting')
    parser.add_argument('--restore-oem-home', action='store_true',
                        help='Restore the complete frozen R38.6 Home set with XY release; require its retained backup and preserve all settings')
    parser.add_argument('--repair-home-procedures', '--repair-transport', action='store_true',
                        help='Update transport/Info/UI while preserving the accepted R24 X/Y and frozen T0/Z sequence')
    parser.add_argument('--with-xy-step-timers', action='store_true',
                        help='Use paired CoreXY STEP timers from R28–R31 with the accepted Home profile')
    parser.add_argument('--serial', required=True, help='Actual MCU UART, e.g. /dev/ttyS5')
    parser.add_argument('--baud', required=True, type=int, help='Actual MCU UART baud, e.g. 750000')
    parser.add_argument('--destination', type=Path, default=Path.home()/'klipper-inventure-r38.6')
    parser.add_argument('--fluidd-root', type=Path, help='Existing nginx Fluidd root if more than one is present')
    parser.add_argument('--fluidd-zip', type=Path, help='Verified local web ZIP with adjacent SHA256SUMS')
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        if result.get('firmware_restart_required'):
            print('R38.6 installed. The MCU configuration latch remains shut down. Review its reason, '
                  'then run FIRMWARE_RESTART once in Fluidd and wait for Ready.')
        elif args.apply:
            if args.repair_home_procedures:
                print('R25 Info host installed and Ready/source/profile verified; R24 Home engines preserved. '
                      'Read INVENTURE_VERSION and INVENTURE_INFO in Fluidd.')
            else:
                print('Complete R38.6 host with the dual-thermal OLED installed and verified. '
                      'In Fluidd run INVENTURE_VERSION and INVENTURE_OLED_STATUS.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit('Inventure installation failed: '+str(exc))


if __name__ == '__main__':
    main()
