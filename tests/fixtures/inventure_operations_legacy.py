# SPDX-License-Identifier: GPL-3.0-or-later
"""P02 global ownership, cancellation, and physical reference supervision.

Pinned Klippy API: ee2c6fb6b00b2b5d097ef70d0868df7d74943b51.
No selector, feeder sequence, homing geometry, or print-cycle policy is invented.
Future procedure backends must hold a lease and execute in its greenlet scope.
"""
from contextlib import contextmanager
import json
from greenlet import getcurrent
from .inventure_operations_core import OperationState, OperationError

MOTION_COMMANDS = {'G0', 'G1', 'G2', 'G3', 'G28', 'RESTORE_GCODE_STATE',
                   'G92',
                   'SET_GCODE_OFFSET', 'ACTIVATE_EXTRUDER',
                   'SYNC_EXTRUDER_MOTION', 'SET_EXTRUDER_ROTATION_DISTANCE',
                   'SET_STEPPER_ENABLE', 'INVENTURE_FEEDER'}
FORBIDDEN_REFERENCE_COMMANDS = {'SET_KINEMATIC_POSITION', 'FORCE_MOVE',
                                'STEPPER_BUZZ', 'MANUAL_STEPPER'}
CAL_EDIT = {'INVENTURE_CAL_SEED', 'INVENTURE_CAL_XY_LINE',
            'INVENTURE_CAL_Z_START', 'INVENTURE_CAL_ADVANCE',
            'INVENTURE_CAL_COMMIT'}


