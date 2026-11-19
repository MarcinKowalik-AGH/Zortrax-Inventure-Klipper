# SPDX-License-Identifier: GPL-3.0-or-later
"""Native-print filament supervision using the R13 transport counters."""
from .inventure_factory_core import delta32, feeder_budget, parameters
from .inventure_jam_sources import JamSources


REPORT = ('inventure_transport_status clock=%u channel=%c live=%c a=%c b=%c '
          'pulses=%u last_pulse_clock=%u starts=%u mode=%c running=%c '
          'direction=%c retry=%c fault=%c budget=%i forward=%u reverse=%u '
          'assist=%c locks=%c')
COMMANDS = {
    'profile': 'inventure_transport_profile channel=%c cartridge=%c duration_ms=%hu budget_steps=%u',
    'lease': 'inventure_transport_lease acknowledge=%u',
    'mode': 'inventure_transport_mode channel=%c mode=%c monitor=%c',
    'select': 'inventure_transport_select channel=%c',
    'query': 'inventure_transport_query',
}
SOURCE_NAMES = {'cartridge': 'CASSETTE', 'spool': 'EXTERNAL'}
UINT32 = 0xffffffff


def _index(tool):
    if tool not in ('T0', 'T1'):
        raise ValueError('Select T0 or T1')
    return int(tool[1])


def classify(source, report, previous_forward, nearly_empty=None):
    """An absent cassette without a verified balance is ambiguous."""
    if source == 'CASSETTE' and report['fault'] not in (0, 2, 3):
        return 'MONITOR_ERROR'
    if not report['a']:
        if source == 'EXTERNAL' or nearly_empty is True:
            return 'RUNOUT'
        return 'CARTRIDGE_ERROR' if nearly_empty is False else 'MATERIAL_MISSING'
    if source == 'EXTERNAL':
        return None
    fault = report['fault']
    # R13 issues each native E STEP and maintains the signed supply budget.
    # Its ABI does not report planner block boundaries; compare at MCU samples.
    exhausted = (previous_forward is not None and
                 delta32(report['forward'], previous_forward) > 0 and report['budget'] < 0)
    if fault or exhausted:
        if nearly_empty is True:
            return 'RUNOUT'
        return 'CASSETTE_JAM' if fault == 3 else 'EXTRUDER_JAM'
    return None


