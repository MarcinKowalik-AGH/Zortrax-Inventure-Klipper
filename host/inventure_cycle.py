# SPDX-License-Identifier: GPL-3.0-or-later
"""P17 hosted policy on top of existing P02 virtual SD / pause_resume.

No competing motion planner, unqualified Z park, silent RFID write or automatic
restart. Legacy PAUSE/CANCEL_PRINT allowed through P02, RESUME requires an
explicit P17 recheck when this module is enabled.
"""
import json
from .inventure_cycle_core import Cycle,CycleError,Phase,Reason

class InventureCycle:
    def __init__(self,config):
        self.printer=config.get_printer();self.reactor=self.printer.get_reactor();self.gcode=self.printer.lookup_object('gcode')
        self.enabled=config.getboolean('enabled',False)
        self.qualified=config.getboolean('hardware_qualified',False)
        self.stopped=False;self.cycle=Cycle();self.ops=self.p14=self.p15=self.p16=self.vsd=None
        self._allow_resume=False;self._allow_cancel=False;self._wrapping=False
        self.timer=self.reactor.register_timer(self._tick)
        for k,fn in [('STATUS',self.cmd_status),('PAUSE',self.cmd_pause),('RESUME',self.cmd_resume),('CANCEL',self.cmd_cancel)]:
            self.gcode.register_command('INVENTURE_CYCLE_'+k,fn,when_not_ready=(k=='STATUS'))
        self.printer.register_event_handler('klippy:connect',self._connect)
        self.printer.register_event_handler('klippy:ready',self._ready)
        self.printer.register_event_handler('klippy:shutdown',self._shutdown)
        self.printer.register_event_handler('klippy:disconnect',self._shutdown)
    def _connect(self):
        self.ops=self.printer.lookup_object('inventure_operations')
        self.vsd=self.printer.lookup_object('virtual_sdcard')
        for attr,name in [('p14','inventure_consumption'),('p15','inventure_preflight'),('p16','inventure_interlocks')]:
            setattr(self,attr,self.printer.lookup_object(name,None))
        if self.enabled:
            if not self.qualified or any(x is None for x in (self.p14,self.p15,self.p16)):
                raise self.printer.config_error('P17 requires qualified P14/P15/P16 adapters')
            previous=self.ops._dispatch
            def guarded(name,handler,original,parsed):
                if name=='RESUME':
                    if not self._allow_resume:raise parsed.error('P17 requires INVENTURE_CYCLE_RESUME and fresh checks')
                    self._allow_resume=False  # one-shot admission, even for nested macros
                    self._resume_checks()
                if name in ('PAUSE','M25'):
                    self._capture_pause('operator')
                if name in ('CANCEL_PRINT','SDCARD_RESET_FILE'):
                    self._capture_cancel()
                try:
                    result=previous(name,handler,original,parsed)
                except Exception:
                    if name=='RESUME':self.cycle.resume_failed()
                    elif name in ('PAUSE','M25') and self.cycle.phase==Phase.PAUSED:
                        # P02 did not acknowledge a completed pause; fail closed.
                        self.cycle.phase=Phase.FAULT;self.cycle.result='PAUSE_FAILED'
                    raise
                if name=='RESUME':self.cycle.resumed()
                if name in ('CANCEL_PRINT','SDCARD_RESET_FILE') and self.cycle.phase==Phase.CANCELLING:self.cycle.cancelled()
                return result
            self.ops._dispatch=guarded
    def _ready(self):
        if self.enabled:self.reactor.update_timer(self.timer,self.reactor.monotonic()+.1)
    def _shutdown(self,*a):
        self.stopped=True
        self.cycle.phase=Phase.FAULT;self.cycle.reason=Reason.FAULT;self.cycle.result='HOST_DISCONNECTED'
        self.reactor.update_timer(self.timer,self.reactor.NEVER)
    def _sync(self):
        if self.ops is None:return
        state=self.ops.state;lease=state.owner
        before=self.cycle.phase
        self.cycle.sync(None if lease is None else str(lease.kind),str(state.mode),
                        None if lease is None else lease.epoch,state.fatal,str(state.result))
        if (self.enabled and self.cycle.phase==Phase.PAUSED and self.cycle.snapshot is None
                and lease is not None and str(lease.kind)=='print' and str(state.mode)=='paused'):
            # Native SD/tool-generated pause did not travel through G-code PAUSE.
            # Capture current session context only after P02 has accepted pause.
            self.cycle.snapshot=self._capture();self.cycle.reason=Reason.SOURCE
        if self.enabled and self.cycle.phase==Phase.FAULT and before!=Phase.FAULT and state.fatal is None:
            self.ops.fatal('P17 detected print lifecycle bypass/lost lease','PROCEDURE_FAULT')
    def _tick(self,now):
        if self.stopped:return self.reactor.NEVER
        self._sync()
        return self.reactor.monotonic()+.1
    def _capture(self):
        # Never serialize the actual file object or pretend to persist physical XYZ.
        state=self.ops.state;now=self.reactor.monotonic()
        job=None if self.vsd is None else self.vsd.current_file
        mat=self.printer.lookup_object('inventure_materials',None)
        cal=self.printer.lookup_object('inventure_calibration',None)
        return dict(job_id=None if job is None else id(job),file_name=None if job is None else getattr(job,'name',None),
                    file_position=None if self.vsd is None else self.vsd.file_position,
                    epoch=state.epoch,position_epoch=state.position_epoch,
                    material_generation=None if mat is None else mat.generation,
                    calibration_revision=None if cal is None else cal.get_status(now).get('revision'),
                    physical_position_verified=False)
    def _capture_pause(self,reason):
        self._sync();lease=self.ops.state.owner
        if lease is None or str(lease.kind)!='print' or str(self.ops.state.mode)!='running':
            raise self.printer.command_error('P17: no running print to pause')
        self.cycle.pause(lease.epoch,reason,self._capture())
    def _capture_cancel(self):
        self._sync();lease=self.ops.state.owner
        if lease is not None and str(lease.kind)=='print' and self.cycle.phase in (Phase.RUNNING,Phase.PAUSED,Phase.RESUMING):
            self.cycle.cancel(lease.epoch)
    def _resume_checks(self):
        self._sync();lease=self.ops.state.owner
        if lease is None or str(lease.kind)!='print' or str(self.ops.state.mode)!='paused':
            raise self.printer.command_error('P17 resume requires P02 paused print lease')
        snap=self.cycle.snapshot
        if snap is None:raise self.printer.command_error('P17 no pause snapshot from this print; restart print')
        now=self.reactor.monotonic();checks={}
        checks['P02_POSITION_EPOCH']=snap.get('position_epoch')==self.ops.state.position_epoch
        checks['P15_FROZEN_JOB']=self.vsd.current_file is not None and id(self.vsd.current_file)==snap.get('job_id')
        mat=self.printer.lookup_object('inventure_materials',None)
        checks['P11_MATERIAL_REVISION']=mat is not None and mat.generation==snap['material_generation']
        cal=self.printer.lookup_object('inventure_calibration',None)
        checks['P10_CALIBRATION_REVISION']=cal is not None and cal.get_status(now).get('revision')==snap['calibration_revision']
        try:self.p16.require_ready();checks['P16_INTERLOCKS']=True
        except Exception:checks['P16_INTERLOCKS']=False
        try:self.printer.lookup_object('inventure_filament').require_print_ready();checks['P13_TRANSPORT']=True
        except Exception:checks['P13_TRANSPORT']=False
        try:
            therm=self.printer.lookup_object('inventure_thermal')
            therm.require_ready()
            status=therm.get_status(now)
            checks['P05_THERMAL']=status.get('pcb_pause') is None and status.get('fatal') is None
        except Exception:checks['P05_THERMAL']=False
        try:self.printer.lookup_object('inventure_fans').require_ready();checks['P06_FANS']=True
        except Exception:checks['P06_FANS']=False
        l=self.p14.get_status(now)
        checks['P14_ACCOUNTING']=bool(l['automatic_step_accounting'] and not l['stopped'] and not l['error'] and not l['jam'] and not l['storage_busy']
            and all(c is not None and not c['uncertain'] and c['intent'] is None and c['report_current'] for c in l['channels']))
        rf=self.printer.lookup_object('inventure_rfid',None)
        checks['P12_RFID']=bool(rf is not None and mat is not None and mat.pair is not None and all(
            sel.source=='spool' or rf.error_for(i,sel.material_id) is None for i,sel in enumerate(mat.pair)))
        try:self.cycle.resume(lease.epoch,snap['job_id'],checks)
        except CycleError as exc:raise self.printer.command_error('P17 '+str(exc))
    def cmd_pause(self,gcmd):
        if not self.enabled or self.stopped:raise gcmd.error('P17 disabled')
        if gcmd.get('ACK','')!='PAUSE_PRINT':raise gcmd.error('ACK=PAUSE_PRINT required')
        # Dispatch through Klipper P02, never mutate SD state directly.
        self.gcode.run_script_from_command('PAUSE')
    def cmd_resume(self,gcmd):
        if not self.enabled or self.stopped:raise gcmd.error('P17 disabled')
        if gcmd.get('ACK','')!='CHECKED_CURRENT_STATE':raise gcmd.error('ACK=CHECKED_CURRENT_STATE required')
        self._allow_resume=True
        try:self.gcode.run_script_from_command('RESUME')
        finally:self._allow_resume=False
    def cmd_cancel(self,gcmd):
        if not self.enabled or self.stopped:raise gcmd.error('P17 disabled')
        if gcmd.get('ACK','')!='CONFIRM_STOP':raise gcmd.error('ACK=CONFIRM_STOP required')
        self.gcode.run_script_from_command('CANCEL_PRINT')
    def get_status(self,now):
        self._sync()
        return dict(self.cycle.to_status(),enabled=self.enabled,hardware_qualified=self.qualified,
            backend='P02_native_pause_resume',stock_purge_resume_sequence_implemented=False,
            automatic_resume=False,checkpoint_survives_reboot=False,physical_hardware_tested=False,
            host_connected=not self.stopped)
    def cmd_status(self,gcmd):gcmd.respond_info('INVENTURE_CYCLE '+json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True))

def load_config(config):return InventureCycle(config)
