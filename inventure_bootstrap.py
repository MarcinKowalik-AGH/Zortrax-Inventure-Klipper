"""Build an inactive, verified Inventure host without touching a running printer.

This is the production equivalent of the release CI's prepare_runtime().  It
uses the bundled Klipper pin, never a checkout of upstream Klipper.  Existing
destinations are never an install target: callers must explicitly verify them.
"""
from __future__ import annotations

from contextlib import redirect_stdout, contextmanager
from contextvars import ContextVar
import ctypes
import errno
from hashlib import sha1, sha256
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import shutil
import subprocess
import tempfile
import types
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parent
_procedure_profile = ContextVar('inventure_procedure_profile', default='current')


@contextmanager
def procedure_profile(name):
    if name not in ('current', 'oem-home-r38.6', 'operator-home-r24'):
        raise ValueError('Unknown Inventure procedure profile: ' + str(name))
    token = _procedure_profile.set(name)
    try:
        yield
    finally:
        _procedure_profile.reset(token)


def current_procedure_profile():
    return _procedure_profile.get()

SOURCE_COMMIT = "ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f"
SOURCE_TAG = "inventure-homing-r38.6"
UPSTREAM_COMMIT = "ee2c6fb6b00b2b5d097ef70d0868df7d74943b51"
HOST_REVISION = "RC8-CONTROL-R7-HOMING-R38.6"
SOURCE_SHA256 = "1c3e388744159cb10d3e27750b598a4cf434abe0b5f8029d5a8dd3b1567de3f8"
BASELINE_SHA256 = "c15f71fc394bf7ca0210cdf16e233b976baa4ab0fb365fbd3d4bb888d8882cab"
HOST_API_SHA256 = "fbac5f09566e12244a1bb21a558c2aef1257a3eace57c9465c70ea0e60fcc841"
VERSION = SOURCE_TAG + "+klipper." + UPSTREAM_COMMIT[:12]
MANIFEST_NAME = ".inventure-bootstrap.json"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _regular_bytes(path):
    _require(stat.S_ISREG(path.lstat().st_mode), "Expected a regular file: " + str(path))
    return path.read_bytes()


def _payload_name(name):
    path = PurePosixPath(name)
    return (len(path.parts) == 2 and path.parts[0] in ("host", "orange_pi")
            and path.suffix in (".py", ".json")) or name == "source/SHA256SUMS" or (
                len(path.parts) == 3 and path.parts[:2] == ("source", "parts")
                and path.name.startswith("part") and path.suffix == ".bin")


def _source_files(repo):
    """Reject dirty/untracked runtime inputs, allowing unrelated installer work."""
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", SOURCE_COMMIT,
         "--", "host", "orange_pi", "source"],
        capture_output=True, check=False, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    _require(result.returncode == 0, "The source checkout must contain release " + SOURCE_COMMIT)
    expected = {}
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        record, raw_path = entry.split(b"\t", 1)
        mode, kind, digest = record.decode("ascii").split()
        name = raw_path.decode("utf-8")
        if _payload_name(name):
            _require(kind == "blob" and mode in ("100644", "100755"),
                     "Non-regular release input: " + name)
            expected[name] = digest
    actual = {"source/SHA256SUMS"}
    for folder in ("host", "orange_pi"):
        actual.update(str(p.relative_to(repo).as_posix()) for p in (repo/folder).iterdir()
                      if p.suffix in (".py", ".json"))
    actual.update(p.relative_to(repo).as_posix() for p in (repo/"source/parts").glob("part*.bin"))
    _require(actual == set(expected), "Unexpected or missing release inputs")
    files = {}
    for name, expected_blob in expected.items():
        data = _regular_bytes(repo/name)
        blob = sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        _require(blob == expected_blob, "Release input differs from " + SOURCE_TAG + ": " + name)
        files[name] = data
    return files