class JamMonitor:
    def __init__(self, owner):
        self.board = owner
        self.printer = owner.printer
        self.reactor = owner.reactor
        self.sources = JamSources(self.printer)
        self.commands = {}
        self.reports = [None, None]
        self.clock_hz = 0.
        self.last_forward = None
        self.print_tool = None
        self.owned_tool = None
        self.recovery = False
        self.borrowed = None
        self.epoch = 0
        self.lease_time = None
        self.lease_error = None
        self.latched = False
        self.timer = self.reactor.register_timer(self._renew)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        self.printer.register_event_handler('klippy:disconnect', self._shutdown)
        self.printer.register_event_handler('extruder:activate_extruder', self._tool_changed)

    def _obj(self, name):
        return self.printer.lookup_object(name, None)

    def _p13(self):
        backend = self._obj('inventure_filament')
        return backend if getattr(backend, 'enabled', False) else None

    def _materials(self):
        obj = self._obj('inventure_materials')
        if (getattr(obj, 'enabled', False) and getattr(obj, 'confirmed', False)
                and getattr(obj, 'pair', None) and not obj.stopped):
            return obj
        return None

    def source(self, tool):
        idx = _index(tool)
        materials = self._materials()
        if materials is not None:
            return SOURCE_NAMES.get(materials.pair[idx].source, 'UNKNOWN')
        return self.sources.values[tool]

    def set_source(self, tool, source):
        idx = _index(tool)
        if source not in ('CASSETTE', 'EXTERNAL', 'UNKNOWN'):
            raise ValueError('SOURCE must be CASSETTE, EXTERNAL, or UNKNOWN')
        if self.owned_tool is not None:
            raise ValueError('Filament transport is active')
        mats = self._materials()
        if mats and source not in ('UNKNOWN', SOURCE_NAMES.get(mats.pair[idx].source)):
            raise ValueError('Change the confirmed material source in the material menu first')
        self.sources.set(tool, source)

    def active_tool(self):
        tools = self._obj('inventure_tools')
        if not getattr(tools, 'enabled', False) or tools.running:
            return None
        try:
            tools.require_binding()
        except self.printer.command_error:
            return None
        if type(tools.bound_tool) is int and tools.bound_tool in (0, 1):
            return 'T%d' % tools.bound_tool
        return None

    def _nearly_empty(self, idx):
        rfid, mats = self._obj('inventure_rfid'), self._materials()
        if not mats or not getattr(rfid, 'enabled', False):
            return None
        if rfid.error_for(idx, mats.pair[idx].material_id) is not None:
            return None
        tag = rfid.channels[idx]['tag']
        remaining = tag.remaining_mm
        # A tagged value can predate actual usage. Prefer a current bound
        # P14 ledger, rejecting uncertain/pending accounting for this tag.
        p14 = self._obj('inventure_consumption')
        if getattr(p14, 'enabled', False):
            row = p14.ledger.channels[idx]
            if (row is None or row.uncertain or row.intent or row.identity != tag.identity()
                    or row.target_mm is None or row.last_timestamp is None
                    or not 0 <= self.reactor.monotonic()-row.last_timestamp <= p14.max_gap):
                return None
            remaining = row.target_mm
        if tag.nominal_mm <= 0:
            return None
        return 20 * remaining <= tag.nominal_mm

    def _inputs(self, tool, now):
        received = self.board.inputs_received
        if (not self.board.connected or received is None or not 0 <= now - received <= .75
                or self.board.status_received is None or not 0 <= now-self.board.status_received <= .75
                or self.board.status.get('fault')):
            raise ValueError('Filament input data is stale or the MCU is faulted')
        row = self.board.inputs.get('raw', {})
        a, b = row.get(tool.lower() + '_a'), row.get(tool.lower() + '_b')
        if a not in (0, 1) or b not in (0, 1):
            raise ValueError('Filament inputs are unavailable')
        return {'a': a, 'b': b}

    def _ensure_native(self, tool):
        if (not self.board.connected or not self.board.allow_actuators
                or self.source(tool) != 'CASSETTE' or self.active_tool() != tool):
            raise ValueError('Cassette transport requires the confirmed active native tool')
        state = self.board._control_query()
        if state['mode'] != 'NATIVE' or not state['armed'] or state['fault'] or state['latched']:
            raise ValueError('Cassette transport requires an armed native print')

    def _commands(self):
        if self.commands:
            return
        mcu = self.board.mcu
        if int(mcu.get_constants().get('INVENTURE_TRANSPORT_ABI', 0)) != 1 or not mcu.check_valid_response(REPORT):
            raise ValueError('MCU transport ABI 1 is required for cassette supervision')
        self.clock_hz = mcu.get_constant_float('CLOCK_FREQ')
        commands = {key: mcu.lookup_command(fmt) for key, fmt in COMMANDS.items()}
        mcu.register_serial_response(self._receive, REPORT)
        self.commands = commands

    def _receive(self, params):
        row = dict(params)
        received = self.reactor.monotonic()
        self.reactor.register_async_callback(lambda now: self._deliver(row, received, now))

    def _deliver(self, row, received, now):
        idx = row.get('channel')
        if idx not in (0, 1) or not 0 <= now - received <= .5:
            return
        if (any(type(row.get(k)) is not int or not 0 <= row[k] <= UINT32
                for k in ('clock', 'pulses', 'last_pulse_clock', 'starts', 'forward', 'reverse'))
                or any(row.get(k) not in (0, 1) for k in ('a', 'b', 'live', 'running'))
                or type(row.get('fault')) is not int or not 0 <= row['fault'] <= 255
                or type(row.get('budget')) is not int or not -2**31 <= row['budget'] < 2**31):
            return
        old = self.reports[idx]
        if old:
            ticks = (row['clock'] - old['clock']) & UINT32
            if not 0 < ticks < 2**31:
                return
        if old and row['pulses'] == old['pulses']:
            age = old['pulse_age_ms'] + ticks * 1000. / self.clock_hz
        else:
            age = (((row['clock'] - row['last_pulse_clock']) & UINT32) * 1000. / self.clock_hz
                   if row['pulses'] or row['last_pulse_clock'] else UINT32)
        self.reports[idx] = dict(row, received=received, pulse_age_ms=age)

    def _start(self, tool, recover):
        self._ensure_native(tool)
        idx = _index(tool)
        legacy = self._p13()
        if legacy:
            if (not recover or legacy.stopped or legacy.running or legacy.assisting or legacy.configuring
                    or legacy.selected != idx or legacy.configured_generation != legacy.materials.generation
                    or legacy.ops.state.mode != 'paused' or legacy.ops.state.owner is None
                    or legacy.ops.state.owner.kind != 'print'):
                raise ValueError('The native print transport has not yielded this paused nozzle')
            # The same P13 owner keeps the lease and its serial handler. Only
            # the selected feeder's mode is delegated during the paused job.
            legacy.require_ready()
            cmds = legacy.commands
        else:
            self._commands()
            cmds = self.commands
        params = parameters({'TOOL': tool}, getattr(self.board, 'procedure_profiles', None),
                            max_target=310.)
        duration, budget = params['feeder_ms'], feeder_budget(params)
        mats = self._materials()
        if mats is not None:
            from .inventure_filament_core import profile
            cfg = profile(mats.pair[idx].material_id)
            duration, budget = cfg['duration'], cfg['budget']
        self.reports = [None, None]
        epoch = self.epoch
        self.owned_tool, self.borrowed, self.recovery = tool, legacy, recover
        try:
            cmds['profile'].send_wait_ack([idx, 1, duration, budget])
            if epoch != self.epoch:
                raise ValueError('Native transport session changed')
            if legacy:
                legacy.modes[idx] = (0, 0)
            else:
                cmds['select'].send_wait_ack([idx])
                if epoch != self.epoch:
                    raise ValueError('Native transport session changed')
                cmds['lease'].send_wait_ack([0x5031334c])
                if epoch != self.epoch:
                    raise ValueError('Native transport session changed')
                self.lease_time = self.reactor.monotonic()
                self.reactor.update_timer(self.timer, self.lease_time + .1)
            self.lease_error = None
        except Exception:
            self.stop_recovery()
            raise

    def _renew(self, now):
        tool = self.owned_tool
        if tool is None:
            return self.reactor.NEVER
        try:
            if (now - self.lease_time >= .45 or self.active_tool() != tool or not self.board.connected
                    or self.board.status_received is None or now-self.board.status_received > .75
                    or not self.board.status.get('armed') or self.board.status.get('fault')):
                raise ValueError('Native transport lease or tool binding was lost')
            self.commands['lease'].send([0x5031334c])
            self.lease_time = now
        except Exception as exc:
            self.lease_error = str(exc)
            self.stop_recovery()
            return self.reactor.NEVER
        return now + .1

    def _shutdown(self, *args):
        # Hardware shutdown/lease expiry owns the outputs at disconnect.
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        self.epoch += 1
        self.owned_tool = self.borrowed = self.print_tool = None
        self.recovery = False
        self.last_forward = None
        self.reports = [None, None]

    def _tool_changed(self, *args):
        # P04 drains the old tool before activating the new shared E queue.
        if self.owned_tool:
            self.stop_recovery()
        self.print_tool = None
        self.last_forward = None

    def stop_recovery(self):
        self.epoch += 1
        tool, legacy = self.owned_tool, self.borrowed
        self.owned_tool = self.borrowed = None
        self.recovery = False
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        if tool and self.board.connected:
            idx = _index(tool)
            commands = legacy.commands if legacy else self.commands
            commands['mode'].send([idx, 0, 0])
            if legacy:
                legacy.modes[idx] = None
        self.lease_time = None

    def reset(self):
        self.stop_recovery()
        self.last_forward = self.print_tool = self.lease_error = None
        self.latched = False

    def begin_recovery(self, tool):
        sd = self._obj('virtual_sdcard')
        pause = self._obj('pause_resume')
        if (sd is None or sd.is_active() or sd.current_file is None or pause is None
                or not pause.is_paused or not pause.sd_paused):
            raise ValueError('Native recovery requires a paused and resumable virtual SD print')
        self.stop_recovery()
        self._start(tool, True)

    def prepare_resume(self, tool):
        """Establish the baseline before the virtual SD source may issue E."""
        if self.active_tool() != tool or self.source(tool) == 'UNKNOWN':
            raise ValueError('Cannot resume without the confirmed active tool and material source')
        idx = _index(tool)
        if self.source(tool) == 'EXTERNAL':
            row = self.sample(tool)
        elif self._p13():
            backend = self._p13()
            backend.require_print_ready()
            if backend.selected != idx:
                raise ValueError('Native feeder selection changed before resume')
            row = backend._sample(idx)
        else:
            self.stop_recovery()
            self._start(tool, False)
            row = self.set_mode(tool, 1, monitor=1)
        if not row['a'] or row.get('fault') or row.get('budget', 0) < 0:
            self.stop_recovery()
            raise ValueError('Filament transport is not ready to resume the print')
        self.print_tool = tool
        self.last_forward = row.get('forward')

    def complete_resume(self, tool):
        if self.print_tool == tool:
            self.latched = False
            self.lease_error = None

    def sample(self, tool):
        idx = _index(tool)
        if self.source(tool) == 'EXTERNAL':
            return self._inputs(tool, self.reactor.monotonic())
        if self.lease_error:
            raise ValueError(self.lease_error)
        if self.owned_tool != tool or self.active_tool() != tool:
            raise ValueError('No active native transport for ' + tool)
        started = self.reactor.monotonic()
        epoch = self.epoch
        legacy = self.borrowed
        rows = legacy.reports if legacy else self.reports
        old = rows[idx]
        commands = legacy.commands if legacy else self.commands
        commands['query'].send([])
        while rows[idx] is old and self.owned_tool == tool and self.reactor.monotonic() < started + .4:
            self.reactor.pause(self.reactor.monotonic() + .01)
        row = rows[idx]
        if legacy and row is not None and row is not old:
            row = dict(row, received=legacy.received[idx], pulse_age_ms=legacy.pulse_ages[idx]*1000.)
        if (self.epoch != epoch or self.owned_tool != tool or row is None or row is old
                or not started <= row['received'] <= self.reactor.monotonic() or not row['live']):
            raise ValueError('No fresh live MCU filament transport report')
        if self.recovery and row['fault']:
            raise ValueError('MCU recovery transport fault %d' % row['fault'])
        return dict(row)

    def set_mode(self, tool, mode, monitor=0):
        if (self.owned_tool != tool or mode not in range(5) or monitor not in (0, 1)
                or self.source(tool) != 'CASSETTE' or not self.recovery and mode != 1):
            raise ValueError('Filament mode is outside the current cassette session')
        if self.lease_error:
            raise ValueError(self.lease_error)
        idx = _index(tool)
        if self.borrowed:
            self.borrowed._mode(mode, idx, monitor)
        else:
            self.commands['mode'].send_wait_ack([idx, mode, int(monitor)])
        row = self.sample(tool)
        if row['mode'] != mode:
            raise ValueError('MCU did not apply the requested native feeder mode')
        return row

    def _print_state(self, now):
        stats = self._obj('print_stats')
        sd = self._obj('virtual_sdcard')
        return bool(stats and sd and sd.is_active() and stats.get_status(now)['state'] == 'printing')

    def legacy_mode_delegated(self, channel):
        """P13 keeps its own lease, electrical checks and report delivery."""
        return self.recovery and self.borrowed is not None and self.owned_tool == 'T%d' % channel

    def _legacy_print_fault(self, channel, row):
        tool = 'T%d' % channel
        if (channel not in (0, 1) or row.get('channel') != channel or row.get('live') != 1
                or row.get('fault') not in (2, 3) or self.source(tool) != 'CASSETTE'):
            return False
        p13 = self._p13()
        if (not p13 or p13.stopped or p13.running or p13.selected != channel
                or p13.ops.state.owner is None or p13.ops.state.owner.kind != 'print'
                or p13.ops.state.fatal is not None
                or p13.received[channel] is None
                or not 0 <= self.reactor.monotonic()-p13.received[channel] <= .5
                or self.active_tool() != tool):
            return False
        return True

    def legacy_fault_managed(self, channel, row):
        jam = self.board.filament_jam
        sd = self._obj('virtual_sdcard')
        return bool(self._legacy_print_fault(channel, row) and jam.active and sd
                    and jam.job is not None and sd.current_file is jam.job
                    and jam.state['source'] == 'CASSETTE' and jam.state['tool'] == 'T%d' % channel)

    def observe_legacy_fault(self, channel, row):
        # Called on the reactor immediately before P13 publishes this report
        # to P14. All unrelated faults and all stale data still follow P13/P14.
        jam = self.board.filament_jam
        if jam.active or not jam.running or not self._legacy_print_fault(channel, row):
            return
        if self._print_state(self.reactor.monotonic()):
            reason = classify('CASSETTE', row, None, self._nearly_empty(channel))
            jam._start(dict(tool='T%d' % channel, source='CASSETTE', reason=reason))
            self.latched = jam.active

    def _legacy_sample(self, tool, now):
        idx = _index(tool)
        backend = self._p13()
        row, stamp = backend.reports[idx], backend.received[idx]
        if row is None or stamp is None or not 0 <= now-stamp <= .5 or not row['live']:
            raise ValueError('Native transport has no current active-tool print report')
        # P13 selects the issued-STEP counter at the first E move after
        # activation and owns AUTO transitions. Ignore the prior tool's E.
        if backend.selected != idx or row['mode'] != 1 or backend.assisting:
            return None
        return row

    def poll(self, eventtime):
        if self.latched or self.recovery:
            return None
        if not self._print_state(eventtime):
            if self.print_tool or self.owned_tool:
                self.reset()
            return None
        tool = self.active_tool()
        if not tool:
            if self.owned_tool:
                self.stop_recovery()
            self.print_tool = self.last_forward = None
            return None
        source = self.source(tool)
        if source == 'UNKNOWN':
            if self.print_tool:
                self.reset()
            return None
        # Source confirmation opts this path in. Never infer source from raw
        # A/B, a silent reader, or an empty amount estimate.
        if source == 'EXTERNAL' and self.owned_tool:
            self.stop_recovery()
        try:
            if self.lease_error:
                raise ValueError(self.lease_error)
            if self.print_tool != tool:
                self.last_forward = None
                self.print_tool = tool
            if source == 'EXTERNAL':
                row = self._inputs(tool, eventtime)
            elif self._p13():
                row = self._legacy_sample(tool, eventtime)
                if row is None:
                    self.last_forward = None
                    return None
            elif self.owned_tool == tool:
                row = self.sample(tool)
            else:
                self.stop_recovery()
                self._start(tool, False)
                row = self.set_mode(tool, 1, monitor=1)
            reason = classify(source, row, self.last_forward,
                              self._nearly_empty(_index(tool)) if source == 'CASSETTE' else None)
            self.last_forward = row.get('forward')
            if not reason:
                return None
            event = dict(tool=tool, source=source, reason=reason)
        except Exception as exc:
            event = dict(tool=tool, source=source, reason='MONITOR_ERROR', detail=str(exc))
        self.latched = True
        self.stop_recovery()
        return event

    def status(self):
        return dict(sources={tool: self.source(tool) for tool in ('T0', 'T1')},
                    active_tool=self.active_tool(), monitored_tool=self.print_tool,
                    transport_owner=self.owned_tool, transport_error=self.lease_error,
                    source_storage_error=self.sources.error, legacy_transport=bool(self._p13()))
