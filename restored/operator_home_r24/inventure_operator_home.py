# SPDX-License-Identifier: GPL-3.0-or-later
"""OEM XY release and conditional operator preparation around frozen R38.6 Z.

The PC14 measurement, PE12 bottom reference and T0 factory motion modules
are inherited unchanged. R24 adds a drained, held NATIVE -> SERVICE entry.
"""
import json
import math

from . import homing
from .inventure_factory_core import FactoryError, XY_RELEASE_MM, XY_RELEASE_SPEED
from .inventure_z_home import OEM_Z_BOTTOM_SPEED

RELEASE = 'RC8-HOME-XY-RELEASE-Z-REUSE-R24'
PROFILE = 'operator-home-r24'
ENTRY_COMMAND = 'inventure_native_held_service owner=%u'
ENTRY_RESPONSE = 'inventure_native_held_service_state owner=%u mode=%c enabled=%c reject=%c'
XY_STEPS_PER_MM = 640.
XY_SEEK_MM = 160.


class OperatorHome:
    def __init__(self, config):
        self.xy_references = {}
        self.xy_clearance_position = None
        self.held_service = None
        self.requested_home_axes = None
        self._g28_installed = False
        super().__init__(config)

    def _install_g28(self):
        if self._g28_installed:return
        gcode=self.printer.lookup_object('gcode')
        gcode.register_command('G28',None)
        gcode.register_command('G28',self.cmd_G28,desc='Inventure OEM release homing; Z/XYZ share operator confirmation')
        self._g28_installed=True

    def cmd_G28(self,gcmd):
        params=dict(gcmd.get_command_parameters())
        params.pop('G',None)
        if params.keys()-{'X','Y','Z'}:
            raise gcmd.error('G28 accepts only X/Y/Z axis selectors')
        axes=''.join(axis for axis in 'XYZ' if axis in params) or 'XYZ'
        command=self.printer.lookup_object('gcode').create_gcode_command(
            'INVENTURE_HOME_REQUEST','INVENTURE_HOME_REQUEST AXES='+axes,dict(AXES=axes))
        self._run_home_axes(command,operator=True)

    def _identify(self):
        super()._identify()
        if int(self.board.mcu.get_constants().get('INVENTURE_NATIVE_HELD_SERVICE_ABI', 0)) != 1:
            raise self.printer.config_error('Operator Home R24 requires the matching R24 MCU held-service handoff')

    def _build_config(self):
        super()._build_config()
        if int(self.board.mcu.get_constants().get('INVENTURE_NATIVE_HELD_SERVICE_ABI', 0)) == 1:
            self.held_service = self.board.mcu.lookup_query_command(ENTRY_COMMAND, ENTRY_RESPONSE)

    def _connect(self):
        super()._connect()
        enables = self.printer.lookup_object('stepper_enable')
        for rail in self.kin.rails[:2]:
            for stepper in rail.get_steppers():
                enables.lookup_enable(stepper.get_name()).register_state_callback(self._xy_enable_changed)
        self._install_g28()

    def _xy_enable_changed(self, print_time, enabled):
        if not enabled:
            self._lost_enable()

    def _lost_enable(self):
        self.xy_references.clear()
        self.xy_clearance_position = None
        if self.kin is not None:
            self._invalidate_native_homing()
        super()._lost_enable()

    def _invalidate_native_homing(self):
        self.xy_references.clear()
        self.xy_clearance_position = None
        super()._invalidate_native_homing()

    def _home_rails_begin(self, state, rails):
        super()._home_rails_begin(state, rails)
        if self.kin is not None:
            for axis, rail in zip('XY', self.kin.rails[:2]):
                if rail in rails:
                    self.xy_references.pop(axis, None)

    def get_status(self, eventtime):
        result = super().get_status(eventtime)
        result.update(homing_safety_release=RELEASE, procedure_restore_release=RELEASE,
                      home_procedure_profile=PROFILE, operator_home_module=__file__,
                      g28_dispatch='shared_operator_home_r24' if self._g28_installed else 'pending_connect',
                      xy_references={axis:dict(value) for axis,value in self.xy_references.items()})
        return result

    def _xy_valid(self, axis):
        state = self.board._control_query()
        return (axis in self.xy_references
                and axis.lower() in self.kin.get_status(self.reactor.monotonic())['homed_axes']
                and state['mode'] == 'NATIVE' and state['armed']
                and not state['fault'] and not state['latched'])

    def _remember_clearance(self):
        self.xy_clearance_position = tuple(self.toolhead.get_position()[:3])

    def _operator_clearance(self, clearance):
        """The existing bounded 10 mm pre-XY retreat, not a second full Z home."""
        self.toolhead.wait_moves()
        self._require_native()
        raw = self.board._z_home_inputs({})
        position = self.toolhead.get_position()
        if (raw['nozzle_contact'] == 1
                and (raw['z_bottom'] or self.xy_clearance_position == tuple(position[:3]))):
            self._remember_clearance()
            return
        known = ('z' in self.kin.get_status(self.reactor.monotonic())['homed_axes']
                 and self.last_result is not None and self.last_result.get('reference_established'))
        if known:
            if position[2] < clearance:
                self._move_to([None, None, clearance], self.park_speed)
        else:
            # Temporary relative Z is discarded even when a bounded retreat
            # reaches PE12. The full bottom reference stays over the pad.
            position[2] = 0.
            self.toolhead.set_position(position, homing_axes='z')
            try:
                target = list(position)
                target[2] = min(clearance, 10.)
                move = homing.HomingMove(self.printer, [(self.bottom, 'inventure_initial_clearance')], self.toolhead)
                move.homing_move(target, min(OEM_Z_BOTTOM_SPEED, self.kin.max_z_velocity), probe_pos=True,
                                 triggered=True, check_triggered=False)
                self.toolhead.wait_moves()
            finally:
                self._discard_z_position()
        self._require_native()
        if self.board._z_home_inputs({})['nozzle_contact'] != 1:
            raise FactoryError('PC14 must be open after the bounded initial retreat; XY was not started')
        self._remember_clearance()

    def _home_limit(self, axis, factor=1.):
        if axis not in 'XY':
            return super()._home_limit(axis, factor)
        self._require_native()
        index = 'XYZ'.index(axis)
        rail = self.kin.rails[index]
        info = rail.get_homing_info()
        if info.positive_dir:
            raise FactoryError('OEM X/Y requires the existing negative limit direction')
        pin = rail.get_endstops()[0][0]
        saved = rail.homing_retract_dist, rail.homing_speed
        self.xy_references.pop(axis, None)
        try:
            # One bounded approach, then reverse to the selected input's
            # release. No fixed retreat or second contact replaces release.
            rail.homing_retract_dist = 0.
            rail.homing_speed = saved[1] * factor
            force = [None, None, None, None]
            target = list(force)
            force[index] = info.position_endstop + XY_SEEK_MM
            target[index] = info.position_endstop
            state = homing.Homing(self.printer)
            state.set_axes([index])
            state.home_rails([rail], force, target)
            self.toolhead.wait_moves()
            self._require_native()
            if not self._is_triggered(pin):
                raise FactoryError(axis+' approach did not leave its selected input active')
            start = self.toolhead.get_position()
            target = list(start)
            target[index] += XY_RELEASE_MM
            move = homing.HomingMove(self.printer, [(pin, 'inventure_'+axis.lower()+'_release')], self.toolhead)
            measured = move.homing_move(target, XY_RELEASE_SPEED, probe_pos=True,
                                        triggered=False, check_triggered=True)
            self.toolhead.wait_moves()
            self._require_native()
            release = measured[index] - info.position_endstop
            actual = self.toolhead.get_position()[index] - info.position_endstop
            if (not 0 < release <= actual <= XY_RELEASE_MM
                    or move.check_no_movement() is not None or self._is_triggered(pin)):
                raise FactoryError(axis+' did not establish a fresh, bounded limit release')
            # Preserve the native/pad datum used by the accepted R38.6 frame.
            # Its release-to-trigger translation is measured, never assumed.
            self.xy_references[axis] = dict(release_mm=release,
                release_steps=round(release * XY_STEPS_PER_MM), halt_mm=actual,
                reference='measured_reverse_release', enable_held=True)
            self._remember_clearance()
        except Exception:
            self.printer.lookup_object('stepper_enable').motor_off()
            self._invalidate_native_homing()
            raise
        finally:
            rail.homing_retract_dist, rail.homing_speed = saved

    def _home_axes(self, gcmd, operator):
        axes = gcmd.get('AXES', '').upper()
        previous = self.requested_home_axes
        self.requested_home_axes = axes
        try:
            if axes not in ('X', 'Y', 'XY'):
                return super()._home_axes(gcmd, operator)
            from .inventure_fluidd import require_idle, native_control_state, select_native_control
            unknown = gcmd.get_command_parameters().keys() - {'AXES','XY_SPEED_FACTOR','XY_CLEARANCE'}
            if unknown:
                raise gcmd.error('Unsupported homing parameters: '+', '.join(sorted(unknown)))
            factor = gcmd.get_float('XY_SPEED_FACTOR', self.xy_speed_factor, minval=.1,maxval=2.)
            clearance = gcmd.get_float('XY_CLEARANCE', self.xy_clearance, above=0.,
                                      maxval=self.kin.rails[2].get_range()[1])
            if not all(map(math.isfinite,(factor,clearance))):
                raise gcmd.error('Homing motion parameters must be finite')
            self._require_idle(self.reactor.monotonic())
            if self.printer.lookup_object('gcode_move').move_transform is not None:
                raise gcmd.error('Inventure homing requires an untransformed working frame')
            if operator:
                require_idle(self.board)
                select_native_control(self.board,native_control_state(self.board))
            self._require_native()
            self._operator_clearance(clearance)
            for axis in 'YX':
                if axis in axes:
                    self._home_limit(axis,factor)
            gcmd.respond_info('INVENTURE_HOME %s done'%axes)
        except Exception as exc:
            raise gcmd.error(str(exc)) from exc
        finally:
            self.requested_home_axes = previous

    def _prepare_t0(self, gcmd, heaters, target, xy_factor=None, pad_speed=None):
        from .inventure_fluidd import require_idle, native_control_state, select_native_control
        require_idle(self.board)
        heaters.turn_off_all_heaters()
        deadline = self.reactor.monotonic() + 10.
        while any(h.last_pwm_value for h in heaters.heaters.values()):
            if self.printer.is_shutdown() or self.reactor.monotonic() >= deadline:
                raise FactoryError('Native heaters did not stop before held T0 preparation')
            require_idle(self.board)
            self.reactor.pause(self.reactor.monotonic()+.1)
        select_native_control(self.board,native_control_state(self.board))
        self.toolhead.wait_moves()
        self._require_native()
        self._operator_clearance(self.xy_clearance)
        for axis in 'YX':
            if self.requested_home_axes == 'XYZ' or not self._xy_valid(axis):
                self._home_limit(axis,self.xy_speed_factor if xy_factor is None else xy_factor)
        endstops = [rail.get_homing_info().position_endstop for rail in self.kin.rails[:2]]
        releases = {axis:self.xy_references[axis]['release_mm'] for axis in 'XY'}
        position = self.toolhead.get_position()
        service_xy = [position[i] - endstops[i] - releases[axis] for i,axis in enumerate('XY')]
        owner = self.board._control_next_token()
        if self.held_service is None:
            raise FactoryError('Matching R24 MCU held-service command is unavailable')
        self.toolhead.wait_moves()
        try:
            row = self.held_service.send([owner])
            self.board._control_validate(row,dict(owner=(1,0xffffffff),mode=(0,2),enabled=(0,1),reject=(0,255)))
            if (row['owner'] != owner or row['reject'] or row['mode'] != 2 or row['enabled'] != 1):
                raise FactoryError('MCU refused the drained, continuously held T0 handoff')
            # This cache update comes from the verified MCU receipt. The old
            # loss-of-ARM callback must not misclassify a held handoff as M84.
            self.board.control_last=dict(self.board.control_last,mode='SERVICE',armed=False)
        except Exception:
            self.board._control_abort()
            self._invalidate_native_homing()
            raise
        params = dict(ACTION='CLEAN',TOOL='T0',TARGET=target,SPEED_PROFILE='OEM')
        if xy_factor is not None:
            params['HOME_SPEED'] = min(rail.homing_speed for rail in self.kin.rails[:2])*xy_factor
        command = self.printer.lookup_object('gcode').create_gcode_command(
            'INVENTURE_PROCEDURE','INVENTURE_PROCEDURE ACTION=CLEAN TOOL=T0',params)
        frame = dict(owner=owner,service_xy=service_xy,release_mm=releases)
        try:
            gcmd.respond_info('V9_NATIVE_Z_PROGRESS '+json.dumps(dict(stage='T0 selection and cleaning',
                                xy_reference='retained_release',xy_rehomed=self.requested_home_axes=='XYZ')))
            context = self.board._factory_start(command,operation='CLEAN',native_homing=dict(
                pad_xy=[self.pad_x,self.pad_y],endstops=endstops,
                pad_speed=self.pad_speed if pad_speed is None else pad_speed,held_xy=frame))
            factory = context['factory']
            while self.board.load_test_context is context:
                now = self.reactor.monotonic()
                if self.printer.is_shutdown() or now >= factory['total_deadline']:
                    raise FactoryError('T0 preparation was interrupted or expired')
                self.reactor.pause(now+.1)
            result = factory.get('result') or {}
            if (self.board.load_test_context is not None or context['tool'] != 0
                    or factory['operation'] != 'CLEAN' or not factory['selection_commanded']
                    or not factory['cleaning_done'] or not result.get('success')
                    or result.get('terminal') != 'HOME_PREPARE'
                    or result.get('native_xy_mm') != [self.pad_x,self.pad_y]
                    or result.get('service_xy_mm') != factory['position'][:2]):
                raise FactoryError('T0 selection and cleaning did not complete')
            self._require_native()
            self.xy_clearance_position = None
            return result
        except Exception:
            self.board._control_abort()
            self._invalidate_native_homing()
            raise
