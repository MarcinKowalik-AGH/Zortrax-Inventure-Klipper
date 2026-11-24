"""Regression data from two 2026-11-24 Inventure incidents, sanitized."""
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from inventure_fault_analyzer import analyze,read_log

class ForensicLogTests(unittest.TestCase):
    def test_native_xy_timer_shutdown_with_uart_loss_but_low_host_load(self):
        lines=[
            "Stats 353.7: sysload=0.13 bytes_write=304457 bytes_read=682980 bytes_retransmit=43188 bytes_invalid=20",
            "Received 353.903425: b'{\"method\":\"gcode/script\",\"params\":{\"script\":\"INVENTURE_HOME_XY\"}}'",
            "Dumping serial stats: bytes_write=305280 bytes_read=685529 bytes_retransmit=43529 bytes_invalid=20",
            "MCU 'mcu' shutdown: Timer too close",
            "Receive: 99 354.904885 0.000000 12: shutdown clock=1975672 static_string_id=Timer too close",
            "MCU 'mcu' shutdown: Timer too close",
        ]
        result=analyze(lines)
        self.assertEqual(result['event_counts'],{'mcu_scheduler_shutdown':1})
        self.assertEqual(result['incidents'][0]['last_home_command'],'INVENTURE_HOME_XY')
        self.assertEqual(result['incidents'][0]['clock_at_shutdown'],1975672)
        self.assertGreater(result['incidents'][0]['serial']['retransmit_fraction'],.14)
        self.assertTrue(any('NOT proof' in warning for warning in result['warnings']))

    def test_real_mcu_safety_shutdown_is_not_mislabeled_uart_disconnection(self):
        result=analyze([
            "MCU 'mcu' shutdown: Inventure V9 build tray PC13 interlock",
        ])
        self.assertEqual(result['event_counts'],{'mcu_safety_shutdown':1})
        self.assertNotIn('mcu_scheduler_shutdown',result['event_counts'])

    def test_handoff_audit_detects_nonmatching_owner_or_missing_receipt(self):
        def event(payload):
            import json
            return 'INVENTURE_OWNER_AUDIT '+json.dumps(payload)
        rows=[
            event(dict(phase='klipper_xy_certified',xy=[0,0])),
            event(dict(phase='service_handoff_requested',owner=8,run=9,token=10)),
            event(dict(phase='service_handoff_verified',owner=8,run=9,token=11,
                       mode='NATIVE',verified=True)),
            event(dict(phase='klipper_xy_imported',owner=8,run=9,token=10)),
        ]
        result=analyze(rows)
        kinds={v['kind'] for v in result['violations']}
        self.assertIn('mcu_handoff_identity_mismatch',kinds)
        self.assertIn('klipper_pose_import_without_handoff',kinds)

    def test_valid_single_handoff_and_clocks_do_not_create_false_alarm(self):
        import json
        rows=['INVENTURE_OWNER_AUDIT '+json.dumps(a) for a in [
            dict(phase='klipper_xy_certified',xy=[0,0]),
            dict(phase='service_handoff_requested',owner=1,run=2,token=3),
            dict(phase='service_handoff_verified',owner=1,run=2,token=3,
                 mode='NATIVE',verified=True),
            dict(phase='klipper_xy_imported',owner=1,run=2,token=3)]]
        rows += ['inventure_scheduler_state clock=4294967100 resync=1 skipped=2',
                 'inventure_scheduler_state clock=9500 resync=1 skipped=2']
        result=analyze(rows)
        self.assertFalse(result['incidents'])
        self.assertFalse(result['violations'])
        self.assertEqual(result['outcome'],'no_fault_observed_in_supplied_log')

    def test_late_sampler_delta_is_reported_without_faking_motor_fault(self):
        result=analyze([
            'inventure_scheduler_state clock=800 resync=2 skipped=4',
            'inventure_scheduler_state clock=1000 resync=3 skipped=9'])
        self.assertEqual(result['violations'][0]['kind'],'sampler_deadlines_missed')
        self.assertEqual(result['violations'][0]['changes'],dict(resync=1,skipped=5))
        self.assertFalse(result['incidents'])

    def test_zip_and_plain_text_read_identically(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            log=root/'klippy.log'
            log.write_bytes(b'\x00MCU \'mcu\' shutdown: Timer too close\n')
            archive=root/'klippy.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.write(log,'klippy.log')
            self.assertEqual(read_log(log),read_log(archive))

if __name__=='__main__':unittest.main()
