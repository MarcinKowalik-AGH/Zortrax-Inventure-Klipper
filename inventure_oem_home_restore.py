"""Restore the complete frozen R38.6 Home implementation before R19.

This is an explicit recovery profile, not a modification of its motion code.
The R38.6 factory interpreter, preparation generator and native Home adapter
must be restored together: R19 removed both the XY release calls and their
measured-coordinate transfer. The later OLED/menu files remain installed.
"""
import ast
from hashlib import sha1, sha256
from pathlib import Path

RELEASE = 'RC8-OEM-HOME-R38.6-RESTORE-R23.7'
PROFILE = 'oem-home-r38.6'
REFERENCE_COMMIT = 'ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f'
REFERENCE_TAG = 'inventure-homing-r38.6'
BASELINE_BLOB = '9355e8f2de8880f80c5302f4270f34fbc7d5cf93'
BASELINE_SHA256 = '91ce84c6d4b95e1fe2450c8d6477c13a93c04aacf0c5682b95057a4f59a02ead'
REFERENCE = {
    'inventure_native_z.py': BASELINE_BLOB,
    'inventure_mcu.py': '50fa4d260d2dee7038962c4f2376733ae34dc62d',
    'inventure_z_home.py': '6473c5473dec6b0889772ff10536390c6ad377c6',
}
TARGET = 'klippy/extras/inventure_native_z.py'
CURRENT_MCU_BLOB = '8d64f94d3ae4257fbc20e936b9145ba0419f34ed'


def blob(data):
    return sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def execution_ast(data):
    tree = ast.parse(data)
    for owner in tree.body:
        if isinstance(owner, ast.ClassDef) and owner.name == 'InventureNativeZ':
            owner.body = [node for node in owner.body
                          if not isinstance(node, ast.FunctionDef) or node.name != 'get_status']
    return ast.dump(tree, include_attributes=False)


def without_service_entry(data):
    tree=ast.parse(data)
    for owner in tree.body:
        if isinstance(owner,ast.ClassDef) and owner.name=='InventureMCU':
            owner.body=[n for n in owner.body if not isinstance(n,ast.FunctionDef)
                        or n.name!='_enter_operator_service']
    return ast.dump(tree,include_attributes=False)


def service_entry(data):
    owner=next(n for n in ast.parse(data).body if isinstance(n,ast.ClassDef) and n.name=='InventureMCU')
    node=next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name=='_enter_operator_service')
    lines=data.decode('utf-8').splitlines(keepends=True)
    return node,lines,''.join(lines[node.lineno-1:node.end_lineno])


def restored_oem_home(repo, files, *, retain_service_entry=True):
    updates, records = {}, {}
    for name, expected in REFERENCE.items():
        path = Path(repo)/'orange_pi'/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing frozen R38.6 Home reference: ' + name)
        source = path.read_bytes()
        if blob(source) != expected:
            raise ValueError('Frozen R38.6 Home reference changed: ' + name)
        after = source
        target = 'klippy/extras/' + name
        if name == 'inventure_mcu.py' and retain_service_entry:
            current=files[target][0]
            if blob(current)!=CURRENT_MCU_BLOB:
                raise ValueError('Unexpected installed SERVICE entry before OEM recovery')
            old,lines,_=service_entry(source)
            _,_,retained=service_entry(current)
            after=(''.join(lines[:old.lineno-1])+retained+''.join(lines[old.end_lineno:])).encode('utf-8')
            if without_service_entry(source)!=without_service_entry(after):
                raise ValueError('OEM interpreter changed outside the retained SERVICE entry')
        if name == 'inventure_native_z.py':
            before = b'                    retained_refinement=self.retained_refinement)\n'
            replacement = (
                "                    retained_refinement=self.retained_refinement,\n"
                "                    host_module=__file__,\n"
                "                    homing_safety_release='RC8-OEM-HOME-R38.6-RESTORE-R23.7',\n"
                "                    procedure_restore_release='RC8-OEM-HOME-R38.6-RESTORE-R23.7',\n"
                "                    procedure_baseline_blob='9355e8f2de8880f80c5302f4270f34fbc7d5cf93',\n"
                "                    home_procedure_profile='oem-home-r38.6')\n").encode('utf-8')
            if after.count(before) != 1:
                raise ValueError('Frozen native Home status changed')
            after = after.replace(before, replacement, 1)
            if execution_ast(source) != execution_ast(after):
                raise ValueError('OEM Home restoration changed an executable procedure')
        compile(after, target, 'exec')
        updates[target] = (after, 0o644)
        records[target] = dict(source='orange_pi/' + name, reference_blob=expected,
                               installed_blob=blob(after), sha256=sha256(after).hexdigest())
        if name == 'inventure_mcu.py' and retain_service_entry:
            records[target].update(retained_current_method='_enter_operator_service',
                                   retained_method_source_blob=CURRENT_MCU_BLOB)
    return updates, dict(release=RELEASE, profile=PROFILE, reference_commit=REFERENCE_COMMIT,
                         reference_tag=REFERENCE_TAG, files=records,
                         executable_home_ast_identical=True,
                         xy_preparation='existing home + home_release Y/X, measured release offsets',
                         native_mcu_handoff='existing verified owner/run/token, continuous PE10',
                         mcu_update_required=False, new_hardware_tested=False,
                         standalone_xy='unchanged native G28; no new release algorithm')


def require_reference_backup(repo, home, host):
    # Reuse only the read-only auditor; do not load or execute a saved module.
    import importlib.util
    path = Path(repo)/'tools/inventure_home_reference_audit.py'
    spec = importlib.util.spec_from_file_location('inventure_oem_home_backup_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(Path(home), Path(host))
    candidates = [row for row in report['backups']
                  if row['target'] == str(Path(host)/TARGET)
                  and row['git_blob'] == BASELINE_BLOB and row['sha256'] == BASELINE_SHA256]
    if not candidates:
        raise ValueError('No retained backup matches the original R38.6 Home reference; no service switch performed')
    selected = max(candidates, key=lambda row: row['installation'])
    if blob(module.read_regular(Path(selected['saved_file']))) != BASELINE_BLOB:
        raise ValueError('The saved R38.6 Home reference changed during readback')
    return selected
