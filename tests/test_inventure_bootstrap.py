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

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def source_copy(self):
        with tempfile.TemporaryDirectory(prefix="inventure-input-test-") as temp:
            source = Path(temp)
            for name in ("host", "orange_pi", "source"):
                shutil.copytree(ROOT/name, source/name, ignore=shutil.ignore_patterns("__pycache__"))
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

    def test_explicit_verification_reads_existing_runtime_without_repairing_it(self):
        path = self.stage/'klippy/extras/inventure_mcu.py'
        before = (path.stat().st_mtime_ns, path.read_bytes())
        result = bootstrap.verify_host(self.stage, repo=ROOT)
        self.assertEqual(result['status'], 'VERIFIED_HOST')
        self.assertEqual(result['software_version'], bootstrap.VERSION)
        self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))

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
