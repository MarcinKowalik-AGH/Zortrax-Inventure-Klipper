#!/usr/bin/env python3
"""Exercise installed R22 operator cycles with simulated physical interfaces.

The real bootstrap, config includes, Klipper macro/token parser, menu, R22
factory dispatcher and restored native Home execute. Deterministic fixtures
replace motion/kinematics, operation leases, MCU replies, sensors and heaters.
No serial port is opened, hardware is flashed or Klipper reactor is run.
"""
import argparse
from contextlib import nullcontext, redirect_stdout
from hashlib import sha256
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class CyclePrinter:
    """A physical-interface fixture, shared by the installed host and menu."""
    def __init__(self, board, model, gcode, native):
        self.board, self.model, self.gcode = board, model, gcode
        self.objects = dict(board.printer.objects)
        self.objects.update(inventure_native_z=native, inventure_mcu=board,
                            gcode=gcode, toolhead=model, gcode_move=model,
                            heaters=model, print_stats=model.stats, stepper_enable=model,
                            homing=types.SimpleNamespace(cmd_G28=self.home))
        operations = self.objects['inventure_operations']
        operations.state.homed = lambda axes: None
        operations.state.selector_unknown = lambda reason: setattr(operations.state, 'selector_tool', None)
        operations.invalidate = lambda axes, reason: model.clear_homing_state(axes)
        operations.procedure = lambda name: nullcontext()

    def home(self, command):
        from test_orange_pi_native_z import Command
        self.model.cmd_G28(Command(**command.get_command_parameters()))
        self.model.shared_enable = True
        if 'Z' in command.get_command_parameters():
            bottom = self.objects['inventure_native_z'].bottom_position
            self.model.position[2] = self.model.physical_z = bottom

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self, module=None):
        return [(name, obj) for name, obj in self.objects.items()
                if module is None or name == module or name.startswith(module + ' ')]

    def get_reactor(self):
        return self.board.reactor

    def get_start_args(self):
        return dict(self.board.install_start_args)

    def register_event_handler(self, *args):
        pass

    def send_event(self, *args):
        return []

    def is_shutdown(self):
        return self.model.is_shutdown()

    def command_error(self, message):
        return self.gcode.error(message)

    config_error = command_error


