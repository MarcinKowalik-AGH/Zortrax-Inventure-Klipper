"""Installed host handoff against the R23 MCU's physical-enable condition."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

import inventure_bootstrap as bootstrap

ROOT = Path(__file__).resolve().parents[1]


def installed_entry():
    _, files, _ = bootstrap._plan(ROOT)
    source = files['klippy/extras/inventure_mcu.py'][0]
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InventureMCU')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_enter_operator_service')
    modes = next(n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'CONTROL_MODES' for t in n.targets))
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[modes, method], type_ignores=[])),
                 'installed/inventure_mcu.py', 'exec'), namespace)
    return namespace['_enter_operator_service']


class PhysicalEnable:
    """M84 is queued until the toolhead drains; an idle motor can stay enabled."""
    def __init__(self, mode='NATIVE'):
        self.mode = mode
        self.enabled = mode == 'NATIVE'
        self.disable_pending = False
        self.events = []
        self.refusal = None
        self.guard_busy = False
        self.after_wait = None

    def wait_moves(self):
        self.events.append('drain')
        if self.disable_pending:
            self.enabled = self.disable_pending = False
        if self.after_wait:
            self.after_wait()

    def motor_off(self):
        self.events.append('disable')
        self.disable_pending = True

    def preflight(self, **kwargs):
        if self.refusal:
            raise ValueError(self.refusal)
        return dict(mode=self.mode)

    def control(self, action, mode):
        self.events.append('request_service')
        # Same independent ownership condition as native_service_quiescent():
        # no transfer while the physical shared PE10 enable or a guard is held.
        reject = 'another_owner_is_busy' if self.enabled or self.guard_busy else None
        if not reject:
            self.mode = 'SERVICE'
        return dict(mode=self.mode, reject=reject, fault=0, latched=0)

    def board(self):
        objects = {'toolhead': self, 'stepper_enable': self}
        return SimpleNamespace(
            printer=SimpleNamespace(lookup_object=lambda name, default=None: objects.get(name, default)),
            _operator_service_preflight=self.preflight, _control_query=self.control,
            _control_schedule=lambda: self.events.append('schedule'),
            _control_stop_outputs=lambda: self.events.append('STOP'))


class OperatorServiceEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enter = staticmethod(installed_entry())

    def test_native_idle_after_home_releases_physical_enable_before_service(self):
        plant = PhysicalEnable()
        self.enter(plant.board())
        self.assertEqual(plant.mode, 'SERVICE')
        self.assertFalse(plant.enabled)
        self.assertEqual(plant.events, ['drain', 'disable', 'drain', 'request_service', 'schedule'])

    def test_preflight_refuses_busy_or_faulted_native_without_disabling_or_requesting(self):
        for reason in ('printing', 'paused', 'native heater target', 'homing running',
                       'filament owner', 'MCU fault', 'MCU latch'):
            with self.subTest(reason=reason):
                plant = PhysicalEnable()
                plant.refusal = reason
                with self.assertRaisesRegex(ValueError, reason):
                    self.enter(plant.board())
                self.assertTrue(plant.enabled)
                self.assertEqual(plant.events, [])

    def test_new_owner_during_drain_is_refused_before_motor_release(self):
        plant = PhysicalEnable()
        plant.after_wait = lambda: setattr(plant, 'refusal', 'print started')
        with self.assertRaisesRegex(ValueError, 'print started'):
            self.enter(plant.board())
        self.assertEqual(plant.events, ['drain'])
        self.assertTrue(plant.enabled)

    def test_busy_mcu_guard_remains_a_refusal_without_stop_or_retry(self):
        plant = PhysicalEnable()
        plant.guard_busy = True
        with self.assertRaisesRegex(ValueError, 'another_owner_is_busy'):
            self.enter(plant.board())
        self.assertEqual(plant.events.count('request_service'), 1)
        self.assertNotIn('STOP', plant.events)
        self.assertEqual(plant.mode, 'NATIVE')

    def test_already_service_and_off_do_not_release_native_motors(self):
        plant = PhysicalEnable('SERVICE')
        self.enter(plant.board())
        self.assertEqual(plant.events, [])
        plant = PhysicalEnable('OFF')
        self.enter(plant.board())
        self.assertEqual(plant.events, ['request_service', 'schedule'])

    def test_missing_native_queue_or_enable_owner_refuses_before_transfer(self):
        for missing in ('toolhead', 'stepper_enable'):
            with self.subTest(missing=missing):
                plant = PhysicalEnable()
                board = plant.board()
                lookup = board.printer.lookup_object
                board.printer.lookup_object = lambda name, default=None: (
                    default if name == missing else lookup(name, default))
                with self.assertRaisesRegex(ValueError, 'requires the Klipper'):
                    self.enter(board)
                self.assertEqual(plant.events, [])

    def test_fault_while_disable_is_queued_prevents_service_request(self):
        plant = PhysicalEnable()
        def after_wait():
            if not plant.enabled:
                plant.refusal = 'MCU fault after drain'
        plant.after_wait = after_wait
        with self.assertRaisesRegex(ValueError, 'MCU fault after drain'):
            self.enter(plant.board())
        self.assertEqual(plant.events, ['drain', 'disable', 'drain'])


if __name__ == '__main__':
    unittest.main()
