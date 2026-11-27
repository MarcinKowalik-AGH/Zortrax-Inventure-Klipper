"""Installed menu artwork/parity and the narrowly scoped config addition."""
import configparser
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ci'))
from check_inventure_menu_audit import check


class MenuAuditTests(unittest.TestCase):
    def test_every_installed_page_and_dialog_has_resolving_shared_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            report = check(Path(directory))
            self.assertEqual(report['original_records'], 66)
            self.assertEqual(report['static_positions'], 133)
            self.assertEqual(report['dynamic_positions'], 221)
            self.assertEqual(report['missing_icons'], 0)
            self.assertTrue(all(row['oled_fluidd_same'] for row in report['rows']))
            self.assertFalse(report['hardware_tested'])

    def test_config_guard_accepts_only_canonical_missing_alignment_entry(self):
        spec = importlib.util.spec_from_file_location('menu_audit_installer', ROOT/'install-new-host.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        before = configparser.RawConfigParser()
        before.read_string('[stepper_x]\nrotation_distance: 40\n')
        canonical = configparser.RawConfigParser()
        canonical.read(ROOT/'config/INVENTURE_DASHBOARD.cfg')
        name = 'gcode_macro INVENTURE_NOZZLE_ALIGNMENT'
        after = configparser.RawConfigParser()
        after.read_string('[stepper_x]\nrotation_distance: 40\n')
        after.add_section(name)
        for key, value in canonical.items(name):
            after.set(name, key, value)

        def verify():
            with patch.object(installer.updater, 'klipper_config_module', return_value=None), \
                 patch.object(installer.layout, 'active_klipper', side_effect=[(before, None), (after, None)]):
                installer.require_identical_configuration(ROOT, ROOT/'before', ROOT/'after')

        verify()
        after.set('stepper_x', 'rotation_distance', '41')
        with self.assertRaisesRegex(ValueError, 'configuration change'):
            verify()
        after.set('stepper_x', 'rotation_distance', '40')
        after.set(name, 'gcode', 'G1 X100')
        with self.assertRaisesRegex(ValueError, 'configuration change'):
            verify()


if __name__ == '__main__':
    unittest.main()