class InstalledRig:
    def __init__(self, printer, klipper, *, hot=False):
        from test_orange_pi_native_z import NativeHomeSequenceTests
        from test_orange_pi_z_home_host import ZHomeHostTests
        from test_orange_pi_load_test import Wire
        from test_orange_pi_zortrax_menu import Config

        self.gcode = gcode = printer.lookup_object('gcode')
        self.native = native = printer.lookup_object('inventure_native_z')
        installed_board = printer.lookup_object('inventure_mcu')
        for obj, filename in ((native, 'inventure_native_z.py'),
                              (installed_board, 'inventure_mcu.py')):
            source = Path(sys.modules[type(obj).__module__].__file__).resolve()
            require(source == klipper / 'klippy/extras' / filename,
                    'Cycle did not load the installed ' + filename)

        fixture = NativeHomeSequenceTests()
        fixture.setUp()
        fixture.use_mcu_reference()
        self.model = model = fixture.model
        model.min_temp, model.max_temp = 0., 310.
        model.heater_target = 205. if hot else 0.
        for index, name in enumerate(('inventure_t1', 'chamber_l', 'chamber_r')):
            heater = types.SimpleNamespace(target_temp=180. if index == 0 else 60., last_pwm_value=0.)
            if not hot:
                heater.target_temp = 0.
            heater.set_temp = lambda value, heater=heater: setattr(heater, 'target_temp', value)
            heater.get_temp = lambda now, heater=heater: (heater.target_temp, heater.target_temp)
            model.heaters[name] = heater
        self.board = board = ZHomeHostTests().host(edges=True)
        board.install_start_args = dict(printer.get_start_args())
        # Reuse only its independently modelled wires/plant. All host methods,
        # including the entire factory interpreter, come from installed R22.
        board.__class__ = type(installed_board)
        board.allow_actuators = True
        board.native_xy_handoff_abi = 1
        board.device.z = 0
        board.control_reply.update(mode=1 if hot else 0, armed=int(hot))
        board.mcu.get_constants = lambda: {
            'INVENTURE_NATIVE_HEATER_HANDOFF_ABI': 1,
            'INVENTURE_NATIVE_Z_CLEARANCE_ABI': 1,
            'INVENTURE_SERVICE_NATIVE_SPLIT_ABI': 1,
            'INVENTURE_POLICY_STATUS_ABI': 1,
        }
        board.commands['factory_native_handoff'] = Wire(board.device.native_handoff)
        self.handoffs = board.commands['factory_native_handoff']
        board.gcode = gcode
        # These registered handlers belong to the configured board. Rebind
        # its physical-interface fixture, retaining the installed methods.
        printer.objects['inventure_mcu'] = board
        for command in ('INVENTURE_PLATFORM_LEVEL', 'ZORTRAX_PLATFORM_LEVEL'):
            gcode.register_command(command, None)
            gcode.register_command(command, board.cmd_platform_level)
        # Physical R23 refuses SERVICE while PE10 is still held, even when
        # native motion is idle. Model the queued Klipper disable separately.
        model.shared_enable = bool(hot)
        model.disable_pending = False
        motor_off, wait_moves = model.motor_off, model.wait_moves

        def release_motors():
            motor_off()
            model.disable_pending = True

        def drain_moves():
            wait_moves()
            if model.disable_pending:
                model.shared_enable = model.disable_pending = False

        model.motor_off, model.wait_moves = release_motors, drain_moves
        actual_control = board.device.control

        def guarded_control(data):
            action, mode = data
            if (action == 1 and mode == 2 and board.control_reply['mode'] == 1
                    and model.shared_enable):
                return dict(board.control_reply, active=1, reject=2)
            return actual_control(data)

        board.commands['control'] = Wire(guarded_control)

        # Preserve the values obtained from the staged config. Bind physical
        # interfaces on the actual registered adapter, not a copied handler.
        interfaces = ('toolhead', 'kin', 'z_enable', 'bottom', 'endstop',
                      'guard', 'refine', 'refine_clearance', 'stepper')
        for name in interfaces:
            setattr(native, name, getattr(fixture.adapter, name))
        model.get_kinematics = lambda: native.kin
        native.board, native.reactor, native.gcode = board, board.reactor, gcode
        native.printer = board.printer = CyclePrinter(board, model, gcode, native)
        native.heaters = model
        native.running = native.sequence_running = native.interrupted = False
        native.last_result = native.guard_owner = None
        native.toolhead.axis_rails[2].get_homing_info = lambda: types.SimpleNamespace(
            positive_dir=True, retract_dist=0., position_endstop=native.bottom_position)
        model.bottom.query_endstop = lambda _: model.physical_z >= native.bottom_position

        model.on_shutdown = native._shutdown
        model.monotonic = board.reactor.monotonic
        model.time = board.reactor.monotonic()
        model.native_mode = bool(hot)
        model.heater_target = 205. if hot else 0.
        board.printer.objects['inventure_filament'].enabled = False
        real_query = board._thermal_query_send

        def thermal_query(command, data, start=False, owner=None):
            if command in (native.guard, native.refine, native.refine_clearance):
                return model._thermal_query_send(command, data, start=start)
            return real_query(command, data, start=start, owner=owner)

        board._thermal_query_send = thermal_query
        self.actions = []
        real_action = board._factory_action

        def factory_action(context, action):
            result = real_action(context, action)
            self.actions.append(dict(action))
            if (action.get('kind') == 'module' and action.get('name') == 'select_tool'
                    and action.get('state') == 'done'):
                model.physical_tool = context['tool']
            return result

        board._factory_action = factory_action
        real_inputs = board.device.inputs

        def inputs():
            row = real_inputs()
            row['raw'].update(model._z_home_inputs({}))
            row['raw']['tray'] = int(bool(model.tray))
            board.inputs_received = board.reactor.monotonic()
            return row

        board._fresh_inputs = inputs
        module = importlib.import_module(type(installed_board).__module__.rsplit('.', 1)[0]
                                         + '.inventure_menu_service')
        # The staged menu already registered this name during config parsing.
        gcode.register_command('INVENTURE_ZORTRAX', None)
        board.zortrax = module.ZortraxMenuService(board, Config())
        self.menu = board.zortrax
        self.lines = []
        gcode.register_output_handler(self.lines.append)
        self.home_module = sys.modules[type(native).__module__].homing

    def run(self, line):
        self.gcode.run_script_from_command(line)

    def confirm(self, origin):
        if origin == 'oled':
            self.menu.panel_move(1)
            command = self.menu.panel_select_command(acknowledged=True)
        else:
            command, = [line.split('|', 1)[1] for line in self.lines
                        if 'Yes / Start|' in line]
        self.run(command)

    def home(self, axes, origin):
        name = 'ALL' if axes == 'XYZ' else axes
        start = len(self.model.calls)
        hands = len(self.handoffs.calls)
        actions = len(self.actions)
        targets = {name: heater.get_temp(0.)[1] for name, heater in self.model.heaters.items()}
        self.lines.clear()
        if origin == 'oled':
            action = 'home_xyz' if axes == 'XYZ' else 'home_' + axes.lower()
            self.menu.open(action)
        else:
            self.run('INVENTURE_HOME_' + name)
        if self.menu.view is not None and self.menu.view.kind == 'confirm':
            require(len(self.model.calls) == start, 'Motion occurred before confirmation')
            self.confirm(origin)
        calls = self.model.calls[start:]
        require(not self.native.running and not self.native.sequence_running,
                'A completed cycle retained a busy Home owner')
        require(self.board.load_test_context is None, 'SERVICE context survived Home')
        require(self.board._control_query()['mode'] == 'NATIVE', 'NATIVE was not restored')
        service = [row for row in self.actions[actions:]
                   if row['kind'] in ('home', 'home_release')]
        require(not service, 'Normal Home invoked another SERVICE Home')
        if 'Z' in axes:
            require(self.native.last_result.get('coordinate_saved'), 'Native Z was not certified')
            require(self.model.position[2] == self.native.park_z, 'Final Z park differs')
            require(len(self.handoffs.calls) == hands + 1, 'Missing or repeated handoff')
            require(self.model.physical_tool == 0, 'T0 selector did not finish before Z reference')
            require(targets == {name: h.get_temp(0.)[1] for name, h in self.model.heaters.items()},
                    'Native heater targets changed after the SERVICE excursion')
            phases = [row[1] for row in calls if row[0] == 'G28']
            expected = ['Z', 'Y', 'X', 'Z'] if axes == 'XYZ' else ['Z', 'Z']
            require(phases == expected, 'Unexpected native Home sequence: ' + repr(phases))
        else:
            require(len(self.handoffs.calls) == hands, 'Cold Home entered SERVICE')
            require(len(self.actions) == actions, 'Cold Home invoked the factory interpreter')
        return dict(axes=axes, origin=origin, native_homes=[row[1] for row in calls if row[0] == 'G28'],
                    handoffs=len(self.handoffs.calls) - hands,
                    z_certified=bool((self.native.last_result or {}).get('coordinate_saved')))


