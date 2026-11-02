# SPDX-License-Identifier: GPL-3.0-or-later
"""P13 owned cartridge/spool procedures and MCU feeder transport supervision.

Native Klipper plans every E move. Sensor reports are observations, while
planned E and MCU-issued STEP counters never attest measured material travel.
"""
import collections,json,math,secrets
from greenlet import getcurrent
from .inventure_filament_core import (STOP,AUTO,FORWARD,REVERSE,GATED,ASSIST,
    SPOOL_SPEED,profile,service_point,delta32,l1_decision,needs_assist,PulseWindow)
from .inventure_toolchange_core import purge_segments,FINAL_X
from .inventure_tools_core import in_box

REPORT='inventure_transport_status clock=%u channel=%c live=%c a=%c b=%c pulses=%u last_pulse_clock=%u starts=%u mode=%c running=%c direction=%c retry=%c fault=%c budget=%i forward=%u reverse=%u assist=%c locks=%c'
COMMANDS=dict(profile='inventure_transport_profile channel=%c cartridge=%c duration_ms=%hu budget_steps=%u',
    lease='inventure_transport_lease acknowledge=%u',mode='inventure_transport_mode channel=%c mode=%c monitor=%c',
    select='inventure_transport_select channel=%c',assist='inventure_transport_assist channel=%c clock=%u duration_hi=%u duration_lo=%u',query='inventure_transport_query')
class Cancelled(Exception):pass