def _zip_files(data, *, prefix=None):
    """Read authenticated ZIP contents without allowing special files or aliases."""
    files, entries = {}, {}
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            _require(sum(i.file_size for i in archive.infolist()) <= MAX_ARCHIVE_BYTES,
                     "Archive is unexpectedly large")
            for info in archive.infolist():
                name = info.orig_filename
                clean = name.rstrip("/")
                path = PurePosixPath(clean)
                _require(bool(clean) and "\x00" not in name and "\\" not in name
                         and not path.is_absolute() and not PureWindowsPath(clean).drive
                         and ".." not in path.parts and str(path) == clean,
                         "Unsafe ZIP path: " + repr(name))
                _require(prefix is None or path.parts[0] == prefix,
                         "Unexpected ZIP root: " + name)
                kind = stat.S_IFMT(info.external_attr >> 16)
                is_dir = info.is_dir()
                _require(kind in ((0, stat.S_IFDIR) if is_dir else (0, stat.S_IFREG)),
                         "Special ZIP entry: " + name)
                _require(not info.flag_bits & 1, "Encrypted ZIP entry: " + name)
                _require(clean not in entries, "Duplicate ZIP path: " + name)
                entries[clean] = is_dir
                if not is_dir:
                    files[clean] = (archive.read(info), 0o644 | ((info.external_attr >> 16) & 0o111))
            for name in entries:
                for parent in PurePosixPath(name).parents:
                    _require(str(parent) not in entries or entries[str(parent)],
                             "ZIP file/directory collision: " + name)
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("Invalid source archive: " + str(exc)) from exc
    return files


def _module(name, path, data):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module


