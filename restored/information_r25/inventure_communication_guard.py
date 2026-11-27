"""Pair interruptible MCU DMA with the existing OLED and Home presentation.

Observe Klipper's existing begin/end events; do not wrap or change a motion
owner, step deadline, coordinate, heater or motor enable. Paired R26 MCU keeps
Home animation active. Older MCU keeps its last image throughout Home. Lease
pings, panel buttons, MCU safety and cached Fluidd progress remain available.
"""
import logging

RELEASE = 'RC8-COMMUNICATION-R26.1'
BACKGROUND_PRIORITY_CLOCK=0x7fffffff00000000
COEXIST_RESPONSE=('inventure_oled_coexist_state irq_ticks=%u blocks=%u wire_bytes=%u errors=%u '
    'peak=%c uart_overruns=%u uart_noise=%u uart_framing=%u uart_rx_overflows=%u')


class CommunicationQuietOLED:
    def __init__(self, *args, **kwargs):
        self._quiet_homing_moves = set()
        self._quiet_homing_started = None
        self._quiet_homing_seconds = 0.
        self._quiet_homing_count = 0
        self._coexistence=False
        self._coexist_state={}
        self._coexist_received=None
        self._coexist_query_at=0.
        super().__init__(*args, **kwargs)
        self.printer.register_event_handler('homing:homing_move_begin', self._quiet_homing_begin)
        self.printer.register_event_handler('homing:homing_move_end', self._quiet_homing_end)
        for event in ('klippy:shutdown', 'klippy:disconnect'):
            self.printer.register_event_handler(event, self._quiet_homing_reset)

    def _build_config(self):
        super()._build_config()
        self._coexistence=str(self.mcu.get_constants().get('INVENTURE_OLED_COEXISTENCE_ABI'))=='1'
        if self._coexistence:
            if not self.mcu.check_valid_response(COEXIST_RESPONSE):
                raise self.printer.config_error('Inventure cooperative OLED lacks its transport diagnostics')
            self.commands['coexist_query']=self.mcu.lookup_command('inventure_oled_coexist_query',cq=self.cq)
            self.mcu.register_serial_response(self._coexist_response,COEXIST_RESPONSE)

    def _coexist_response(self,params):
        data={key:params[key] for key in ('irq_ticks','blocks','wire_bytes','errors','peak',
            'uart_overruns','uart_noise','uart_framing','uart_rx_overflows')}
        received=params.get('#receive_time',self.reactor.monotonic())
        def deliver(now):
            if self.connected and not self.shutdown_latched:
                self._coexist_state=data
                self._coexist_received=received
        self.reactor.register_async_callback(deliver)

    def _quiet_homing_begin(self, move):
        if not self._quiet_homing_moves and not self._coexistence:
            self._quiet_homing_started = self.reactor.monotonic()
            self._quiet_homing_count += 1
        self._quiet_homing_moves.add(move)

    def _quiet_homing_end(self, move):
        self._quiet_homing_moves.discard(move)
        if not self._quiet_homing_moves:
            self._quiet_homing_reset()

    def _quiet_homing_reset(self, *args):
        if self._quiet_homing_started is not None:
            self._quiet_homing_seconds += max(0., self.reactor.monotonic()-self._quiet_homing_started)
        self._quiet_homing_started = None
        self._quiet_homing_moves.clear()
        self.render_at = 0.

    def _send_one(self, now):
        # The ACK callback also pumps the transport. Gating _tick alone would
        # leave that second pixel producer running during a homing move.
        if self._home_pixels_paused():
            return False
        return super()._send_one(now)

    def _home_sequence_active(self):
        # Cached owner flags cover heat, SERVICE handoff and MCU-local PC14,
        # which emit no Klipper HomingMove events. Never query the MCU here.
        native = self.printer.lookup_object('inventure_native_z', None)
        menu = getattr(self.board, 'zortrax', None)
        presentation = getattr(menu, 'home_ui', None)
        return bool(getattr(presentation, 'busy', False) or
                    native is not None and (native.sequence_running or native.running))

    def _home_pixels_paused(self):
        # Only the paired F207 driver with interruptible SPI/DMA preparation
        # advertises this capability. Older MCU retains the R25 quiet fallback.
        return not self._coexistence and (bool(self._quiet_homing_moves) or self._home_sequence_active())

    def _next_tick(self, now):
        # A queued image must not turn the paused sender into a 100 us loop.
        if self._home_pixels_paused():
            return now+.1
        return super()._next_tick(now)

    def _tick(self, now):
        if not self._home_pixels_paused():
            due=super()._tick(now)
            if self._coexistence and self.connected and not self.shutdown_latched and now>=self._coexist_query_at:
                self.commands['coexist_query'].send([],reqclock=BACKGROUND_PRIORITY_CLOCK)
                self._coexist_query_at=now+2.
            return due
        if not self.connected or self.shutdown_latched:
            return self.reactor.NEVER
        self._tick_count += 1
        # Same 500 ms lease/query schedule as the existing thermal hold.
        # Board heartbeats, endstops and the stop handler keep their owners.
        if now >= self.next_query:
            if self.stream.enabled:
                generation = self.stream.generation
                self.commands['ping'].send([generation >> 32, generation & 0xffffffff])
            self.commands['query'].send([])
            self.next_query = now+.5
        self._held_keys(now)
        if now >= self.render_at:
            # This only replaces the cached image. No SPI/UART pixel command.
            try:
                self._render(now)
            except Exception:
                logging.exception('Inventure cached Home display unavailable')
            self.render_at = now+.5
        return now+.1

    def get_status(self, now):
        result = super().get_status(now)
        result.update(communication_guard_release=RELEASE,
            oled_transport_mode='cooperative_dma' if self._coexistence else 'legacy_quiet',
            transport_health=dict(self._coexist_state),
            transport_health_age=None if self._coexist_received is None else max(0.,now-self._coexist_received),
            home_sequence_pixels_paused=self._home_sequence_active() and not self._coexistence,
            home_pixels_paused=self._home_pixels_paused(),
            native_homing_pixels_paused=bool(self._quiet_homing_moves) and not self._coexistence,
            native_homing_quiet_count=self._quiet_homing_count,
            native_homing_quiet_seconds=self._quiet_homing_seconds + (
                max(0., now-self._quiet_homing_started) if self._quiet_homing_started is not None else 0.))
        return result


