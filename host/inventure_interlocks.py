# SPDX-License-Identifier: GPL-3.0-or-later
"""P16 runtime global interlock, conservative fail-closed host integration.

This cannot establish physical lock position: the available two bits encode
commanded state only. No new GPIO/PWM ownership and no actuator operation.
"""
import json
from .inventure_interlocks_core import EXPECTED, evaluate

class InventureInterlocks:
    def __init__(self,config):
        self.printer=config.get_printer(); self.reactor=self.printer.get_reactor()
        self.gcode=self.printer.lookup_object('gcode')
        self.enabled=config.getboolean('enabled',False)
        self.qualified=config.getboolean('hardware_qualified',False)
        self.stable_time=config.getfloat('stable_time',.55,minval=.5,maxval=3.)
        self.max_age=config.getfloat('max_age',.5,above=0.,maxval=.5)
        self.fatal_on_active_loss=config.getboolean('fatal_on_active_loss',True)
        self.board=self.ops=self.filament=None
        self.closed_since=None; self.last=None;self.latch=None;self.stopped=False
        self.timer=self.reactor.register_timer(self._tick)
        self.gcode.register_command('INVENTURE_INTERLOCKS_STATUS',self.cmd_status,when_not_ready=True)
        self.gcode.register_command('INVENTURE_INTERLOCKS_CHECK',self.cmd_check)
        self.printer.register_event_handler('klippy:connect',self._connect)
        self.printer.register_event_handler('klippy:ready',self._ready)
        for event in ('klippy:shutdown','klippy:disconnect','gcode:request_restart'):
            self.printer.register_event_handler(event,self._shutdown)
    def _connect(self):
        self.board=self.printer.lookup_object('inventure_mcu')
        self.ops=self.printer.lookup_object('inventure_operations')
        self.filament=self.printer.lookup_object('inventure_filament',None)
        if self.enabled:
            previous=self.ops.begin
            def guarded_begin(kind):
                self.require_ready(require_lock=(kind == 'print'))
                return previous(kind)
            self.ops.begin=guarded_begin
    def _ready(self):
        if self.enabled:self.reactor.update_timer(self.timer,self.reactor.monotonic()+.05)
    def _shutdown(self,*args):
        self.stopped=True;self.closed_since=None;self.latch=self.latch or 'HOST_SHUTDOWN'
        self.reactor.update_timer(self.timer,self.reactor.NEVER)
    def _sample(self, require_lock=True):
        now=self.reactor.monotonic()
        b=self.board.get_status(now)
        raw=b.get('inputs',{}).get('raw',{})
        age=b.get('inputs_age',99.)
        fresh=(b.get('connected') and b.get('armed') and not b.get('fault')
               and type(age) in (int,float) and 0<=age<=self.max_age
               and b.get('status_age',99.)<=self.max_age and
               all(raw.get(k)==v for k,v in EXPECTED.items()))
        if fresh:
            if self.closed_since is None:self.closed_since=now
        else:self.closed_since=None
        stable=0. if self.closed_since is None else max(0.,now-self.closed_since)
        # P13 transport report is source of lock *command* bits, not an extra sensor.
        lockmask=None
        if self.filament is not None:
            try:
                a=self.filament._sample(0); c=self.filament._sample(1)
                if a['locks']==c['locks']:lockmask=a['locks']
            except Exception:pass
        e=evaluate(raw,age,bool(b.get('connected')),bool(b.get('armed')),
                   b.get('fault'),b.get('status_age',99.),stable,lockmask,
                   max_age=self.max_age,min_stable=self.stable_time,
                   require_lock=require_lock)
        self.last={'enabled':self.enabled,'hardware_qualified':self.qualified,
                   'ready':e.permitted and self.enabled and self.qualified and not self.latch,
                   'reasons':list(e.reasons),'latch':self.latch,
                   'stable_seconds':e.stable_seconds,
                   'lock_command_bits':lockmask,
                   'lock_command_confirmed':e.confirmed_lock_command,
                   'lock_required':require_lock,
                   'physical_lock_position_verified':False,
                   'snapshot_age':age,'timestamp':now}
        return self.last
    def require_ready(self, require_lock=True):
        if not self.enabled or not self.qualified:raise self.printer.command_error('P16 disabled/unqualified')
        s=self._sample(require_lock=require_lock)
        if not s['ready']:raise self.printer.command_error('P16 interlock: '+str(s['reasons'])+' latch='+str(s['latch']))
    def _tick(self,now):
        if self.stopped:return self.reactor.NEVER
        try:
            owner=self.ops.state.owner if self.ops is not None else None
            s=self._sample(require_lock=(owner is not None and owner.kind == 'print'))
            active=owner is not None and self.ops.state.mode=='running'
            if active and not s['ready'] and self.latch is None:
                self.latch='INTERLOCK_LOST:'+','.join(s['reasons'])
                if self.fatal_on_active_loss:
                    # Use P02 fatal to invalidate XYZ and invoke Klipper shutdown.
                    self.ops.fatal(self.latch,'P16_GLOBAL_INTERLOCK')
        except Exception as exc:
            if self.ops is not None and self.ops.state.owner is not None and self.latch is None:
                self.latch='INTERLOCK_EXCEPTION:'+type(exc).__name__
                self.ops.fatal(self.latch,'P16_GLOBAL_INTERLOCK')
        return self.reactor.monotonic()+.05
    def get_status(self,now):
        if self.last is None and self.board is not None:self._sample()
        return dict(self.last or {'enabled':self.enabled,'hardware_qualified':self.qualified,
                 'ready':False,'reasons':['NO_REPORT'],'latch':self.latch},
                 hardware_tests_performed=False)
    def cmd_check(self,gcmd):
        self.require_ready();gcmd.respond_info('P16_INTERLOCKS_READY')
    def cmd_status(self,gcmd):
        gcmd.respond_info('P16_INTERLOCKS '+json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True))

def load_config(config):return InventureInterlocks(config)
