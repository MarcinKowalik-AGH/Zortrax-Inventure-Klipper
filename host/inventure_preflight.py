# SPDX-License-Identifier: GPL-3.0-or-later
"""P15: asynchronous read-only preflight and a one-use native-file start gate.

Disabled unless configured. Actuator qualifications are NOT changed by this
module. P14 readiness must be supplied by the actual P14 module; no fixture or
old report is installed as a substitute. Default integration gate stays closed.
"""
from dataclasses import asdict
from greenlet import getcurrent
import io
import json
import threading
import uuid
from .inventure_preflight_core import (Job, Ticket, PreflightError, parse_json,
    read_regular, inspect_gcode, evaluate, finite, context_fingerprint, MAX_BYTES, VERSION)

class MemoryFile(io.BytesIO):
    def __init__(self, raw, name):
        super().__init__(raw)
        self.name = name

class InventurePreflight:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.enabled = config.getboolean('enabled', False)
        self.integration_qualified = config.getboolean('p14_integration_qualified', False)
        self.start_qualified = config.getboolean('start_qualified', False)
        self.ttl = config.getfloat('token_ttl', 30., minval=1., maxval=300.)
        try:
            finite(self.ttl, 'token TTL', 1., 300.)
        except PreflightError as exc:
            raise config.error(str(exc))
        self.session = uuid.uuid4().hex
        self.epoch = 0; self.busy = False; self.stopped = False; self.worker_running = False; self.starting = False
        self.ticket = self.job = self.payload = self.report = None
        self.closed_since = None; self.launching = None; self.launch_file = None
        self.ops = self.vsd = self.board = None
        self.timer = self.reactor.register_timer(self._tick)
        for suffix, callback in [('CHECK',self.cmd_check), ('START',self.cmd_start),
                                 ('CANCEL',self.cmd_cancel), ('STATUS',self.cmd_status)]:
            self.gcode.register_command('INVENTURE_PREFLIGHT_'+suffix, callback,
                                        when_not_ready=(suffix == 'STATUS'))
        self.printer.register_event_handler('klippy:connect',self._connect)
        self.printer.register_event_handler('klippy:ready',self._ready)
        self.printer.register_event_handler('klippy:shutdown',self._shutdown)
        self.printer.register_event_handler('klippy:disconnect',self._shutdown)
        self.printer.register_event_handler('gcode:request_restart',self._shutdown)

    def _connect(self):
        self.ops = self.printer.lookup_object('inventure_operations')
        self.vsd = self.printer.lookup_object('virtual_sdcard')
        self.board = self.printer.lookup_object('inventure_mcu')
        if self.enabled:
            original = self.ops.begin
            def begin(kind):
                if kind == 'print':
                    if (self.launching is not getcurrent()
                            or self.vsd.current_file is not self.launch_file
                            or self.vsd.file_position != 0):
                        raise self.printer.command_error(
                            'P15: new print requires INVENTURE_PREFLIGHT_START with a current token')
                return original(kind)
            self.ops.begin = begin

    def _ready(self):
        if self.enabled:
            self.reactor.update_timer(self.timer,self.reactor.monotonic()+.1)

    def _shutdown(self,*args):
        self.stopped = True; self.epoch += 1; self.busy = False
        self.ticket = self.job = self.payload = None
        self.closed_since = None; self.launching = self.launch_file = None
        self.report = {'status':'STOPPED','reason':'Host shutdown/disconnect/restart'}
        self.reactor.update_timer(self.timer,self.reactor.NEVER)

    def _doors(self,now):
        b = self.board.get_status(now)
        raw = b.get('inputs',{}).get('raw',{})
        age = b.get('inputs_age')
        valid = (type(age) in (int,float) and 0. <= age <= .5
                 and raw.get('front_door') == 1 and raw.get('lid') == 1)
        if not valid:
            self.closed_since = None
        elif self.closed_since is None:
            self.closed_since = now
        return 0. if self.closed_since is None else max(0.,now-self.closed_since)

    def _tick(self,now):
        # Reactor eventtime may predate another callback that delivered input.
        # Evaluate freshness against a new monotonic reading, not that old tick.
        now = self.reactor.monotonic()
        if self.stopped:
            return self.reactor.NEVER
        self._doors(now)
        if self.ticket is not None and now >= self.ticket.deadline:
            self.ticket = None
            if self.report is not None:
                self.report = dict(self.report,status='EXPIRED')
        return now+.1

    def _status(self,name,now):
        obj = self.printer.lookup_object(name,None)
        if obj is None:
            return None
        value = obj.get_status(now)
        return value if isinstance(value,dict) else None

    def _snapshot(self):
        now = self.reactor.monotonic()
        s = {'host_session':self.session, 'p14_integration_qualified':self.integration_qualified, 'p16_required':True,
             'doors_closed_for':self._doors(now)}
        for key,name in [('board','inventure_mcu'),('io','inventure_io'),('operations','inventure_operations'),
                         ('thermal','inventure_thermal'),('fans','inventure_fans'),('calibration','inventure_calibration'),
                         ('materials','inventure_materials'),('rfid','inventure_rfid'),('filament','inventure_filament'),
                         ('consumption','inventure_consumption'),('interlocks','inventure_interlocks')]:
            s[key] = self._status(name,now)
        m = self.printer.lookup_object('inventure_materials',None)
        s['material_generation'] = None if m is None else m.generation
        th = self.printer.lookup_object('inventure_thermal',None)
        s['heater_targets'] = [] if th is None else [h.target_temp for h in th.heaters]
        s['thermal_error'] = None
        if th is None:
            s['thermal_error'] = 'Missing P05'
        else:
            try:
                th.require_ready()
                # Read-only equivalent of pre-print PCB check. before_print()
                # consumes warning acknowledgment and is left to P02 at start.
                from .inventure_thermal_core import pcb_preflight
                warning = pcb_preflight(th.model.channels[7].temperature)
                if warning == 197 or warning == 196 and not th.warning_ack:
                    s['thermal_error'] = 'PCB preflight #%d' % warning
            except Exception as exc:
                s['thermal_error'] = str(exc)
        fans = self.printer.lookup_object('inventure_fans',None)
        s['fan_error'] = None
        try:
            if fans is None:
                raise ValueError('Missing P06')
            fans.require_ready()
        except Exception as exc:
            s['fan_error'] = str(exc)
        tc = self.printer.lookup_object('inventure_toolchange',None)
        # enabled can only be true after its own config qualification checks.
        s['toolchange_ready'] = tc is not None and tc.enabled and not tc.running
        s['toolchange_routed'] = tc is not None and tc.route_t
        s['qualifications'] = {'start':self.start_qualified,'p14':self.integration_qualified}
        return s

    def _require_idle(self,gcmd):
        if not self.enabled or self.stopped:
            raise gcmd.error('P15 disabled/stopped')
        if self.busy or self.worker_running or self.starting:
            raise gcmd.error('P15 file inspection already running')
        if self.ops.state.owner is not None or self.ops.state.fatal is not None or self.vsd.is_active():
            raise gcmd.error('P15 requires idle printer without a fatal fault')

    def cmd_check(self,gcmd):
        self._require_idle(gcmd)
        manifest_name = gcmd.get('MANIFEST')
        spool_ack = gcmd.get('ACK','') == 'JOB_AND_SPOOL_SUPPLY_CHECKED'
        self.epoch += 1; epoch = self.epoch
        self.busy = self.worker_running = True; self.ticket = self.job = self.payload = None
        self.report = {'status':'CHECKING','manifest':manifest_name,'scope':'read_only'}
        root = self.vsd.sdcard_dirname
        def worker():
            try:
                meta = parse_json(read_regular(root,manifest_name,maximum=65536))
                job = Job.parse(meta); raw = read_regular(root,job.name,maximum=MAX_BYTES)
                scan = inspect_gcode(raw,job)
                result = (job,raw,scan,None)
            except Exception as exc:
                result = (None,None,None,str(exc))
            self.reactor.register_async_callback(lambda eventtime:self._complete(epoch,spool_ack,result))
        threading.Thread(target=worker,name='inventure-p15-file-inspection',daemon=True).start()
        gcmd.respond_info('P15 CHECKING: no movement, heating or RFID write was requested')

    def _complete(self,epoch,spool_ack,result):
        self.worker_running = False
        if self.stopped or epoch != self.epoch:
            return
        self.busy = False
        job,raw,scan,error = result
        if error is not None:
            self.report = {'status':'REJECTED','reason':error}
        else:
            try:
                snap = self._snapshot()
                checks = evaluate(job,snap,spool_ack)
                self.job,self.payload = job,raw
                self.report = {'status':'BLOCKED','file':job.name,'sha256':job.sha256,'scan':scan,
                               'checks':[asdict(c) for c in checks], 'scope':'software_preflight_not_hardware_certification'}
                if all(c.passed for c in checks):
                    if self.start_qualified:
                        self.ticket = Ticket(job,snap,self.reactor.monotonic(),self.ttl,spool_ack)
                        self.report['status'] = 'READY'
                    else:
                        self.report['checks'].append({'id':'START_QUALIFIED','passed':False,
                            'reason':'Start remains unqualified','module':'P15'})
            except Exception as exc:
                self.ticket = self.job = self.payload = None
                self.report = {'status':'REJECTED','reason':str(exc)}
        self.printer.send_event('inventure:preflight_status',self.get_status(self.reactor.monotonic()))

    def cmd_start(self,gcmd):
        self._require_idle(gcmd)
        if not self.start_qualified or not self.integration_qualified:
            raise gcmd.error('P15 start unqualified: actual P14 integration is required')
        if gcmd.get('ACK','') != 'START_CHECKED_JOB':
            raise gcmd.error('ACK=START_CHECKED_JOB required')
        ticket = self.ticket
        if ticket is None or self.job is None or self.payload is None:
            raise gcmd.error('P15 no ready job/token')
        try:
            ticket.consume(gcmd.get('TOKEN',''),self._snapshot(),self.reactor.monotonic())
        except PreflightError as exc:
            if ticket.used:
                self.ticket = None
            raise gcmd.error(str(exc))
        self.ticket = None
        # Local immutable references survive cancellation or a callback while
        # _reset_file yields. No concurrent CHECK/START may replace this source.
        job, payload, epoch = self.job, self.payload, self.epoch
        self.starting = True
        f = None
        try:
            self.vsd._reset_file()
            f = io.TextIOWrapper(MemoryFile(payload,job.name),encoding='utf-8',newline='')
            self.vsd.current_file = f
            self.vsd.file_position = 0; self.vsd.file_size = len(payload)
            self.vsd.print_stats.set_current_file(job.name)
            # _reset_file may yield. Recheck cancellation and live readiness.
            snap = self._snapshot()
            if (self.stopped or epoch != self.epoch
                    or context_fingerprint(snap) != ticket.context or any(
                        not c.passed for c in evaluate(ticket.job,snap,ticket.spool_ack))):
                raise gcmd.error('P15 context/readiness changed while preparing source')
            self.launching,self.launch_file = getcurrent(),f
            self.vsd.do_resume()  # P02.begin, native Klippy; no second planner
            self.report = dict(self.report,status='STARTED')
        except Exception:
            if f is not None and self.vsd.current_file is f and not self.vsd.is_active():
                f.close();self.vsd.current_file = None
                self.vsd.file_size = self.vsd.file_position = 0
            self.report = dict(self.report or {},status='START_FAILED')
            raise
        finally:
            self.starting = False
            self.launching = self.launch_file = None
            self.payload = None

    def cmd_cancel(self,gcmd):
        # Cancels P15 inspection/token, never impersonates CANCEL_PRINT.
        self.epoch += 1; self.busy = False
        self.ticket = self.job = self.payload = None
        self.report = {'status':'CANCELLED','reason':'Preflight only; an active print uses CANCEL_PRINT'}
        self.cmd_status(gcmd)

    def get_status(self,now):
        return {'version':VERSION,'enabled':self.enabled,'busy':self.busy,'starting':self.starting,'stopped':self.stopped,
                'status':None if self.report is None else dict(self.report),
                'token':None if self.ticket is None or self.ticket.used or now >= self.ticket.deadline else self.ticket.token,
                'token_expires_in':None if self.ticket is None else max(0.,self.ticket.deadline-now),
                'p14_integration_qualified':self.integration_qualified,'start_qualified':self.start_qualified,
                'hardware_tests_performed':False,'file_snapshot':'immutable_copy_max_16MiB',
                'automatic_loading':False,'automatic_homing':False,'automatic_heating':False}

    def cmd_status(self,gcmd):
        raw = json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True,separators=(',',':'))
        for i in range(0,len(raw),1800):
            gcmd.respond_info('INVENTURE_PREFLIGHT_PART %d/%d %s' % (i//1800+1,(len(raw)+1799)//1800,raw[i:i+1800]))

def load_config(config):
    return InventurePreflight(config)
