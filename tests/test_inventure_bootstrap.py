"""Exercise the real release archive and refuse modified/colliding installations."""
from contextlib import contextmanager
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
UI_TARGETS = {Path('klippy/extras/inventure_menu_service.py'),
              Path('klippy/extras/inventure_oled_graphics.py')}
spec = importlib.util.spec_from_file_location("inventure_bootstrap", ROOT/"inventure_bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def archive(entries):
    data = io.BytesIO()
    with warnings.catch_warnings(), ZipFile(data, "w") as zipped:
        warnings.simplefilter("ignore", UserWarning)
        for name, value in entries:
            zipped.writestr(name, value)
    return data.getvalue()


class SafeArchiveTests(unittest.TestCase):
    def test_rejects_paths_that_escape_or_alias_destinations(self):
        for name in ("../escape", "/absolute", "C:/absolute", "C:relative",
                     "a\\b", "a/../b", "a//b", "./a"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe ZIP path"):
                bootstrap._zip_files(archive([(name, b"never written")]))

    def test_rejects_symlinks_devices_and_file_directory_collisions(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK):
            entry = ZipInfo("untrusted")
            entry.external_attr = (kind | 0o777) << 16
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Special ZIP"):
                bootstrap._zip_files(archive([(entry, b"target")]))
        for entries in ([('same', b'a'), ('same', b'b')],
                        [('parent', b'a'), ('parent/child', b'b')],
                        [('parent/child', b'a'), ('parent', b'b')]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                bootstrap._zip_files(archive(entries))

    def test_preserves_authenticated_execute_bits_without_special_permissions(self):
        entry = ZipInfo("root/script.sh")
        entry.external_attr = (stat.S_IFREG | 0o4755) << 16
        files = bootstrap._zip_files(archive([(entry, b"#!/bin/sh\n")]), prefix="root")
        self.assertEqual(files['root/script.sh'], (b"#!/bin/sh\n", 0o755))
        with self.assertRaisesRegex(ValueError, "Unexpected ZIP root"):
            bootstrap._zip_files(archive([('other/file', b'x')]), prefix="root")


@unittest.skipUnless(sys.platform.startswith("linux"), "The target uses Linux atomic directory publication")
class RealHostBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="test-inventure-bootstrap-")
        cls.root = Path(cls.temp.name)
        cls.stage = cls.root/"verified-host"
        original_run = subprocess.run
        cls.commands = []

        def read_only_git(args, **kwargs):
            cls.commands.append(args)
            if args[0] != "git" or "ls-tree" not in args:
                raise AssertionError("Unexpected external action during preparation: " + repr(args))
            return original_run(args, **kwargs)

        with patch.object(subprocess, "run", side_effect=read_only_git):
            bootstrap.prepare_host(cls.stage, repo=ROOT)
        cls.metadata = json.loads((cls.stage/bootstrap.MANIFEST_NAME).read_text())
        _, cls.legacy_files, cls.legacy_metadata = bootstrap._plan(ROOT, restore_oled=False, restore_native_z=False)
        cls.legacy = cls.root/"legacy-host"
        bootstrap._write_files(cls.legacy, cls.legacy_files)
        (cls.legacy/bootstrap.MANIFEST_NAME).write_bytes(bootstrap._metadata_bytes(cls.legacy_metadata))
        (cls.legacy/bootstrap.MANIFEST_NAME).chmod(0o644)
        _, cls.oled_only_files, cls.oled_only_metadata = bootstrap._plan(
            ROOT, restore_oled=True, restore_native_z=False)
        cls.oled_only = cls.root/"oled-only-host"
        bootstrap._write_files(cls.oled_only, cls.oled_only_files)
        (cls.oled_only/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.oled_only_metadata))
        (cls.oled_only/bootstrap.MANIFEST_NAME).chmod(0o644)
        _, cls.r18_files, cls.r18_metadata = bootstrap._plan(
            ROOT, restore_motion_owner=False)
        cls.r18 = cls.root/"r18-host"
        bootstrap._write_files(cls.r18, cls.r18_files)
        (cls.r18/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.r18_metadata))
        (cls.r18/bootstrap.MANIFEST_NAME).chmod(0o644)
        _, cls.r19_files, cls.r19_metadata = bootstrap._plan(
            ROOT, restore_service_excursion=False)
        cls.r19 = cls.root/"r19-host"
        bootstrap._write_files(cls.r19, cls.r19_files)
        (cls.r19/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.r19_metadata))
        (cls.r19/bootstrap.MANIFEST_NAME).chmod(0o644)
        _,cls.r20_files,cls.r20_metadata=bootstrap._plan(ROOT,restore_scheduler=False)
        cls.r20=cls.root/'r20-host'
        bootstrap._write_files(cls.r20,cls.r20_files)
        (cls.r20/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.r20_metadata))
        (cls.r20/bootstrap.MANIFEST_NAME).chmod(0o644)
        _,cls.r21_files,cls.r21_metadata=bootstrap._plan(ROOT,restore_safety_policy=False)
        cls.r21=cls.root/'r21-host'
        bootstrap._write_files(cls.r21,cls.r21_files)
        (cls.r21/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.r21_metadata))
        (cls.r21/bootstrap.MANIFEST_NAME).chmod(0o644)
        _,cls.r22_files,cls.r22_metadata=bootstrap._plan(ROOT,restore_operator_service=False)
        cls.r22=cls.root/'r23-rc1-host'
        bootstrap._write_files(cls.r22,cls.r22_files)
        (cls.r22/bootstrap.MANIFEST_NAME).write_bytes(
            bootstrap._metadata_bytes(cls.r22_metadata))
        (cls.r22/bootstrap.MANIFEST_NAME).chmod(0o644)
        _, cls.r23_files, cls.r23_metadata = bootstrap._plan(ROOT, restore_leveling_ui=False)
        cls.r23 = cls.root/'r23-rc2-host'
        bootstrap._write_files(cls.r23,cls.r23_files)
        (cls.r23/bootstrap.MANIFEST_NAME).write_bytes(bootstrap._metadata_bytes(cls.r23_metadata))
        (cls.r23/bootstrap.MANIFEST_NAME).chmod(0o644)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def source_copy(self):
        with tempfile.TemporaryDirectory(prefix="inventure-input-test-") as temp:
            source = Path(temp)
            for name in ("host", "orange_pi", "source", "restored"):
                shutil.copytree(ROOT/name, source/name, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copyfile(ROOT/'inventure_oled_restore.py', source/'inventure_oled_restore.py')
            shutil.copyfile(ROOT/'inventure_native_z_restore.py', source/'inventure_native_z_restore.py')
            shutil.copyfile(ROOT/'inventure_motion_owner_restore.py', source/'inventure_motion_owner_restore.py')
            shutil.copyfile(ROOT/'inventure_service_excursion_restore.py', source/'inventure_service_excursion_restore.py')
            shutil.copyfile(ROOT/'inventure_scheduler_restore.py', source/'inventure_scheduler_restore.py')
            shutil.copyfile(ROOT/'inventure_safety_policy_restore.py', source/'inventure_safety_policy_restore.py')
            shutil.copyfile(ROOT/'inventure_operator_service_restore.py', source/'inventure_operator_service_restore.py')
            shutil.copyfile(ROOT/'inventure_leveling_ui_restore.py', source/'inventure_leveling_ui_restore.py')
            # Reuse only the read-only object database; no shared worktree/index operation.
            gitdir = subprocess.check_output(
                ['git', '-C', str(ROOT), 'rev-parse', '--absolute-git-dir'], text=True).strip()
            (source/'.git').write_text('gitdir: ' + gitdir + '\n')
            yield source

    def test_builds_real_r38_6_sources_with_pinned_api_and_normal_modes(self):
        self.assertEqual(self.metadata['host_revision'], bootstrap.HOST_REVISION)
        self.assertEqual(self.metadata['upstream_commit'], bootstrap.UPSTREAM_COMMIT)
        self.assertEqual(self.metadata['source_commit'], bootstrap.SOURCE_COMMIT)
        self.assertEqual(sha256((self.stage/'klippy/mcu.py').read_bytes()).hexdigest(), bootstrap.HOST_API_SHA256)
        self.assertEqual(stat.S_IMODE((self.stage/'scripts/check-gcc.sh').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.stage/'klippy/extras/inventure_mcu.py').stat().st_mode), 0o644)
        self.assertTrue(self.commands)
        util_spec = importlib.util.spec_from_file_location("bootstrap_pinned_util", self.stage/'klippy/util.py')
        util = importlib.util.module_from_spec(util_spec)
        util_spec.loader.exec_module(util)
        self.assertEqual(util.get_git_version()['version'], bootstrap.VERSION)
        self.assertFalse((self.stage/'.version').exists())
        renderer = self.stage/'klippy/extras/inventure_oled_render.py'
        restoration = self.metadata['runtime_restorations']['oled_dual_thermal_v2']
        self.assertEqual(sha256(renderer.read_bytes()).hexdigest(), restoration['adapted_sha256'])
        self.assertEqual(restoration['release'], 'RC8-OLED-DUAL-THERMAL-V2')
        native = self.metadata['runtime_restorations']['native_z_r18']
        self.assertEqual(native['release'], 'RC8-NATIVE-Z-POLICY-R18')
        owner = self.metadata['runtime_restorations']['single_motion_owner_r19']
        self.assertEqual(owner['release'], 'RC8-KLIPPER-SINGLE-MOTION-OWNER-R19')
        self.assertEqual(owner['release'], 'RC8-KLIPPER-SINGLE-MOTION-OWNER-R19')
        service = self.metadata['runtime_restorations']['exclusive_service_r20']
        self.assertEqual(service['release'], 'RC8-EXCLUSIVE-SERVICE-HANDOFF-R20')
        for target,record in owner['files'].items():
            if target not in service['files']:
                self.assertEqual(sha256((self.stage/target).read_bytes()).hexdigest(),
                                 record['sha256'])
        scheduler=self.metadata['runtime_restorations']['scheduler_r21']
        self.assertEqual(scheduler['release'],'RC8-STM32-SCHEDULER-DIAGNOSTICS-R21')
        for target,record in service['files'].items():
            if target not in scheduler['files']:
                self.assertEqual(sha256((self.stage/target).read_bytes()).hexdigest(),
                                 record['sha256'])
        policy=self.metadata['runtime_restorations']['safety_policy_r22']
        self.assertEqual(policy['release'],'RC8-SERVICE-NATIVE-POLICY-R22')
        for target,record in scheduler['files'].items():
            if target!=policy['target']:
                self.assertEqual(sha256((self.stage/target).read_bytes()).hexdigest(),
                                 record['sha256'])
        entry=self.metadata['runtime_restorations']['operator_service_r23']
        self.assertEqual(entry['prior_blob'],policy['installed_blob'])
        self.assertEqual(sha256((self.stage/entry['target']).read_bytes()).hexdigest(),
                         entry['sha256'])
        self.assertEqual(restoration['source_sha256'],
                         'c37e1c9c9e085193818b9b9d472c0dc3f1ac1399465bb5db6d3f4b0c099ad437')
        self.assertNotEqual(self.metadata['files']['klippy/extras/inventure_mcu.py'],
                            self.legacy_metadata['files']['klippy/extras/inventure_mcu.py'])

    def test_explicit_verification_reads_existing_runtime_without_repairing_it(self):
        path = self.stage/'klippy/extras/inventure_mcu.py'
        before = (path.stat().st_mtime_ns, path.read_bytes())
        result = bootstrap.verify_host(self.stage, repo=ROOT)
        self.assertEqual(result['status'], 'VERIFIED_HOST')
        self.assertEqual(result['software_version'], bootstrap.VERSION)
        self.assertFalse(result['runtime_restoration_required'])
        self.assertEqual(result['oled_restoration'], self.metadata['runtime_restorations']['oled_dual_thermal_v2'])
        self.assertEqual(result['native_z_restoration'], self.metadata['runtime_restorations']['native_z_r18'])
        self.assertEqual(result['single_motion_owner_restoration'],
                         self.metadata['runtime_restorations']['single_motion_owner_r19'])
        self.assertEqual(result['exclusive_service_restoration'],
                         self.metadata['runtime_restorations']['exclusive_service_r20'])
        self.assertEqual(result['scheduler_restoration'],
                         self.metadata['runtime_restorations']['scheduler_r21'])
        self.assertEqual(result['safety_policy_restoration'],
                         self.metadata['runtime_restorations']['safety_policy_r22'])
        self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))

    def test_legacy_recognition_matches_the_previously_generated_runtime_manifest(self):
        # Recorded from the actual pre-restoration R38.6 bootstrap, before this
        # change.  It independently freezes the whole previous file map.
        self.assertEqual(sha256(bootstrap._metadata_bytes(self.legacy_metadata)).hexdigest(),
                         '0d4db8e73a5dd9dabefbb7b6e85e51b76077d68bcf33884849bf654a08b63c5c')
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (
            self.legacy/bootstrap.MANIFEST_NAME,
            self.legacy/'klippy/extras/inventure_oled_render.py')}
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            bootstrap.verify_host(self.legacy, repo=ROOT)
        for result in (bootstrap.verify_legacy_host(self.legacy, repo=ROOT),
                       bootstrap.verify_host(self.legacy, repo=ROOT, allow_legacy=True)):
            self.assertEqual(result['status'], 'VERIFIED_LEGACY_HOST')
            self.assertTrue(result['runtime_restoration_required'])
            self.assertIsNone(result['oled_restoration'])
        self.assertEqual(before, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in before})
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            bootstrap.verify_legacy_host(self.stage, repo=ROOT)

    def test_legacy_update_plan_writes_nothing_and_only_changes_renderer_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.legacy, stage)
            before = {p.relative_to(stage): (p.stat().st_mtime_ns, p.read_bytes())
                      for p in stage.rglob('*') if p.is_file()}
            plan = bootstrap.prepare_runtime_update(stage, repo=ROOT)
            expected_names = {Path('klippy/extras/inventure_oled_render.py'),
                              Path('klippy/extras/inventure_native_z.py'),
                              Path('klippy/extras/inventure_z_home.py'),
                              Path('klippy/extras/inventure_mcu.py'),
                              Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS
            self.assertEqual({p.relative_to(stage) for p in plan['files']}, expected_names)
            self.assertEqual(plan['metadata'], self.metadata)
            self.assertEqual(plan['restoration'], self.metadata['runtime_restorations']['oled_dual_thermal_v2'])
            self.assertEqual(before, {p.relative_to(stage): (p.stat().st_mtime_ns, p.read_bytes())
                                      for p in stage.rglob('*') if p.is_file()})
            for path, (old, new) in plan['files'].items():
                self.assertEqual(path.read_bytes(), old)
                path.write_bytes(new)
            result = bootstrap.verify_host(stage, repo=ROOT)
            self.assertFalse(result['runtime_restoration_required'])
            changed = {p.relative_to(stage) for p in stage.rglob('*') if p.is_file()
                       and p.read_bytes() != before[p.relative_to(stage)][1]}
            self.assertEqual(changed, expected_names)
            with self.assertRaisesRegex(ValueError, 'metadata differs'):
                bootstrap.prepare_runtime_update(stage, repo=ROOT)
            # The transaction's saved bytes are sufficient for exact rollback.
            for path, (old, _) in plan['files'].items():
                path.write_bytes(old)
            self.assertTrue(bootstrap.verify_legacy_host(stage, repo=ROOT)['runtime_restoration_required'])

    def test_current_oled_only_runtime_migrates_only_native_z_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.oled_only, stage)
            self.assertTrue(bootstrap.verify_legacy_host(stage, repo=ROOT)['runtime_restoration_required'])
            before_oled = (stage/'klippy/extras/inventure_oled_render.py').read_bytes()
            plan = bootstrap.prepare_runtime_update(stage, repo=ROOT)
            expected_names = {Path('klippy/extras/inventure_native_z.py'),
                              Path('klippy/extras/inventure_z_home.py'),
                              Path('klippy/extras/inventure_mcu.py'),
                              Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS
            self.assertEqual({p.relative_to(stage) for p in plan['files']}, expected_names)
            self.assertEqual(plan['native_z_restoration'],
                             self.metadata['runtime_restorations']['native_z_r18'])
            for path, (original, updated) in plan['files'].items():
                self.assertEqual(path.read_bytes(), original)
                path.write_bytes(updated)
            self.assertEqual((stage/'klippy/extras/inventure_oled_render.py').read_bytes(), before_oled)
            self.assertEqual(bootstrap.verify_host(stage, repo=ROOT)['status'], 'VERIFIED_HOST')
            for path, (original, updated) in plan['files'].items():
                path.write_bytes(original)
            self.assertEqual(bootstrap.verify_legacy_host(stage, repo=ROOT)['status'], 'VERIFIED_LEGACY_HOST')

    def test_r18_host_migrates_exact_motion_owner_modules_only(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.r18, stage)
            self.assertEqual(bootstrap.verify_legacy_host(stage, repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')
            oled = (stage/'klippy/extras/inventure_oled_render.py').read_bytes()
            plan = bootstrap.prepare_runtime_update(stage, repo=ROOT)
            expected = {Path('klippy/extras/inventure_native_z.py'),
                        Path('klippy/extras/inventure_z_home.py'),
                        Path('klippy/extras/inventure_mcu.py'),
                        Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS
            self.assertEqual({p.relative_to(stage) for p in plan['files']}, expected)
            self.assertEqual(plan['single_motion_owner_restoration'],
                             self.metadata['runtime_restorations']['single_motion_owner_r19'])
            for dest,(old,new) in plan['files'].items():
                self.assertEqual(dest.read_bytes(), old)
                dest.write_bytes(new)
            self.assertEqual((stage/'klippy/extras/inventure_oled_render.py').read_bytes(),oled)
            self.assertEqual(bootstrap.verify_host(stage, repo=ROOT)['status'], 'VERIFIED_HOST')
            for dest,(old,new) in plan['files'].items():
                dest.write_bytes(old)
            self.assertEqual(bootstrap.verify_legacy_host(stage, repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')

    def test_r19_host_updates_only_two_service_excursion_modules_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.r19, stage)
            self.assertEqual(bootstrap.verify_legacy_host(stage, repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')
            oled = (stage/'klippy/extras/inventure_oled_render.py').read_bytes()
            factory = (stage/'klippy/extras/inventure_z_home.py').read_bytes()
            plan = bootstrap.prepare_runtime_update(stage, repo=ROOT)
            expected = {Path('klippy/extras/inventure_native_z.py'),
                        Path('klippy/extras/inventure_mcu.py'), Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS
            self.assertEqual({p.relative_to(stage) for p in plan['files']}, expected)
            self.assertEqual(plan['exclusive_service_restoration'],
                             self.metadata['runtime_restorations']['exclusive_service_r20'])
            for dest,(old,new) in plan['files'].items():
                self.assertEqual(dest.read_bytes(),old)
                dest.write_bytes(new)
            self.assertEqual((stage/'klippy/extras/inventure_oled_render.py').read_bytes(),oled)
            self.assertEqual((stage/'klippy/extras/inventure_z_home.py').read_bytes(),factory)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT)['status'],'VERIFIED_HOST')
            for dest,(old,new) in plan['files'].items():
                dest.write_bytes(old)
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')

    def test_r20_runtime_updates_only_scheduler_readonly_and_audit_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            stage=Path(temp)/'host'
            shutil.copytree(self.r20,stage)
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')
            oled=(stage/'klippy/extras/inventure_oled_render.py').read_bytes()
            old_z_program=(stage/'klippy/extras/inventure_z_home.py').read_bytes()
            plan=bootstrap.prepare_runtime_update(stage,repo=ROOT)
            changed={p.relative_to(stage) for p in plan['files']}
            self.assertEqual(changed,{
                Path('klippy/extras/inventure_mcu.py'),
                Path('klippy/extras/inventure_native_z.py'),
                Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS)
            self.assertEqual(plan['scheduler_restoration'],
                             self.metadata['runtime_restorations']['scheduler_r21'])
            for path,(before,after) in plan['files'].items():
                self.assertEqual(path.read_bytes(),before)
                path.write_bytes(after)
            self.assertEqual((stage/'klippy/extras/inventure_oled_render.py').read_bytes(),oled)
            self.assertEqual((stage/'klippy/extras/inventure_z_home.py').read_bytes(),old_z_program)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT)['status'],'VERIFIED_HOST')
            for path,(before,after) in plan['files'].items():
                path.write_bytes(before)
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')

    def test_r21_to_r22_upgrades_only_mcu_policy_module_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            stage=Path(temp)/'host'
            shutil.copytree(self.r21,stage)
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')
            oled=(stage/'klippy/extras/inventure_oled_render.py').read_bytes()
            z=(stage/'klippy/extras/inventure_native_z.py').read_bytes()
            factory=(stage/'klippy/extras/inventure_z_home.py').read_bytes()
            plan=bootstrap.prepare_runtime_update(stage,repo=ROOT)
            self.assertEqual({p.relative_to(stage) for p in plan['files']},
                             {Path('klippy/extras/inventure_mcu.py'),
                              Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS)
            self.assertEqual(plan['safety_policy_restoration'],
                             self.metadata['runtime_restorations']['safety_policy_r22'])
            for path,(old,new) in plan['files'].items():
                self.assertEqual(path.read_bytes(),old)
                path.write_bytes(new)
            self.assertEqual((stage/'klippy/extras/inventure_oled_render.py').read_bytes(),oled)
            self.assertEqual((stage/'klippy/extras/inventure_native_z.py').read_bytes(),z)
            self.assertEqual((stage/'klippy/extras/inventure_z_home.py').read_bytes(),factory)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT)['status'],'VERIFIED_HOST')
            for path,(old,new) in plan['files'].items():
                path.write_bytes(old)
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')

    def test_published_r23_rc1_migrates_only_service_entry_and_rolls_back_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            stage=Path(temp)/'host'
            shutil.copytree(self.r22,stage)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT,allow_legacy=True)['status'],
                             'VERIFIED_LEGACY_HOST')
            before={p:p.read_bytes() for p in stage.rglob('*') if p.is_file()}
            plan=bootstrap.prepare_runtime_update(stage,repo=ROOT)
            self.assertEqual({p.relative_to(stage) for p in plan['files']},
                             {Path('klippy/extras/inventure_mcu.py'),Path(bootstrap.MANIFEST_NAME)} | UI_TARGETS)
            self.assertFalse(plan['operator_service_restoration']['mcu_update_required'])
            for path,(old,new) in plan['files'].items():
                self.assertEqual(path.read_bytes(),old)
                path.write_bytes(new)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT)['status'],'VERIFIED_HOST')
            for path,(old,new) in plan['files'].items():path.write_bytes(old)
            self.assertEqual(before,{p:p.read_bytes() for p in stage.rglob('*') if p.is_file()})
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],
                             'VERIFIED_LEGACY_HOST')

    def test_published_r23_rc2_upgrades_only_two_ui_modules_and_preserves_motor_program(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.r23, stage)
            before = {p:p.read_bytes() for p in stage.rglob('*') if p.is_file()}
            plan = bootstrap.prepare_runtime_update(stage,repo=ROOT)
            self.assertEqual({p.relative_to(stage) for p in plan['files']},
                             UI_TARGETS | {Path(bootstrap.MANIFEST_NAME)})
            self.assertFalse(plan['leveling_ui_restoration']['mcu_update_required'])
            for path,(_,new) in plan['files'].items():path.write_bytes(new)
            self.assertEqual(bootstrap.verify_host(stage,repo=ROOT)['status'],'VERIFIED_HOST')
            for path in before:
                if path not in plan['files']:self.assertEqual(path.read_bytes(),before[path])
            for path,(old,_) in plan['files'].items():path.write_bytes(old)
            self.assertEqual(before,{p:p.read_bytes() for p in stage.rglob('*') if p.is_file()})
            self.assertEqual(bootstrap.verify_legacy_host(stage,repo=ROOT)['status'],'VERIFIED_LEGACY_HOST')

    def test_leveling_ui_tamper_is_rejected_before_staging(self):
        with self.source_copy() as source,tempfile.TemporaryDirectory() as temp:
            path = source/'restored/leveling_ui_r23/inventure_menu_service.py'
            path.write_bytes(path.read_bytes()+b'\n# unaudited UI edit\n')
            with self.assertRaisesRegex(ValueError,'Leveling UI restoration integrity mismatch'):
                bootstrap.prepare_host(Path(temp)/'host',repo=source)
            self.assertFalse((Path(temp)/'host').exists())

    def test_r22_refuses_unaudited_policy_module_before_install(self):
        with self.source_copy() as source,tempfile.TemporaryDirectory() as temp:
            path=source/'restored/safety_policy_r22/inventure_mcu.py'
            original=path.read_bytes()
            try:
                path.write_bytes(original+b'\n# unreviewed NATIVE safety change\n')
                with self.assertRaisesRegex(ValueError,'R22 safety policy module integrity mismatch'):
                    bootstrap.prepare_host(Path(temp)/'host',repo=source)
                self.assertFalse((Path(temp)/'host').exists())
            finally:
                path.write_bytes(original)

    def test_r21_restoration_source_tamper_is_detected_before_writing(self):
        with self.source_copy() as source,tempfile.TemporaryDirectory() as temp:
            for relative in ('restored/scheduler_r21/inventure_mcu.py',
                             'restored/scheduler_r21/inventure_native_z.py'):
                path=source/relative
                original=path.read_bytes()
                try:
                    path.write_bytes(original+b'\n# unreviewed scheduler or handoff change\n')
                    with self.subTest(relative=relative),self.assertRaisesRegex(
                            ValueError,'R21 scheduler module integrity mismatch'):
                        bootstrap.prepare_host(Path(temp)/'host',repo=source)
                    self.assertFalse((Path(temp)/'host').exists())
                finally:
                    path.write_bytes(original)

    def test_r20_source_integrity_rejects_unverified_service_changes(self):
        with self.source_copy() as source, tempfile.TemporaryDirectory() as temp:
            for relative in ('restored/exclusive_service_r20/inventure_native_z.py',
                             'restored/exclusive_service_r20/inventure_mcu.py'):
                path = source/relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\n# unreviewed MCU handoff change\n')
                    with self.subTest(relative=relative), self.assertRaisesRegex(
                            ValueError, 'R20 SERVICE excursion source integrity mismatch'):
                        bootstrap.prepare_host(Path(temp)/'host', repo=source)
                    self.assertFalse((Path(temp)/'host').exists())
                finally:
                    path.write_bytes(original)

    def test_r19_source_integrity_blocks_unverified_motion_changes(self):
        with self.source_copy() as source, tempfile.TemporaryDirectory() as temp:
            for relative in ('restored/single_owner_r19/inventure_native_z.py',
                             'restored/single_owner_r19/inventure_z_home.py',
                             'restored/single_owner_r19/inventure_mcu.py'):
                path = source/relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original+b'\n# unexpected motion change\n')
                    with self.subTest(relative=relative), self.assertRaisesRegex(
                            ValueError,'R19 motion-owner source integrity mismatch'):
                        bootstrap.prepare_host(Path(temp)/'host',repo=source)
                    self.assertFalse((Path(temp)/'host').exists())
                finally:
                    path.write_bytes(original)

    def test_altered_native_z_restoration_is_not_silently_installed(self):
        with self.source_copy() as source, tempfile.TemporaryDirectory() as temp:
            path = source/'restored/native_z_r18/inventure_native_z.py'
            original = path.read_bytes()
            try:
                path.write_bytes(original+b'\n# unexpected unverified native safety change\n')
                with self.assertRaisesRegex(ValueError, 'R18 NATIVE Z source integrity mismatch'):
                    bootstrap.prepare_host(Path(temp)/'host', repo=source)
                self.assertFalse((Path(temp)/'host').exists())
            finally:
                path.write_bytes(original)

    def test_partial_or_modified_legacy_runtime_never_becomes_a_restoration_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.legacy, stage)
            renderer = stage/'klippy/extras/inventure_oled_render.py'
            original = renderer.read_bytes()
            for replacement in (original + b'\n# unexpected edit\n',
                                (self.stage/'klippy/extras/inventure_oled_render.py').read_bytes()):
                renderer.write_bytes(replacement)
                for call in (lambda: bootstrap.verify_host(stage, repo=ROOT, allow_legacy=True),
                             lambda: bootstrap.prepare_runtime_update(stage, repo=ROOT)):
                    with self.assertRaisesRegex(ValueError, 'Installed host file differs'):
                        call()
                self.assertEqual(renderer.read_bytes(), replacement)
            renderer.write_bytes(original)
            mcu = stage/'klippy/extras/inventure_mcu.py'
            mcu.write_bytes(mcu.read_bytes()+b'\n# unexpected edit\n')
            with self.assertRaisesRegex(ValueError, 'Installed host file differs'):
                bootstrap.prepare_runtime_update(stage, repo=ROOT)

    def test_altered_or_symlinked_restoration_source_is_rejected_before_staging(self):
        with self.source_copy() as source, tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)/'host'
            for name in ('restored/oled_dual_thermal_v2/inventure_oled_render.py',
                         'restored/oled_dual_thermal_v2/provenance.json'):
                path = source/name
                original = path.read_bytes()
                path.write_bytes(original+b'\n')
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'OLED source checksum mismatch'):
                    bootstrap.prepare_host(dest, repo=source)
                self.assertFalse(dest.exists())
                path.unlink()
                path.symlink_to(ROOT/name)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Symlink in restored OLED source'):
                    bootstrap.prepare_host(dest, repo=source)
                self.assertFalse(dest.exists())
                path.unlink()
                path.write_bytes(original)

    def test_preparation_refuses_existing_directory_file_and_broken_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            collision = Path(temp)/'collision'
            collision.write_bytes(b'keep')
            broken = Path(temp)/'broken'
            broken.symlink_to(Path(temp)/'absent')
            for target in (self.stage, collision, broken):
                with self.subTest(target=target), patch.object(bootstrap, '_plan') as plan:
                    with self.assertRaises(FileExistsError):
                        bootstrap.prepare_host(target, repo=ROOT)
                    plan.assert_not_called()
            self.assertEqual(collision.read_bytes(), b'keep')
            self.assertTrue(broken.is_symlink())

    def test_modified_source_code_manifest_and_archive_part_are_rejected_before_staging(self):
        with self.source_copy() as source, tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)/'host'
            for name in ('host/inventure_mcu.py', 'orange_pi/THERMAL_HOST_MANIFEST.json',
                         'orange_pi/install_thermal_test.py', 'source/parts/part0000.bin'):
                path = source/name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\n# changed\n')
                    with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Release input differs'):
                        bootstrap.prepare_host(dest, repo=source)
                    self.assertFalse(dest.exists())
                    self.assertEqual(list(Path(temp).iterdir()), [])
                finally:
                    path.write_bytes(original)

    def test_untracked_or_symlinked_runtime_payload_is_rejected(self):
        with self.source_copy() as source:
            extra = source/'orange_pi/inventure_unreviewed.py'
            extra.write_text('raise RuntimeError("must not execute")\n')
            with self.assertRaisesRegex(ValueError, 'Unexpected or missing release inputs'):
                bootstrap._plan(source)
            extra.unlink()
            path = source/'host/inventure_oled.py'
            path.unlink()
            path.symlink_to(ROOT/'host/inventure_oled.py')
            with self.assertRaisesRegex(ValueError, 'Expected a regular file'):
                bootstrap._plan(source)

    def test_failed_core_check_cleans_temporary_build_and_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)/'host'
            with patch.object(bootstrap, '_core_check', side_effect=ValueError('injected verification failure')):
                with self.assertRaisesRegex(ValueError, 'injected verification failure'):
                    bootstrap.prepare_host(destination, repo=ROOT)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_collision_during_publication_does_not_replace_new_directory(self):
        real_publish = bootstrap._publish_new
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)/'host'

            def collide(stage, target):
                target.mkdir()
                (target/'keep').write_text('created by another process')
                return real_publish(stage, target)

            with patch.object(bootstrap, '_publish_new', side_effect=collide):
                with self.assertRaises(FileExistsError):
                    bootstrap.prepare_host(destination, repo=ROOT)
            self.assertEqual((destination/'keep').read_text(), 'created by another process')
            self.assertEqual(list(Path(temp).iterdir()), [destination])

    def test_verification_rejects_modified_sources_even_with_rewritten_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.stage, stage)
            module = stage/'klippy/extras/inventure_mcu.py'
            module.write_bytes(module.read_bytes() + b'\n# local modification\n')
            with self.assertRaisesRegex(ValueError, 'Installed host file differs'):
                bootstrap.verify_host(stage, repo=ROOT)
            meta = json.loads((stage/bootstrap.MANIFEST_NAME).read_text())
            meta['files']['klippy/extras/inventure_mcu.py']['sha256'] = sha256(module.read_bytes()).hexdigest()
            (stage/bootstrap.MANIFEST_NAME).write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'metadata differs'):
                bootstrap.verify_host(stage, repo=ROOT)

    def test_verification_rejects_changed_permissions_and_untracked_python(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.stage, stage)
            script = stage/'scripts/check-gcc.sh'
            script.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'permissions differ'):
                bootstrap.verify_host(stage, repo=ROOT)
            script.chmod(0o755)
            extra = stage/'klippy/extras/inventure_unreviewed.py'
            extra.write_text('not a release module')
            with self.assertRaisesRegex(ValueError, 'Unexpected file'):
                bootstrap.verify_host(stage, repo=ROOT)

    def test_verification_reports_normal_runtime_cache_but_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/'host'
            shutil.copytree(self.stage, stage)
            helper = stage/'klippy/chelper/c_helper.so'
            helper.write_bytes(b'generated cache fixture; never loaded')
            result = bootstrap.verify_host(stage, repo=ROOT)
            self.assertIn('klippy/chelper/c_helper.so', result['generated_files'])
            helper.unlink()
            helper.symlink_to(stage/'klippy/util.py')
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                bootstrap.verify_host(stage, repo=ROOT)


if __name__ == '__main__':
    unittest.main()