class InventureOperations:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.board = self.printer.load_object(config, 'inventure_mcu')
        self.state = OperationState()
        self.contexts = {}
        self.th = self.vsd = self.pause = None
        self.clear_kinematics = None
        self.installed = False
        self.touched = set()
        self.cancel_in_progress = False
        self.watchdog = self.reactor.register_timer(self._watchdog)
        for name, fn in [('INVENTURE_OPERATION_BEGIN', self.cmd_begin),
                         ('INVENTURE_OPERATION_EXECUTE', self.cmd_execute),
                         ('INVENTURE_OPERATION_END', self.cmd_end),
                         ('INVENTURE_OPERATION_CANCEL', self.cmd_cancel),
                         ('INVENTURE_OPERATION_STOP', self.cmd_stop)]:
            self.gcode.register_command(name, fn)
        self.gcode.register_command('INVENTURE_OPERATION_STATUS', self.cmd_status,
                                    when_not_ready=True)
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        self.printer.register_event_handler('klippy:disconnect', self._disconnect)
        self.printer.register_event_handler('gcode:request_restart', self._restart)
        self.printer.register_event_handler('stepper_enable:motor_off', self._motor_off)
        self.printer.register_event_handler('inventure:calibration_changed', self._cal_changed)
        self.printer.register_event_handler('inventure:calibration_status', self._cal_status)
        self.printer.register_event_handler('inventure:power_hold', self._power_hold)

    def _core(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except OperationError as exc:
            raise self.printer.command_error(str(exc))

    def current_lease(self):
        stack = self.contexts.get(getcurrent(), ())
        if not stack:
            return None
        lease = stack[-1]
        self._core(self.state.check, lease)
        return lease

    @contextmanager
    def scope(self, lease):
        self._core(self.state.check, lease)
        greenlet = getcurrent()
        stack = self.contexts.setdefault(greenlet, [])
        stack.append(lease)
        try:
            yield lease
        finally:
            stack.pop()
            if not stack:
                self.contexts.pop(greenlet, None)

    def begin(self, kind):
        if self.state.owner is not None or self.state.fatal is not None:
            self._core(self.state.begin, kind)  # report exclusivity before preflight
        fans=self.printer.lookup_object('inventure_fans',None)
        if fans is not None:fans.before_operation(kind)
        if kind == 'print':
            self._require_print_reference()
            self.require_motion()
            thermal = self.printer.lookup_object('inventure_thermal', None)
            if thermal is not None: thermal.before_print()
        lease = self._core(self.state.begin, kind)
        if kind == 'selector':
            self.state.selector_unknown('selector_started')
        return lease

    def require_motion(self):
        io = self.printer.lookup_object('inventure_io', None)
        if io is None:
            raise self.printer.command_error('Inventure motion requires the full IO profile')
        io.require_motion()

    @contextmanager
    def procedure(self, kind, parent_kinds=()):
        parent = self.current_lease()
        if parent is not None:
            if parent.kind != kind and parent.kind not in parent_kinds:
                raise self.printer.command_error('Inventure procedure conflicts with ' + parent.kind)
            yield parent
            self._core(self.state.check, parent)
            return
        lease = self.begin(kind)
        try:
            with self.scope(lease):
                yield lease
        except Exception:
            if self.state.owner == lease and self.state.fatal is None:
                if lease.epoch in self.touched:
                    self.invalidate('xyz', kind + '_failed')
                self.end(lease, 'failed')
            raise
        else:
            self.end(lease)

    def end(self, lease, result='completed'):
        fans=self.printer.lookup_object('inventure_fans',None)
        if lease.kind=='print' and fans is not None:fans.prepare_end(result)
        epoch = self._core(self.state.start_drain, lease, result)
        try:
            self._stop_feeders()
            # A drain executes all accepted planned moves. It never purges them.
            self.th.wait_moves()
        except Exception:
            if self.state.epoch == epoch and self.state.fatal is None:
                self.fatal('Operation completion could not drain safely', 'DRAIN_FAILED')
            raise
        self._core(self.state.finish_drain, epoch, result)
        self.touched.discard(lease.epoch)
        if lease.kind == 'print':
            thermal = self.printer.lookup_object('inventure_thermal', None)
            if thermal is not None: thermal.ended()
            if fans is not None:fans.ended(result)

    def _stop_feeders(self):
        # Feeder duration timers are independent of toolhead.wait_moves().
        # ABI3 already defines duration=0 as STOP. Wait for the protocol ACK
        # before releasing ownership; no new MCU command/ABI is required.
        command = self.board.commands.get('feeder')
        if command is not None:
            for channel in (0, 1):
                command.send_wait_ack([channel, 0, 0])

    def invalidate(self, axes, reason):
        self.state.invalidate(axes, reason)
        if self.th is not None:
            self.clear_kinematics(axes)
        self.printer.send_event('inventure:reference_invalidated', reason)

    def require_position(self, axes='xyz'):
        if self.th is not None:
            actual = self.th.get_kinematics().get_status(
                self.reactor.monotonic()).get('homed_axes', '')
            lost = ''.join(a for a in axes if self.state.axes[a]['valid'] and a not in actual)
            if lost:
                self.invalidate(lost, 'kinematics_reference_lost')
        self._core(self.state.require_position, axes)

    def _require_print_reference(self):
        self.require_position()
        motion = self.printer.lookup_object('inventure_motion', None)
        if motion is not None: motion.require_full_reference()
        status = self.get_status(self.reactor.monotonic())
        frame = status['toolframe']
        cal = self.printer.lookup_object('inventure_calibration', None)
        if (not frame['valid'] or status['logical_extruder_tool'] != frame['tool']
                or status['selector']['tool'] != frame['tool']
                or cal is None or frame['calibration_revision'] != cal.store.revision):
            raise self.printer.command_error(
                'Inventure print needs matching logical tool, confirmed selector and valid toolframe')
        tools = self.printer.lookup_object('inventure_tools', None)
        if tools is not None: tools.require_binding()

    def _ready(self):
        self.reactor.update_timer(self.watchdog, self.reactor.monotonic() + .1)

    def _shutdown(self):
        self.state.fail(self.printer.get_state_message()[0])
        if self.th is not None:
            self.clear_kinematics('xyz')
        self.reactor.update_timer(self.watchdog, self.reactor.NEVER)

    def _disconnect(self):
        self.state.fail('Host/MCU disconnected', 'CONNECTION_LOST')
        if self.th is not None:
            self.clear_kinematics('xyz')

    def _restart(self, *args):
        self.state.fail('Host restart requested', 'HOST_RESTART')
        if self.th is not None:
            self.clear_kinematics('xyz')

    def fatal(self, reason, code='PROCEDURE_FAULT'):
        self.state.fail(reason, code)
        self.invalidate('xyz', reason)
        self.printer.invoke_shutdown('Inventure: ' + reason)

    def _watchdog(self, now):
        if self.state.fatal is not None:
            return self.reactor.NEVER
        # A reservation without any physical action does not pretend MCU arm.
        if self.touched or any(a['valid'] for a in self.state.axes.values()):
            b = self.board.get_status(now)
            if (not b.get('connected') or not b.get('armed') or b.get('fault')
                    or b.get('status_age', 99.) > .75):
                self.fatal('MCU lost, disarmed, faulted or stale', 'MCU_STATUS_LOST')
                return self.reactor.NEVER
        return now + .1

    def _motor_off(self, *args):
        self.invalidate('xyz', 'motor_off_shared_PE10')
        if self.state.owner is not None and self.state.mode == 'running':
            self.fatal('Motors disabled during an operation', 'MOTOR_DISABLED')

    def _cal_changed(self, *args):
        self.invalidate('xyz', 'calibration_changed')

    def _cal_status(self, status):
        lease = self.state.owner
        if lease is not None and lease.kind == 'calibration' and status['state'] in ('failed', 'aborted'):
            if not self.cancel_in_progress and self.state.fatal is None:
                if status['state'] == 'failed':
                    self.invalidate('xyz', 'calibration_failed')
                    self.end(lease, 'failed')
                else:
                    self.cancel('Calibration aborted')

    def _power_hold(self):
        if self.state.fatal is None:
            self.cancel('Panel POWER hold')

    def cancel(self, reason):
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.running:
            filament.abort(reason);return
        alignment=self.printer.lookup_object('inventure_alignment',None)
        if alignment is not None and alignment.running:
            alignment.abort(reason);return
        leveling=self.printer.lookup_object('inventure_leveling',None)
        if leveling is not None and leveling.running:
            leveling.abort(reason);return
        if self.cancel_in_progress:
            return
        lease = self.state.owner
        if lease is not None and lease.kind=='rfid_write':
            self.fatal('P12 cancel during RFID write; outcome unknown','RFID_WRITE_CANCEL');return
        if lease is not None and lease.kind=='material_config':
            self.fatal('P11 cancel during material write; durable outcome unknown','MATERIAL_WRITE_CANCEL');return
        epoch = self._core(self.state.request_cancel, reason)
        if epoch is None:
            return
        self.cancel_in_progress = True
        try:
            cal = self.printer.lookup_object('inventure_calibration', None)
            contact = self.printer.lookup_object('inventure_contact', None)
            tools = self.printer.lookup_object('inventure_tools', None)
            tc = self.printer.lookup_object('inventure_toolchange',None)
            if tc is not None and tc.running:
                self.fatal('Cancellation during P08 toolchange','TOOLCHANGE_CANCEL');return
            if tools is not None and tools.running:
                self.fatal('Cancellation during mechanical tool selection', 'SELECTOR_CANCEL')
                return
            if contact is not None and contact.running:
                self.fatal('Cancellation during contact movement', 'CONTACT_CANCEL')
                return
            self._stop_feeders()
            if lease.kind == 'calibration' and cal is not None:
                cal._abort(reason)
            if lease.kind == 'print' and self.vsd is not None:
                self._original_sd_cancel()
            if lease.kind in ('selector', 'calibration', 'homing'):
                self.state.selector_unknown(reason)
                self.invalidate('xyz', 'cancelled_' + lease.kind)
            self.th.wait_moves()
            self._core(self.state.finish_drain, epoch, 'cancelled', reason)
            self.printer.send_event('inventure:operation_cancelled',lease,reason)
            self.touched.discard(lease.epoch)
            if self.pause is not None and lease.kind == 'print':
                self.pause.is_paused = self.pause.pause_command_sent = self.pause.sd_paused = False
                thermal = self.printer.lookup_object('inventure_thermal', None)
                if thermal is not None: thermal.ended()
                fans=self.printer.lookup_object('inventure_fans',None)
                if fans is not None:fans.ended('cancelled')
        except Exception:
            if self.state.fatal is None:
                self.fatal('Cancellation could not drain safely', 'CANCEL_FAILED')
            raise
        finally:
            self.cancel_in_progress = False

    def _connect(self):
        self.th = self.printer.lookup_object('toolhead')
        self.vsd = self.printer.lookup_object('virtual_sdcard', None)
        self.pause = self.printer.lookup_object('pause_resume', None)
        kin = self.th.get_kinematics()
        self.clear_kinematics = kin.clear_homing_state
        def clear_reference(axes):
            self.invalidate(axes, 'kinematic_reference_cleared')
            if self.state.owner is not None and self.state.mode == 'running':
                self.fatal('Kinematic reference cleared during an operation')
        kin.clear_homing_state = clear_reference
        original_move, original_home = self.th.move, kin.home
        original_set_position = self.th.set_position

        def move(position, speed):
            before = self.th.get_position()
            if list(position) == list(before):
                return original_move(position, speed)
            self.require_motion()
            lease = self.current_lease()
            if self.state.owner is not None and lease is None:
                raise self.printer.command_error('Inventure motion belongs to the active procedure')
            alignment=self.printer.lookup_object('inventure_alignment',None)
            if alignment is not None and alignment.running and not alignment.in_scope():
                raise self.printer.command_error('P10 alignment owns the motion context')
            leveling=self.printer.lookup_object('inventure_leveling',None)
            if leveling is not None and leveling.running and not leveling.in_scope():
                raise self.printer.command_error('P09 leveling owns the motion context')
            tc=self.printer.lookup_object('inventure_toolchange',None)
            if tc is not None and tc.running and not tc.in_scope():
                raise self.printer.command_error('P08 toolchange owns the motion context')
            if lease is not None and lease.kind in ('material_config','rfid_write'):
                raise self.printer.command_error('P11/P12 configuration/write is stationary')
            if lease is not None and lease.kind=='fan_test':
                raise self.printer.command_error('P06 Fan Test is stationary; movement is refused')
            if lease is None or lease.kind != 'homing':
                axes = ''.join(a for i, a in enumerate('xyz') if position[i] != before[i])
                self.require_position(axes)
                if position[3] != before[3]:
                    if lease is None or lease.kind not in ('print', 'load', 'unload', 'calibration','toolchange','alignment'):
                        raise self.printer.command_error('Inventure extrusion requires an owned print/load/unload/calibration')
                    tools = self.printer.lookup_object('inventure_tools', None)
                    if tools is not None: tools.check_extrusion()
                if lease is not None and lease.kind == 'print':
                    tools = self.printer.lookup_object('inventure_tools', None)
                    if tools is None or not tools.in_selector_scope(): self._require_print_reference()
            if lease is not None:
                self.touched.add(lease.epoch)
            result = original_move(position, speed)
            # The planner can yield while flushing. A cancelled old caller may
            # not update the bookkeeping of a newer procedure after resuming.
            if lease is not None: self._core(self.state.check, lease)
            motion = self.printer.lookup_object('inventure_motion', None)
            if motion is not None: motion.note_accepted_move(before, self.th.get_position())
            return result

        def home(homing_state):
            self.require_motion()
            axes = ''.join('xyz'[a] for a in homing_state.get_axes())
            with self.procedure('homing') as lease:
                motion = self.printer.lookup_object('inventure_motion', None)
                if motion is not None:
                    return motion.home(homing_state, lease)
                self.invalidate(axes, 'homing_in_progress')
                result = original_home(homing_state)
                self.th.wait_moves()
                self._core(self.state.check, lease)
                actual = kin.get_status(self.reactor.monotonic()).get('homed_axes', '')
                if not all(a in actual for a in axes):
                    raise self.printer.command_error('Inventure homing did not establish every requested axis')
                self.state.homed(axes)
                return result

        def set_position(position, homing_axes=()):
            lease = self.current_lease()
            if homing_axes and (lease is None or lease.kind != 'homing'):
                raise self.printer.command_error('Inventure only completed homing may establish XYZ reference')
            if lease is None:
                self.invalidate('xyz', 'unowned_position_assignment')
                raise self.printer.command_error('Inventure unowned physical position assignment refused')
            return original_set_position(position, homing_axes=homing_axes)

        self.th.move, kin.home, self.th.set_position = move, home, set_position
        enable = self.printer.lookup_object('stepper_enable', None)
        if enable is not None:
            def driver_state(print_time, value):
                if not value and any(a['valid'] for a in self.state.axes.values()):
                    self._motor_off()
            for name in enable.get_steppers():
                enable.lookup_enable(name).register_state_callback(driver_state)
            original_enable = enable.set_motors_enable
            def set_enable(names, value):
                if not value:
                    self.invalidate('xyz', 'individual_motor_disable_shared_PE10')
                    if self.state.owner is not None and self.state.mode == 'running':
                        self.fatal('Motor disable requested during an operation', 'MOTOR_DISABLED')
                elif self.state.owner is not None and self.current_lease() is None:
                    raise self.printer.command_error('Inventure enable belongs to the active procedure')
                return original_enable(names, value)
            enable.set_motors_enable = set_enable
        if self.vsd is not None:
            self._install_sd()
        # Wrapping after config loading also covers registered macro aliases,
        # mux commands and API endpoints that dispatch through G-code.
        for name, handler in list(self.gcode.ready_gcode_handlers.items()):
            def guarded(gcmd, name=name, handler=handler):
                parsed = (gcmd if self.gcode.is_traditional_gcode(name) else
                          self.gcode._get_extended_params(gcmd))
                return self._dispatch(name, handler, gcmd, parsed)
            self.gcode.ready_gcode_handlers[name] = guarded
            if self.gcode.base_gcode_handlers.get(name) is handler:
                self.gcode.base_gcode_handlers[name] = guarded
        self.installed = True

    def _install_sd(self):
        sd = self.vsd
        original_resume, original_pause = sd.do_resume, sd.do_pause
        original_work = sd.work_handler
        self._original_sd_cancel = sd.do_cancel
        def resume():
            thermal = self.printer.lookup_object('inventure_thermal', None)
            if thermal is not None and self.state.owner is not None:
                thermal.before_resume()
            lease = self.state.owner
            if lease is None:
                lease = self.begin('print')
            else:
                if lease.kind != 'print':
                    raise self.printer.command_error('Inventure busy: ' + lease.kind)
                self._require_print_reference()
                self.require_motion()
                if self.state.mode == 'paused':
                    self._core(self.state.resume, lease)
                self._core(self.state.check, lease)
            try:
                return original_resume()
            except Exception:
                if self.state.owner == lease and self.state.mode == 'running':
                    self.end(lease, 'failed')
                raise
        def pause():
            lease = self.state.owner
            if lease is not None and lease.kind == 'print' and self.state.mode in ('running', 'paused'):
                self._core(self.state.pause, lease, 'source')
            result = original_pause()
            self.th.wait_moves()
            return result
        def cancel():
            if self.state.owner is not None and self.state.owner.kind == 'print':
                return self.cancel('SD print cancelled')
            return self._original_sd_cancel()
        def work(eventtime):
            lease = self.state.owner
            if lease is None or lease.kind != 'print':
                self.fatal('SD worker has no print lease', 'UNOWNED_SD_WORKER')
                return self.reactor.NEVER
            try:
                with self.scope(lease):
                    return original_work(eventtime)
            finally:
                if self.state.owner == lease and self.state.mode != 'cancelling' and self.state.fatal is None:
                    state = sd.print_stats.get_status(self.reactor.monotonic())['state']
                    if state == 'complete':
                        self.end(lease)
                    elif state == 'error':
                        self.invalidate('xyz', 'print_error')
                        self.end(lease, 'failed')
                    elif state == 'paused':
                        self._core(self.state.pause, lease, 'source')
        sd.do_resume, sd.do_pause, sd.do_cancel, sd.work_handler = resume, pause, cancel, work

    def _dispatch(self, name, handler, original, parsed):
        lease = self.state.owner
        stack = self.contexts.get(getcurrent(), ())
        if stack and name not in ('M112', 'STATUS', 'INVENTURE_OPERATION_STATUS', 'TURN_OFF_HEATERS'):
            self._core(self.state.check, stack[-1], ('running', 'paused', 'draining'))
        if name in FORBIDDEN_REFERENCE_COMMANDS:
            raise parsed.error('Inventure forbids synthetic XYZ reference and untracked motion')
        if name == 'G92' and (not parsed.get_command_parameters() or any(a in parsed.get_command_parameters() for a in 'XYZ')):
            raise parsed.error('P04 untracked G92 XYZ origin changes are forbidden; E-only reset is supported')
        if name == 'SET_EXTRUDER_ROTATION_DISTANCE' and self.printer.lookup_object('inventure_motion', None) is not None:
            raise parsed.error('P03 audited effective step scales cannot change at runtime')
        if name in MOTION_COMMANDS and lease is not None and name != 'SET_STEPPER_ENABLE':
            if self.current_lease() is None:
                raise parsed.error('Inventure command conflicts with active ' + lease.kind)
        if name == 'SYNC_EXTRUDER_MOTION':
            context = self.current_lease()
            if context is None or context.kind not in ('selector', 'calibration'):
                raise parsed.error('Shared E synchronization requires owned selector/calibration')
            if self.printer.lookup_object('inventure_tools', None) is not None:
                raise parsed.error('P04 shared E binding belongs to completed selector/toolframe backend')
        if name in ('M23', 'SDCARD_PRINT_FILE'):
            if lease is not None:
                raise parsed.error('Inventure busy: ' + lease.kind)
            if name == 'SDCARD_PRINT_FILE':
                self._require_print_reference()
        if name == 'M24' and self.pause is not None and self.pause.is_paused:
            raise parsed.error('Use RESUME; M24 cannot bypass paused reference checks')
        if name in ('SDCARD_RESET_FILE', 'CANCEL_PRINT'):
            if lease is not None:
                if lease.kind != 'print':
                    raise parsed.error('Inventure command conflicts with ' + lease.kind)
                self.cancel('Operator cancelled print')
            return handler(original)
        if name == 'CLEAR_PAUSE' and lease is not None:
            raise parsed.error('Inventure paused ownership must end through RESUME or CANCEL_PRINT')
        if name in ('PAUSE', 'M25'):
            if lease is None or lease.kind != 'print':
                raise parsed.error('Inventure no owned print to pause')
            self._core(self.state.pause, lease, 'operator')
            self.th.wait_moves()
            return self.pause.cmd_PAUSE(parsed)
        if name == 'RESUME':
            if lease is None or lease.kind != 'print':
                raise parsed.error('Inventure no owned print to resume')
            self._require_print_reference()  # before RESTORE_GCODE_STATE MOVE=1
            self.require_motion()
            thermal = self.printer.lookup_object('inventure_thermal', None)
            if thermal is not None: thermal.before_resume()
            self._core(self.state.resume, lease)
            try:
                with self.scope(lease):
                    result = handler(original)
                if thermal is not None: thermal.resumed()
                return result
            except Exception:
                if self.state.owner == lease and self.state.fatal is None:
                    self._core(self.state.pause, lease, 'operator')
                raise
        if name in CAL_EDIT:
            created = lease is None
            if created:
                lease = self.begin('calibration')
            elif lease.kind != 'calibration':
                raise parsed.error('Inventure calibration conflicts with ' + lease.kind)
            try:
                with self.scope(lease):
                    if name in ('INVENTURE_CAL_Z_START', 'INVENTURE_CAL_ADVANCE'):
                        self.require_position()
                    result = handler(original)
                    self._core(self.state.check, lease)
                if name == 'INVENTURE_CAL_COMMIT':
                    self.end(lease)
                return result
            except Exception:
                if self.state.owner == lease and self.state.fatal is None and created:
                    self.end(lease, 'failed')
                raise
        if name == 'INVENTURE_CAL_ABORT':
            if lease is not None:
                if lease.kind != 'calibration':
                    raise parsed.error('Inventure calibration abort conflicts with ' + lease.kind)
                self.cancel('Operator cancelled calibration')
                return parsed.respond_info('Calibration cancelled; rehome required; no recovery move')
        if name == 'INVENTURE_CONTACT_PROBE':
            with self.procedure('calibration'):
                self.require_position()
                return handler(original)
        if name in ('INVENTURE_TOOLFRAME_SYNC', 'INVENTURE_TOOLFRAME_AFTER_SELECT'):
            tools = self.printer.lookup_object('inventure_tools', None)
            internal = tools is not None and tools.in_selector_scope()
            if name == 'INVENTURE_TOOLFRAME_AFTER_SELECT' and tools is not None and tools.enabled and not internal:
                raise parsed.error('P04 AFTER_SELECT requires completed internal mechanical selector')
            parents = ('calibration','print','toolchange','alignment','load','unload') if internal else ('calibration',)
            if name == 'INVENTURE_TOOLFRAME_SYNC':
                target = parsed.get_int('TOOL',0,minval=0,maxval=1)
                if self.state.selector_tool != target:
                    raise parsed.error('Base toolframe must match the confirmed physical tool; confirm and home first')
            with self.procedure('selector', parent_kinds=parents):
                result = handler(original)
                self.current_lease() # reject a cancelled caller after toolframe wait_moves
                frame = self.printer.lookup_object('inventure_toolframe')
                evidence = 'qualified_open_loop_selector_trajectory' if internal else 'qualified_toolframe_operator_confirmation'
                self.state.confirm_selector(frame.active, evidence)
                self._core(self.state.confirm_frame, frame.active, frame.revision)
                if tools is not None: tools.bind_frame()
                return result
        if name == 'INVENTURE_FEEDER':
            context = self.current_lease()
            if context is None or context.kind not in ('load', 'unload', 'calibration'):
                raise parsed.error('Inventure feeder requires owned load/unload/calibration')
            if parsed.get_int('DURATION_MS', 0):
                self.require_motion()
                self.touched.add(context.epoch)
        if name == 'SET_GCODE_OFFSET' and self.current_lease() is None:
            result = handler(original)
            self.invalidate('xyz', 'external_gcode_offset_change')
            return result
        return handler(original)

    def cmd_begin(self, gcmd):
        kind = gcmd.get('KIND').lower()
        if kind not in ('print', 'load', 'unload', 'selector', 'calibration'):
            raise gcmd.error('Use KIND=print/load/unload/selector/calibration; homing uses G28')
        lease = self.begin(kind)
        gcmd.respond_info('INVENTURE_LEASE TOKEN=%d KIND=%s' % (lease.epoch, lease.kind))

    def _gcmd_lease(self, gcmd):
        lease = self.state.owner
        if lease is None or gcmd.get_int('TOKEN') != lease.epoch:
            raise gcmd.error('Inventure stale or foreign operation lease')
        self._core(self.state.check, lease)
        return lease

    def cmd_execute(self, gcmd):
        lease = self._gcmd_lease(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.running:raise gcmd.error('Use dedicated P13 filament commands')
        if lease.kind in ('leveling','alignment'):raise gcmd.error('Use dedicated P09/P10 wizard commands')
        if lease.kind in ('material_config','rfid_write'):raise gcmd.error('Use dedicated P11/P12 material/RFID commands')
        script = gcmd.get('GCODE')
        if '\n' in script or '\r' in script:
            raise gcmd.error('Execute accepts exactly one G-code command')
        with self.scope(lease):
            self.gcode.run_script_from_command(script)
            self._core(self.state.check, lease)

    def cmd_end(self, gcmd):
        lease = self._gcmd_lease(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.running:raise gcmd.error('Use dedicated P13 filament commands')
        if lease.kind in ('leveling','alignment'):raise gcmd.error('Use dedicated P09/P10 wizard commands')
        if lease.kind in ('material_config','rfid_write'):raise gcmd.error('Use dedicated P11/P12 material/RFID commands')
        if lease.kind == 'print' and self.vsd is not None and self.vsd.current_file is not None:
            raise gcmd.error('SD job must finish or be cancelled by its source')
        cal = self.printer.lookup_object('inventure_calibration', None)
        if lease.kind == 'calibration' and cal is not None and (cal._busy() or cal.pending is not None):
            raise gcmd.error('Calibration must commit or abort its active/candidate result')
        if lease.kind == 'selector' and not self.state.frame_valid:
            raise gcmd.error('Selector completion requires confirmed toolframe')
        self.end(lease)

    def cmd_cancel(self, gcmd):
        self.cancel('Operator cancelled operation')

    def cmd_stop(self, gcmd):
        self.fatal('Operator immediate stop', 'OPERATOR_STOP')

    def get_status(self, eventtime):
        result = self.state.status()
        logical = self.th.get_extruder().get_name() if self.th is not None else None
        result.update(logical_extruder=logical,
                      logical_extruder_tool={'extruder': 0, 'extruder1': 1}.get(logical),
                      planned_xyze=list(self.th.get_position()) if self.th is not None else None,
                      planned_position_is_physical_feedback=False,
                      position_persisted=False, guards_installed=self.installed,
                      active_scopes=len(self.contexts))
        gm = self.printer.lookup_object('gcode_move', None)
        if gm is not None:
            coordinates = gm.get_status(eventtime)
            result['logical_gcode_xyze'] = list(coordinates['gcode_position'])
            result['gcode_homing_origin'] = list(coordinates['homing_origin'])
        motion = self.printer.lookup_object('inventure_motion', None)
        if motion is not None:
            result['reference_quality'] = dict(motion.quality)
            result['xyz_print_reference_valid'] = all(
                motion.quality[a] == 1 and self.state.axes[a]['valid'] for a in 'xyz')
        tools = self.printer.lookup_object('inventure_tools', None)
        if tools is not None: result['tools'] = tools.get_status(eventtime)
        return result

    def cmd_status(self, gcmd):
        gcmd.respond_info('INVENTURE_OPERATIONS ' + json.dumps(
            self.get_status(self.reactor.monotonic()), sort_keys=True))


def load_config(config):
    return InventureOperations(config)
