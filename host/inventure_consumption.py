# SPDX-License-Identifier: GPL-3.0-or-later
"""P14 reimplementation: conservative issue-side STEP accounting and jam latch.

This is a NEW RC implementation, not a recovered copy of earlier P14 source.
RFID readback is delegated exclusively to qualified P12. There is no background
hot-tag write and no unqualified jam-recovery motion.
"""
import json, math
from .inventure_consumption_core import Ledger, AccountingError, AtomicJournal, classify_recovery

class InventureConsumption:
    def __init__(self,config):
        self.printer=config.get_printer();self.reactor=self.printer.get_reactor();self.gcode=self.printer.lookup_object('gcode')
        self.enabled=config.getboolean('enabled',False)
        self.qualified=config.getboolean('hardware_qualified',False)
        self.accounting_qualified=config.getboolean('step_accounting_qualified',False)
        self.max_gap=config.getfloat('report_max_gap',.5,minval=.1,maxval=.5)
        self.clock_hz=None;self.stopped=False;self.storage_busy=False;self.last_event=None
        path=config.get('journal_path','~/printer_data/config/inventure_p14_ledger.json')
        self.journal=AtomicJournal(__import__('os').path.expanduser(path))
        try:self.ledger=self.journal.load()
        except AccountingError as exc:
            self.ledger=Ledger();self.ledger.error='P14 '+str(exc)+'; reconcile backup offline'
        self.ops=self.materials=self.filament=self.rfid=self.board=None
        self.timer=self.reactor.register_timer(self._tick)
        for name,fn in [('STATUS',self.cmd_status),('BIND',self.cmd_bind),('COMMIT',self.cmd_commit),('RECOVERY_PLAN',self.cmd_recovery)]:
            self.gcode.register_command('INVENTURE_CONSUMPTION_'+name,fn,when_not_ready=name=='STATUS')
        self.printer.register_event_handler('klippy:connect',self._connect)
        self.printer.register_event_handler('klippy:ready',self._ready)
        self.printer.register_event_handler('klippy:shutdown',self._shutdown)
        self.printer.register_event_handler('klippy:disconnect',self._shutdown)
        self.printer.register_event_handler('inventure:transport_report',self._report)
    def _connect(self):
        for k,name in [('ops','inventure_operations'),('materials','inventure_materials'),('filament','inventure_filament'),('rfid','inventure_rfid'),('board','inventure_mcu')]:
            setattr(self,k,self.printer.lookup_object(name,None))
        if self.enabled:
            if not self.qualified or not self.accounting_qualified:raise self.printer.config_error('P14 source/STEP hardware qualification required')
            if any(v is None for v in (self.ops,self.materials,self.filament,self.rfid,self.board)):
                raise self.printer.config_error('P14 requires P02 P11 P12 P13 and MCU')
            self.clock_hz=float(self.board.mcu.get_constant_float('CLOCK_FREQ'))
            if not 0<self.clock_hz<1e9:raise self.printer.config_error('P14 invalid MCU clock')
    def _ready(self):
        if self.enabled and not self.ledger.error:self.reactor.update_timer(self.timer,self.reactor.monotonic()+.1)
    def _shutdown(self,*args):
        self.stopped=True;self.reactor.update_timer(self.timer,self.reactor.NEVER)
    def _save(self):
        try:
            self.storage_busy=True;self.journal.save(self.ledger)
        except Exception as exc:
            self.ledger.error='STORAGE_WRITE_FAILED:'+str(exc)
            raise
        finally:self.storage_busy=False
    def _latch(self,why):
        self.ledger.error=why
        for c in self.ledger.channels:
            if c is not None:c.uncertain=True
        try:self._save()
        except Exception:pass
        owner=None if self.ops is None else self.ops.state.owner
        if owner is not None and self.ops.state.fatal is None:
            self.ops.fatal('P14 unsafe accounting: '+why,'FILAMENT_RUNTIME')
    def _report(self,channel,p):
        if not self.enabled or self.stopped or self.ledger.error:return
        if type(channel) is not int or channel not in (0,1):self._latch('INVALID_CHANNEL');return
        c=self.ledger.channels[channel]
        if c is None:return
        now=self.reactor.monotonic()
        try:
            if self.materials.pair is None or self.materials.pair[channel].source!=c.source or self.materials.pair[channel].material_id!=c.material_id:
                raise AccountingError('MATERIAL_SOURCE_CHANGED')
            if c.source=='cartridge':
                tag=self.rfid.channels[channel]['tag']
                if tag is None or tag.identity()!=c.identity or self.rfid.channels[channel]['uncertain']:
                    raise AccountingError('TAG_IDENTITY_CHANGED')
                if c.intent is None and tag.remaining_mm!=c.durable_anchor_mm:
                    raise AccountingError('RFID_DURABLE_BASE_CHANGED_OUTSIDE_P14')
            if p.get('fault'):
                self.ledger.jam={'tool':channel,'fault':p.get('fault'),'at':now,'cause':'P13_MCU_TRANSPORT_FAULT'}
                raise AccountingError('JAM_FAULT_%s'%p['fault'])
            f,r=self.ledger.account(channel,p,now,clock_hz=self.clock_hz,max_gap=self.max_gap)
            if f or r:
                self._save()
                self.last_event=dict(tool=channel,forward_steps=f,reverse_steps=r,at=now,
                                     accounting='issued_step_no_reverse_credit',physical_feedback_proven=False)
        except Exception as exc:self._latch(str(exc))
    def _tick(self,now):
        if self.stopped or not self.enabled:return self.reactor.NEVER
        try:
            for i,c in enumerate(self.ledger.channels):
                if c is None:continue
                if c.intent is not None:
                    tag=self.rfid.channels[i]['tag'];state=self.rfid.channels[i];b=state['balance']
                    if state['uncertain'] or state['error'] is not None and state['busy'] is None:
                        raise AccountingError('RFID_COMMIT_UNCERTAIN')
                    if state['busy'] is None and tag is not None and b is not None and tag.identity()==c.identity and b.durable_mm==c.intent['target_mm'] and state['last_write'] is not None and state['last_write'].get('status')=='durable_readback':
                        c.durable_anchor_mm=c.intent['target_mm'];c.intent=None;self.ledger.sequence+=1;self._save()
        except Exception as exc:self._latch(str(exc))
        return self.reactor.monotonic()+.1
    def _idle(self,gcmd):
        if not self.enabled or self.stopped or self.ledger.error:raise gcmd.error('P14 disabled/stopped/uncertain')
        if self.ops.state.owner is not None or self.ops.state.fatal is not None:raise gcmd.error('P14 needs idle printer')
        if self.materials.storage_busy or self.filament.running:raise gcmd.error('P14 conflicts with material write/service')
        self.filament.require_ready()
    def _cold(self,gcmd):
        self._idle(gcmd)
        self.rfid._idle(gcmd)
    def cmd_bind(self,gcmd):
        self._cold(gcmd)
        i=gcmd.get_int('TOOL',minval=0,maxval=1)
        if gcmd.get('ACK','')!='VERIFIED_CURRENT_SOURCE':raise gcmd.error('ACK=VERIFIED_CURRENT_SOURCE required')
        if self.materials.pair is None:raise gcmd.error('P11 material selection missing')
        sel=self.materials.pair[i];old=self.ledger.channels[i]
        if old is not None:raise gcmd.error('P14 existing source journal; never reset counters from a new BIND')
        if sel.source=='cartridge':
            if self.rfid.error_for(i,sel.material_id) is not None:raise gcmd.error('P12 identity not verified')
            balance = self.rfid.channels[i]['balance']
            if balance is None or balance.dirty:raise gcmd.error('P12 tag balance pending or not established')
            tag=self.rfid.channels[i]['tag'];identity=tag.identity();target=tag.remaining_mm
        else:
            if not self.materials.confirmed:raise gcmd.error('External spool needs P11 operator confirmation')
            identity='external:%d:%d'%(i,self.materials.generation);target=None
        self.ledger.bind(i,sel.source,sel.material_id,identity,target)
        try:self._save()
        except Exception:raise gcmd.error('P14 could not persist binding; unsafe')
        gcmd.respond_info('P14 T%d bound. First transport report is baseline only.'%i)
    def cmd_commit(self,gcmd):
        self._cold(gcmd)
        if gcmd.get('ACK','')!='SAVE_TAG_BALANCE':raise gcmd.error('ACK=SAVE_TAG_BALANCE required')
        i=gcmd.get_int('TOOL',minval=0,maxval=1);c=self.ledger.channels[i]
        if c is None or c.source!='cartridge' or c.uncertain or c.intent is not None:raise gcmd.error('P14 cartridge ledger not clean')
        if not self.rfid.allow_writes or self.rfid.error_for(i,c.material_id) is not None:raise gcmd.error('P12 not qualified for tag write')
        state=self.rfid.channels[i];tag=state['tag'];b=state['balance']
        if tag.identity()!=c.identity or b is None or b.dirty or b.durable_mm!=tag.remaining_mm or b.durable_mm!=c.durable_anchor_mm:raise gcmd.error('P12 pending/external ledger or identity mismatch')
        delta=b.durable_mm-c.target_mm
        if delta<0 or not -2**31<delta<2**31:raise gcmd.error('P14 invalid or crediting delta')
        if not delta:
            gcmd.respond_info('P14 no whole millimeters to commit');return
        # The P14 journal intention must be durable BEFORE any RFID block8 write.
        c.intent={'target_mm':c.target_mm,'old_mm':b.durable_mm,'delta_mm':delta,'identity':c.identity}
        self.ledger.sequence+=1
        try:self._save()
        except Exception:raise gcmd.error('P14 intent persistence failed; RFID untouched')
        try:
            b.consume(delta)
            lease=self.ops.begin('rfid_write')
            self.rfid.start(i,'write',lease)
        except Exception as exc:
            self._latch('RFID_COMMIT_LAUNCH_UNKNOWN:'+str(exc))
            raise gcmd.error('P14 uncertain RFID start; do not retry blindly')
        gcmd.respond_info('P14 tag write pending. Wait for P12 full readback before new print.')
    def cmd_recovery(self,gcmd):
        now=self.reactor.monotonic()
        op={} if self.ops is None else self.ops.get_status(now)
        i=gcmd.get_int('TOOL',minval=0,maxval=1)
        p={} if self.filament is None else self.filament.get_status(now)
        r=(p.get('channels') or [None,None])[i] or {}
        d=classify_recovery(jam=self.ledger.jam,paused=op.get('mode')=='paused',material_present=r.get('a')==1,
            pulses=0,starts=0,physical_safe=False,attempts=0)
        gcmd.respond_info('P14 recovery diagnostic only; no R1/R2 mechanical backend authorized: '+json.dumps(d.__dict__))
    def get_status(self,now):
        channels=[]
        for c in self.ledger.channels:
            if c is None:channels.append(None);continue
            d=c.as_dict()
            d['report_current']=c.last_timestamp is not None and 0<=now-c.last_timestamp<=self.max_gap
            channels.append(d)
        active=bool(self.enabled and self.qualified and self.accounting_qualified and not self.stopped
            and self.ledger.error is None and self.ledger.jam is None and all(c is not None and not c.uncertain
            and c.intent is None and c.last_timestamp is not None and 0<=now-c.last_timestamp<=self.max_gap
            for c in self.ledger.channels))
        return dict(enabled=self.enabled,stopped=self.stopped,automatic_step_accounting=active,
            hardware_qualified=self.qualified,error=self.ledger.error,storage_busy=self.storage_busy,jam=self.ledger.jam,
            sequence=self.ledger.sequence,channels=channels,latest_event=self.last_event,
            recovery_motion_implemented=False,physical_filament_travel_verified=False,source_version='P14_RECONSTRUCTED_RC2')
    def cmd_status(self,gcmd):gcmd.respond_info('INVENTURE_CONSUMPTION '+json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True))

def load_config(config):return InventureConsumption(config)