def inspect_refusals(printer, klipper):
    """Run a new installed rig per fault; none of these are hardware tests."""
    from test_orange_pi_load_test import Wire

    def actuations(rig):
        native = sum(row[0] in ('G28', 'edge', 'move') for row in rig.model.calls)
        starts = sum(row[0] in ('motion', 'output', 'control') and row[1] == 1
                     for row in rig.board.device.packets)
        return native, starts

    expected = {
        'tray_absent': ('tray',), 'printing': ('idle',), 'paused': ('idle',),
        'latched': ('blocked native control',), 'disconnected': ('disconnected',),
        'cancel_and_replay': ('token', 'active', 'expired', 'stale', 'no current menu view'),
        'shutdown': ('token', 'active', 'expired', 'stale', 'stopped', 'no current menu view'),
        'bad_handoff': ('handoff',), 'contact_timeout': ('timeout',),
    }
    rows = []
    for condition in ('tray_absent', 'printing', 'paused', 'latched', 'disconnected',
                      'cancel_and_replay', 'bad_handoff', 'contact_timeout', 'shutdown'):
        rig = InstalledRig(printer, klipper)
        if condition in ('contact_timeout', 'shutdown'):
            # Start fault injection only after a complete healthy cycle.
            rig.home('XYZ', 'fluidd')
        initial = actuations(rig)
        rejection = None
        if condition == 'tray_absent':
            rig.model.tray = 1
        elif condition in ('printing', 'paused'):
            rig.model.print_state = condition
        elif condition == 'latched':
            rig.board.control_reply['latched'] = 1
        elif condition == 'disconnected':
            rig.board.connected = False
        elif condition == 'bad_handoff':
            original = rig.board.device.native_handoff
            rig.board.commands['factory_native_handoff'] = Wire(
                lambda args: dict(original(args), token=args[2] + 1))
        elif condition == 'contact_timeout':
            rig.model.timeout_edge = ('PC14', rig.native.coarse_speed, True)
        if condition in ('cancel_and_replay', 'shutdown'):
            rig.lines.clear()
            rig.run('INVENTURE_HOME_ALL')
            accept, = [line.split('|', 1)[1] for line in rig.lines if 'Yes / Start|' in line]
            if condition == 'shutdown':
                rig.native._shutdown()
                rig.menu._shutdown()
                rig.board.connected = False
            else:
                # Default OLED selection is Back: execute its exact command.
                rig.run(rig.menu.panel_select_command(acknowledged=True))
            command = accept
        else:
            command = 'INVENTURE_HOME_ALL ACK=PAD_AND_CLEARANCE_CHECKED'
        try:
            rig.run(command)
        except Exception as exc:
            rejection = str(exc)
        require(rejection, condition + ' was not refused')
        require(any(word in rejection.lower() for word in expected[condition]),
                condition + ' failed for an unexpected reason: ' + rejection)
        if condition in ('bad_handoff', 'contact_timeout'):
            require(not (rig.native.last_result or {}).get('coordinate_saved'),
                    condition + ' published a Z zero')
            require(not rig.native.sequence_running and not rig.native.running,
                    condition + ' retained the Home owner')
            require(not any(h.get_temp(0.)[1] for h in rig.model.heaters.values()),
                    condition + ' left a native heater target active')
            require(rig.board.load_test_context is None, condition + ' retained SERVICE')
            require(not rig.board.device.active(), condition + ' left a simulated output active')
        else:
            require(initial == actuations(rig),
                    condition + ' moved or energized a device before refusal')
        rows.append(dict(condition=condition, status='PASS', rejection=rejection))
    return rows


