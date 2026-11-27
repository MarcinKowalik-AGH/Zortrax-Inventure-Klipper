"""Keep optional UART traffic out of native endstop moves.

Observe Klipper's existing begin/end events; do not wrap or change a motion
owner, step deadline, coordinate, heater or motor enable. The physical OLED
keeps its last image during the short native homing move. Lease pings, panel
buttons, MCU safety and cached Fluidd progress remain available.
"""
import logging

RELEASE = 'RC8-COMMUNICATION-GUARD-R25.3'


class CommunicationQuietOLED:
    def __init__(self, *args, **kwargs):
        self._quiet_homing_moves = set()
        self._quiet_homing_started = None
        self._quiet_homing_seconds = 0.
        self._quiet_homing_count = 0
        super().__init__(*args, **kwargs)
        self.printer.register_event_handler('homing:homing_move_begin', self._quiet_homing_begin)
        self.printer.register_event_handler('homing:homing_move_end', self._quiet_homing_end)
        for event in ('klippy:shutdown', 'klippy:disconnect'):
            self.printer.register_event_handler(event, self._quiet_homing_reset)

    def _quiet_homing_begin(self, move):
        if not self._quiet_homing_moves:
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
        if self._quiet_homing_moves:
            return False
        return super()._send_one(now)

    def _next_tick(self, now):
        # A queued image must not turn the paused sender into a 100 us loop.
        if self._quiet_homing_moves:
            return now+.1
        return super()._next_tick(now)

    def _tick(self, now):
        if not self._quiet_homing_moves:
            return super()._tick(now)
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
            native_homing_pixels_paused=bool(self._quiet_homing_moves),
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
        return result
