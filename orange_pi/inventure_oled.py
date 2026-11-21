# SPDX-License-Identifier: GPL-3.0-or-later
"""V9 SSD1322 panel. User project renderer + bounded OLED transport.

This is an application UI, not a bootloader menu. No Flash/erase/reboot actions.
"""
import collections
import functools
import json
import logging
import math
import secrets
from .inventure_mcu import HOST_REVISION
from .inventure_oled_render import Frame
from .inventure_oled_graphics import KlipperStatus, activity_index, menu_wire, sleep_wire
from .inventure_oem_menu import MENU, OEMMenu
from .inventure_oled_uifast_transport import FastOledStream, OledStream

# Klipper's native serialqueue background priority: motion/heater deadlines
# remain ahead of optional pixels, on the existing, separate OLED queue.
BACKGROUND_PRIORITY_CLOCK=0x7fffffff00000000

@functools.lru_cache(maxsize=16)
def fading_sleep_frame(level):
    wire = sleep_wire()
    if level == 16:
        return wire
    # Each SSD1322 byte holds two independent four-bit grayscale pixels.
    nibble = [round(value * level / 16.) for value in range(16)]
    palette = bytes((nibble[value >> 4] << 4) | nibble[value & 15] for value in range(256))
    return wire.translate(palette)