def _plan(repo, *, restore_oled=True, restore_native_z=True,
          restore_motion_owner=None, restore_service_excursion=None,
          restore_scheduler=None, restore_safety_policy=None, restore_operator_service=None,
          restore_leveling_ui=None, restore_leveling_stages=None, restore_operator_menu=None, restore_configuration_audit=None,
          restore_accepted_home=None, procedure_profile=None, oem_service_entry=True,
          operator_home_revision='current', information_revision='current'):
    if restore_motion_owner is None:
        restore_motion_owner = restore_oled and restore_native_z
    if restore_service_excursion is None:
        restore_service_excursion = restore_motion_owner
    if restore_scheduler is None:
        restore_scheduler = restore_service_excursion
    if restore_safety_policy is None:
        restore_safety_policy = restore_scheduler
    if restore_operator_service is None:
        restore_operator_service = restore_safety_policy
    if restore_leveling_ui is None:
        restore_leveling_ui = restore_operator_service
    if restore_leveling_stages is None:
        restore_leveling_stages = restore_leveling_ui
    if restore_operator_menu is None:
        restore_operator_menu = restore_leveling_stages
    if restore_configuration_audit is None:
        restore_configuration_audit = restore_operator_menu
    if restore_accepted_home is None:
        restore_accepted_home = restore_configuration_audit
    source = _source_files(repo)
    expected_outer = source["source/SHA256SUMS"].decode("ascii").split()[0]
    archive = b"".join(source[name] for name in sorted(source) if name.startswith("source/parts/"))
    _require(expected_outer == SOURCE_SHA256 and sha256(archive).hexdigest() == SOURCE_SHA256,
             "Source archive checksum mismatch")
    outer = _zip_files(archive)
    baseline = outer["baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip"][0]
    hashes = json.loads(outer["baseline/expected_hashes.json"][0])
    _require(hashes["SOURCE_FULL_KLIPPER_PINNED.zip"]["sha256"] == BASELINE_SHA256
             and sha256(baseline).hexdigest() == BASELINE_SHA256,
             "Pinned Klipper archive checksum mismatch")
    files = {str(PurePosixPath(name).relative_to("klipper-inventure")): value
             for name, value in _zip_files(baseline, prefix="klipper-inventure").items()}
    _require(hashes["klippy/mcu.py"]["sha256"] == HOST_API_SHA256
             and sha256(files["klippy/mcu.py"][0]).hexdigest() == HOST_API_SHA256,
             "Pinned Klipper host API checksum mismatch")
    marker = json.loads(files[".inventure-v9-patch.json"][0])
    _require(marker.get("upstream") == UPSTREAM_COMMIT, "Wrong Klipper upstream pin")
    for folder in ("host", "orange_pi"):
        for name in sorted(source):
            if PurePosixPath(name).parent != PurePosixPath(folder):
                continue
            target = ("klippy/serialhdl.py" if PurePosixPath(name).name == "serialhdl.py"
                      else "klippy/extras/" + PurePosixPath(name).name)
            files[target] = (source[name], 0o644)
    patch_name = "orange_pi/patch_jam_operations.py"
    patch = _module("inventure_bootstrap_operations", repo/patch_name, source[patch_name])
    operations = "klippy/extras/inventure_operations.py"
    patched = patch.render(files[operations][0])
    _require(sha256(patched).hexdigest() == patch.PATCHED_SHA256,
             "Native operation ownership patch checksum mismatch")
    files[operations] = (patched, 0o644)
    # The pinned util.get_git_version() reads this file beside util.py.
    files["klippy/.version"] = ((VERSION + "\n").encode("ascii"), 0o644)
    manifest = json.loads(source["orange_pi/THERMAL_HOST_MANIFEST.json"])
    _require(manifest.get("host_revision") == HOST_REVISION, "Unexpected host release")
    metadata = dict(
        schema_version=1, created_as="inactive_host_stage", host_revision=HOST_REVISION,
        source_commit=SOURCE_COMMIT, source_tag=SOURCE_TAG, upstream_commit=UPSTREAM_COMMIT,
        source_archive_sha256=SOURCE_SHA256, baseline_archive_sha256=BASELINE_SHA256,
        host_api_sha256=HOST_API_SHA256, software_version=VERSION,
        source_inputs_sha256={name: sha256(data).hexdigest() for name, data in sorted(source.items())},
        files={name: dict(sha256=sha256(data).hexdigest(), mode=mode)
               for name, (data, mode) in sorted(files.items())})
    if restore_oled:
        # The historical incremental updater retained this independent OLED
        # patch.  It was absent from the pinned Home/MCU snapshot, so a fresh
        # installation must restore it explicitly with its own provenance.
        path = repo/"inventure_oled_restore.py"
        restoration = _module("inventure_bootstrap_oled_restore", path, _regular_bytes(path))
        renderer, provenance = restoration.restored_renderer(repo)
        target = restoration.TARGET
        _require(sha256(files[target][0]).hexdigest() == restoration.LEGACY_SHA256,
                 "Unexpected base OLED renderer before restoration")
        files[target] = (renderer, 0o644)
        metadata["files"][target] = dict(sha256=sha256(renderer).hexdigest(), mode=0o644)
        metadata["runtime_restorations"] = {"oled_dual_thermal_v2": provenance}
    if restore_native_z:
        # R18 is a post-release NATIVE policy fix, not a change to the
        # cryptographically pinned R38.6 source tree.
        path = repo/"inventure_native_z_restore.py"
        restoration = _module("inventure_bootstrap_native_z", path, _regular_bytes(path))
        target = restoration.TARGET
        new_module, provenance = restoration.restored_native_z(repo, files[target][0])
        files[target] = (new_module, 0o644)
        metadata["files"][target] = dict(sha256=sha256(new_module).hexdigest(), mode=0o644)
        metadata.setdefault("runtime_restorations", {})["native_z_r18"] = provenance
    if restore_motion_owner:
        _require(restore_native_z and restore_oled,
                 "R19 native motion owner requires both authenticated R18 and OLED restorations")
        path = repo/"inventure_motion_owner_restore.py"
        restoration = _module("inventure_bootstrap_motion_owner", path, _regular_bytes(path))
        updates, provenance = restoration.restored_motion_owner(repo, files)
        files.update(updates)
        for target,(data,mode) in updates.items():
            metadata["files"][target] = dict(sha256=sha256(data).hexdigest(),mode=mode)
        metadata.setdefault("runtime_restorations", {})["single_motion_owner_r19"] = provenance
    if restore_service_excursion:
        _require(restore_motion_owner, "R20 service handoff requires authenticated R19")
        path = repo/"inventure_service_excursion_restore.py"
        restoration = _module("inventure_bootstrap_service_excursion", path, _regular_bytes(path))
        updates, provenance = restoration.restored_service_excursion(repo, files)
        files.update(updates)
        for target,(data,mode) in updates.items():
            metadata["files"][target] = dict(sha256=sha256(data).hexdigest(),mode=mode)
        metadata.setdefault("runtime_restorations", {})["exclusive_service_r20"] = provenance
    if restore_scheduler:
        _require(restore_service_excursion,
                 'R21 diagnostics require the authenticated R20 service layer')
        path = repo/'inventure_scheduler_restore.py'
        restoration = _module('inventure_bootstrap_scheduler',path,_regular_bytes(path))
        updates,provenance=restoration.restored_scheduler(repo,files)
        files.update(updates)
        for target,(data,mode) in updates.items():
            metadata['files'][target]=dict(sha256=sha256(data).hexdigest(),mode=mode)
        metadata.setdefault('runtime_restorations',{})['scheduler_r21']=provenance
    if restore_safety_policy:
        _require(restore_scheduler,'R22 safety policy requires R21 diagnostics')
        path=repo/'inventure_safety_policy_restore.py'
        restoration=_module('inventure_bootstrap_safety_policy',path,_regular_bytes(path))
        updates,provenance=restoration.restored_safety_policy(repo,files)
        files.update(updates)
        for target,(data,mode) in updates.items():
            metadata['files'][target]=dict(sha256=sha256(data).hexdigest(),mode=mode)
        metadata.setdefault('runtime_restorations',{})['safety_policy_r22']=provenance
    if restore_operator_service:
        _require(restore_safety_policy, 'Operator SERVICE restoration requires the R22 policy layer')
        path = repo/'inventure_operator_service_restore.py'
        restoration = _module('inventure_bootstrap_operator_service', path, _regular_bytes(path))
        updates, provenance = restoration.restored_operator_service(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['operator_service_r23'] = provenance
    if restore_leveling_ui:
        _require(restore_operator_service, 'Leveling UI requires the verified R23 SERVICE entry')
        path = repo/'inventure_leveling_ui_restore.py'
        restoration = _module('inventure_bootstrap_leveling_ui', path, _regular_bytes(path))
        updates, provenance = restoration.restored_leveling_ui(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['leveling_ui_r23'] = provenance
    if restore_leveling_stages:
        _require(restore_leveling_ui, 'Leveling stages require the verified R23.2 OLED UI')
        path = repo/'inventure_leveling_stages_restore.py'
        restoration = _module('inventure_bootstrap_leveling_stages', path, _regular_bytes(path))
        updates, provenance = restoration.restored_leveling_stages(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['leveling_stages_r23'] = provenance
    if restore_operator_menu:
        _require(restore_leveling_stages, 'Operator menu requires the R23.3 procedure display')
        path = repo/'inventure_operator_menu_restore.py'
        restoration = _module('inventure_bootstrap_operator_menu', path, _regular_bytes(path))
        updates, provenance = restoration.restored_operator_menu(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['operator_menu_r23'] = provenance
    if restore_configuration_audit:
        _require(restore_operator_menu, 'Configuration audit requires the R23.4 operator menu')
        path = repo/'inventure_configuration_audit_restore.py'
        restoration = _module('inventure_bootstrap_configuration_audit', path, _regular_bytes(path))
        updates, provenance = restoration.restored_configuration_audit(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['configuration_audit_r23'] = provenance
    if restore_accepted_home:
        _require(restore_configuration_audit, 'Accepted Home restoration requires the recorded R23.5 input')
        path = repo/'inventure_accepted_home_restore.py'
        restoration = _module('inventure_bootstrap_accepted_home', path, _regular_bytes(path))
        updates, provenance = restoration.restored_accepted_home(repo, files)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['accepted_home_r23'] = provenance
    profile = _procedure_profile.get() if procedure_profile is None else procedure_profile
    _require(profile in ('current', 'oem-home-r38.6', 'operator-home-r24'), 'Unknown procedure profile')
    if profile in ('oem-home-r38.6', 'operator-home-r24'):
        _require(restore_accepted_home, 'OEM recovery requires the authenticated current runtime input')
        path = repo/'inventure_oem_home_restore.py'
        restoration = _module('inventure_bootstrap_oem_home', path, _regular_bytes(path))
        updates, provenance = restoration.restored_oem_home(repo, files, retain_service_entry=oem_service_entry)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['oem_home_r38_6'] = provenance
    if profile == 'operator-home-r24':
        path = repo/'inventure_operator_home_restore.py'
        restoration = _module('inventure_bootstrap_operator_home', path, _regular_bytes(path))
        updates, provenance = restoration.restored_operator_home(repo, files, revision=operator_home_revision)
        files.update(updates)
        for target, (data, mode) in updates.items():
            metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
        metadata.setdefault('runtime_restorations', {})['operator_home_r24'] = provenance
        if operator_home_revision in ('current', 'rc7'):
            path = repo/'inventure_menu_audit_restore.py'
            presentation = _module('inventure_bootstrap_menu_audit', path, _regular_bytes(path))
            updates, provenance = presentation.restored_menu_audit(repo, files)
            files.update(updates)
            for target, (data, mode) in updates.items():
                metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
            metadata['runtime_restorations']['menu_audit_r24'] = provenance
        if operator_home_revision == 'current':
            path = repo/'inventure_information_restore.py'
            info = _module('inventure_bootstrap_information', path, _regular_bytes(path))
            updates, provenance = info.restore(repo, files, revision=information_revision)
            files.update(updates)
            for target, (data, mode) in updates.items():
                metadata['files'][target] = dict(sha256=sha256(data).hexdigest(), mode=mode)
            metadata['runtime_restorations']['information_r25'] = provenance
    return source, files, metadata


def _write_files(root, files):
    root.mkdir(mode=0o755)
    for name, (data, mode) in files.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with path.open("xb") as output:
            output.write(data)
        path.chmod(mode)
    for directory, _, _ in os.walk(root):
        Path(directory).chmod(0o755)


def _core_check(target, source, temporary, *, native_restored=False, single_owner=False):
    snapshot = temporary/"release-inputs"
    inputs = {name: (data, 0o644) for name, data in source.items()
              if PurePosixPath(name).parts[0] in ("host", "orange_pi")}
    _write_files(snapshot, inputs)
    path = snapshot/"orange_pi/install_thermal_test.py"
    core = _module("inventure_bootstrap_verifier", path, source["orange_pi/install_thermal_test.py"])
    verification_target = target
    if native_restored or single_owner:
        # The pinned offline installer validates the exact R38.6 input modules.
        # Verify those in an isolated shadow, never mutate the installed
        # runtime while Klipper owns a stepper or heater.
        verification_target = temporary/"core-compat-host"
        extras = verification_target/"klippy/extras"
        extras.parent.mkdir(parents=True)
        shutil.copytree(target/"klippy/extras", extras,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(target/"klippy/serialhdl.py",
                     verification_target/"klippy/serialhdl.py")
        for name in (("inventure_native_z.py","inventure_z_home.py","inventure_mcu.py")
                     if single_owner else ("inventure_native_z.py",)) + (
                         "inventure_menu_service.py", "inventure_oled_graphics.py", "inventure_oled.py",
                         "inventure_menu_core.py", "inventure_oem_menu.py"):
            (extras/name).write_bytes(source["orange_pi/"+name])
    with redirect_stdout(io.StringIO()):
        result = core.install(verification_target, apply=False)
    _require(result.get("status") == "ALREADY_INSTALLED"
             and result.get("host_revision") == HOST_REVISION,
             "Prepared host does not match the complete release manifest")


def _metadata_bytes(metadata):
    return (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _generated_file(name, expected):
    # These are generated by the pinned Python/C loader, not release inputs.
    path = PurePosixPath(name)
    if name == "klippy/chelper/c_helper.so":
        return True
    if (path.parent.name == "__pycache__"
            and re.fullmatch(r"[A-Za-z_]\w*\.cpython-\d+(?:\.opt-[12])?\.pyc", path.name, re.ASCII)):
        # A rolled-back/newer module can leave a PEP 3147 cache after its .py
        # was removed. CPython never imports that orphan from __pycache__.
        # Accept caches only inside an authenticated Python source directory;
        # source files, extension modules, symlinks and other extras stay checked.
        return any(PurePosixPath(source).suffix == ".py"
                   and PurePosixPath(source).parent == path.parent.parent
                   for source in expected)
    return False


def _verify_files(target, metadata):
    _require(target.is_dir() and not target.is_symlink(), "Expected a regular host directory")
    recorded = json.loads(_regular_bytes(target/MANIFEST_NAME))
    _require(recorded == metadata, "Host installation metadata differs from the release")
    _require(stat.S_IMODE((target/MANIFEST_NAME).stat().st_mode) == 0o644,
             "Host installation metadata permissions differ")
    expected = metadata["files"]
    for name, record in expected.items():
        path = target/name
        _require(sha256(_regular_bytes(path)).hexdigest() == record["sha256"],
                 "Installed host file differs: " + name)
        _require(stat.S_IMODE(path.stat().st_mode) == record["mode"],
                 "Installed host file permissions differ: " + name)
    generated = []
    for directory, dirs, names in os.walk(target, followlinks=False):
        for name in dirs + names:
            path = Path(directory)/name
            _require(not path.is_symlink(), "Symlink in installed host: " + str(path))
        for name in names:
            path = Path(directory)/name
            rel = path.relative_to(target).as_posix()
            _require(stat.S_ISREG(path.lstat().st_mode), "Special file in installed host: " + rel)
            if rel in expected or rel == MANIFEST_NAME:
                continue
            _require(_generated_file(rel, expected), "Unexpected file in installed host: " + rel)
            generated.append(rel)
    return generated


def _publish_new(stage, destination):
    """Linux rename with NOREPLACE also closes the destination collision race."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    _require(rename is not None, "Atomic no-replace directory publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(error, "Destination already exists", str(destination))
        raise OSError(error, os.strerror(error), str(destination))


def prepare_host(destination, repo=ROOT) -> Path:
    """Create a NEW verified runtime; never stop services or modify configuration."""
    destination = Path(destination).expanduser().absolute()
    if os.path.lexists(destination):
        raise FileExistsError("Destination already exists; use verify_host explicitly: " + str(destination))
    destination = destination.resolve(strict=False)
    repo = Path(repo).expanduser().resolve(strict=True)
    _require(not destination.is_relative_to(repo), "Stage the host outside its source checkout")
    source, files, metadata = _plan(repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.parent.resolve(strict=True)/destination.name
    with tempfile.TemporaryDirectory(prefix=".inventure-host-stage-", dir=destination.parent) as temp:
        temporary = Path(temp)
        stage = temporary/"host"
        _write_files(stage, files)
        marker = stage/MANIFEST_NAME
        marker.write_bytes(_metadata_bytes(metadata))
        marker.chmod(0o644)
        _verify_files(stage, metadata)
        _core_check(stage, source, temporary,
                    native_restored='native_z_r18' in metadata.get('runtime_restorations', {}),
                    single_owner='single_motion_owner_r19' in metadata.get('runtime_restorations', {}))
        _publish_new(stage, destination)
    return destination


def _verify_runtime(target, source, metadata, *, restoration_required):
    generated = _verify_files(target, metadata)
    with tempfile.TemporaryDirectory(prefix="inventure-host-verify-") as temp:
        _core_check(target, source, Path(temp),
                    native_restored='native_z_r18' in metadata.get('runtime_restorations', {}),
                    single_owner='single_motion_owner_r19' in metadata.get('runtime_restorations', {}))
    return dict(status="VERIFIED_LEGACY_HOST" if restoration_required else "VERIFIED_HOST",
                path=str(target), host_revision=HOST_REVISION,
                source_commit=SOURCE_COMMIT, source_tag=SOURCE_TAG,
                upstream_commit=UPSTREAM_COMMIT, software_version=VERSION,
                runtime_restoration_required=restoration_required,
                runtime_restorations=metadata.get("runtime_restorations", {}),
                oled_restoration=metadata.get("runtime_restorations", {}).get("oled_dual_thermal_v2"),
                native_z_restoration=metadata.get("runtime_restorations", {}).get("native_z_r18"),
                single_motion_owner_restoration=metadata.get("runtime_restorations", {}).get("single_motion_owner_r19"),
                exclusive_service_restoration=metadata.get("runtime_restorations", {}).get("exclusive_service_r20"),
                scheduler_restoration=metadata.get("runtime_restorations", {}).get("scheduler_r21"),
                safety_policy_restoration=metadata.get("runtime_restorations", {}).get("safety_policy_r22"),
                operator_service_restoration=metadata.get("runtime_restorations", {}).get("operator_service_r23"),
                leveling_ui_restoration=metadata.get("runtime_restorations", {}).get("leveling_ui_r23"),
                leveling_stages_restoration=metadata.get("runtime_restorations", {}).get("leveling_stages_r23"),
                operator_menu_restoration=metadata.get("runtime_restorations", {}).get("operator_menu_r23"),
                configuration_audit_restoration=metadata.get("runtime_restorations", {}).get("configuration_audit_r23"),
                accepted_home_restoration=metadata.get("runtime_restorations", {}).get("accepted_home_r23"),
                oem_home_restoration=metadata.get("runtime_restorations", {}).get("oem_home_r38_6"),
                generated_files=sorted(generated))


def verify_host(destination, repo=ROOT, *, allow_legacy=False) -> dict:
    """Verify the complete runtime without repairing or overwriting anything.

    With explicit allow_legacy, the exact earlier R38.6 bootstrap is also
    recognized and reported as requiring restoration.  Modified or unknown
    runtimes are never accepted.  Normal generated C/Python caches are allowed.
    """
    target = Path(destination).expanduser().absolute()
    repo = Path(repo).expanduser().resolve(strict=True)
    source, _, metadata = _plan(repo)
    if allow_legacy:
        recorded = json.loads(_regular_bytes(target/MANIFEST_NAME))
        if recorded != metadata:
            return verify_legacy_host(target, repo=repo)
    return _verify_runtime(target, source, metadata, restoration_required=False)


def _predecessor_plans(repo):
    """Every authenticated prefix through the published R23.5 runtime."""
    # Try the recent public operator runtimes first. Rebuilding every older
    # prefix before recognizing RC6/RC7 is expensive on an Orange Pi SD card.
    # Matching and verifying the complete authenticated manifest is unchanged.
    if _procedure_profile.get() == 'operator-home-r24':
        yield _plan(repo, procedure_profile='operator-home-r24', information_revision='rc4')
        yield _plan(repo, procedure_profile='operator-home-r24', information_revision='rc3')
        yield _plan(repo, procedure_profile='operator-home-r24', information_revision='rc1')
        for revision in ('rc7', 'rc6', 'rc5', 'rc4', 'rc2', 'rc1'):
            yield _plan(repo, procedure_profile='operator-home-r24', operator_home_revision=revision)
    if _procedure_profile.get() in ('current', 'operator-home-r24'):
        yield _plan(repo, procedure_profile='oem-home-r38.6')
    yield _plan(repo, procedure_profile='oem-home-r38.6', oem_service_entry=False)
    if _procedure_profile.get() != 'current':
        yield _plan(repo, procedure_profile='current')
    names = ('restore_oled', 'restore_native_z', 'restore_motion_owner',
             'restore_service_excursion', 'restore_scheduler', 'restore_safety_policy',
             'restore_operator_service', 'restore_leveling_ui', 'restore_leveling_stages',
             'restore_operator_menu', 'restore_configuration_audit')
    for enabled in range(len(names), -1, -1):
        yield _plan(repo, restore_accepted_home=False, procedure_profile='current',
                    **{name: index < enabled for index, name in enumerate(names)})


def verify_legacy_host(destination, repo=ROOT) -> dict:
    """Recognize only the two authenticated predecessor runtime layouts.

    Original R38.6 had generic OLED and native Z. Current Orange Pi hosts
    already have the restored dual-thermal OLED but not the R18 Z policy.
    """
    target = Path(destination).expanduser().absolute()
    repo = Path(repo).expanduser().resolve(strict=True)
    recorded = json.loads(_regular_bytes(target/MANIFEST_NAME))
    for source, _, metadata in _predecessor_plans(repo):
        if recorded == metadata:
            return _verify_runtime(target, source, metadata, restoration_required=True)
    raise ValueError("Installed host metadata differs from every verified R38.6 predecessor")


def prepare_runtime_update(destination, repo=ROOT) -> dict:
    """Plan atomic restoration of OLED and/or R18 NATIVE Z without stopping service.

    Accept only a byte-for-byte verified pre-OLED R38.6 or current OLED-only
    host. No unexpected runtime edits are permitted; the caller owns the
    service/rollback transaction.
    """
    target = Path(destination).expanduser().absolute()
    repo = Path(repo).expanduser().resolve(strict=True)
    recorded = json.loads(_regular_bytes(target/MANIFEST_NAME))
    previous = None
    for source, files, metadata in _predecessor_plans(repo):
        if recorded == metadata:
            _verify_runtime(target, source, metadata, restoration_required=True)
            previous = files, metadata
            break
    _require(previous is not None,
             "Installed host metadata differs from every verified R38.6 predecessor")
    previous_files, previous_metadata = previous
    _, files, metadata = _plan(repo)
    changed = {name for name in files.keys() | previous_files.keys()
               if files.get(name) != previous_files.get(name)}
    allowed = {"klippy/extras/inventure_oled_render.py",
               "klippy/extras/inventure_menu_service.py",
               "klippy/extras/inventure_oled_graphics.py",
               "klippy/extras/inventure_oem_menu.py",
               "klippy/extras/inventure_menu_core.py",
               "klippy/extras/inventure_oled.py",
               "klippy/extras/inventure_native_z.py",
               "klippy/extras/inventure_z_home.py",
               "klippy/extras/inventure_mcu.py"}
    if _procedure_profile.get() == 'operator-home-r24':
        allowed.update('klippy/extras/'+name for name in
                       ('inventure_operator_home.py','inventure_home_ui.py','inventure_home_sprites.json',
                        'inventure_menu_presentation.py','inventure_oem_menu_sprites.json',
                                    'inventure_information.py','inventure_information_ui.py','inventure_info_store.py',
                                    'inventure_runtime.py','inventure_counter_sync.py','inventure_information_errors.py',
                                    'inventure_communication_guard.py'))
    _require(bool(changed) and changed <= allowed,
             "Unexpected runtime files in R18 restoration")
    updates = {}
    for name in changed:
        destination_file = target/name
        if name not in previous_files:
            _require(_procedure_profile.get() == 'operator-home-r24'
                     and name in {'klippy/extras/'+n for n in
                                  ('inventure_operator_home.py','inventure_home_ui.py','inventure_home_sprites.json',
                                   'inventure_menu_presentation.py','inventure_oem_menu_sprites.json',
                                    'inventure_information.py','inventure_information_ui.py','inventure_info_store.py',
                                    'inventure_runtime.py','inventure_counter_sync.py','inventure_information_errors.py',
                                    'inventure_communication_guard.py')}
                     and not destination_file.exists() and not destination_file.is_symlink(),
                     "Unexpected new runtime module: " + name)
            before = None
        else:
            before = _regular_bytes(destination_file)
            _require(before == previous_files[name][0],
                     "Runtime module changed while preparing restoration: " + name)
        updates[destination_file] = before, files[name][0]
    manifest = target/MANIFEST_NAME
    manifest_before = _regular_bytes(manifest)
    _require(json.loads(manifest_before) == previous_metadata,
             "Host metadata changed while preparing restoration")
    updates[manifest] = manifest_before, _metadata_bytes(metadata)
    return dict(files=updates, metadata=metadata,
                restoration=metadata["runtime_restorations"]["oled_dual_thermal_v2"],
                native_z_restoration=metadata["runtime_restorations"]["native_z_r18"],
                single_motion_owner_restoration=metadata["runtime_restorations"]["single_motion_owner_r19"],
                exclusive_service_restoration=metadata["runtime_restorations"]["exclusive_service_r20"],
                scheduler_restoration=metadata["runtime_restorations"]["scheduler_r21"],
                safety_policy_restoration=metadata["runtime_restorations"]["safety_policy_r22"],
                operator_service_restoration=metadata["runtime_restorations"]["operator_service_r23"],
                leveling_ui_restoration=metadata["runtime_restorations"]["leveling_ui_r23"],
                leveling_stages_restoration=metadata["runtime_restorations"]["leveling_stages_r23"],
                operator_menu_restoration=metadata["runtime_restorations"]["operator_menu_r23"],
                configuration_audit_restoration=metadata["runtime_restorations"]["configuration_audit_r23"],
                accepted_home_restoration=metadata["runtime_restorations"]["accepted_home_r23"],
                oem_home_restoration=metadata["runtime_restorations"].get("oem_home_r38_6"))