def inspect_home_to_leveling(printer, klipper):
    rows = []
    for origin in ('fluidd', 'oled'):
        rig = InstalledRig(printer, klipper)
        rig.home('XYZ', origin)
        require(rig.model.shared_enable, 'Completed Home did not retain the physical motor owner')
        rig.run('TURN_OFF_HEATERS')
        rig.board.nozzle_z_dual_abi = 1
        require(rig.board.connected and rig.board.allow_actuators,
                'Leveling fixture lost its connected actuator interface')
        require(rig.board._z_home_mcu_available(),
                'Leveling fixture lost its bounded Z-edge interface: %s' % rig.board.commands.keys())
        require(rig.board._factory_motion_available(0),
                'Leveling fixture lacks the R23 factory motion interface')
        before = len(rig.model.calls)
        if origin == 'oled':
            rig.lines.clear()
            rig.menu.open('leveling')
            require(rig.board.load_test_context is None, 'Leveling moved before confirmation')
            rig.confirm(origin)
        else:
            rig.run('INVENTURE_LEVEL')
        require(rig.board._control_query()['mode'] == 'SERVICE', 'Leveling did not acquire SERVICE')
        require(not rig.model.shared_enable, 'Leveling retained the native physical enable')
        require(rig.board.load_test_context['factory']['operation'] == 'PLATFORM_LEVEL',
                'The installed level command did not reach the factory backend')
        require(any(row[0] == 'motor_off' for row in rig.model.calls[before:]),
                'Leveling did not release the Klipper stepper owner')
        rows.append(dict(origin=origin, status='PASS', service_acquired=True,
                         native_enable_released=True, physical_interfaces='simulated'))
        rig.run('INVENTURE_LEVEL_STOP')
        require(rig.board.platform_level_status()['phase'] == 'cancelled',
                'The installed cancel macro did not end the leveling session')
    return rows


