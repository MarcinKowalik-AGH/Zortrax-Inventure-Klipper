"""Saved operator selections survive host restart and never replace confirmed P11 routes."""
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_orange_pi_door_policy import adapter

monitor_module = importlib.import_module(adapter.__package__ + '.inventure_jam_monitor')
storage_module = importlib.import_module(adapter.__package__ + '.inventure_jam_sources')


class JamSourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.path = self.base / 'inventure_jam_sources.json'
        self.objects = {}
        printer = SimpleNamespace(
            get_start_args=lambda: dict(config_file=str(self.base / 'printer.cfg')),
            register_event_handler=lambda *a: None,
            lookup_object=self.objects.get)
        self.board = SimpleNamespace(printer=printer, reactor=SimpleNamespace(
            register_timer=lambda *a: object()))

    def restart(self):
        return monitor_module.JamMonitor(self.board)

    def test_each_source_survives_host_recreation_and_P11_takes_precedence(self):
        first = self.restart()
        first.set_source('T0', 'EXTERNAL')
        first.set_source('T1', 'CASSETTE')
        restored = self.restart()
        self.assertEqual([restored.source(t) for t in ('T0', 'T1')], ['EXTERNAL', 'CASSETTE'])
        materials = SimpleNamespace(enabled=True, confirmed=True, stopped=False, pair=[
            SimpleNamespace(source='cartridge'), SimpleNamespace(source='spool')])
        self.objects['inventure_materials'] = materials
        self.assertEqual([restored.source(t) for t in ('T0', 'T1')], ['CASSETTE', 'EXTERNAL'])
        with self.assertRaisesRegex(ValueError, 'confirmed material source'):
            restored.set_source('T0', 'EXTERNAL')
        self.objects.clear()
        restored.set_source('T1', 'UNKNOWN')
        self.assertEqual(self.restart().sources.values, dict(T0='EXTERNAL', T1='UNKNOWN'))

    def test_corrupt_file_is_reported_and_never_silently_directs_transport(self):
        self.restart().set_source('T0', 'EXTERNAL')
        record = json.loads(self.path.read_text())
        record['sources']['T0'] = 'CASSETTE'
        self.path.write_text(json.dumps(record))
        restored = self.restart()
        self.assertEqual(restored.sources.values, dict(T0='UNKNOWN', T1='UNKNOWN'))
        self.assertIn('damaged', restored.status()['source_storage_error'])
        restored.set_source('T1', 'EXTERNAL')
        self.assertIsNone(restored.status()['source_storage_error'])
        self.assertEqual(self.restart().sources.values, dict(T0='UNKNOWN', T1='EXTERNAL'))

    def test_atomic_commit_failure_preserves_last_good_file_and_memory(self):
        monitor = self.restart()
        monitor.set_source('T1', 'EXTERNAL')
        before = self.path.read_bytes()
        with patch.object(storage_module.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'disk full'):
                monitor.set_source('T1', 'CASSETTE')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(monitor.source('T1'), 'EXTERNAL')
        self.assertEqual(self.restart().source('T1'), 'EXTERNAL')
        self.assertEqual(list(self.base.iterdir()), [self.path])

    def test_invalid_content_or_symlink_stays_unknown(self):
        self.path.write_text('not JSON')
        self.assertEqual(self.restart().source('T1'), 'UNKNOWN')
        other = self.base / 'other'
        self.path.rename(other)
        self.path.symlink_to(other)
        monitor = self.restart()
        self.assertIn('regular file', monitor.sources.error)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            monitor.set_source('T0', 'CASSETTE')
        self.assertEqual(other.read_text(), 'not JSON')
