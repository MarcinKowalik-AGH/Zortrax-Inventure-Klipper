"""Restore the recorded pre-R23.5 Home implementation; retain UI diagnostics."""
import ast
from hashlib import sha1, sha256
import importlib.util
from pathlib import Path

RELEASE = 'RC8-ACCEPTED-HOME-R23.6'
TARGET = 'klippy/extras/inventure_native_z.py'
BASELINE_BLOB = '3fc954974a4a375e003b50c8624304301457f1de'
BASELINE_SHA256 = '3e82ecc981bcf2b6e009dd10d579c9b1c8de1084c3e63f7c5d15ab4af72425d2'
SOURCE = 'restored/accepted_home_r23/inventure_native_z.py'
PRIOR_BLOB = 'eb92dd3eb7bb54aee5b710b34957494250a547e9'
INSTALLED_BLOB = '5263263a899be9d08dbeed495d04e8663371ed4c'


def blob(data):
    return sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()


def execution_ast(data):
    tree = ast.parse(data)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InventureNativeZ')
    status = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == 'get_status')
    calls = [n for n in ast.walk(status) if isinstance(n, ast.Call)]
    if (len(status.body) != 1 or not isinstance(status.body[0], ast.Return)
            or len(calls) != 1 or not isinstance(calls[0].func, ast.Name)
            or calls[0].func.id != 'dict'):
        raise ValueError('Home status must remain a read-only dictionary expression')
    owner.body = [n for n in owner.body if not (isinstance(n, ast.FunctionDef) and n.name == 'get_status')]
    return ast.dump(tree, include_attributes=False)


def restored_accepted_home(repo, files):
    before = files[TARGET][0]
    source, reference = repo/SOURCE, repo/'restored/scheduler_r21/inventure_native_z.py'
    for path in (source, reference):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or non-regular accepted Home source: '+str(path))
    after, baseline = source.read_bytes(), reference.read_bytes()
    if (blob(before) != PRIOR_BLOB or blob(after) != INSTALLED_BLOB
            or blob(baseline) != BASELINE_BLOB or sha256(baseline).hexdigest() != BASELINE_SHA256):
        raise ValueError('Accepted Home restoration integrity mismatch')
    if execution_ast(after) != execution_ast(baseline):
        raise ValueError('Accepted Home procedure changed outside get_status')
    compile(after, TARGET, 'exec')
    record = dict(source=SOURCE, prior_blob=PRIOR_BLOB, installed_blob=INSTALLED_BLOB,
                  sha256=sha256(after).hexdigest())
    return {TARGET: (after, 0o644)}, dict(release=RELEASE, baseline_blob=BASELINE_BLOB,
        baseline_sha256=BASELINE_SHA256, procedure_ast_identical=True,
        only_status_differs_from_baseline=True, mcu_update_required=False,
        physical_collision_root_cause_established=False, files={TARGET: record})


def require_reference_backup(repo, home, host):
    path = repo/'tools/inventure_home_reference_audit.py'
    spec = importlib.util.spec_from_file_location('inventure_accepted_home_backup_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(home, host)
    candidates = [row for row in report['backups']
                  if row['target'] == str(host/TARGET) and row['git_blob'] == BASELINE_BLOB
                  and row['sha256'] == BASELINE_SHA256]
    if not candidates:
        raise ValueError('No retained installer backup matches the pre-R23.5 Home reference; no service switch performed')
    selected = max(candidates, key=lambda row: row['installation'])
    if blob(module.read_regular(Path(selected['saved_file']))) != BASELINE_BLOB:
        raise ValueError('The retained Home backup changed during readback')
    return selected
