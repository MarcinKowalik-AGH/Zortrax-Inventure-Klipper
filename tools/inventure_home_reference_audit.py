#!/usr/bin/env python3
"""Identify current and backed-up native Home code without touching the printer.

Reads code and installer metadata only. Does not import Klipper, contact an API,
send G-code, change a file, stop a service or restore any procedure.
"""
import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
import stat


NATIVE = Path('klippy/extras/inventure_native_z.py')
PRE_AUDIT_BLOB = '3fc954974a4a375e003b50c8624304301457f1de'
R23_5_BLOB = 'eb92dd3eb7bb54aee5b710b34957494250a547e9'
RESTORED_HOME_BLOB = '5263263a899be9d08dbeed495d04e8663371ed4c'
MAX_BYTES = 4 * 1024 * 1024


def read_regular(path, parent=None):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('Symlink in diagnostic path: ' + str(path))
    if parent is not None and not path.is_relative_to(parent):
        raise ValueError('Backup path is outside its installation directory')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError('Expected a bounded regular diagnostic input')
    return path.read_bytes()


def fingerprint(data):
    blob = sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
    return dict(bytes=len(data), git_blob=blob, sha256=sha256(data).hexdigest(),
                reference=('public_pre_R23_5' if blob == PRE_AUDIT_BLOB else
                           'R23_5_added_raw_switch_checks' if blob == R23_5_BLOB else
                           'pre_R23_5_procedures_with_R23_6_status' if blob == RESTORED_HOME_BLOB else
                           'other_code'))


def audit(home, runtime=None):
    home = Path(home).expanduser().absolute()
    runtime = Path(runtime).expanduser().absolute() if runtime else home/'klipper-inventure-r38.6'
    result = dict(status='READ_ONLY_HOME_REFERENCE_AUDIT', runtime=str(runtime),
                  current=None, backups=[], errors=[],
                  pre_R23_5_reference_blob=PRE_AUDIT_BLOB,
                  pre_R23_5_reference_backup_found=False,
                  physically_accepted_bytes_identified=False,
                  printer_state_changed=False)
    try:
        current = result['current'] = fingerprint(read_regular(runtime/NATIVE))
        marker = json.loads(read_regular(runtime/'.inventure-bootstrap.json'))
        declared = marker.get('files', {}).get(NATIVE.as_posix(), {}).get('sha256')
        current['matches_runtime_manifest'] = declared == current['sha256']
        current['restorations'] = {
            key: record.get('release') for key, record in marker.get('runtime_restorations', {}).items()
            if isinstance(record, dict)}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result['errors'].append(dict(scope='current_runtime', error=str(exc)))
    root = home/'inventure-installations'
    for record_path in sorted(root.glob('*/backup.json')):
        transaction = record_path.parent
        try:
            record = json.loads(read_regular(record_path, root))
            files = record.get('files')
            if not isinstance(files, dict):
                raise ValueError('Invalid installer backup file map')
            for target, saved in files.items():
                if Path(target).parts[-3:] != NATIVE.parts or saved is None:
                    continue
                data = read_regular(Path(saved), transaction)
                row = dict(installation=transaction.name, target=target, saved_file=saved,
                           **fingerprint(data))
                result['backups'].append(row)
                if row['git_blob'] == PRE_AUDIT_BLOB:
                    result['pre_R23_5_reference_backup_found'] = True
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            result['errors'].append(dict(scope=transaction.name, error=str(exc)))
    result['interpretation'] = (
        'A matching backup identifies the saved code bytes. Correlate its installation '
        'time with the three completed physical runs before selecting a restoration. '
        'This audit neither restores code nor confirms that a Home movement is safe.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.home, args.runtime), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