class CommunicationQuietInformation:
    def _communication_busy(self, now):
        native = self.printer.lookup_object('inventure_native_z', None)
        if native is not None and native.get_status(now).get('running'):
            return 'inventure_native_z'
        oled = self.printer.lookup_object('inventure_oled', None)
        if getattr(oled, '_quiet_homing_moves', None):
            return 'native_homing'
        if self.board.load_test_context is not None:
            return 'service'
        stats = self.printer.lookup_object('print_stats', None)
        if stats is not None and stats.get_status(now).get('state') in ('printing', 'paused'):
            return 'print_stats'
        toolhead = self.printer.lookup_object('toolhead', None)
        if toolhead is not None:
            queued, estimated, empty = toolhead.check_busy(now)
            if not empty or queued > estimated:
                return 'toolhead'
        return None

    def synchronize(self, now):
        if self.ready and self.transport is not None and not self.printer.is_shutdown():
            reason = self._communication_busy(now)
            if reason:
                # Defer the complete optional query/journal/Flash sync. Cached
                # Info and its independent print-time checkpoints still work.
                self.wait_reason = reason
                return now+5.
        return super().synchronize(now)

    def get_status(self, now):
        result = super().get_status(now)
        result['communication_guard_release'] = RELEASE
        oled=self.printer.lookup_object('inventure_oled',None)
        if oled is not None:
            result['oled_transport_mode']='cooperative_dma' if getattr(oled,'_coexistence',False) else 'legacy_quiet'
            result['transport_health']=dict(getattr(oled,'_coexist_state',{}))
        return result
