# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure filament recovery for the active, native virtual SD print."""
import json
import logging
import uuid

from .inventure_jam_monitor import JamMonitor
from .inventure_jam_motion import JamMotion


class RunEnded(Exception):
    """A yielding operation lost its print, its tool or its recovery session."""


class MissingCassetteFilament(Exception):
    """Input A opened while the cassette recovery transport was selected."""


class FilamentJam:
    PHASES_ACTIVE = ('parking', 'recovering', 'waiting', 'verifying', 'resuming')
    TOOLS = ('T0', 'T1')

    def __init__(self, board):
        self.board = board
        self.printer, self.reactor, self.gcode = board.printer, board.reactor, board.gcode
        self.monitor = JamMonitor(board)
        self.motion = JamMotion(board)
        self.job = self.context = self.resume_caller = self.blocked_file = None
        self.pause_started = False
        self.worker = False
        self.running = False
        self.ready_once = False
        self.state = dict(run_id=None, prompt_id=0, phase='idle', tool=None, source=None,
                          reason=None, trigger=None, evidence=None, message='Ready',
                          allowed_actions=[], stage=None)
        self.timer = self.reactor.register_timer(self._tick)
        self.gcode.register_command('ZORTRAX_FILAMENT_JAM', self.cmd, when_not_ready=True)
        self.printer.register_event_handler('klippy:ready', self._ready)
        for event in ('klippy:shutdown', 'klippy:disconnect'):
            self.printer.register_event_handler(event, self._shutdown)
        self.printer.register_event_handler('virtual_sdcard:reset_file', self._file_reset)

    @property
    def active(self):
        return self.state['phase'] in self.PHASES_ACTIVE

    def _ready(self):
        self.running = True
        if not self.ready_once:
            self._protect_print_resume()
            self.ready_once = True
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def _protect_print_resume(self):
        # User macros (incl. renamed RESUME) can move back to PAUSE_STATE
        # *before* native do_resume(). Reject at both public entry points.
        sd = self._sd()
        if sd is None:
            return
        resume = sd.do_resume

        def guarded_resume():
            self._check_resume()
            return resume()
        sd.do_resume = guarded_resume

        commands = self.gcode.get_status(self.reactor.monotonic())['commands']
        for name in list(commands):
            if name == 'M24' or 'RESUME' in name:
                self._protect_command(name)

    def _check_resume(self):
        if self.blocked_file is not None and self.blocked_file is self._print_file():
            raise self.gcode.error('Recovery ended without a verified return. Cancel the interrupted print.')
        if self.active:
            from greenlet import getcurrent
            if self.resume_caller is not getcurrent() or not self.motion.authorized_resume():
                raise self.gcode.error('Resolve or cancel filament recovery first')

    def _protect_command(self, name):
        when_not_ready = name in self.gcode.base_gcode_handlers
        previous = self.gcode.register_command(name, None)
        if previous is None:
            return

        def guarded(gcmd):
            self._check_resume()
            return previous(gcmd)
        self.gcode.register_command(name, guarded, when_not_ready=when_not_ready)

    def _sd(self):
        return self.printer.lookup_object('virtual_sdcard', None)

    def _print_file(self, running=False):
        sd = self._sd()
        if sd is None or (running and not sd.is_active()):
            return None
        return sd.current_file

    def _paused_print(self):
        stats = self.printer.lookup_object('print_stats', None)
        return self._print_file() is not None and getattr(stats, 'state', '') == 'paused'

    def _print_profile(self):
        required = ('virtual_sdcard', 'pause_resume', 'inventure_operations',
                    'inventure_tools', 'extruder_stepper shared_e', 'extruder1')
        objects = {name: self.printer.lookup_object(name, None) for name in required}
        missing = [name for name, obj in objects.items() if obj is None]
        inactive = []
        ops, tools = objects['inventure_operations'], objects['inventure_tools']
        if ops is not None and not getattr(ops, 'installed', False):
            inactive.append('inventure_operations')
        if tools is not None and not getattr(tools, 'enabled', False):
            inactive.append('inventure_tools')
        if not getattr(self.board, 'allow_actuators', False):
            inactive.append('inventure_mcu.allow_actuators')
        return dict(configured=not missing and not inactive, missing=missing, inactive=inactive)

    def _service_test(self):
        record = (getattr(self.board, 'load_test_context', None)
                  or getattr(self.board, 'load_test_last', None) or {})
        factory = record.get('factory') or {}
        op = factory.get('operation')
        if op not in ('JAM_CASSETTE', 'JAM_EXTERNAL'):
            return None
        tool = record.get('tool')
        if type(tool) is int:
            tool = 'T%d' % tool
        result = factory.get('result')
        fields = ('success', 'terminal', 'transport_confirmed', 'flow_confirmed',
                  'cleaning_done', 'print_resumed')
        return dict(tool=tool, source=op[4:], phase=record.get('phase'), stage=factory.get('phase'),
                    result={k: result[k] for k in fields if k in result} if result else None,
                    reason=record.get('reason') or (result or {}).get('reason'))

    def get_status(self, eventtime=None):
        status = dict(self.state, active=self.active,
                      allowed_actions=list(self.state['allowed_actions']))
        if not self.active and not self.worker and self.running and self.board.connected:
            if self._print_file(running=True) is None:
                status['allowed_actions'] = [] if self._paused_print() else ['SOURCE']
            elif self.monitor.active_tool() in self.TOOLS:
                status['allowed_actions'] = ['REPORT', 'MISSING', 'BROKEN', 'EXCHANGE']
        status['sources'] = {tool: self.monitor.source(tool) for tool in self.TOOLS}
        status['monitor'] = self.monitor.status()
        status['print_profile'] = self._print_profile()
        status['service_test'] = self._service_test()
        return status

    def _change(self, phase, message, actions=(), stage=None):
        self.state.update(phase=phase, message=message, allowed_actions=list(actions),
                          prompt_id=self.state['prompt_id'] + 1, stage=stage)
        if (self.state['tool'] == 'T1' and self.state['reason'] == 'EXTRUDER_JAM'
                and phase == 'waiting' and stage in ('R1', 'reload')):
            self.state['pcb_class'] = self._pcb_class()
        self.gcode.respond_info('Filament recovery | %s | %s' % (self.state['tool'], message))

    def _pcb_class(self):
        thermal = self.printer.lookup_object('inventure_thermal', None)
        if thermal is None:
            return None
        try:
            report = thermal.get_status(self.reactor.monotonic())
            age, level = report['sensor_ages'][7], report['pcb_class']
            channel = report['channels'][7]
            if (age is not None and 0 <= age <= thermal.stale and not report['fatal']
                    and channel['samples'] > 0 and not channel['bad_sensor'] and not channel['fault']
                    and type(level) is int and 0 <= level <= 4):
                return level
        except (IndexError, KeyError, TypeError, ValueError):
            pass
        return None

    def _shutdown(self, *args):
        self.running = False
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        self._invalidate('Printer disconnected. Recovery stopped.')

    def _file_reset(self, *args):
        self._invalidate('Print file changed. Recovery stopped.')
        self.blocked_file = None

    def _reset_monitor(self):
        try:
            self.monitor.reset()
        except Exception:
            logging.exception('Inventure jam monitor could not release transport')

    def _invalidate(self, message):
        if self.active:
            if self.pause_started and self.job is self._print_file():
                self.blocked_file = self.job
                message += ' Cancel the interrupted print before starting another.'
            self._change('cancelled', message)
            self.motion.abort(self.context or self.motion.context)
        self.job = self.context = None
        self._reset_monitor()

    def _owns(self, run_id):
        return self.active and self.state['run_id'] == run_id

    def _guard(self, run_id):
        if not self._owns(run_id) or not self.running:
            raise RunEnded()
        if self._print_file() is not self.job or self.monitor.active_tool() != self.state['tool']:
            self._invalidate('The print or active nozzle changed. Recovery stopped.')
            raise RunEnded()
        if self.monitor.source(self.state['tool']) != self.state['source']:
            self._invalidate('The selected filament source changed. Recovery stopped.')
            raise RunEnded()

    def _tick(self, eventtime):
        if not self.running:
            return self.reactor.NEVER
        try:
            if self.active:
                self._guard(self.state['run_id'])
            elif not self.worker:
                job = self._print_file(running=True)
                event = self.monitor.poll(eventtime)
                if (event and job is not None and job is self._print_file(running=True)
                        and not self.active and not self.worker):
                    self._start(event)
        except RunEnded:
            pass
        except Exception as exc:
            logging.exception('Inventure filament monitoring')
            tool = self.monitor.active_tool()
            if (not self.active and tool in self.TOOLS and self._print_file(running=True)
                    is not None and self.monitor.source(tool) in ('EXTERNAL', 'CASSETTE')):
                self._start(dict(tool=tool, source=self.monitor.source(tool),
                                 reason='MONITOR_ERROR', detail=str(exc)))
        return self.reactor.monotonic() + .25

    def _start(self, event):
        job = self._print_file(running=True)
        tool = event['tool']
        if (self.worker or self.active or job is None or tool not in self.TOOLS
                or tool != self.monitor.active_tool()):
            raise ValueError('Filament recovery requires the active nozzle of a running virtual SD print')
        if getattr(self.board, 'load_test_context', None) or getattr(self.board, 'feeder_run_context', None):
            raise ValueError('Finish the active service procedure before starting print recovery')
        self.monitor.stop_recovery()
        self.job, self.context = job, None
        self.pause_started = False
        self.state.update(run_id=uuid.uuid4().hex, prompt_id=0, tool=tool, source=event['source'],
                          reason=event['reason'], diagnostic=event.get('detail'),
                          trigger=event.get('trigger'), evidence=event.get('evidence'), pcb_class=None)
        self._change('parking', 'Pausing the print and parking over the waste bin.')
        self._schedule(self._park)

    def _schedule(self, operation):
        if self.worker:
            raise ValueError('Recovery is already working')
        run_id = self.state['run_id']
        self.worker = True

        def run(eventtime):
            try:
                self._guard(run_id)
                operation(run_id)
            except RunEnded:
                pass
            except Exception as exc:
                if self._owns(run_id):
                    self._hold_error(run_id, exc)
            finally:
                self.worker = False
        self.reactor.register_callback(run)

    def _park(self, run_id):
        # Acquire the interpreter after any SD command completes. do_pause can
        # now drain the SD worker without interrupting an in-flight macro.
        with self.gcode.get_mutex():
            self._guard(run_id)
            self.motion.pause()
        self.pause_started = True
        self._change('parking', 'Parking over the waste bin.', ('CANCEL',))
        self._guard(run_id)
        self.context = self.motion.park(int(self.state['tool'][1]), lambda: self._guard(run_id))
        self._guard(run_id)
        if self.state['source'] == 'UNKNOWN':
            self._change('waiting', 'Select the material source for this nozzle.', ('SOURCE', 'CANCEL'))
        elif self.state['reason'] != 'EXTRUDER_JAM':
            self._service_prompt()
        else:
            self._recover(run_id)

    def _service_prompt(self):
        reason = self.state['reason']
        if reason == 'BROKEN_FILAMENT':
            self._change('waiting', 'Clear the broken piece from the guide tube and refit the tube. '
                         'Unload the remaining filament if needed.',
                         ('UNLOAD', 'CLEARED', 'CANCEL'), 'clear_break')
            return
        if reason == 'REPLACE_FILAMENT':
            self._change('waiting', 'Unload the selected filament before replacement.', ('UNLOAD', 'CANCEL'))
            return
        if reason == 'MONITOR_ERROR':
            self._change('waiting', 'Check the filament monitor and transport before retrying.',
                         ('RETRY', 'UNLOAD', 'CANCEL'))
            return
        if reason in ('RUNOUT', 'CARTRIDGE_ERROR', 'MATERIAL_MISSING'):
            message = ('Fit filament from the external spool.' if self.state['source'] == 'EXTERNAL'
                       else 'Check the cassette and fit replacement material.')
            self._change('waiting', message + ' Select Load replacement for an empty guide tube.',
                         ('LOAD', 'RETRY', 'UNLOAD', 'BROKEN', 'CANCEL'))
            return
        message = ('Check the external spool.' if self.state['source'] == 'EXTERNAL'
                   else 'Check the cassette and clear its filament path.')
        self._change('waiting', message + ' Select Retry when ready.', ('RETRY', 'UNLOAD', 'BROKEN', 'CANCEL'))

    def _hold_error(self, run_id, exc):
        # An exception can follow a partial move or an unacknowledged return.
        # At this point there is no generic safe XYZE from which to retry.
        detail = str(exc) or type(exc).__name__
        self.motion.abort(self.context or self.motion.context)
        self._reset_monitor()
        if self._owns(run_id):
            self.state['diagnostic'] = detail
            self._change('waiting', 'Recovery stopped. %s' % detail, ('CANCEL',), 'fault')

    def _recover(self, run_id):
        source = self.state['source']
        if source == 'EXTERNAL':
            self._change('recovering', 'Reloading the external filament over the waste bin.', ('CANCEL',), 'external')
            for _ in range(4):
                if not self._external_present(run_id):
                    return
                self._move(run_id, self.motion.feed, 5.)
            if not self._external_present(run_id):
                return
            self._move(run_id, self.motion.clean)
            self._change('verifying', 'Confirm that filament came out of the nozzle over the bin.',
                         ('CONFIRM', 'RETRY', 'UNLOAD', 'CANCEL'), 'external_flow')
            return
        if source != 'CASSETTE':
            raise ValueError('Select the material source first')
        tool = self.state['tool']
        guard = lambda: self._guard(run_id)
        self._guard(run_id)
        self.monitor.begin_recovery(tool)
        cleaned = False
        try:
            if not self.monitor.sample(tool)['a']:
                raise MissingCassetteFilament()
            if not self._cassette_phase(run_id, 'R1'):
                self.monitor.stop_recovery()
                self._move(run_id, self.motion.restore_e)
                self._change('waiting', 'Transport pulses missing. Inspect the guide tube for a break '
                             'and check the nozzle. Select Broken filament if a break is visible.',
                             ('RETRY', 'UNLOAD', 'BROKEN', 'CANCEL'), 'R1')
                return
            if not self._cassette_phase(run_id, 'R2'):
                self._change('recovering', 'Reloading the cassette.', ('CANCEL',), 'reload')
                def progress(message):
                    guard()
                    self.state['message'] = str(message)
                # The native load adapter takes the interpreter mutex per
                # move so the operator can cancel between transport segments.
                guard()
                loaded = self.motion.reload(self.context, self.monitor, guard, progress)
                guard()
                cleaned = loaded == 'CLEANED'
                if not loaded:
                    self._move(run_id, self.motion.restore_e)
                    self._change('waiting', 'Cassette reload failed. Check the filament path and retry.',
                                 ('RETRY', 'UNLOAD', 'BROKEN', 'CANCEL'), 'reload')
                    return
        except MissingCassetteFilament:
            self.monitor.stop_recovery()
            self._move(run_id, self.motion.restore_e)
            if self.state['reason'] != 'RUNOUT':
                self.state['reason'] = 'MATERIAL_MISSING'
            self._service_prompt()
            return
        finally:
            self.monitor.stop_recovery()
        if not cleaned:
            self._move(run_id, self.motion.clean)
        self._resume(run_id)

    def _exchange_stroke(self, run_id, unload):
        tool = self.state['tool']
        guard = lambda: self._guard(run_id)
        guard()
        if self.state['source'] == 'CASSETTE':
            self.monitor.begin_recovery(tool)
            try:
                outcome = self.motion.reload(self.context, self.monitor, guard,
                    lambda stage: self.state.update(message=stage), unload=unload)
            finally:
                self.monitor.stop_recovery()
            guard()
            if not unload and not outcome:
                self._change('waiting', 'Cassette load did not confirm transport. Check the guide tube, '
                             'then load again or remove the filament.',
                             ('LOAD', 'UNLOAD', 'BROKEN', 'CANCEL'), 'reload')
                return
            already_clean = outcome == 'CLEANED'
        else:
            # An operator advances each portion and can finish early, including
            # when inlet A has cleared but a broken piece is still in the tube.
            if not unload and not self._external_present(run_id):
                return
            limit = 100 if unload else 250
            amount = min(20, limit - self.exchange_mm)
            if amount <= 0:
                raise ValueError('External spool travel limit reached')
            for _ in range(amount):
                self._move(run_id, self.motion.feed, -1. if unload else 1.)
            self.exchange_mm += amount
            more = self.exchange_mm < limit
            if unload:
                choices = ('MORE_OUT', 'CLEARED', 'CANCEL') if more else ('CLEARED', 'CANCEL')
            else:
                choices = ('MORE_IN', 'WIPE', 'UNLOAD', 'CANCEL') if more else ('WIPE', 'UNLOAD', 'CANCEL')
            self._change('waiting', 'Inspect the filament path after %d mm.' % self.exchange_mm, choices)
            return
        if unload:
            message = ('Remove the cassette and broken pieces.' if outcome else
                       'Inlet A still detects filament. Remove the cassette and clear the broken filament.')
            self._change('waiting', message,
                         ('CLEARED', 'CANCEL'))
        elif already_clean:
            self._change('verifying', 'Check visible nozzle flow.', ('FLOW', 'UNLOAD', 'CANCEL'))
        else:
            self._change('waiting', 'Cassette loaded. Wipe the nozzle.', ('WIPE', 'UNLOAD', 'CANCEL'))

    def _exchange_wipe(self, run_id):
        self._move(run_id, self.motion.clean)
        self._change('verifying', 'Confirm visible nozzle flow over the bin.', ('FLOW', 'UNLOAD', 'CANCEL'))

    def _external_present(self, run_id):
        self._guard(run_id)
        if self.monitor.sample(self.state['tool'])['a']:
            return True
        self.state['reason'] = 'RUNOUT'
        self._service_prompt()
        return False

    def _move(self, run_id, method, *args):
        self._guard(run_id)
        result = method(self.context, *args)
        self._guard(run_id)
        return result

    def _cassette_phase(self, run_id, stage):
        tool = self.state['tool']
        pulse_test = stage == 'R1'
        mode, field, threshold = (2, 'pulses', 10) if pulse_test else (1, 'starts', 3)
        self._change('recovering', 'Checking filament motion.' if pulse_test else
                     'Checking automatic feeder starts.', ('CANCEL',), stage)
        initial = self.monitor.set_mode(tool, mode)
        if not initial['a']:
            raise MissingCassetteFilament()
        baseline = last = initial[field]
        previous_sample = self.reactor.monotonic()
        last_edge = (previous_sample - initial['pulse_age_ms'] * .001
                     if pulse_test and initial['pulse_age_ms'] <= 2000 else None)
        for _ in range(50):
            self._move(run_id, self.motion.feed, 1.)
            sample = self.monitor.sample(tool)
            if not sample['a']:
                raise MissingCassetteFilament()
            value = sample[field]
            delta = (value - last) & 0xffffffff
            if delta > 0x7fffffff:
                raise ValueError('Filament transport counter reset during recovery')
            last = value
            if pulse_test:
                now = self.reactor.monotonic()
                # Sampling after short, drained moves gives a fresh edge age.
                age_ms = sample['pulse_age_ms']
                edge_time = now - age_ms * .001
                gap = now - previous_sample
                previous_sample = now
                if age_ms > 2000:
                    baseline, last_edge = value, None
                    continue
                if delta:
                    if gap > 2. or (last_edge is not None and edge_time - last_edge > 2.):
                        # A cumulative count cannot date the earlier edges of
                        # a batch on opposite sides of a two-second silence.
                        baseline = (value - 1) & 0xffffffff
                    last_edge = edge_time
            if ((value - baseline) & 0xffffffff) >= threshold:
                return True
        return False

    def _resume(self, run_id):
        if self.state['source'] == 'EXTERNAL' and not self._external_present(run_id):
            return
        self._change('resuming', 'Returning to the print.', ('CANCEL',))
        from greenlet import getcurrent

        def returning():
            self._guard(run_id)
            if self.state['source'] == 'EXTERNAL' and not self.monitor.sample(self.state['tool'])['a']:
                raise ValueError('External filament disappeared during the return to the print')

        returning()
        self.resume_caller = getcurrent()
        try:
            self.motion.resume(self.context, returning,
                               lambda: self.monitor.prepare_resume(self.state['tool']))
        finally:
            self.resume_caller = None
        if not self._owns(run_id):
            return
        self.monitor.complete_resume(self.state['tool'])
        self._change('completed', 'Filament recovery complete. Print resumed.')
        self.job = self.context = None

    def cmd(self, gcmd):
        action = str(gcmd.get('ACTION', 'STATUS')).upper()
        try:
            self._command(action, gcmd)
        except (ValueError, RuntimeError) as exc:
            raise gcmd.error(str(exc))
        gcmd.respond_info('INVENTURE_FILAMENT_JAM ' + json.dumps(self.get_status(), sort_keys=True))

    def _command(self, action, gcmd):
        if action == 'STATUS':
            return
        if not self.running or not self.board.connected:
            raise ValueError('Connect the Inventure MCU first')
        if self.active:
            if gcmd.get('RUN_ID', self.state['run_id']) != self.state['run_id']:
                raise ValueError('This filament recovery session has ended')
            if gcmd.get_int('PROMPT_ID', self.state['prompt_id']) != self.state['prompt_id']:
                raise ValueError('This filament recovery prompt has changed')
        elif gcmd.get('RUN_ID', None) is not None or gcmd.get('PROMPT_ID', None) is not None:
            raise ValueError('This filament recovery session has ended')
        if action == 'SOURCE':
            tool = str(gcmd.get('TOOL')).upper()
            source = str(gcmd.get('SOURCE')).upper()
            if tool not in self.TOOLS or source not in ('CASSETTE', 'EXTERNAL', 'UNKNOWN'):
                raise ValueError('Select TOOL=T0/T1 and SOURCE=CASSETTE/EXTERNAL/UNKNOWN')
            if self.active:
                if ('SOURCE' not in self.state['allowed_actions'] or tool != self.state['tool']
                        or source == 'UNKNOWN' or self.worker):
                    raise ValueError('The recovery source cannot be changed now')
            elif self.worker or self._paused_print() or self._print_file(running=True) is not None:
                raise ValueError('Select the source before printing')
            self.monitor.set_source(tool, source)
            if self.active:
                if self.monitor.source(tool) != source:
                    raise ValueError('The configured material source differs from the selection')
                self.state['source'] = source
                if source == 'EXTERNAL' and self.state['reason'] == 'MATERIAL_MISSING':
                    self.state['reason'] = 'RUNOUT'
                self._service_prompt()
            return
        if not self.active and action in ('REPORT', 'MISSING', 'BROKEN', 'EXCHANGE'):
            tool = self.monitor.active_tool()
            if tool not in self.TOOLS or str(gcmd.get('TOOL', tool)).upper() != tool:
                raise ValueError('Report filament problems on the active print nozzle')
            source = self.monitor.source(tool)
            reason = {'EXCHANGE': 'REPLACE_FILAMENT', 'REPORT': 'EXTRUDER_JAM',
                      'BROKEN': 'BROKEN_FILAMENT',
                      'MISSING': 'RUNOUT' if source == 'EXTERNAL' else 'MATERIAL_MISSING'}[action]
            self._start(dict(tool=tool, source=source, reason=reason, trigger='operator'))
            return
        if not self.active or action not in self.state['allowed_actions']:
            raise ValueError('Action unavailable at this stage of filament recovery')
        if action == 'CANCEL':
            self._invalidate('Recovery cancelled. The print will not be resumed by recovery.')
            return
        if self.worker:
            raise ValueError('Recovery is already working')
        if action == 'BROKEN':
            self.state['reason'] = 'BROKEN_FILAMENT'
            self._service_prompt()
        elif action in ('UNLOAD', 'LOAD', 'MORE_OUT', 'MORE_IN'):
            if action in ('UNLOAD', 'LOAD'):
                self.exchange_mm = 0
            unload = action in ('UNLOAD', 'MORE_OUT')
            self._change('recovering', 'Unloading filament.' if unload else 'Loading filament.', ('CANCEL',))
            self._schedule(lambda run_id: self._exchange_stroke(run_id, unload))
        elif action == 'CLEARED':
            present = self.monitor.inlet_present(self.state['tool'])
            if present and self.state['reason'] != 'BROKEN_FILAMENT':
                raise ValueError('Inlet A still detects filament. Clear the path before inserting replacement material.')
            message = ('Filament is at the inlet. Refit the guide tube and select Load.'
                       if present else 'Insert replacement material and select Load.')
            self._change('waiting', message, ('LOAD', 'CANCEL'))
        elif action == 'WIPE':
            self._change('recovering', 'Wiping nozzle.', ('CANCEL',))
            self._schedule(self._exchange_wipe)
        elif action == 'FLOW':
            self._change('waiting', 'Flow confirmed. Ready to return to the print.', ('CONTINUE', 'UNLOAD', 'CANCEL'))
        elif action == 'CONTINUE':
            self._change('resuming', 'Returning to the print.', ('CANCEL',))
            self._schedule(self._resume)
        elif action == 'RETRY':
            self._change('recovering', 'Retrying filament recovery.', ('CANCEL',))
            self._schedule(self._recover)
        elif action == 'CONFIRM':
            self._change('resuming', 'External filament flow confirmed.', ('CANCEL',))
            self._schedule(self._resume)
