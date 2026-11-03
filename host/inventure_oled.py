# SPDX-License-Identifier: GPL-3.0-or-later
"""V9 SSD1322 panel. User project renderer + bounded MCU ABI3 transport.

This is an application UI, not a bootloader menu. No Flash/erase/reboot actions.
"""
import collections
import json
import logging
import secrets
from .inventure_oled_render import Frame
from .inventure_oem_menu import OEMMenu
from .inventure_oled_transport import OledStream

class InventureOLED:
    def __init__(self,config):
        self.printer=config.get_printer(); self.reactor=self.printer.get_reactor()
        self.board=self.printer.load_object(config,'inventure_mcu')
        self.mcu=self.board.mcu;self.gcode=self.printer.lookup_object('gcode')
        self.speed=config.getint('spi_speed',2000000,minval=500000,maxval=2000000)
        self.oem_ui_enabled=config.getboolean('oem_menu',False)
        self.menu_profile=config.get('menu_profile','m300').strip().lower()
        if self.menu_profile not in ('m300','zortrax'):
            raise config.error('menu_profile must be m300 or zortrax')
        root_page='launcher' if self.menu_profile=='m300' else 'root'
        self.oem_menu=OEMMenu(config.get('oem_language','pl'),root_page=root_page) if self.oem_ui_enabled else None
        self.oem_start_screen=config.get('oem_start_screen','klipper').strip().lower()
        if self.oem_start_screen not in ('klipper','menu'):
            raise config.error('oem_start_screen must be klipper or menu')
        self.oem_display_mode=('status' if self.oem_start_screen=='klipper' else 'menu')
        self._oem_render_cache=None
        self._rendered_frames=0;self._render_cache_hits=0
        self._rendered_screen='not_rendered'
        self._rendered_menu_page=None
        self.interval=config.getfloat('chunk_interval',.002,minval=.002,maxval=.050)
        self.stream=OledStream(config.getfloat('ack_timeout',1.,minval=.1,maxval=10.),
            config.getfloat('refresh_interval',.2,minval=.2,maxval=10.))
        self.page='home';self.selection=0;self.message=None
        self.last_buttons=0;self.pressed_at={};self.repeat_at={}
        self.power_fired=False;self.connected=False;self.render_at=0.
        self.last_nav_press=-1.;self.last_ok_press=-1.
        # Button actions bypass periodic refresh throttling, but are coalesced
        # if a previous frame is still in flight.
        self._user_refresh_pending=False
        # New host instances use a new 63-bit starting generation; recoveries
        # increment it. Tokens never wrap or reset within this stream.
        self.stream.generation=secrets.randbits(63)
        self.max_retries=config.getint('recovery_attempts',3,minval=0,maxval=3)
        self.retry_base=config.getfloat('recovery_backoff',1.,minval=.5,maxval=10.)
        self.stable_time=config.getfloat('recovery_stable_time',30.,minval=5.,maxval=120.)
        self.retries=0;self.recover_at=None;self.recovery_reason=None
        self.recovery_state='stopped';self.confirmed_since=None
        self.last_error=None;self.shutdown_latched=False;self.events=collections.deque(maxlen=64)
        self.wire_state={};self.state_received=None;self.state_rejected=0;self.next_query=0.
        self.cq=self.mcu.alloc_command_queue();self.commands={}
        # TEST01: read-only telemetry for physical OLED ACK timeout investigation.
        self._oled_last_tx=None;self._oled_last_ack=None
        self._oled_session_sent=0;self._oled_session_acked=0
        self._ack_pump_active=False;self._ack_pump_deferrals=0
        self.mcu.register_config_callback(self._build_config)
        self.printer.register_event_handler('klippy:ready',self._ready)
        self.printer.register_event_handler('klippy:shutdown',self._shutdown)
        self.printer.register_event_handler('klippy:disconnect',self._disconnect)
        self.printer.register_event_handler('gcode:request_restart',self._restart)
        self.printer.register_event_handler('inventure:buttons',self._buttons)
        self.gcode.register_command('INVENTURE_OLED_REINIT',self.cmd_reinit)
        self.gcode.register_command('INVENTURE_OLED_TEXT',self.cmd_text)
        self.gcode.register_command('INVENTURE_OLED_STATUS',self.cmd_status,when_not_ready=True)
        self.gcode.register_command('INVENTURE_MENU_STATUS',self.cmd_menu_status,when_not_ready=True)
        self.timer=self.reactor.register_timer(self._tick)

    def _event(self,code,**data):
        self.events.append(dict(time=self.reactor.monotonic(),code=code,**data))

    def _build_config(self):
        if self.mcu.get_constant_float('INVENTURE_ABI')!=3:
            raise self.printer.config_error('OLED requires Inventure MCU ABI3')
        if float(self.mcu.get_constants().get('INVENTURE_OLED_ABI',0))!=2:
            raise self.printer.config_error('OLED requires INVENTURE_OLED_ABI=2')
        formats={
            'begin':'inventure_oled_begin generation_hi=%u generation_lo=%u token=%u',
            'write':'inventure_oled_write generation_hi=%u generation_lo=%u token=%u dc=%c data=%*s',
            'control':'inventure_oled_control generation_hi=%u generation_lo=%u token=%u reset=%c gate=%c',
            'ping':'inventure_oled_ping generation_hi=%u generation_lo=%u',
            'stop':'inventure_oled_stop','query':'inventure_oled_query'}
        for name,fmt in formats.items():self.commands[name]=self.mcu.lookup_command(fmt,cq=self.cq)
        for fmt,callback in [
            ('inventure_oled_ack generation_hi=%u generation_lo=%u token=%u result=%c',self._ack),
            ('inventure_oled_state clock=%u generation_hi=%u generation_lo=%u configured=%c active=%c latched=%c fault=%c reset=%c',self._state)]:
            if not self.mcu.check_valid_response(fmt):raise self.printer.config_error('OLED ABI2 response schema mismatch')
            self.mcu.register_serial_response(callback,fmt)
        # ACK replays are safe ONLY on the MCU implementing last-token caching.
        self.stream.retry_enabled=(int(self.mcu.get_constants().get(
            'INVENTURE_OLED_REPLAY_ABI',0))==1)
        self.mcu.add_config_cmd('inventure_oled_config rate=%d'%self.speed)

    def _ready(self):
        if self.shutdown_latched:return
        self.connected=True;now=self.reactor.monotonic()
        # On a fresh host Ready, choose the configured Klipper status page
        # deterministically. No stock/OEM menu can inherit a stale selection.
        self.oem_display_mode=('status' if self.oem_start_screen=='klipper' else 'menu')
        if self.oem_menu is not None:self.oem_menu.home()
        self.message=None
        self._oem_render_cache=None
        self._render(now);self.render_at=now+self.stream.refresh_interval
        self._start(now,'initial_start')
        logging.info('INVENTURE_UI_READY display=%s profile=%s root=%s',
                     self.oem_display_mode,self.menu_profile,
                     self.oem_menu.root_page if self.oem_menu is not None else 'legacy')
        self.reactor.update_timer(self.timer,self.reactor.NOW)

    def _start(self,now,reason):
        self.stream.start(now);self.recover_at=None;self.confirmed_since=None
        self._oled_session_sent=self.stream.sent
        self._oled_session_acked=self.stream.acked
        self.recovery_state='initializing' if reason=='initial_start' else 'recovering'
        self._event('OLED_SESSION_STARTED',reason=reason,generation=self.stream.generation)

    def _stop_wire(self):
        if 'stop' in self.commands:
            try:
                self.commands['stop'].send([]);self.commands['query'].send([])
            except Exception as exc:
                self._event('OLED_BLANK_COMMAND_UNAVAILABLE',reason=str(exc))

    def _shutdown(self,*args):
        self.shutdown_latched=True;self.connected=False
        self.recover_at=None;self.recovery_reason='Klipper shutdown'
        local_recovery=int(self.mcu.get_constants().get('INVENTURE_LOCAL_OLED_RECOVERY_ABI',0))==1
        self.recovery_state='shutdown';self.stream.stop(
            'Klipper shutdown; MCU local status requested' if local_recovery else
            'Klipper shutdown; MCU RESET held LOW')
        self.reactor.update_timer(self.timer,self.reactor.NEVER)
        # Nonblocking best effort only. The MCU has its own local shutdown hook
        # and watchdog; correctness does not depend on this packet arriving.
        self._stop_wire();self._event('OLED_SHUTDOWN_LOCAL_STATUS_REQUESTED'
            if local_recovery else 'OLED_SHUTDOWN_BLANK_REQUESTED')

    def _disconnect(self):self._shutdown()
    def _restart(self,*args):self._shutdown()

    def _base_live(self,now):
        s=self.board.get_status(now)
        return bool(self.connected and self.board.connected and not self.shutdown_latched
                    and s.get('status_age',99.)<=.75 and not s.get('fault'))

    def _wire_live(self,now,operational=True):
        s=self.wire_state
        fresh=self.state_received is not None and now-self.state_received<=.75
        if not fresh or not s.get('configured') or s.get('latched'):return False
        if not operational:return True
        generation=(s.get('generation_hi',0)<<32)|s.get('generation_lo',0)
        return bool(s.get('active') and s.get('fault')==0 and s.get('reset')==1
                    and generation==self.stream.generation)

    def _ack(self,params):
        received=self.reactor.monotonic()
        generation=(params['generation_hi']<<32)|params['generation_lo']
        token,result=params['token'],params['result']
        def deliver(now):
            if self.connected and not self.shutdown_latched:
                accepted=self.stream.ack(token,received,generation,result,processed_at=now)
                if accepted:
                    tx=self._oled_last_tx
                    matching=(tx is not None and tx['generation']==generation
                              and tx['token']==token)
                    self._oled_last_ack={'generation':generation,'token':token,
                        'operation':tx['operation'] if matching else 'unknown',
                        'queued_to_receive_ms':round(1000*(received-tx['queued_at']),2)
                        if matching else None}
                    # M300-derived ACK-driven pump: do not wait for the next
                    # 20 ms fallback timer on every 32-byte ABI2 operation.
                    # Still strictly one outstanding operation, no DMA/RLE
                    # claims and no MCU/bootloader modifications.
                    if self.stream.enabled and not self.shutdown_latched:
                        # A test MCU can deliver ACK synchronously from send().
                        # Never let the ACK pump recurse through hundreds of
                        # 32-byte operations; continue at the next reactor tick.
                        if getattr(self, '_ack_pump_active', False):
                            self._ack_pump_deferrals+=1
                            self.reactor.update_timer(self.timer,self.reactor.NOW)
                        else:
                            self._ack_pump_active=True
                            try:self._send_one(now)
                            finally:self._ack_pump_active=False
        self.reactor.register_async_callback(deliver)

    def _state(self,params):
        received=self.reactor.monotonic();sample={k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(lambda now:self._deliver_state(sample,received))

    def _deliver_state(self,sample,received):
        if self.wire_state:
            delta=(sample['clock']-self.wire_state['clock'])&0xffffffff
            if not 0<delta<0x80000000:self.state_rejected+=1;return
        self.wire_state=sample;self.state_received=received
        # A late query can confirm the blanked MCU after host shutdown, but it
        # never starts a new session or clears any global/panel latch.

    def _lose(self,now,reason):
        if self.shutdown_latched or self.recovery_state in ('backoff','fault'):return
        self.stream.stop(reason);self.confirmed_since=None
        self.last_error={'time':now,'reason':reason,'generation':self.stream.generation}
        self.recovery_reason=reason;self._stop_wire()
        # Distinguish ONE missing ACK per attempt from many outstanding frames.
        self._event('OLED_TRANSPORT_LOST',reason=reason,
            attempt_sent=self.stream.sent-self._oled_session_sent,
            attempt_acked=self.stream.acked-self._oled_session_acked,
            last_tx=dict(self._oled_last_tx or {}),
            last_ack=dict(self._oled_last_ack or {}))
        logging.warning('Inventure OLED ACK diagnostic: %s',
            json.dumps(self.events[-1],sort_keys=True))
        if self.retries>=self.max_retries:
            self.recover_at=None;self.recovery_state='fault'
            self._event('OLED_RECOVERY_EXHAUSTED',attempts=self.retries)
        else:
            self.recover_at=now+self.retry_base*(2**self.retries)
            self.recovery_state='backoff'

    def _send_one(self,now):
        # Transport's pending-token gate preserves the single-inflight ABI2
        # safety contract. Duplicate/out-of-order ACK cannot advance it.
        try:operation=self.stream.step(now)
        except TimeoutError:
            self._lose(now,self.stream.failure);return
        if self.stream.failure and self.recovery_state not in ('backoff','fault'):
            self._lose(now,self.stream.failure);return
        if not operation:return
        kind,token,args=operation;gen=self.stream.generation
        detail={'generation':gen,'token':token,'operation':kind,
                'phase':self.stream.phase,'queued_at':round(now,6)}
        if kind=='write':
            dc,blob=args
            detail.update(dc=dc,length=len(blob),first8_hex=blob[:8].hex())
        elif kind=='control':
            detail.update(reset=args[0],gate=args[1])
        self._oled_last_tx=detail
        try:self.commands[kind].send([gen>>32,gen&0xffffffff,token,*args])
        except Exception as exc:self._lose(now,'OLED command unavailable: '+str(exc))

    def _tick(self,now):
        if not self.connected or self.shutdown_latched:return self.reactor.NEVER
        if now>=self.next_query:
            try:
                if self.stream.enabled:
                    gen=self.stream.generation;self.commands['ping'].send([gen>>32,gen&0xffffffff])
                self.commands['query'].send([])
            except Exception as exc:
                self._lose(now,'OLED command unavailable: '+str(exc))
            self.next_query=now+.25
        self._held_keys(now)
        if now>=self.render_at:
            self._render(now);self.render_at=now+self.stream.refresh_interval
        if self.recovery_state=='backoff' and now>=self.recover_at:
            # Don't burn through retries while the underlying MCU is absent.
            if self._base_live(now) and self._wire_live(now,False):
                self.retries+=1
                try:self._start(now,'automatic_retry')
                except ValueError as exc:
                    self.recovery_state='fault';self.recover_at=None;self.recovery_reason=str(exc)
            else:self.recover_at=now+.25
        self._send_one(now)
        if self.stream.ready:
            if not self._base_live(now) or not self._wire_live(now):
                self._lose(now,'OLED/MCU status not fresh or session not active')
            else:
                if self.recovery_state!='ready':
                    self.recovery_state='ready';self.recovery_reason=None;self.confirmed_since=now
                    self._event('OLED_FULL_FRAME_CONFIRMED',generation=self.stream.generation,
                                frames=self.stream.generation_frames)
                if self.confirmed_since is not None and now-self.confirmed_since>=self.stable_time:
                    self.retries=0
        return self._next_tick(now)

    def _next_tick(self,now):
        # Keep 2 ms pacing during transfers and held keys. An unchanged screen
        # needs only service/render deadlines, not 500 reactor calls per second.
        busy=self.stream.enabled and (self.stream.operations is not None or
            self.stream.pending is not None or self.stream.latest!=self.stream.previous)
        if busy or self.pressed_at:return now+self.interval
        due=min(now+.050,self.next_query,self.render_at)
        if self.recovery_state=='backoff' and self.recover_at is not None:
            due=min(due,self.recover_at)
        return max(now+self.interval,due)

    def _wake(self):
        if self.connected and not self.shutdown_latched:
            self.reactor.update_timer(self.timer,self.reactor.NOW)

    def _cal(self):return self.printer.lookup_object('inventure_calibration',None)
    def _rows(self,now):
        status=self.board.get_status(now)
        if self.page=='home':
            return 'Main Menu',[('Board Status','board'),('Temperatures','temps'),
                                ('Inputs','inputs'),('Calibration','cal'),('Information','about')]
        if self.page=='board':
            return 'Diagnostics',[('Back','home'),('MCU ABI3 / OLED ABI2',None),
                ('Armed: %s'%status.get('armed','?'),None),
                ('Fault: %s'%status.get('fault','?'),None),
                ('ADC mask: %s'%status.get('seen','?'),None)]
        if self.page=='temps':
            names=('T0','T1','Chamber L','Chamber R','Center','Power Supply','Mainboard','Tool PCB')
            return 'Temperatures',[('Back','home')]+[(n+': '+('---' if t is None else '%.1f°C'%t),None)
                for n,t in zip(names,status['temperatures'])]
        if self.page=='inputs':
            values=status.get('inputs',{}).get('raw',{})
            return 'Input States',[('Back','home')]+[
                (name+': '+str(values.get(name,'?')),None) for name in self.board.INPUT_NAMES]
        if self.page=='cal':
            return 'Nozzle Calibration',[('Back','home'),('Current Offsets','offsets'),
                ('Measure Z Height','start_z'),('Choose X Line','line_x'),
                ('Choose Y Line','line_y'),('Save Candidate','confirm'),('Abort Calibration','abort')]
        if self.page=='offsets':
            cal=self._cal();st=cal.get_status(now) if cal else {}
            c=st.get('committed',{});total=st.get('total_offsets',{})
            if not c.get('established',False):
                return 'Not Calibrated',[('Back','cal'),('No stored offsets',None),
                                         ('Zero is not measured',None)]
            return 'Offsets / Total Shift',[('Back','cal')]+[
                ('O%s=%s / total=%s'%(axis.upper(),c.get('o'+axis,'?'),total.get(axis,'?')),None)
                for axis in ('x','y','z')]+[('State: '+str(st.get('state','unavailable')),None)]
        if self.page in ('line_x','line_y'):
            axis=self.page[-1].upper()
            return '%s Line (7=Neutral)'%axis,[('Back','cal')]+[
                ('%d : shift %+.2f mm'%(n,-(n-7)*.05),('line',axis,n)) for n in range(1,14)]
        if self.page=='confirm':
            return 'Save Calibration / Host JSON',[('Back','cal'),('Confirm Save','commit'),
                                                  ('Do Not Save','abort')]
        return 'Information',[('Back','home'),('Inventure V9 / P13',None),
                              ('Author: Marcin Kowalik',None),('OLED: LAB test mode',None)]

    def _render(self,now):
        # The RC7.2 static OEM screen was recomposed every .25s even when
        # unchanged; that wasted Orange Pi reactor time. Cache by UI generation.
        cal=self._cal()
        cal_status=cal.get_status(now) if cal else {}
        measuring=cal_status.get('state') in (
            'preparing_t0','measuring_t0','preparing_t1','measuring_t1')
        snapshot=None
        menu_view=None
        klipper_status=None
        if self.oem_menu is not None and not measuring and not self.message:
            if self.oem_display_mode=='status':
                klipper_status=self.board.get_status(now)
                boot=klipper_status.get('p4_boot',{})
                snapshot=('status',klipper_status.get('connected'),
                          klipper_status.get('fault'),
                          klipper_status.get('mcu_release'),boot.get('baud'))
            else:
                menu_view=self.oem_menu.view()
                snapshot=('menu',menu_view['page'],menu_view['generation'],
                          menu_view['selected'],menu_view['window'],
                          menu_view.get('icon'),menu_view.get('notice'))
            if snapshot==self._oem_render_cache:
                self._render_cache_hits+=1
                self._user_refresh_pending=False
                return
        frame=Frame()
        if measuring:
            counts=cal_status.get('sample_counts',[0,0])
            frame.progress('Nozzle Z Height',
                           'T0 %d/2  T1 %d/3'%(counts[0],counts[1]),
                           sum(counts)*20)
        elif self.message:
            frame.status(self.message[0],self.message[1])
        elif klipper_status is not None:
            frame.klipper_status(klipper_status)
        elif menu_view is not None:
            frame.oem_menu(menu_view)
        elif self.oem_menu is not None:
            frame.oem_menu(self.oem_menu.view())
        else:
            title,rows=self._rows(now)
            self.selection=max(0,min(self.selection,len(rows)-1))
            frame.menu(title,[r[0] for r in rows],self.selection,self.page!='home')
        self.stream.request(frame.wire_bytes(),urgent=self._user_refresh_pending)
        if measuring:self._rendered_screen='calibration'
        elif self.message:self._rendered_screen='message'
        elif klipper_status is not None:self._rendered_screen='klipper_status'
        elif menu_view is not None or self.oem_menu is not None:
            self._rendered_screen='oem_menu'
        else:self._rendered_screen='legacy'
        self._rendered_menu_page=(menu_view or {}).get('page')
        self._rendered_frames+=1
        self._oem_render_cache=snapshot
        self._user_refresh_pending=False

    def _script(self,script):
        try:self.gcode.run_script(script)
        except Exception as exc:
            logging.exception('Inventure panel action failed')
            self.message=('Operation unavailable',str(exc)[:40])

    def _select(self,now):
        if self.message:
            self.message=None;self._user_refresh_pending=True;self.render_at=0.
            return
        if self.oem_menu is not None:
            if self.oem_display_mode=='status':
                self.oem_menu.home()
                self.oem_display_mode='menu'
            elif self.oem_menu.select()=='exit_to_status':
                self.oem_display_mode='status'
            self.render_at=0.;self._user_refresh_pending=True
            return
        _,rows=self._rows(now);action=rows[self.selection][1]
        if action is None:return
        if isinstance(action,tuple):
            _,axis,line=action
            self._script('INVENTURE_CAL_XY_LINE AXIS=%s LINE=%d'%(axis,line))
            if self.message is None:self.page='cal';self.selection=0
        elif action=='start_z':self._script('INVENTURE_CAL_Z_START')
        elif action=='abort':self._script('INVENTURE_CAL_ABORT');self.page='cal';self.selection=0
        elif action=='commit':self._script('INVENTURE_CAL_COMMIT ACK=SAVE_HOST_CALIBRATION');self.page='cal';self.selection=0
        else:self.page=action;self.selection=0
        self.render_at=0.;self._user_refresh_pending=True

    def _buttons(self,now,mask,sequence):
        if not self.connected:return
        pressed=mask&~self.last_buttons; released=self.last_buttons&~mask
        if pressed & 3 == 3:pressed &= ~3  # simultaneous UP+DOWN: no selection movement
        for bit in range(4):
            key=1<<bit
            if pressed&key:
                self.pressed_at[bit]=now
                if bit<2:
                    if now-self.last_nav_press>.100:
                        self._navigate(-1 if bit==0 else 1,now);self.last_nav_press=now
                    self.repeat_at[bit]=now+.300
                elif bit==2:
                    if now-self.last_ok_press>.200:
                        self._select(now);self.last_ok_press=now
                else:self.power_fired=False
            if released&key:
                if bit==3 and not self.power_fired:
                    if self.oem_menu is not None:
                        if self.oem_display_mode=='menu':
                            if not self.oem_menu.back():
                                self.oem_display_mode='status'
                    else:
                        self.message=None;self.page='home' if self.page in ('home','board','temps','inputs','about') else 'cal';self.selection=0
                    self._user_refresh_pending=True
                self.pressed_at.pop(bit,None);self.repeat_at.pop(bit,None)
        self.last_buttons=mask;self.render_at=0.
        self._wake()

    def _navigate(self,direction,now):
        self.message=None
        if self.oem_menu is not None:
            if self.oem_display_mode=='menu':
                self.oem_menu.move(direction)
                self.render_at=0.;self._user_refresh_pending=True
            return
        _,rows=self._rows(now);self.selection=(self.selection+direction)%len(rows)
        self.render_at=0.;self._user_refresh_pending=True

    def _held_keys(self,now):
        for bit in (0,1):
            if self.last_buttons&3==3:continue
            if bit in self.repeat_at and now>=self.repeat_at[bit]:
                self._navigate(-1 if bit==0 else 1,now);self.repeat_at[bit]=now+.101
        if 3 in self.pressed_at and not self.power_fired and now-self.pressed_at[3]>.300:
            self.power_fired=True
            self.printer.send_event('inventure:power_hold')
            self.message=('Operation interrupted','No MCU reset / no PSU cutoff')
            self._user_refresh_pending=True;self.render_at=0.

    def cmd_reinit(self,gcmd):
        now=self.reactor.monotonic()
        if not self._base_live(now) or not self._wire_live(now,False):
            raise gcmd.error('OLED reinit needs fresh connected fault-free MCU and unlatched panel')
        self.retries=0;self.recovery_reason='manual_reinit';self._render(now)
        try:self._start(now,'manual_reinit')
        except ValueError as exc:raise gcmd.error(str(exc))
        self._wake()
    def cmd_text(self,gcmd):
        self.message=(gcmd.get('LINE1','Inventure'),gcmd.get('LINE2',''))
        self.render_at=0.;self._user_refresh_pending=True
        self._wake()
    def cmd_menu_status(self,gcmd):
        details=self.oem_menu.status() if self.oem_menu is not None else {'oem_menu':False}
        details['display_mode']=self.oem_display_mode if self.oem_menu is not None else 'legacy'
        details['layout']='M300_FULL_HEIGHT_FIVE_ROWS_NO_HEADER'
        details['menu_profile']=self.menu_profile
        details['screen_requested']=self.oem_start_screen
        details['rendered_screen']=self._rendered_screen
        gcmd.respond_info('V9_MENU '+json.dumps(details,sort_keys=True))
    def cmd_status(self,gcmd):gcmd.respond_info('V9_OLED '+json.dumps(self.get_status(self.reactor.monotonic()),sort_keys=True))
    def get_status(self,now):
        ready=bool(self.stream.ready and self._base_live(now) and self._wire_live(now)
                   and self.recovery_state=='ready')
        return {'ready':ready,'transport_ready':self.stream.ready,'ever_ready':self.stream.ever_ready,
                'enabled':self.stream.enabled,'connected':self.connected,'state':self.recovery_state,
                'error':self.stream.failure or self.recovery_reason,'last_error':self.last_error,
                'sent':self.stream.sent,'acked':self.stream.acked,'frames':self.stream.completed_frames,
                'generation':self.stream.generation,'generation_frames':self.stream.generation_frames,
                'recovery_attempts':self.retries,'max_recovery_attempts':self.max_retries,
                'recover_at':self.recover_at,'ignored_acks':self.stream.ignored_acks,
                'mcu_state':dict(self.wire_state),'mcu_state_age':None if self.state_received is None else max(0.,now-self.state_received),
                'rejected_state_reports':self.state_rejected,'shutdown_latched':self.shutdown_latched,
                'shutdown_policy':('MCU_LOCAL_STATUS_NO_ACTUATOR_RESTART'
                    if int(self.mcu.get_constants().get('INVENTURE_LOCAL_OLED_RECOVERY_ABI',0))==1
                    else 'MCU_RESET_LOW_CS_HIGH_PD3_UNCHANGED'),
                'blank_confirmed':bool(self.wire_state.get('configured') and self.wire_state.get('reset')==0
                    and self.wire_state.get('active')==0 and self.state_received is not None and now-self.state_received<=.75),
                'page':self.page,'selection':self.selection,
                'display_mode':self.oem_display_mode,
                'menu_profile':self.menu_profile,
                'screen_requested':self.oem_start_screen,
                'rendered_screen':self._rendered_screen,
                'host_frame_acknowledged':bool(self.stream.ready and
                    self.stream.previous is not None and
                    self.stream.previous==self.stream.latest),
                'ack_pump_deferrals':self._ack_pump_deferrals,
                'rendered_menu_page':self._rendered_menu_page,
                'oem_menu':self.oem_menu.status() if self.oem_menu is not None else None,'events':list(self.events),
                'ack_diagnostic':{'last_tx':self._oled_last_tx,
                    'last_ack':self._oled_last_ack,
                    'attempt_sent':self.stream.sent-self._oled_session_sent,
                    'attempt_acked':self.stream.acked-self._oled_session_acked},
                'ui_transport':{'host_release':'RC7.5-M300-ACK',
                    'chunk_interval_s':self.interval,
                    'refresh_interval_s':self.stream.refresh_interval,
                    'idle_poll_interval_s':.050,
                    'rendered_frames':self._rendered_frames,
                    'render_cache_hits':self._render_cache_hits,
                    'full_frames':self.stream.full_frames,
                    'partial_frames':self.stream.partial_frames,
                    'committed_wire_bytes':self.stream.committed_wire_bytes,
                    'last_window':self.stream.last_window,
                    'last_windows':self.stream.last_windows,
                    'urgent_refresh':self.stream.urgent_refresh},
                'hardware_verified':False,'ack_proves':'MCU_SPI_transaction_not_optical_pixels'}

def load_config(config):return InventureOLED(config)