class InventureOLED:
    def __init__(self,config):
        self.printer=config.get_printer(); self.reactor=self.printer.get_reactor()
        self.board=self.printer.load_object(config,'inventure_mcu')
        self.mcu=self.board.mcu;self.gcode=self.printer.lookup_object('gcode')
        self.speed=config.getint('spi_speed',2000000,minval=500000,maxval=3750000)
        self.use_fast=config.getboolean('fast_transport',True)
        self.fast=False;self._fast_tx={}
        self._fast_state={};self._next_fast_query=0.
        self.oem_ui_enabled=config.getboolean('oem_menu',False)
        self.menu_profile=config.get('menu_profile','m300').strip().lower()
        if self.menu_profile not in ('m300','zortrax'):
            raise config.error('menu_profile must be m300 or zortrax')
        root_page='launcher' if self.menu_profile=='m300' else 'root'
        self.oem_menu=OEMMenu(config.get('oem_language','en'),root_page=root_page,bench=True,
            operational=True) if self.oem_ui_enabled else None
        self.native_status=KlipperStatus(config) if self.oem_menu is not None else None
        self.oem_start_screen=config.get('oem_start_screen','klipper').strip().lower()
        if self.oem_start_screen not in ('klipper','menu'):
            raise config.error('oem_start_screen must be klipper or menu')
        self.oem_display_mode=('status' if self.oem_start_screen=='klipper' else 'menu')
        self._oem_render_cache=None
        self._rendered_frames=0;self._render_cache_hits=0
        self._rendered_screen='not_rendered'
        self._rendered_menu_page=None
        self.sleep_timeout=config.getfloat('sleep_timeout',300.,minval=0.,maxval=86400.)
        self.sleep_notice=config.getfloat('sleep_notice',10.,minval=1.,maxval=60.)
        if not (math.isfinite(self.sleep_timeout) and math.isfinite(self.sleep_notice)):
            raise config.error('OLED sleep timers must be finite')
        self.configured_sleep = self.sleep_timeout
        self.configured_notice = self.sleep_notice
        self.configured_dim_min = config.getint('dim_minutes', 5, minval=1, maxval=60)
        self.configured_sleep_min = config.getint('sleep_minutes', 15, minval=1, maxval=60)
        self.configured_brightness = config.getint('brightness', 16, minval=1, maxval=16)
        legacy_timeout = config.get('sleep_timeout', None) is not None
        self.configured_policy = config.get('timer_policy', 'inventure' if legacy_timeout else 'minutes')
        if self.configured_policy not in ('minutes', 'inventure'):
            raise config.error('OLED timer_policy must be minutes or inventure')
        self.minute_timers = False
        self.sleep_phase='awake'
        self.last_activity=0.
        self.notice_at=None
        self.display_work=False
        self.display_work_error=None
        self.display_check_at=0.
        self.wake_wait=False
        self.wake_gesture=False
        self.activity_hook_installed=False
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
        self.thermal_test_hold=False
        self._ack_pump_active=False;self._ack_pump_deferrals=0
        self._work_at=0.;self._button_sample_at=None;self._stale_button_events=0
        self._navigation_event = self._navigation_frame = None
        self._input_to_render_ms = collections.deque(maxlen=128)
        self._input_to_ack_ms = collections.deque(maxlen=128)
        self._tick_count=0;self._render_wire=None
        self.mcu.register_config_callback(self._build_config)
        self.printer.register_event_handler('klippy:ready',self._ready)
        self.printer.register_event_handler('klippy:shutdown',self._shutdown)
        self.printer.register_event_handler('klippy:disconnect',self._disconnect)
        self.printer.register_event_handler('gcode:request_restart',self._restart)
        self.printer.register_event_handler('inventure:menu_buttons',self._buttons)
        self.gcode.register_command('INVENTURE_DISPLAY_CONTROL', self.cmd_display_control)
        self.gcode.register_command('INVENTURE_OLED_REINIT',self.cmd_reinit)
        self.gcode.register_command('INVENTURE_OLED_TEXT',self.cmd_text)
        self.gcode.register_command('INVENTURE_OLED_WAKE',self.cmd_wake)
        self.gcode.register_command('INVENTURE_OLED_STATUS',self.cmd_status,when_not_ready=True)
        self.gcode.register_command('INVENTURE_MENU_STATUS',self.cmd_menu_status,when_not_ready=True)
        self.timer=self.reactor.register_timer(self._tick)
        from .inventure_oled_preview import OledPreview
        self.preview = OledPreview(self)
        self.printer.add_object('inventure_oled_preview', self.preview)
        self.preview.bind()

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
        constants=self.mcu.get_constants()
        self.fast=(self.use_fast
                   and int(constants.get('INVENTURE_OLED_FAST_ABI',0))==1
                   and int(constants.get('INVENTURE_OLED_FAST_QUEUE',0))==4
                   and int(constants.get('INVENTURE_OLED_FAST_WIRE',0))==256)
        if self.fast:
            burst=('inventure_oled_burst generation_hi=%u generation_lo=%u '
                   'token=%u dc=%c encoding=%c length=%hu data=%*s')
            self.commands['burst']=self.mcu.lookup_command(burst,cq=self.cq)
            self.commands['fast_query']=self.mcu.lookup_command('inventure_oled_fast_query',cq=self.cq)
            fast_state=('inventure_oled_fast_state spi_hz=%u blocks=%u wire_bytes=%u '
                        'errors=%u peak=%c')
            if not self.mcu.check_valid_response(fast_state):
                raise self.printer.config_error('OLED fast response schema mismatch')
            self.mcu.register_serial_response(self._fast_state_response,fast_state)
            baud=self.mcu._conn_helper.get_serialport()[1]
            self.stream=FastOledStream(self.stream.timeout,self.stream.refresh_interval,
                self.stream.generation,baud*.020)
        else:
            if self.speed>2000000:
                raise self.printer.config_error('SPI above 2000000 requires fast OLED MCU and host')
            self.stream.retry_enabled=int(constants.get('INVENTURE_OLED_REPLAY_ABI',0))==1
        self.mcu.add_config_cmd('inventure_oled_config rate=%d'%self.speed)

    def _ready(self):
        if self.shutdown_latched:return
        self.connected=True;now=self.reactor.monotonic()
        self._apply_display_settings()
        self.last_activity=now
        self._observe_external_activity()
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
        if self.sleep_phase!='awake':
            self._display_activity(now)
            self._render(now)
        self.stream.set_display_mode('awake')
        self.stream.start(now);self.recover_at=None;self.confirmed_since=None
        self._fast_tx.clear()
        self._navigation_event = self._navigation_frame = None
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
        self.preview.reset()
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
        if hasattr(self.board, 'oled_link_live'):
            return self.connected and not self.shutdown_latched and self.board.oled_link_live(now)
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
        received=params.get('#receive_time',self.reactor.monotonic())
        generation=(params['generation_hi']<<32)|params['generation_lo']
        token,result=params['token'],params['result']
        def deliver(now):
            if self.connected and not self.shutdown_latched:
                accepted=self.stream.ack(token,received,generation,result,processed_at=now)
                if accepted:
                    tx=(self._fast_tx.pop(token,None) if getattr(self,'fast',False)
                        else self._oled_last_tx)
                    matching=(tx is not None and tx['generation']==generation
                              and tx['token']==token)
                    if getattr(self,'fast',False):
                        # A successful later FIFO ACK may cover missing earlier
                        # return packets. Release their diagnostic records too.
                        for old in tuple(self._fast_tx):
                            if old<=token:self._fast_tx.pop(old,None)
                    self._oled_last_ack={'generation':generation,'token':token,
                        'operation':tx['operation'] if matching else 'unknown',
                        'queued_to_receive_ms':round(1000*(received-tx['queued_at']),2)
                        if matching else None}
                    # ACKs return window credit immediately. The reactor
                    # timer also services delays, timeouts and work pacing.
                    if self.stream.enabled and not self.shutdown_latched:
                        # A test MCU can deliver ACK synchronously from send().
                        # Never let the ACK pump recurse through hundreds of
                        # 32-byte operations; continue at the next reactor tick.
                        if getattr(self, '_ack_pump_active', False):
                            self._ack_pump_deferrals+=1
                            self.reactor.update_timer(self.timer,self.reactor.NOW)
                        else:
                            deferrals=self._ack_pump_deferrals
                            self._ack_pump_active=True
                            try:
                                if getattr(self,'fast',False):self._pump(now)
                                else:self._send_one(now)
                            finally:self._ack_pump_active=False
                            if self.fast and self.stream.enabled and self._ack_pump_deferrals==deferrals:
                                self.reactor.update_timer(self.timer,self._next_tick(now))
                            else:
                                self.reactor.update_timer(self.timer,self.reactor.NOW)
        self.reactor.register_async_callback(deliver)

    def _fast_state_response(self,params):
        sample={key:params[key] for key in ('spi_hz','blocks','wire_bytes','errors','peak')}
        self.reactor.register_async_callback(lambda now:self._fast_state.update(sample))

    def _state(self,params):
        received=params.get('#receive_time',self.reactor.monotonic())
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(lambda now:self._deliver_state(sample,received))

    def _deliver_state(self,sample,received):
        now=self.reactor.monotonic()
        if (not isinstance(received,(int,float)) or isinstance(received,bool)
                or not math.isfinite(received) or received>now+.001):
            self.state_rejected+=1;return
        if self.wire_state:
            delta=(sample['clock']-self.wire_state['clock'])&0xffffffff
            if not 0<delta<0x80000000:self.state_rejected+=1;return
        self.wire_state=sample;self.state_received=received
        # A late query can confirm the blanked MCU after host shutdown, but it
        # never starts a new session or clears any global/panel latch.

    def _lose(self,now,reason):
        if self.shutdown_latched or self.recovery_state in ('backoff','fault'):return
        self.stream.stop(reason);self.confirmed_since=None
        self.preview.reset()
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
        elif kind=='burst':
            dc,encoding,length,blob=args
            detail.update(dc=dc,encoding=encoding,length=length,payload=len(blob))
        elif kind=='control':
            detail.update(reset=args[0],gate=args[1])
        self._oled_last_tx=detail
        if getattr(self,'fast',False):self._fast_tx[token]=detail
        try:self.commands[kind].send([gen>>32,gen&0xffffffff,token,*args],
                                    reqclock=BACKGROUND_PRIORITY_CLOCK)
        except Exception as exc:
            self._lose(now,'OLED command unavailable: '+str(exc));return False
        return True

    def _pump(self,now):
        if self.fast and now>=getattr(self,'_work_at',0.):
            self.stream.active_work=self._active_work(now)
            self._work_at=now+.100
        for _ in range(4 if self.fast else 1):
            if not self._send_one(now):break
        self._publish_input_receipt()
        pending = self._navigation_frame
        if pending is not None:
            generation, wire, received, previous_count = pending
            if generation != self.stream.generation or not self.stream.enabled:
                self._navigation_frame = None
            elif self.stream.completed_frames > previous_count and wire == self.stream.last_frame_bytes:
                self._input_to_ack_ms.append(1000. * (self.stream.last_complete_at - received))
                self._navigation_frame = None
            elif wire != self.stream.latest and wire != self.stream.target:
                self._navigation_frame = None

    @staticmethod
    def _timing_summary(samples):
        return {'samples': len(samples),
                'last': round(samples[-1], 2) if samples else None,
                'p95': round(sorted(samples)[math.ceil(.95 * len(samples)) - 1], 2) if samples else None}

    def _active_work(self,now):
        if getattr(self.board,'load_test_context',None) is not None:return True
        if getattr(self.board,'feeder_run_context',None) is not None:return True
        if getattr(self.board,'motion_last',{}).get('active'):return True
        operations=self.printer.lookup_object('inventure_operations',None)
        if operations is not None and operations.get_status(now).get('operation'):return True
        heaters=self.printer.lookup_object('heaters',None)
        if heaters is not None and any(heaters.lookup_heater(name.split()[-1]).get_status(now).get('target',0.)>0.
                for name in heaters.get_all_heaters()):return True
        idle=self.printer.lookup_object('idle_timeout',None)
        stats=self.printer.lookup_object('print_stats',None)
        return bool((idle and idle.get_status(now)['state']=='Printing')
                    or (stats and stats.get_status(now)['state'] in ('printing','paused')))

    def _observe_external_activity(self):
        if not self.sleep_timeout or self.activity_hook_installed:return
        webhooks=self.printer.lookup_object('webhooks',None)
        endpoints=getattr(webhooks,'_endpoints',{})
        original=endpoints.get('gcode/script')
        if original is None:return
        passive={'INVENTURE_OLED_STATUS','INVENTURE_MENU_STATUS',
                 'INVENTURE_PANEL_IO_STATUS','INVENTURE_STATUS','INVENTURE_VERSION',
                 'INVENTURE_BENCH_STATUS','ZORTRAX_FANS','ZORTRAX_TEMPERATURES',
                 'TEST_INVENTURE_STATUS'}
        def observed(request):
            for line in request.get_str('script').splitlines():
                words=line.partition(';')[0].split()
                if words and words[0].upper() not in passive:
                    self._display_activity(self.reactor.monotonic())
                    break
            return original(request)
        endpoints['gcode/script']=observed
        self.activity_hook_installed=True

    def _display_working(self,now):
        if getattr(self.board,'load_test_context',None) is not None:return True
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        if zortrax is not None and zortrax.active is not None:return True
        if self._active_work(now):return True
        for name,key,values in (
                ('print_stats','state',('paused',)),
                ('inventure_operations','operation',None),
                ('inventure_filament','running',None),
                ('inventure_calibration','state',('preparing_t0','measuring_t0',
                                                   'preparing_t1','measuring_t1'))):
            obj=self.printer.lookup_object(name,None)
            if obj is not None:
                value=obj.get_status(now).get(key)
                if (value in values if values is not None else bool(value)):return True
        toolhead=self.printer.lookup_object('toolhead',None)
        if toolhead is not None and hasattr(toolhead,'check_busy'):
            planned,estimated,empty=toolhead.check_busy(now)
            if not empty or planned>estimated:return True
        heaters=self.printer.lookup_object('heaters',None)
        return bool(heaters and any(heaters.lookup_heater(name.split()[-1]).get_status(now)['target']>0.
                                    for name in heaters.get_all_heaters()))

    def _display_phase(self,phase):
        if self.sleep_phase==phase:return
        if self.sleep_phase in ('notice','off'):
            self._oem_render_cache=None
        self.sleep_phase=phase
        if phase == 'off':
            self.preview.observe(self.reactor.monotonic(), ('off',), None)
        self.notice_at=None
        self.notice_level = self.stream.fade_step = 16
        self.render_at=0.
        self._user_refresh_pending=True

    def _display_activity(self,now):
        if not self.connected or self.shutdown_latched:return
        self.last_activity=now
        if self.sleep_phase in ('notice','off'):
            self.wake_wait=True
        if self.sleep_phase!='awake':
            self._display_phase('awake')
            self._wake()

    def _service_display(self,now):
        if self.wake_wait and self.stream.display_mode=='awake' and (
                self._rendered_screen!='sleep_notice' and self.stream.ready
                and self.stream.previous==self.stream.latest
                and self.stream.operations is None and self.stream.pending is None):
            self.wake_wait=False
        if not self.sleep_timeout or not self.stream.ready:return
        if self.minute_timers:
            idle = 0. if self.last_buttons else now - self.last_activity
            remaining = self.sleep_timeout - idle
            notice = min(self.sleep_notice, self.sleep_timeout - self.dim_timeout)
            if remaining <= 0.:
                self._display_phase('off')
            elif remaining <= notice:
                self._display_phase('notice')
                if self.stream.previous != sleep_wire():
                    self.notice_at = None
                elif self.notice_at is None and self.stream.display_mode == 'dim' and self.stream.operations is None:
                    self.notice_at = now
                elapsed = max(0., now - self.notice_at) if self.notice_at is not None else 0.
                level = max(1, 16 - int(16 * elapsed / notice))
                if self.notice_level != level:
                    self.notice_level = self.stream.fade_step = level
                    self.render_at = 0.
            else:
                self._display_phase('dim' if idle >= self.dim_timeout else 'awake')
            return
        if now>=self.display_check_at:
            try:
                self.display_work=self._display_working(now)
                self.display_work_error=None
            except Exception as exc:
                # An unavailable work detector must keep the view visible.
                self.display_work=True
                self.display_work_error=str(exc)
            self.display_check_at=now+.25
        if self.last_buttons or now-self.last_activity<self.sleep_timeout:
            self._display_phase('awake')
        elif self.display_work:
            self._display_phase('dim')
        elif self.sleep_phase=='notice':
            confirmed=(self.stream.display_mode=='dim' and self.stream.previous==sleep_wire()
                       and self.stream.operations is None and self.stream.pending is None)
            if not confirmed:self.notice_at=None
            elif self.notice_at is None:self.notice_at=now
            elif now-self.notice_at>=self.sleep_notice:self._display_phase('off')
        elif self.sleep_phase!='off':
            self._display_phase('notice')

    def set_thermal_test_hold(self, hold):
        self.thermal_test_hold=bool(hold)
        if not hold:
            self.next_query=0.;self.render_at=0.
        self._wake()

    def _tick(self,now):
        if not self.connected or self.shutdown_latched:return self.reactor.NEVER
        self._tick_count=getattr(self,'_tick_count',0)+1
        if self.thermal_test_hold:
            # Freeze bulk pixels and recovery retries during the bounded
            # heater/cooling trial. Keep the current panel lease and buttons.
            if now>=self.next_query:
                if self.stream.enabled:
                    gen=self.stream.generation
                    self.commands['ping'].send([gen>>32,gen&0xffffffff])
                self.commands['query'].send([])
                self.next_query=now+.5
            self._held_keys(now)
            return now+.1
        if now>=self.next_query:
            try:
                if self.stream.enabled:
                    gen=self.stream.generation;self.commands['ping'].send([gen>>32,gen&0xffffffff])
                self.commands['query'].send([])
                if self.fast and now>=self._next_fast_query:
                    self.commands['fast_query'].send([])
                    self._next_fast_query=now+1.
            except Exception as exc:
                self._lose(now,'OLED command unavailable: '+str(exc))
            self.next_query=now+.25
        self._held_keys(now)
        self._service_display(now)
        if now>=self.render_at:
            self._render(now)
            self.render_at=(self.reactor.NEVER if self.sleep_phase in ('notice','off')
                            else now+self.stream.refresh_interval)
        self.stream.set_display_mode('dim' if self.sleep_phase=='notice' else self.sleep_phase)
        if self.recovery_state=='backoff' and now>=self.recover_at:
            # Don't burn through retries while the underlying MCU is absent.
            if self._base_live(now) and self._wire_live(now,False):
                self.retries+=1
                try:self._start(now,'automatic_retry')
                except ValueError as exc:
                    self.recovery_state='fault';self.recover_at=None;self.recovery_reason=str(exc)
            else:self.recover_at=now+.25
        self._pump(now)
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
        if self.fast:
            # ACK returns DMA credit. A pending ACK is not a reason to poll
            # every 2 ms: wake only for its deadline, pacing, a new view or IO.
            due=min(now+.050,self.next_query,self.render_at,
                    self.stream.next_wakeup(now))
            if self.last_buttons&3!=3:
                for when in self.repeat_at.values():due=min(due,when)
            if 3 in self.pressed_at and not self.power_fired:
                due=min(due,self.pressed_at[3]+.301)
            if self.recovery_state=='backoff' and self.recover_at is not None:
                due=min(due,self.recover_at)
            return max(now+.0001,due)
        # The legacy single-operation ABI retains its original pacing.
        busy=self.stream.enabled and (self.stream.operations is not None or
            self.stream.pending is not None or self.stream.latest!=self.stream.previous)
        if busy or self.pressed_at:return now+self.interval
        if self.sleep_phase=='off' and self.stream.display_mode=='off':
            return max(now+self.interval, self.next_query if self.minute_timers else
                       min(self.next_query, self.display_check_at))
        due=min(now+.050,self.next_query,self.render_at)
        if self.recovery_state=='backoff' and self.recover_at is not None:
            due=min(due,self.recover_at)
        return max(now+self.interval,due)

    def _wake(self):
        self._publish_input_receipt()
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
            read=getattr(self.board,'panel_temperature',None)
            values=[read(i,now)[0] for i in range(8)] if read else status['temperatures']
            return 'Temperatures',[('Back','home')]+[(n+': '+('---' if t is None else '%.1f°C'%t),None)
                for n,t in zip(names,values)]
        if self.page=='inputs':
            values=status.get('inputs',{}).get('raw',{}) if status.get('inputs_age',99.)<.75 else {}
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
        if self.sleep_phase=='off':return
        if self.sleep_phase=='notice':
            self._render_wire = sleep_wire()
            if self.stream.latest != self._render_wire:
                self.stream.request(self._render_wire, urgent=True)
            preview = fading_sleep_frame(self.notice_level) if self.minute_timers else self._render_wire
            self.preview.observe(now, ('sleep_notice',), preview)
            self._rendered_screen='sleep_notice'
            self._rendered_menu_page=None
            self._rendered_frames+=1
            self._user_refresh_pending=False
            return
        # The RC7.2 static OEM screen was recomposed every .25s even when
        # unchanged; that wasted Orange Pi reactor time. Cache by UI generation.
        cal=self._cal()
        cal_status=cal.get_status(now) if cal else {}
        measuring=cal_status.get('state') in (
            'preparing_t0','measuring_t0','preparing_t1','measuring_t1')
        snapshot=None
        menu_view=None
        native_wire=None
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        procedure_view=None if zortrax is None else zortrax.view_status()
        if self.oem_menu is not None and not measuring and not self.message:
            if procedure_view is not None:
                menu_view=procedure_view
                snapshot=('zortrax',menu_view['page'],menu_view['generation'],menu_view['selected'],
                          menu_view.get('icon'),activity_index(menu_view, now))
            elif self.oem_display_mode=='status':
                native_wire=self.native_status.render(now,self.board)
                snapshot=('status',native_wire)
            else:
                menu_view=self.oem_menu.view()
                snapshot=('menu',menu_view['page'],menu_view['generation'],
                          menu_view['selected'],menu_view['window'],
                          menu_view.get('icon'),menu_view.get('notice'))
            if snapshot==self._oem_render_cache:
                self._render_cache_hits+=1
                self._user_refresh_pending=False
                return
        frame=Frame() if native_wire is None and menu_view is None else None
        if native_wire is not None:
            wire=native_wire
        elif measuring:
            counts=cal_status.get('sample_counts',[0,0])
            frame.progress('Nozzle Z Height',
                           'T0 %d/2  T1 %d/3'%(counts[0],counts[1]),
                           sum(counts)*20)
        elif self.message:
            frame.status(self.message[0],self.message[1])
        elif menu_view is not None:
            wire=menu_wire(menu_view, index=activity_index(menu_view, now))
        elif self.oem_menu is not None:
            wire=menu_wire(self.oem_menu.view())
            frame=None
        else:
            title,rows=self._rows(now)
            self.selection=max(0,min(self.selection,len(rows)-1))
            frame.menu(title,[r[0] for r in rows],self.selection,self.page!='home')
        if frame is not None:wire=frame.wire_bytes()
        self._render_wire=wire
        self.stream.request(wire,urgent=self._user_refresh_pending)
        self._zortrax_frame=(procedure_view['page'],procedure_view['generation'],wire,
            self.stream.generation) if procedure_view is not None and menu_view is procedure_view else None
        if measuring:self._rendered_screen='calibration'
        elif self.message:self._rendered_screen='message'
        elif native_wire is not None:self._rendered_screen='klipper_status'
        elif menu_view is not None or self.oem_menu is not None:
            self._rendered_screen='oem_menu'
        else:self._rendered_screen='legacy'
        self._rendered_menu_page=(menu_view or {}).get('page')
        self._rendered_frames+=1
        self._oem_render_cache=snapshot
        self._user_refresh_pending=False
        self._frame_receipt=self._input_receipt()
        event, self._navigation_event = self._navigation_event, None
        if event is not None and event[0] == self._frame_receipt:
            rendered = self.reactor.monotonic()
            if rendered >= event[1]:
                self._input_to_render_ms.append(1000. * (rendered - event[1]))
                self._navigation_frame = (self.stream.generation, wire, event[1], self.stream.completed_frames)
        self._publish_input_receipt()
        self.preview.observe(now, (self._rendered_screen, self._frame_receipt, self.sleep_phase), wire)

    def _input_receipt(self):
        service=getattr(getattr(self,'board',None),'zortrax',None)
        view=None if service is None else service.view
        if view is not None:
            # Retain the actual view, not id(view): queued serial input must
            # keep it alive and cannot match a later allocation at the same ID.
            scope=('procedure',view,view.kind)
            selection=(view.generation,view.selected)
        elif self.oem_menu is not None:
            scope=('menu',self.oem_display_mode,self.oem_menu.page)
            selection=(self.oem_menu.generation,self.oem_menu.selected)
        else:
            scope=('legacy',self.page);selection=self.selection
        return (self.stream.generation,scope,selection,bool(self.message))

    def _publish_input_receipt(self):
        # Read atomically by the serial handler; it never calls renderer/menu
        # methods outside the reactor. Captured presses cannot select a new row.
        board=getattr(self,'board',None)
        if board is not None:
            current=self._input_receipt()
            shown=bool(self.sleep_phase in ('awake', 'dim') and self.stream.display_mode != 'off' and self.stream.ready
                and getattr(self,'_frame_receipt',None)==current
                and self.stream.last_frame_bytes==getattr(self,'_render_wire',None))
            board._oled_input_receipt=current+(shown,)

    def _script(self,script):
        try:self.gcode.run_script(script)
        except Exception as exc:
            logging.exception('Inventure panel action failed')
            self.message=('Operation unavailable',str(exc)[:40])

    def _bench_action(self, action, now):
        if action.startswith('bench_manual_'):
            parts = action.removeprefix('bench_manual_').split('_')
            if len(parts) == 1:
                command = 'INVENTURE_FAN_MANUAL_' + parts[0].upper()
            else:
                channel, verb = parts
                ack = {'on': ' ACK=COLD', 'pass': ' ACK=OBSERVED', 'fail': ' ACK=OBSERVED'}
                command = ('INVENTURE_FAN_MANUAL CHANNEL=%s ACTION=%s%s'
                           % (channel, verb.upper(), ack.get(verb, '')))
            self._script(command)
            if self.message is None:
                result = self.board.fan_manual_last
                channel = result['channel']
                label = 'Fan test idle' if channel is None else 'F%d %s' % (channel, result['phase'])
                on, off = result['tach_on_edges'], result['tach_off_edges']
                details = ('Tach ON %s / OFF %s' % (on, off)
                           if on is not None or off is not None else 'Observe the fan')
                self.message = (label, details)
            return
        if action == 'bench_fans_status':
            rows = []
            for start in (0,3):
                values = []
                for i in range(start,start+3):
                    duty = self.board.panel_fan_duty(i,now)
                    value = '--' if duty is None else '%d%%' % round(duty*100/255)
                    values.append('F%d %s' % (i,value))
                rows.append('  '.join(values))
            self.message = tuple(rows)
            return
        if action == 'bench_heat_status':
            values = [self.board.panel_temperature(i,now)[0] for i in range(4)]
            values = ['--' if value is None else '%.1fC' % value for value in values]
            self.message = ('T0 %s    T1 %s' % tuple(values[:2]),
                            'L  %s    R  %s' % tuple(values[2:]))
            return
        fans = self.printer.lookup_object('inventure_fans',None)
        if fans is None or not hasattr(fans,'bench_status'):
            self.message = ('Bench control unavailable','P05/P06 profile in Fluidd')
            return
        if action == 'bench_result':
            status = fans.bench_status()
            heat = status['heat'] or status['last_heat']
            fan = status['fan'] or status['last_fan']
            if heat is not None:
                heat_label = 'Heat %s %s' % (heat['channel'],heat['result'] or heat['phase'])
            else:
                heat_label = 'Heat: idle'
            if fan is not None:
                fan_label = 'Fan %s %s' % (','.join(map(str,fan.get('channels',()))),
                    fan.get('phase') or fan.get('result',''))
            else:
                fan_label = 'Fan: idle'
            self.message = (heat_label.replace('_',' '),fan_label.replace('_',' '))
            return
        commands = {
            'bench_heat_stop':'INVENTURE_HEAT_TEST_CANCEL',
        }
        commands.update({'bench_heat_'+ch:'INVENTURE_HEAT_TEST CHANNEL='+ch+' ACK=OBSERVE'
                         for ch in ('T0','T1','LEFT','RIGHT')})
        self._script(commands[action])
        if self.message is None:
            self.message = ('Bench command accepted','See Test status')

    def _select(self,now):
        if self.message:
            self.message=None;self._user_refresh_pending=True;self.render_at=0.
            return
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        if zortrax is not None and zortrax.view is not None:
            try:
                command=zortrax.panel_select_command(self._zortrax_acknowledged())
                if command:self._script(command)
            except ValueError as exc:self.message=('Wait / release OK',str(exc)[:40])
            self.render_at=0.;self._user_refresh_pending=True
            return
        if self.oem_menu is not None:
            if self.oem_display_mode=='status':
                self.oem_menu.home()
                self.oem_display_mode='menu'
            else:
                selected = self.oem_menu.select()
                if selected == 'exit_to_status':
                    self.oem_display_mode='status'
                elif selected == 'bench':
                    self._bench_action(self.oem_menu.last_action,now)
                elif selected in ('inert','language') and zortrax is not None:
                    try:
                        zortrax.open(self.oem_menu.last_action)
                        self.oem_menu.notice=None
                    except ValueError as exc:self.message=('Operation unavailable',str(exc)[:40])
                elif selected == 'inert' and self.oem_menu.last_action in ('door_unlock','door_lock'):
                    enabled=int(self.oem_menu.last_action == 'door_lock')
                    self._script('INVENTURE_DOOR_POLICY ENABLE=%d' % enabled)
                    if self.message is None:
                        self.oem_menu.notice=None
                        self.message=(('Door check enabled','Close front and top door') if enabled
                                      else ('Door lock disabled','Beware of moving parts'))
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

    def _buttons(self,now,mask,sequence,received_at=None,receipt=None):
        raw_mask=mask
        received=now if received_at is None else received_at
        fresh=(isinstance(received,(int,float)) and not isinstance(received,bool)
               and math.isfinite(received) and 0.<=now-received<=.750)
        self._button_sample_at=received if fresh else None
        if (fresh and self.connected and (self.wake_wait or self.wake_gesture
                or self.sleep_phase in ('notice', 'off'))):
            # A dark panel has no selectable receipt. A physical OK still wakes
            # it; its release must never accept the newly displayed selection.
            menu = getattr(self.board, 'zortrax', None)
            if menu is not None and not menu.observe_buttons(raw_mask, sequence):
                return
            if raw_mask != self.last_buttons:
                self._display_activity(now)
                self.wake_gesture = bool(raw_mask)
                if not raw_mask & 4:
                    self._ok_blocked_until_release = False
                if raw_mask & 8 and not self.last_buttons & 8:
                    self.pressed_at[3] = now
                    self.power_fired = False
                if self.last_buttons & 8 and not raw_mask & 8:
                    self.pressed_at.pop(3, None)
                self.last_buttons = raw_mask
            return
        acknowledged=(receipt is None or len(receipt)<5 or receipt[4])
        if receipt is not None:receipt=receipt[:4]
        current=self._input_receipt() if receipt is not None else None
        if not fresh or receipt is not None and receipt[:2]!=current[:2]:
            # Consume releases without granting new presses from a stale view.
            mask&=self.last_buttons
            self.repeat_at.clear()
            self._stale_button_events=getattr(self,'_stale_button_events',0)+1
        elif receipt is not None and (receipt!=current or not acknowledged):
            mask&=~4
            self._stale_button_events=getattr(self,'_stale_button_events',0)+1
        # A discarded OK remains physically held. An unrelated button edge
        # must not turn it into a new OK press on the now-confirmed screen.
        if raw_mask&4 and not mask&4:self._ok_blocked_until_release=True
        if getattr(self,'_ok_blocked_until_release',False):mask&=~4
        pressed=mask&~self.last_buttons
        if pressed&3 and pressed&4:
            self._ok_blocked_until_release=True;mask&=~4
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        if zortrax is not None and not zortrax.observe_buttons(mask,sequence):return
        if fresh and not raw_mask&4:self._ok_blocked_until_release=False
        if not self.connected or mask==self.last_buttons:return
        pressed=mask&~self.last_buttons; released=self.last_buttons&~mask
        self._display_activity(now)
        if pressed & 3 == 3:pressed &= ~3  # simultaneous UP+DOWN: no selection movement
        for bit in range(4):
            key=1<<bit
            if pressed&key:
                self.pressed_at[bit]=now
                if bit<2:
                    if now-self.last_nav_press>.030:
                        previous_view = self._input_receipt()
                        self._navigate(-1 if bit==0 else 1,now);self.last_nav_press=now
                        new_view = self._input_receipt()
                        if new_view != previous_view:
                            self._navigation_event = (new_view, received)
                    self.repeat_at[bit]=now+.300
                elif bit==2:
                    if now-self.last_ok_press>.200:
                        self._select(now);self.last_ok_press=now
                else:self.power_fired=False
            if released&key:
                if bit==3 and not self.power_fired:
                    if zortrax is not None and zortrax.view is not None:
                        zortrax.panel_back()
                    elif self.oem_menu is not None:
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
        self.render_at=0.;self._user_refresh_pending=True
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        if zortrax is not None and zortrax.view is not None:
            zortrax.panel_move(direction);return
        if self.oem_menu is not None:
            if self.oem_display_mode=='menu':
                self.oem_menu.move(direction)
                self.render_at=0.;self._user_refresh_pending=True
            return
        _,rows=self._rows(now);self.selection=(self.selection+direction)%len(rows)
        self.render_at=0.;self._user_refresh_pending=True

    def _zortrax_acknowledged(self):
        view=getattr(getattr(self,'board',None),'zortrax',None)
        frame=getattr(self,'_zortrax_frame',None)
        if view is None or view.view is None or frame is None:return False
        current=view.view.render()
        return bool(self.sleep_phase=='awake' and not self.message and self.stream.ready
            and frame[0]==current['page'] and frame[1]==current['generation']
            and frame[2]==self.stream.last_frame_bytes and frame[3]==self.stream.generation)

    def _held_keys(self,now):
        for bit in (0,1):
            if self.last_buttons&3==3:continue
            sampled=getattr(self,'_button_sample_at',None)
            if sampled is not None and now-sampled>.750:
                self.repeat_at.pop(bit,None);continue
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
        self._display_activity(now)
        self.retries=0;self.recovery_reason='manual_reinit';self._render(now)
        try:self._start(now,'manual_reinit')
        except ValueError as exc:raise gcmd.error(str(exc))
        self._wake()
    def cmd_text(self,gcmd):
        self._display_activity(self.reactor.monotonic())
        self.message=(gcmd.get('LINE1','Inventure'),gcmd.get('LINE2',''))
        self.render_at=0.;self._user_refresh_pending=True
        self._wake()
    def cmd_wake(self,gcmd):
        self._display_activity(self.reactor.monotonic())
    def timer_preferences(self, values):
        # Earlier minute-mode records could omit the other threshold. Keep the
        # 5/10 effective pair those records used before the defaults changed.
        if values.get('timer_policy') == 'minutes':
            return values.get('dim_min', 5), values.get('sleep_min', 10)
        return values.get('dim_min', self.configured_dim_min), values.get('sleep_min', self.configured_sleep_min)

    def _apply_display_settings(self):
        menu = getattr(self.board, 'zortrax', None)
        values = menu.settings.value if menu is not None else {}
        enabled = values.get('sleep_enabled', bool(self.configured_sleep))
        self.sleep_timeout = values.get('idle_s', self.configured_sleep or 300.) if enabled else 0.
        policy = 'inventure' if 'idle_s' in values or 'notice_s' in values else self.configured_policy
        self.minute_timers = values.get('timer_policy', policy) == 'minutes'
        if self.minute_timers:
            dim, sleep = self.timer_preferences(values)
            self.dim_timeout = min(dim, sleep) * 60
            self.sleep_timeout = sleep * 60 if enabled else 0.
        self.sleep_notice = values.get('notice_s', self.configured_notice)
        self.stream.awake_level = values.get('oled_level', self.configured_brightness) - 1

    def cmd_display_control(self, gcmd):
        menu = self.board.zortrax
        page = gcmd.get('PAGE', None)
        updates = {}
        if page is not None:
            page = page.lower()
            if page not in ('root', 'settings', 'information', 'language', 'sorting'):
                raise gcmd.error('Unknown Inventure menu page')
            if self.oem_menu is None or menu.active is not None or menu.fan_session is not None or menu.view is not None and menu.view.kind == 'confirm':
                raise gcmd.error('Finish the active menu prompt before changing pages')
            if page != 'root' and page not in MENU:
                raise gcmd.error('This page is unavailable')
            menu.view = None
            self.oem_menu.home()
            if page != self.oem_menu.root_page:
                self.oem_menu.stack.append((self.oem_menu.page, 0, self.oem_menu.window))
                self.oem_menu.page = page
                self.oem_menu.generation += 1
            self.oem_display_mode = 'menu'
            self.message = None
        if page and gcmd.get_int('PROMPT', 0, minval=0, maxval=1):
            menu.open_operator_page()
        if gcmd.get('LEVEL', None) is not None:
            updates['oled_level'] = gcmd.get_int('LEVEL', minval=1, maxval=16)
        if gcmd.get('ENABLED', None) is not None:
            updates['sleep_enabled'] = bool(gcmd.get_int('ENABLED', minval=0, maxval=1))
        for option, key, high in (('IDLE', 'idle_s', 86400), ('NOTICE', 'notice_s', 3600)):
            if gcmd.get(option, None) is not None:
                updates[key] = gcmd.get_int(option, minval=1, maxval=high)
        for option, key in (('DIM', 'dim_min'), ('SLEEP', 'sleep_min')):
            if gcmd.get(option, None) is not None:
                updates[key] = gcmd.get_int(option, minval=1, maxval=60)
        if {'dim_min', 'sleep_min'} & updates.keys():
            if {'idle_s', 'notice_s'} & updates.keys():
                raise gcmd.error('Use DIM/SLEEP minutes or IDLE/NOTICE seconds in one command')
            dim, sleep = self.timer_preferences(menu.settings.value)
            updates.setdefault('dim_min', dim)
            updates.setdefault('sleep_min', sleep)
            updates['timer_policy'] = 'minutes'
        elif {'idle_s', 'notice_s'} & updates.keys():
            updates['timer_policy'] = 'inventure'
        if updates:
            menu.settings.set(**updates)
            self._apply_display_settings()
            self._observe_external_activity()
        if updates or page or gcmd.get_int('WAKE', 0, minval=0, maxval=1):
            self._display_activity(self.reactor.monotonic())
            self.render_at = 0.
            self._user_refresh_pending = True
            self._wake()
        if self.minute_timers:
            dim, sleep = self.timer_preferences(menu.settings.value)
            gcmd.respond_info('OLED timers | preferred DIM %d min | effective DIM %d min | SLEEP %d min' %
                              (dim, self.dim_timeout // 60, sleep))
            return
        gcmd.respond_info('Inventure OLED | brightness %d/16 | sleep %s | idle %g s | notice %g s' % (
            self.stream.awake_level + 1, 'on' if self.sleep_timeout else 'off', self.sleep_timeout, self.sleep_notice))

    def cmd_menu_status(self,gcmd):
        details=self.oem_menu.status() if self.oem_menu is not None else {'oem_menu':False}
        zortrax=getattr(getattr(self,'board',None),'zortrax',None)
        if zortrax is not None:
            details['zortrax_menu_api']=1
            details['procedure']=None if zortrax.active is None else {
                'backend':zortrax.active['backend'],'action':zortrax.active['action']}
            details['procedure_view']=None if zortrax.view is None else zortrax.view.status()
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
                'thermal_test_hold':self.thermal_test_hold,
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
                'display_sleep':{'phase':self.sleep_phase,'panel_mode':self.stream.display_mode,
                    'timeout_s':self.sleep_timeout,'notice_s':self.sleep_notice,
                    'dim_after_s':self.dim_timeout if self.minute_timers else None,
                    'brightness_level':self.stream.awake_level+1,'storage':'orange_pi',
                    'timer_policy':'minutes' if self.minute_timers else 'inventure',
                    'notice_acknowledged_at':self.notice_at,'wake_pending':self.wake_wait,
                    'work_status_error':self.display_work_error},
                'host_frame_acknowledged':bool(self.stream.ready and
                    self.stream.previous is not None and
                    self.stream.previous==self.stream.latest),
                'ack_pump_deferrals':self._ack_pump_deferrals,
                'scheduler_ticks':getattr(self,'_tick_count',0),
                'stale_button_events':getattr(self,'_stale_button_events',0),
                'uart_buffer_stats':dict(getattr(self.board,'uart_buffer_stats',{})),
                'rendered_menu_page':self._rendered_menu_page,
                'oem_menu':self.oem_menu.status() if self.oem_menu is not None else None,'events':list(self.events),
                'ack_diagnostic':{'last_tx':self._oled_last_tx,
                    'last_ack':self._oled_last_ack,
                    'attempt_sent':self.stream.sent-self._oled_session_sent,
                    'attempt_acked':self.stream.acked-self._oled_session_acked},
                'ui_transport':{'host_release':HOST_REVISION,
                    'navigation_api':'INVENTURE-NAV-1',
                    'mode':'packbits_spi2_dma' if self.fast else 'legacy_abi2',
                    'spi_requested_hz':self.speed,
                    'mcu_dma':dict(self._fast_state),
                    'window':4 if self.fast else 1,
                    'peak_pending':self.stream.peak_pending if self.fast else None,
                    'active_work':self.stream.active_work if self.fast else None,
                    'work_uart_budget_bytes_s':self.stream.work_budget if self.fast else None,
                    'last_payload_bytes':self.stream.last_payload_bytes if self.fast else None,
                    'last_frame_ms':self.stream.last_frame_ms if self.fast else None,
                    'optimized_frames':self.stream.optimized_frames if self.fast else 0,
                    'coalesced_requests':self.stream.coalesced_requests if self.fast else 0,
                    'recovered_acks':self.stream.recovered_acks if self.fast else 0,
                    'frame_p95_ms':(sorted(self.stream.frame_samples)[int(.95*(len(self.stream.frame_samples)-1))]
                        if self.fast and self.stream.frame_samples else None),
                    'button_to_host_frame_ms':self._timing_summary(self._input_to_render_ms),
                    'button_to_mcu_frame_ack_ms':self._timing_summary(self._input_to_ack_ms),
                    'status_footer_font':'5x7',
                    'status_layout':KlipperStatus.layout,
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