def inspect_cycles(klipper, config, repetitions):
    sys.path.insert(0, str(klipper / 'klippy'))
    import klippy
    import reactor
    from test_orange_pi_native_z import HomingMove, Z_STEPS_PER_MM

    class PhysicalHomingMove(HomingMove):
        def homing_move(self, position, speed, probe_pos, triggered, check_triggered):
            model = self.model
            if self.pin is not model.bottom:
                return super().homing_move(position, speed, probe_pos, triggered, check_triggered)
            # Use the lower reference parsed from this installed config.
            bottom = printer.lookup_object('inventure_native_z').bottom_position
            require(not triggered and position[2] < bottom, 'Invalid PE12 release direction')
            model.edge_calls.append(('PE12', speed, triggered,
                                     round(position[2] * Z_STEPS_PER_MM), check_triggered))
            model.calls.append(('edge', False, 1))
            release = bottom - 1. / Z_STEPS_PER_MM
            model.z += release - model.physical_z
            model.physical_z = release
            return model.get_position()

    loop = reactor.Reactor()
    printer = klippy.Printer(loop, None, {
        'config_file': str(config), 'debuginput': True, 'start_reason': 'startup',
        'software_version': 'offline-r22-full-cycle-acceptance',
    })
    try:
        printer._read_config()
        gcode = printer.lookup_object('gcode')
        gcode.gcode_handlers = gcode.ready_gcode_handlers
        gcode.is_printer_ready = True
        sessions = []
        for hot in (False, True):
            rig = InstalledRig(printer, klipper, hot=hot)
            cycles = []
            with patch.object(rig.home_module, 'HomingMove', PhysicalHomingMove):
                for repetition in range(repetitions):
                    # This includes the reported failure trigger: Home All,
                    # then ordinary XY again, with no host or MCU restart.
                    for axes in ('X', 'Y', 'XY', 'XYZ', 'Z', 'XY'):
                        origin = 'fluidd' if repetition % 2 == 0 else 'oled'
                        cycles.append(rig.home(axes, origin))
            sessions.append(dict(initial_state='HOT_NATIVE' if hot else 'COLD_OFF',
                                 repetitions=repetitions, cycles=cycles,
                                 handoffs=len(rig.handoffs.calls)))
        with patch.object(rig.home_module, 'HomingMove', PhysicalHomingMove):
            refusals = inspect_refusals(printer, klipper)
            home_to_leveling = inspect_home_to_leveling(printer, klipper)
        return dict(status='PASS_INSTALLED_R22_FULL_CYCLES', sessions=sessions,
                    cycle_count=sum(len(s['cycles']) for s in sessions),
                    refusals=refusals, home_to_leveling=home_to_leveling,
                    physical_interfaces='simulated', serial_opened=False,
                    native_mesh_probe_configured=printer.lookup_object('probe', None) is not None,
                    firmware_flashed=False, hardware_tested=False,
                    runtime_sha256={name: sha256((klipper / 'klippy/extras' / name).read_bytes()).hexdigest()
                                    for name in ('inventure_mcu.py', 'inventure_native_z.py',
                                                 'inventure_z_home.py', 'inventure_menu_service.py')})
    finally:
        dispatcher = printer.lookup_object('aio_executor', None)
        if dispatcher is not None:
            dispatcher._handle_disconnect()
        loop.finalize()


def check(repetitions):
    from inventure_bootstrap import prepare_host
    from inventure_bootstrap_config import stage_config
    with tempfile.TemporaryDirectory(prefix='inventure-r22-cycles-') as directory:
        root = Path(directory)
        host = prepare_host(root / 'host', repo=ROOT)
        source = root / 'original/printer.cfg'
        source.parent.mkdir()
        source.write_text('[mcu]\nserial: /dev/ttyS5\nbaud: 750000\n'
                          '[printer]\nkinematics: none\nmax_velocity: 30\nmax_accel: 200\n'
                          '[inventure_mcu]\nallow_actuators: False\n')
        (root / 'gcodes').mkdir()
        config = stage_config(host, root / 'config', source, serial='/dev/ttyS5',
                              baud=750000, gcodes_path=root / 'gcodes',
                              restore_field_profile=True)
        before = {p: p.read_bytes() for p in (root / 'config').rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, __file__, '--klipper', str(host),
                                 '--config', config['config_path'], '--repetitions', str(repetitions)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + '\n' + result.stderr)
        require(before == {p: p.read_bytes() for p in (root / 'config').rglob('*') if p.is_file()},
                'Acceptance test changed staged configuration or calibration state')
        return dict(json.loads(result.stdout), config_unchanged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 100:
        parser.error('--repetitions must be 3..100')
    if bool(args.klipper) != bool(args.config):
        parser.error('--klipper and --config must be supplied together')
    try:
        with redirect_stdout(sys.stderr):
            report = (inspect_cycles(args.klipper.resolve(), args.config.resolve(), args.repetitions)
                      if args.klipper else check(args.repetitions))
    except Exception as exc:
        report = dict(status='FAIL_INSTALLED_R22_FULL_CYCLES', error=str(exc), hardware_tested=False)
        if args.klipper:
            import traceback
            traceback.print_exc(file=sys.stderr)
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end='')
    return int(report['status'] != 'PASS_INSTALLED_R22_FULL_CYCLES')


if __name__ == '__main__':
    raise SystemExit(main())