class InventureFilament:
    def __init__(self,config):
        self.printer=config.get_printer();self.reactor=self.printer.get_reactor();self.gcode=self.printer.lookup_object('gcode')
        self.board=self.printer.load_object(config,'inventure_mcu');self.mcu=self.board.mcu
        self.enabled=config.getboolean('enabled',False)
        self.qualifications={f:config.getboolean(f,False) for f in ('feeder_electrics_qualified','sensors_qualified','raw_speeds_qualified','service_geometry_qualified')}
        self.timeout=config.getfloat('procedure_timeout',900.,minval=30.,maxval=3600.)
        self.prompt_timeout=config.getfloat('prompt_timeout',120.,minval=1.,maxval=1800.)
        self.identity_timeout=config.getfloat('identity_verify_timeout',30.,minval=1.,maxval=120.)
        self.load_speed=config.getfloat('load_raw_speed',3.,above=0.,maxval=10.)
        self.spool_speed=config.getfloat('spool_speed',SPOOL_SPEED,above=0.,maxval=10.)
        self.purge_speed=config.getfloat('purge_speed',20.,above=0.,maxval=50.)
        self.chunk=config.getfloat('raw_chunk_mm',.5,minval=.05,maxval=3.)
        self.delay_speeds={n:config.getfloat('unload_speed_d%d'%n,1.,above=0.,maxval=10.) for n in (30,40,100,150,200,300)}
        if not all(math.isfinite(x) for x in (self.timeout,self.prompt_timeout,self.identity_timeout,self.load_speed,self.spool_speed,self.purge_speed,self.chunk,*self.delay_speeds.values())):
            raise config.error('P13 requires finite time, distance and speed settings')
        self.running=self.stopped=self.configuring=self.assisting=False
        self.caller=self.assist_caller=self.lease=None;self.phase='idle';self.error=self.result=None
        self.cancel_reason=self.prompt=self.done=None;self.job=0;self.prompt_sequence=0;self.session_nonce=secrets.token_hex(16)
        self.expected_targets=None;self.closed_required=self.service_permit=False
        self.history=collections.deque(maxlen=256);self.trace=collections.deque(maxlen=4096)
        self.reports=[None,None];self.received=[None,None];self.pulse_ages=[99.,99.];self.generation=0;self.rejected=0;self.commands={}
        self.configured_generation=None;self.modes=[None,None];self.selected=None;self.loaded=[None,None]
        self.last_buttons=None;self.jog_until=0.;self.ops=self.tools=self.th=self.gm=self.materials=self.thermal=self.fans=None
        self.timer=self.reactor.register_timer(self._tick)
        for ev,fn in [('klippy:connect',self._connect),('klippy:ready',self._ready),('klippy:shutdown',self._shutdown),('inventure:buttons',self._buttons)]:self.printer.register_event_handler(ev,fn)
        for n,fn in [('LOAD',self.cmd_load),('UNLOAD',self.cmd_unload),('CONTINUE',self.cmd_continue),('JOG',self.cmd_jog),('CANCEL',self.cmd_cancel),('WAIT',self.cmd_wait),('STATUS',self.cmd_status)]:
            self.gcode.register_command('INVENTURE_FILAMENT_'+n,fn,when_not_ready=n=='STATUS')
        if self.enabled:
            for key,value in self.qualifications.items():
                if not value:raise config.error('P13 requires '+key)
            self.mcu.register_config_callback(self._build_config)
    def _build_config(self):
        if float(self.mcu.get_constants().get('INVENTURE_TRANSPORT_ABI',0))!=1 or not self.mcu.check_valid_response(REPORT):raise self.printer.config_error('P13 transport ABI1/status mismatch')
        self.commands={k:self.mcu.lookup_command(v) for k,v in COMMANDS.items()}
        self.mcu.register_serial_response(self._receive,REPORT)
    def _connect(self):
        if not self.enabled:return
        for attr,name in [('ops','inventure_operations'),('tools','inventure_tools'),('th','toolhead'),('gm','gcode_move'),('materials','inventure_materials'),('thermal','inventure_thermal'),('fans','inventure_fans')]:setattr(self,attr,self.printer.lookup_object(name))
        if not self.tools.enabled or not self.materials.enabled or not self.fans.process_qualified:raise self.printer.config_error('P13 requires qualified P04, enabled P11 and qualified process fan')
        self.heaters=self.thermal.heaters
        for h in self.heaters:
            original=h.set_temp
            def set_temp(value,original=original):
                if value>0 and self.running and not self.in_scope():raise self.printer.command_error('P13 owns positive heater targets')
                return original(value)
            h.set_temp=set_temp
        original_move=self.th.move
        def move(position,speed):
            if self.running and not self.in_scope() or self.assisting and getcurrent() is not self.assist_caller:raise self.printer.command_error('P13 owns the motion caller')
            before=list(self.th.get_position())
            if before[3]==position[3]:return original_move(position,speed)
            self.require_ready();lease=self.ops.current_lease()
            if lease is None:raise self.printer.command_error('P13 E requires an owned operation')
            self.tools.require_binding();tool=self.ops.state.selector_tool;self._sync_tool(tool)
            if self.materials.pair[tool].source!='cartridge' or not needs_assist(before,position):return original_move(position,speed)
            self.assisting=True;self.assist_caller=getcurrent()
            try:
                start=self.th.get_last_move_time();result=original_move(position,speed);end=self.th.get_last_move_time()
                self.ops._core(self.ops.state.check,lease)
                clock=self.mcu.print_time_to_clock(start);duration=self.mcu.print_time_to_clock(end)-clock
                if duration<=0:raise self.printer.command_error('P13 invalid native retract duration')
                self.commands['assist'].send([tool,clock&0xffffffff,duration>>32,duration&0xffffffff],reqclock=clock)
                self.trace.append(dict(event='scheduled_assist',tool=tool,start_clock=clock,duration_ticks=duration))
                self.th.wait_moves();self.ops._core(self.ops.state.check,lease);return result
            finally:self.assisting=False;self.assist_caller=None;self.modes[tool]=None
        self.th.move=move
        original_begin=self.ops.begin
        def begin(kind):
            if kind=='print':self.require_print_ready()
            return original_begin(kind)
        self.ops.begin=begin
    def _ready(self):
        if self.enabled:self.reactor.update_timer(self.timer,self.reactor.monotonic()+.05)
    def _shutdown(self):
        self.stopped=True;self.generation+=1;self.prompt=None;self.service_permit=False;self.loaded=[None,None]
        self.reactor.update_timer(self.timer,self.reactor.NEVER)
    def in_scope(self):return self.running and getcurrent() is self.caller and self.ops.current_lease()==self.lease
    def identity_diagnostic_permitted(self,channel):
        return bool(self.enabled and not self.stopped and self.running and self.action=='load' and self.tool==channel
            and self.pair[channel].source=='cartridge' and self.phase in ('prepare','insert_cartridge','verify_cartridge')
            and (not self.closed_required or self.phase=='verify_cartridge') and not self.service_permit and not any(h.target_temp for h in self.heaters))
    def service_identity_allowed(self):
        if not self.service_permit or not self.in_scope() or self.lease.kind not in ('load','unload'):return False
        rfid=self.printer.lookup_object('inventure_rfid',None)
        return all(s.source=='spool' or rfid is not None and rfid.error_for(t,s.material_id) is None for t,s in enumerate(self.pair))
    def _receive(self,params):
        payload=dict(params);received=self.reactor.monotonic();generation=self.generation
        self.reactor.register_async_callback(lambda now:self._deliver(payload,received,generation,now))
    def _deliver(self,p,received,generation,now):
        if self.stopped or generation!=self.generation or now-received>.5:self.rejected+=1;return
        try:
            p={k:p[k] for k in ('clock','channel','live','a','b','pulses','last_pulse_clock','starts','mode','running','direction','retry','fault','budget','forward','reverse','assist','locks')}
            if any(type(v) is not int for v in p.values()):raise ValueError()
            i=p['channel']
            if i not in (0,1) or any(p[k] not in (0,1) for k in ('live','a','b','running','direction','assist')) or not 0<=p['mode']<=ASSIST or not 0<=p['locks']<=3:raise ValueError()
            if any(not 0<=p[k]<=0xffffffff for k in ('clock','pulses','last_pulse_clock','starts','forward','reverse')) or not -0x80000000<=p['budget']<=0x7fffffff or any(not 0<=p[k]<=255 for k in ('retry','fault')):raise ValueError()
            old=self.reports[i]
            if old is not None and not 0<delta32(p['clock'],old['clock'])<0x80000000:raise ValueError()
        except (ValueError,KeyError,TypeError):self.rejected+=1;return
        rate=self.mcu.get_constant_float('CLOCK_FREQ')
        if old is None or p['pulses']!=old['pulses']:
            self.pulse_ages[i]=((p['clock']-p['last_pulse_clock'])&0xffffffff)/rate if p['pulses'] or p['last_pulse_clock'] else 99.
        else:self.pulse_ages[i]+=delta32(p['clock'],old['clock'])/rate
        self.reports[i]=p;self.received[i]=received;self.printer.send_event('inventure:transport_report',i,dict(p))
    def _configure(self):
        if self.configuring or self.running or self.assisting or self.materials.pair is None:return
        pair=self.materials.pair;generation=self.materials.generation;self.configuring=True
        try:
            for i,s in enumerate(pair):
                p=profile(s.material_id);self.commands['profile'].send_wait_ack([i,int(s.source=='cartridge'),p['duration'],p['budget']]);self.modes[i]=(STOP,0)
            if pair==self.materials.pair and generation==self.materials.generation:self.configured_generation=generation;self.loaded=[None,None]
        finally:self.configuring=False
    def _mode(self,mode,tool=None,monitor=False):
        i=self.tool if tool is None else tool;value=(mode,int(monitor))
        if self.modes[i]!=value:self.commands['mode'].send_wait_ack([i,*value]);self.modes[i]=value
    def _stop(self):
        for i in (0,1):self.board.commands['feeder'].send_wait_ack([i,0,0]);self.modes[i]=None
    def _sync_tool(self,tool):
        if self.selected!=tool:
            self.th.wait_moves();self.tools.require_binding();self.commands['select'].send_wait_ack([tool]);self.selected=tool
    def _sample(self,i):
        now=self.reactor.monotonic();r=self.reports[i];age=99. if self.received[i] is None else now-self.received[i]
        if r is None or age>.5 or not r['live'] or r['fault']:
            owner=self.ops.state.owner
            if (self.running or owner is not None and owner.kind=='print') and self.ops.state.fatal is None:self.ops.fatal('P13 stale/inactive/faulted transport','FILAMENT_RUNTIME')
            raise self.printer.command_error('P13 stale/inactive/faulted transport')
        return r
    def _pulse_age(self,i):
        self._sample(i)
        return self.pulse_ages[i]+max(0.,self.reactor.monotonic()-self.received[i])
    def require_ready(self):
        if not self.enabled or self.stopped or self.ops.state.fatal is not None:raise self.printer.command_error('P13 disabled/stopped/fatal')
        if self.materials.pair is None or self.configured_generation!=self.materials.generation:raise self.printer.command_error('P13 material transport profile not current')
        b=self.board.get_status(self.reactor.monotonic())
        if not b.get('connected') or not b.get('armed') or b.get('fault') or b.get('status_age',99.)>.5:raise self.printer.command_error('P13 needs fresh armed board')
        for i in (0,1):self._sample(i)
    def require_print_ready(self):
        self.require_ready();self.materials.require_ready()
        # P15 source-aware correction: external spools do not use cassette A/B.
        # Keep MCU freshness/fault guards for both channels and operator proof.
        for i, selection in enumerate(self.materials.pair):
            report = self._sample(i)
            if selection.source == 'cartridge':
                if not report['a']:
                    raise self.printer.command_error('P13 cartridge print requires material presence A on T%d' % i)
            else:
                if not self.materials.confirmed:
                    raise self.printer.command_error('P13 external spool requires current loaded confirmation')
                if report['mode'] != STOP or report['running'] or report['assist']:
                    raise self.printer.command_error('P13 external spool requires helper feeder OFF')
    def _tick(self,now):
        if self.stopped:return self.reactor.NEVER
        try:
            if not self.board.connected:return now+.1
            b=self.board.get_status(now)
            if b.get('armed') and not b.get('fault') and b.get('status_age',99.)<=.5:
                if self.configured_generation!=self.materials.generation:self._configure()
                self.commands['lease'].send([0x5031334c])
                if self.configured_generation==self.materials.generation and self.materials.pair is not None and not self.running and not self.assisting:
                    owner=self.ops.state.owner
                    active=owner is not None and self.ops.state.mode=='running' and (owner.kind=='print' or owner.kind in ('toolchange','alignment','calibration') and any(h.target_temp for h in self.heaters[:2]))
                    for i,s in enumerate(self.materials.pair):self._mode(AUTO if active and s.source=='cartridge' else STOP,i,active)
                if self.jog_until and now>=self.jog_until:self._mode(STOP);self.jog_until=0.
            self.commands['query'].send([])
            owner=self.ops.state.owner
            if self.running or owner is not None and owner.kind=='print':self.require_ready()
            if self.running and self.reactor.monotonic()>=self.deadline:self.ops.fatal('P13 procedure deadline expired','FILAMENT_RUNTIME')
        except self.printer.command_error as exc:
            self.error=str(exc)
            if (self.running or self.ops.state.owner is not None and self.ops.state.owner.kind=='print') and self.ops.state.fatal is None:self.ops.fatal(str(exc),'FILAMENT_RUNTIME')
        return self.reactor.monotonic()+.1 if not self.stopped else self.reactor.NEVER
    def _guard(self):
        if self.cancel_reason:raise Cancelled(self.cancel_reason)
        self.ops._core(self.ops.state.check,self.lease)
        if not self.in_scope():raise self.printer.command_error('P13 foreign procedure caller')
        self.require_ready();self.ops.require_motion();self.ops._require_print_reference();self.thermal.require_ready();self.fans.require_ready()
        if self.reactor.monotonic()>=self.deadline:raise self.printer.command_error('P13 procedure deadline expired')
        if self.materials.pair!=self.pair or self.materials.generation!=self.material_generation:raise self.printer.command_error('P13 material/source generation changed')
        if self.tools.frame.cal.store.revision!=self.revision or self.tools.frame.cal.store.value!=self.calibration:raise self.printer.command_error('P13 calibration changed')
        if self.gm.move_transform is not None:raise self.printer.command_error('P13 requires no external motion transform')
        now=self.gm.get_status(self.reactor.monotonic())
        if any(now[k]!=self.saved[k] for k in ('absolute_coordinates','absolute_extrude','extrude_factor','speed_factor','speed')):raise self.printer.command_error('P13 GCode modes changed externally')
        if self.expected_targets is not None and any(h.target_temp!=v for h,v in zip(self.heaters,self.expected_targets)):raise self.printer.command_error('P13 heater targets changed externally')
        if self.closed_required:
            b=self.board.get_status(self.reactor.monotonic());r=b.get('inputs',{}).get('raw',{})
            if b.get('inputs_age',99.)>.5 or any(r.get(k)!=v for k,v in (('front_door',1),('lid',1),('tray',0),('bin',0))):raise self.printer.command_error('P13 needs fresh closed doors, tray and bin')
    def _phase(self,name):
        self._guard();self.phase=name;self.history.append(name);self.printer.send_event('inventure:filament_phase',name);self._guard()
    def _set_temp(self,value):
        self.heaters[self.tool].set_temp(value);self.expected_targets=[h.target_temp for h in self.heaters]
    def _verify_cartridge(self):
        self._phase('verify_cartridge');rfid=self.printer.lookup_object('inventure_rfid');c=rfid.channels[self.tool]
        if c['busy'] is None:rfid.start(self.tool,'scan')
        end=self.reactor.monotonic()+self.identity_timeout
        while c['busy'] is not None and self.reactor.monotonic()<end:self._guard();self.reactor.pause(self.reactor.monotonic()+.02)
        self._guard()
        if c['busy'] is not None:rfid.links[self.tool].cancel('P13_IDENTITY_TIMEOUT');raise self.printer.command_error('P13 cartridge verification timeout')
        if rfid.error_for(self.tool,self.pair[self.tool].material_id):raise self.printer.command_error('No proper verified cartridge detected')
    def _heat(self):
        self._phase('heat');self._set_temp(self.material_profile['temperature'])
        while True:
            self._guard();h=self.heaters[self.tool];c=self.thermal.model.channels[self.tool]
            if c.ready and h.can_extrude and c.temperature is not None and c.temperature>=h.min_extrude_temp:return
            self.reactor.pause(self.reactor.monotonic()+.05)
    def _preflight(self):
        for point in (0,9,10,12+self.tool):
            xy=service_point(point,self.tool,self.calibration)
            if not in_box([xy[i]+self.gm.base_position[i] for i in (0,1)],self.tools.box):raise self.printer.command_error('P13 service envelope exceeded before E')
        for x in FINAL_X[self.tool]:
            if not in_box([x+self.gm.base_position[0],152.+self.gm.base_position[1]],self.tools.box):raise self.printer.command_error('P13 wipe envelope exceeded before E')
        if not self.tools.z_min<=self.th.get_position()[2]<=self.tools.z_max:raise self.printer.command_error('P13 qualified Z clearance required')
    def _move(self,xy=None,e=0.,speed=None):
        self._guard();p=list(self.th.get_position())
        if xy is not None:
            q=[xy[i]+self.gm.base_position[i] for i in (0,1)]
            if not in_box(q,self.tools.box):raise self.printer.command_error('P13 service envelope exceeded')
            p[:2]=q
        if e:
            self.tools.check_extrusion();h=self.heaters[self.tool]
            if not h.can_extrude or self.thermal.model.channels[self.tool].temperature<h.min_extrude_temp:raise self.printer.command_error('P13 nozzle too cold')
            p[3]+=e
        self.th.move(p,speed or self.tools.travel_speed);self.th.wait_moves();self._guard();self.gm.reset_last_position()
        self.trace.append(dict(phase=self.phase,tool=self.tool,xy=None if xy is None else list(xy),e=e))
    def _xy(self,point):self._move(service_point(point,self.tool,self.calibration))
    def _purge(self,amount,clean=False):
        back=list(self.gm.get_status(self.reactor.monotonic())['gcode_position'])[:2];self._xy(0)
        x,y=service_point(0,self.tool,self.calibration);steps=0
        for dx,e in purge_segments(self.tool,amount):
            x+=dx;self._move((x,y),e,self.purge_speed);steps+=round(e*960.)
        if clean:
            self._move((x,152.))
            for x in FINAL_X[self.tool]:self._move((x,152.))
        else:self._move(back)
        return steps
    def _prompt(self,kind):
        self._phase(kind)
        if kind=='insert_cartridge':self.printer.lookup_object('inventure_rfid').prepare_insertion(self.tool)
        self.prompt_sequence+=1;self.prompt=dict(token='%s:%d:%d'%(self.session_nonce,self.job,self.prompt_sequence),kind=kind,accepted=False)
        self.printer.send_event('inventure:filament_prompt',dict(self.prompt));end=self.reactor.monotonic()+self.prompt_timeout
        while not self.prompt['accepted']:
            self._guard()
            if self.reactor.monotonic()>=end:raise self.printer.command_error('P13 prompt timeout')
            self.reactor.pause(self.reactor.monotonic()+.05)
        self.prompt=None;self.jog_until=0.;self._mode(STOP)
    def _buttons(self,eventtime,buttons,sequence):
        if self.last_buttons is not None:
            try:
                if delta32(sequence,self.last_buttons)==0:return
            except ValueError:return
        self.last_buttons=sequence
        if self.running and self.prompt is not None and buttons&4:self.prompt['accepted']=True
    def _raw(self,amount,speed,stop=lambda:False):
        remaining=abs(amount);sign=1 if amount>=0 else -1;done=0.
        while remaining>1.e-8:
            self._guard()
            if stop():break
            mm=min(remaining,self.chunk);self._move(e=sign*mm,speed=speed);done+=mm;remaining-=mm
        return done
    def _cartridge_load(self):
        self._mode(AUTO);self._phase('L1');base=self._sample(self.tool)['starts'];total=0
        while True:
            total+=self._purge(20);decision=l1_decision(self._pulse_age(self.tool),delta32(self._sample(self.tool)['starts'],base),total)
            if decision=='success':return dict(terminal='L1_starts',purge_steps=total)
            if decision=='exhausted':raise self.printer.command_error('P13 L1 500mm budget exhausted after full portion')
            if decision=='recover':break
        self._phase('L2');self._mode(FORWARD);end=self.reactor.monotonic()+5.
        while self.reactor.monotonic()<end and not self._sample(self.tool)['a']:self._guard();self.reactor.pause(self.reactor.monotonic()+.02)
        self._mode(STOP)
        if self.reactor.monotonic()>=end or not self._sample(self.tool)['a']:raise self.printer.command_error('P13 L2 missing A within 5s')
        self._phase('L3');self._xy(12+self.tool);self._mode(FORWARD);window=PulseWindow(self._sample(self.tool)['pulses'])
        def edges():return window.success(self._sample(self.tool)['pulses'],self._pulse_age(self.tool))
        travel=self._raw(250.,self.load_speed,edges);ok=edges();self._mode(STOP);self._xy(0)
        if not ok:raise self.printer.command_error('P13 L3 no six accepted edges within 250mm')
        self._phase('L4');base=self._sample(self.tool)['starts'];self._mode(AUTO);purged=self._purge(100,True)
        if delta32(self._sample(self.tool)['starts'],base)<3:
            self._cartridge_unload();raise self.printer.command_error('P13 L4 missing three starts; fallback unload completed')
        self._xy(0);return dict(terminal='L4_starts',L1_purge_steps=total,L3_mm=travel,L4_purge_steps=purged)
    def _cartridge_unload(self):
        self._phase('U0');self._mode(REVERSE);end=self.reactor.monotonic()+10.
        while self.reactor.monotonic()<end:
            self._guard();r=self._sample(self.tool)
            if not r['a'] or r['b']:break
            self.reactor.pause(self.reactor.monotonic()+.02)
        self._mode(GATED);seed=self.reactor.monotonic();seed_count=self._sample(self.tool)['pulses'];last=seed
        def quiet():
            nonlocal last,seed_count
            r=self._sample(self.tool)
            if r['pulses']!=seed_count:seed_count=r['pulses'];last=self.reactor.monotonic()-self._pulse_age(self.tool)
            return self.reactor.monotonic()-last>4.
        travel=0.
        for phase,length,delay in [('U1',self.material_profile['l1'],self.material_profile['d1']),('U2',self.material_profile['l2'],self.material_profile['d2'])]:
            self._phase(phase);travel+=self._raw(-length,self.delay_speeds[delay],quiet)
        self._phase('U3');self._mode(REVERSE);end=self.reactor.monotonic()+6.;speed=self.delay_speeds[self.material_profile['d2']]
        while self.reactor.monotonic()<end and self._sample(self.tool)['a']:
            mm=min(self.chunk,max(0.,end-self.reactor.monotonic())*speed)
            if mm<1.e-6:break
            self._move(e=-mm,speed=speed);travel+=mm
        empty=not self._sample(self.tool)['a'];self._mode(STOP)
        return dict(process_completed=True,sensor_empty_proven=empty,terminal='A_low' if empty else '6000ms_elapsed',native_E_mm=travel,stock_ledger_mm=435,stock_ledger_applied=False)
    def _spool(self):
        self._mode(STOP)
        if self.action=='load':self._xy(12+self.tool)
        self._phase('spool_'+self.action);self.prompt_sequence+=1
        self.prompt=dict(token='%s:%d:%d'%(self.session_nonce,self.job,self.prompt_sequence),kind='stop_spool',accepted=False)
        self.printer.send_event('inventure:filament_prompt',dict(self.prompt))
        amount=250. if self.action=='load' else -100.
        travel=self._raw(amount,self.spool_speed,lambda:self.prompt['accepted']);accepted=self.prompt['accepted'];self.prompt=None
        self._prompt('remove_extrudate' if self.action=='load' else 'remove_spool')
        if self.action=='load':self._xy(10 if self.tool==0 else 9);self._xy(0)
        return dict(native_E_mm=travel,terminal='operator' if accepted else 'distance_budget',source_retained=True,physical_empty_proven=False)
    def _start(self,action,gcmd):
        if not self.enabled or self.stopped:raise gcmd.error('P13 disabled/stopped')
        if self.running or self.assisting or self.ops.state.owner is not None:raise gcmd.error('P13 conflicts with active operation')
        self.require_ready();self.ops._require_print_reference();self.tools.require_binding();self.thermal.require_ready();self.fans.require_ready()
        tool=gcmd.get_int('TOOL',self.ops.state.selector_tool,minval=0,maxval=1);pair=self.materials.pair;material_profile=profile(pair[tool].material_id)
        if self.gm.move_transform is not None:raise gcmd.error('P13 requires no external motion transform')
        saved=self.gm.get_status(self.reactor.monotonic())
        if abs(saved['extrude_factor']-1.)>1.e-9:raise gcmd.error('P13 service requires M221 S100')
        h=self.heaters[tool]
        if not h.min_extrude_temp<=material_profile['temperature']<=h.max_temp:raise gcmd.error('P13 material service temperature outside heater limits')
        if action=='load' and pair[tool].source=='cartridge':
            rfid=self.printer.lookup_object('inventure_rfid',None)
            if rfid is None or not rfid.enabled:raise gcmd.error('P13 cartridge requires P12 RFID')
            if any(h.target_temp for h in self.heaters) or any(c.temperature is None or c.temperature>=50. for c in self.thermal.model.channels[:4]):raise gcmd.error('P13 cartridge insertion requires cold printer and zero targets')
            if any(c['uncertain'] or c['busy']=='write' or c['balance'] is not None and c['balance'].dirty for c in rfid.channels):raise gcmd.error('P13 pending/uncertain RFID balance must be resolved first')
        if gcmd.get('ACK','')!='TRANSPORT_PATH_CHECKED':raise gcmd.error('ACK=TRANSPORT_PATH_CHECKED required')
        lease=self.ops.begin(action)
        self.action=action;self.tool=tool;self.pair=pair;self.material_profile=material_profile;self.material_generation=self.materials.generation
        self.calibration=self.tools.frame.cal.store.value;self.revision=self.tools.frame.cal.store.revision;self.saved=saved
        self.old_limits=list(self.tools.motion.kin.limits);self.old_locks=self._sample(tool)['locks'];self.old_targets=[h.target_temp for h in self.heaters]
        self.running=True;self.lease=lease;self.caller=None;self.cancel_reason=None;self.error=self.result=None;self.prompt=None
        self.phase='prepare';self.closed_required=False;self.service_permit=False;self.expected_targets=list(self.old_targets)
        self.job+=1;self.history.clear();self.trace.clear();self.deadline=self.reactor.monotonic()+self.timeout;self.done=self.reactor.completion()
        self.reactor.register_callback(lambda now:self._worker(gcmd));gcmd.respond_info('P13 job=%d action=%s tool=%d'%(self.job,action,tool))
    def _worker(self,gcmd):
        completed=False;cancelled=False;self.caller=getcurrent();lease=self.lease
        try:
            with self.ops.scope(lease):
                self._phase('prepare');self.th.wait_moves();self._stop()
                if self.action=='load':self._prompt('insert_cartridge' if self.pair[self.tool].source=='cartridge' else 'insert_spool')
                self.closed_required=True;self._guard()
                if self.action=='load' and self.pair[self.tool].source=='cartridge':self._verify_cartridge()
                self.service_permit=True;self.materials.require_ready()
                for i in (0,1):self.board.commands['lock'].send_wait_ack([i,1])
                for i in (0,1):self.tools.motion.kin.limits[i]=self.tools.box[i]
                self._phase('select_tool');self.tools.select(self.tool,gcmd)
                self._preflight();self._heat();self._sync_tool(self.tool);self._phase('service_center');self._xy(0)
                if self.pair[self.tool].source=='spool':self.result=self._spool()
                elif self.action=='load':self.result=self._cartridge_load()
                else:self.result=self._cartridge_unload();self._prompt('remove_cartridge')
                self._phase('complete');completed=True
        except Cancelled as exc:self.error=str(exc);self.result=None;cancelled=True
        except Exception as exc:self.error=str(exc);self.result=None
        finally:
            self.prompt=None;self.jog_until=0.
            if self.stopped or self.ops.state.fatal is not None or self.ops.state.owner!=lease:
                completed=False;self.result=None;self.error=self.error or 'P13 interrupted by shutdown or ownership loss'
            if not self.stopped and self.ops.state.fatal is None and self.ops.state.owner==lease:
                try:
                    with self.ops.scope(lease):
                        self._stop();self.th.wait_moves();self.ops._core(self.ops.state.check,lease)
                        targets=self.old_targets if completed and self.action=='load' else [0.]*len(self.heaters)
                        for h,v in zip(self.heaters,targets):h.set_temp(v)
                        for i in (0,1):self.board.commands['lock'].send_wait_ack([i,(self.old_locks>>i)&1])
                        for i,a in enumerate('xy'):
                            if self.ops.state.axes[a]['valid']:self.tools.motion.kin.limits[i]=self.old_limits[i]
                        self.gm.reset_last_position();self.gm.base_position[3]=self.th.get_position()[3]-self.saved['gcode_position'][3]*self.saved['extrude_factor']
                        self.ops.end(lease,'completed' if completed else 'cancelled' if cancelled else 'failed')
                except Exception as exc:
                    self.error='P13 cleanup: '+str(exc);completed=False
                    if self.ops.state.fatal is None:self.ops.fatal(self.error,'FILAMENT_CLEANUP')
            if self.action=='unload' or not completed:self.materials.confirmed=False
            self.loaded[self.tool]=dict(action=self.action,completed=completed,source=self.pair[self.tool].source,material_id=self.pair[self.tool].material_id,generation=self.material_generation,physical_feedback=False) if not self.stopped else None
            self.service_permit=False;self.running=False;self.caller=self.lease=None;self.expected_targets=None
            self.phase='completed' if completed else 'cancelled' if cancelled else 'failed';self.done.complete(completed)
    def abort(self,reason):
        if not self.running:return
        self.cancel_reason=reason
        if self.phase=='verify_cartridge':self.printer.lookup_object('inventure_rfid').links[self.tool].cancel('P13_CANCELLED')
        if not self.stopped:self._stop()
    def cmd_load(self,gcmd):self._start('load',gcmd)
    def cmd_unload(self,gcmd):self._start('unload',gcmd)
    def cmd_cancel(self,gcmd):self.abort('Operator cancelled P13')
    def cmd_continue(self,gcmd):
        if not self.running or self.prompt is None or self.prompt['accepted'] or gcmd.get('TOKEN','')!=self.prompt['token']:raise gcmd.error('P13 stale/foreign prompt token')
        self.prompt['accepted']=True
    def cmd_jog(self,gcmd):
        if not self.running or self.prompt is None or self.prompt['kind']!='insert_cartridge' or gcmd.get('TOKEN','')!=self.prompt['token']:raise gcmd.error('P13 jog requires current cartridge insertion prompt token')
        ms=gcmd.get_int('DURATION_MS',minval=1,maxval=1500);direction=gcmd.get_int('DIRECTION',minval=0,maxval=1)
        self._mode(FORWARD if direction else REVERSE);self.jog_until=self.reactor.monotonic()+ms/1000.
    def cmd_wait(self,gcmd):
        if self.running:self.done.wait(self.reactor.monotonic()+gcmd.get_float('TIMEOUT',30.,above=0.,maxval=3600.),None)
        self.cmd_status(gcmd)
    def get_status(self,now):
        current=[not self.stopped and r is not None and self.received[i] is not None and now-self.received[i]<=.5 and bool(r['live']) and not r['fault'] for i,r in enumerate(self.reports)]
        b=self.board.get_status(now);ready=bool(self.enabled and not self.stopped and self.ops is not None and self.ops.state.fatal is None and self.materials.pair is not None and self.configured_generation==self.materials.generation and b.get('armed') and not b.get('fault') and b.get('status_age',99.)<=.5 and all(current))
        return dict(enabled=self.enabled,ready=ready,stopped=self.stopped,running=self.running,phase=self.phase,job=self.job,
            prompt=self.prompt,error=self.error,result=self.result,history=list(self.history),loaded=self.loaded,
            channels=[None if r is None else dict(r,report_is_current=current[i],age=None if self.received[i] is None else max(0.,now-self.received[i])) for i,r in enumerate(self.reports)],
            configured_generation=self.configured_generation,rejected_reports=self.rejected,trace_entries=len(self.trace),qualifications=self.qualifications,
            hardware_tests=False,physical_material_feedback=False,native_Klipper_planner=True,automatic_tag_accounting=False,stock_NVM_written=False)
    def cmd_status(self,gcmd):
        raw=json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True,separators=(',',':'))
        if len(raw)<=1800:gcmd.respond_info('V9_FILAMENT '+raw);return
        for i in range(0,len(raw),1800):gcmd.respond_info('V9_FILAMENT_PART %d/%d %s'%(i//1800+1,(len(raw)+1799)//1800,raw[i:i+1800]));self.reactor.pause(self.reactor.monotonic()+.005)
def load_config(config):return InventureFilament(config)
