#!/usr/bin/env python3
"""Install the Inventure and Klipper macro categories in Fluidd's Moonraker database."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

from orange_pi.inventure_fluidd import LEGACY_COMMANDS

GROUPS = ('ZORTRAX INVENTURE', 'Klipper')
KLIPPER = frozenset(('BED_MESH', 'CANCEL_PRINT', 'END_PRINT', 'OLED_TIMERS', 'PID_BED',
                     'PID_CHAMBER', 'PID_HOTEND', 'PID_SAVE', 'PID_STATUS', 'START_PRINT',
                     'PAUSE', 'RESUME', 'COOLDOWN', 'PRINT_CONTROL_STATUS', 'FILAMENT_READY',
                     'M141', 'M191', 'M600', 'M601', 'PAUSE_TEMP_STATUS', 'BED_MESH_CALIBRATE'))
# Secondary buttons stay available in the category editor and console. An existing
# explicit Fluidd visibility preference takes priority for operator controls.
SECONDARY_PREFIXES = ('INVENTURE_DIAG_', 'INVENTURE_DISPLAY_', 'INVENTURE_CAL_', 'INVENTURE_PRINT_',
                      'INVENTURE_MATERIAL_', 'INVENTURE_TEST_')
SECONDARY = {'M141', 'M191', 'INVENTURE_CALIBRATION_STATE', 'INVENTURE_FAN_POLICY', 'INVENTURE_THERMAL_READINGS',
             'INVENTURE_MOTION_REFERENCES', 'INVENTURE_MENU_CHOOSE', 'INVENTURE_MENU_CONFIRM', 'INVENTURE_MENU_BACK',
             'INVENTURE_JAM_SERVICE_STEP', 'INVENTURE_JAM_SERVICE_TEST', 'INVENTURE_JAM_CONFIRM_FLOW',
             'INVENTURE_JAM_CLEARED', 'INVENTURE_JAM_RETRY', 'INVENTURE_JAM_UNLOAD', 'INVENTURE_JAM_LOAD',
             'INVENTURE_JAM_MORE_IN', 'INVENTURE_JAM_MORE_OUT', 'INVENTURE_JAM_FLOW', 'INVENTURE_JAM_WIPE',
             'INVENTURE_JAM_CONTINUE', 'INVENTURE_JAM_EXCHANGE'}
BASE = 'http://127.0.0.1:7125'


def configured_names(configfile):
    return {section.split(None, 1)[1].upper()
            for key in ('config', 'settings') for section in configfile.get(key, {})
            if section.lower().startswith('gcode_macro ')}


def merge_groups(before, names):
    if not isinstance(before, dict):
        raise ValueError('Unexpected Fluidd macro settings')
    after = copy.deepcopy(before)
    categories = after.setdefault('categories', [])
    rows = after.setdefault('stored', [])
    if not isinstance(categories, list) or not isinstance(rows, list):
        raise ValueError('Unexpected Fluidd macro categories or rows')
    by_name = {}
    ids = set()
    for item in categories:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('name', 'id')) or item['id'] in ids:
            raise ValueError('Invalid or duplicate Fluidd category ID')
        ids.add(item['id'])
        if item['name'] in GROUPS:
            if item['name'] in by_name:
                raise ValueError('Duplicate Fluidd category ' + item['name'])
            by_name[item['name']] = item
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            raise ValueError('Invalid Fluidd macro row')
        name = row['name'].upper()
        if name in indexed:
            raise ValueError('Duplicate Fluidd macro ' + name)
        indexed[name] = row
    for old, new in LEGACY_COMMANDS.items():
        if new not in names:
            continue
        if old not in indexed:
            if old in names:
                indexed[old] = dict(name=old, visible=False, categoryId='0')
                rows.append(indexed[old])
            continue
        row = indexed[old]
        if old in names:  # a locally modified old macro is still callable
            row['visible'] = False
        elif new in indexed:
            rows.remove(row)
            del indexed[old]
        else:
            del indexed[old]
            row['name'] = new
            indexed[new] = row
    assignments = {name: GROUPS[0] if name.startswith('INVENTURE_') else GROUPS[1]
                   for name in sorted(names) if name not in LEGACY_COMMANDS and
                   (name.startswith('INVENTURE_') or name in KLIPPER)}
    for group in GROUPS:
        if group not in assignments.values():
            continue
        if group not in by_name:
            cid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://zortrax-inventure.local/macros/' + group))
            if cid in ids:
                raise ValueError('Fluidd category ID is already in use')
            by_name[group] = dict(id=cid, name=group)
            categories.append(by_name[group])
            ids.add(cid)
        for name, target in assignments.items():
            if target != group:
                continue
            if name not in indexed:
                indexed[name] = dict(name=name, visible=not (name in SECONDARY or name.startswith(SECONDARY_PREFIXES)))
                rows.append(indexed[name])
            indexed[name]['categoryId'] = by_name[group]['id']
            if name.startswith('INVENTURE_TEST_') or name in ('INVENTURE_DISPLAY_TEXT_TEST', 'INVENTURE_DISPLAY_REINIT'):
                indexed[name]['visible'] = False
    if all(group in by_name for group in GROUPS):
        first, second = (categories.index(by_name[group]) for group in GROUPS)
        if first > second:
            categories[first], categories[second] = categories[second], categories[first]
    return after, assignments


def request(base, endpoint, data=None):
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('MOONRAKER_API_KEY'):
        headers['X-Api-Key'] = os.environ['MOONRAKER_API_KEY']
    req = urllib.request.Request(base.rstrip('/') + endpoint, headers=headers,
                                 data=None if data is None else json.dumps(data).encode())
    with urllib.request.urlopen(req, timeout=6) as response:
        return json.load(response)['result']


def read_groups(base):
    try:
        return request(base, '/server/database/item?namespace=fluidd&key=macros')['value']
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return {}


def install_english(base):
    key = 'uiSettings.general.locale'
    endpoint = '/server/database/item?namespace=fluidd&key=' + key
    try:
        if request(base, endpoint)['value'] == 'en':
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    request(base, '/server/database/item', dict(namespace='fluidd', key=key, value='en'))
    if request(base, endpoint)['value'] != 'en':
        raise ValueError('Moonraker did not confirm the English Fluidd locale')


class MCUConfigShutdown(ValueError):
    """Klipper parsed the config but the primary MCU rejected configuration."""
    def __init__(self, state, reason):
        super().__init__('Klipper: ' + reason)
        self.state = state
        self.reason = reason


def live_names(base, wait, expected_revision=None, expected_pid=None):
    deadline = time.monotonic() + wait
    while True:
        try:
            if expected_pid is not None:
                current_pid = request(base, '/printer/info').get('process_id')
                if current_pid != expected_pid:
                    raise ConnectionError('Waiting for Klipper PID %s, Moonraker reports %s'
                                          % (expected_pid, current_pid))
            status = request(base, '/printer/objects/query?webhooks&configfile')['status']
            names = configured_names(status.get('configfile', {}))
            state = status.get('webhooks', {}).get('state')
            reason = str(status.get('webhooks', {}).get('state_message', state))
            if state in ('error', 'shutdown'):
                if reason.partition('\n')[0].strip() == "Can not update MCU 'mcu' config as it is shutdown":
                    raise MCUConfigShutdown(state, reason)
                raise ValueError('Klipper: ' + reason)
            if state == 'ready':
                if 'INVENTURE_DIAGNOSTICS' not in names:
                    reason = 'the active config is missing INVENTURE_DIAGNOSTICS'
                elif expected_revision is None:
                    return names
                else:
                    board = request(base, '/printer/objects/query?inventure_mcu=host_revision')
                    actual = board['status'].get('inventure_mcu', {}).get('host_revision')
                    if actual == expected_revision:
                        return names
                    reason = 'running host revision %s, expected %s' % (actual, expected_revision)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 502, 503):
                raise
            reason = str(exc)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError('Inventure dashboard macros are not ready: ' + str(reason))
        time.sleep(1)


def setup(base, directory, apply=False, wait=0, rollback=None, *, installed_names=None,
          expected_revision=None, expected_pid=None, english=False):
    runtime = {}
    names = set()
    if rollback is None:
        try:
            names = live_names(base, wait, expected_revision, expected_pid)
            runtime = dict(klipper_state='ready', macro_source='running_klipper')
            if expected_revision is not None:
                runtime['running_host_revision'] = expected_revision
        except MCUConfigShutdown as exc:
            if installed_names is None:
                raise
            names = installed_names()
            if 'INVENTURE_DIAGNOSTICS' not in names:
                raise ValueError('The installed configuration is missing INVENTURE_DIAGNOSTICS') from exc
            runtime = dict(klipper_state=exc.state, klipper_message=exc.reason,
                           macro_source='installed_klipper_config', firmware_restart_required=True)
    before = read_groups(base)
    if rollback is not None:
        backup = json.loads(rollback.read_text())
        if (backup.get('namespace'), backup.get('key'), backup.get('after')) != ('fluidd', 'macros', before):
            raise ValueError('This backup does not match the current Fluidd settings')
        after, assignments = backup['before'], {}
    else:
        after, assignments = merge_groups(before, names)
    result = dict(runtime, status='ALREADY_GROUPED' if before == after else 'GROUPS_PLANNED',
                  groups={g: sorted(k for k,v in assignments.items() if v==g) for g in GROUPS})
    if apply and before != after:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = directory / ('inventure_fluidd_groups_' + stamp + '.json')
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
            json.dump(dict(namespace='fluidd', key='macros', before=before, after=after), out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        if read_groups(base) != before:
            raise ValueError('Fluidd settings changed while preparing the update')
        response = request(base, '/server/database/item', dict(namespace='fluidd',key='macros',value=after))
        if response.get('value') != after or read_groups(base) != after:
            raise ValueError('Moonraker did not confirm the groups. Backup: ' + str(path))
        result.update(status='GROUPS_INSTALLED', backup=str(path))
    if english and apply and rollback is None:
        try:
            install_english(base)
        except Exception:
            if result.get('backup'):
                setup(base, directory, apply=True, rollback=Path(result['backup']))
            raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--english', action='store_true', help='Set this Fluidd instance to English')
    parser.add_argument('--moonraker', default=BASE)
    parser.add_argument('--config-directory', type=Path, default=Path.home()/'printer_data/config')
    parser.add_argument('--wait', type=int, default=0)
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(setup(args.moonraker, args.config_directory, args.apply, args.wait,
                               args.rollback, english=args.english), indent=2))
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit('Fluidd groups: ' + str(exc))
