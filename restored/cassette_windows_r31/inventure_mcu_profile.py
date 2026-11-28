# SPDX-License-Identifier: GPL-3.0-or-later
class InventureMCU(InventureMCU):
    _FEED_START = 'inventure_factory_feed_start owner=%u run=%u condition=%c timeout_ms=%hu'
    _FEED_KEEPALIVE = 'inventure_factory_feed_keepalive owner=%u run=%u'
    _FEED_STATE = 'inventure_factory_feed_state owner=%u run=%u state=%c a=%c b=%c'
    _FEED_PHASES = {
        'L2': (2, 1, 'wait_a', 'a_timeout'),
        'U0': (3, 2, 'wait_unload', None),
        'U3': (3, 3, 'retract_until_a', 'unload_a_timeout'),
    }
    _FEED_RUNNING, _FEED_REACHED, _FEED_EXPIRED, _FEED_CANCELLED, _FEED_REJECTED = range(1, 6)

    def _build_config(self):
        super()._build_config()
        self._feed_start = None
        if (str(self.mcu.get_constants().get('INVENTURE_FACTORY_FEED_WINDOW_ABI')) == '1'
            and 'factory_session' in self.commands):
            self._feed_start = self.mcu.lookup_command(self._FEED_START)
            self._feed_keepalive = self.mcu.lookup_command(self._FEED_KEEPALIVE)
            self.mcu.register_serial_response(self._feed_response, self._FEED_STATE)

    def _feed_response(self, params):
        self.reactor.register_async_callback(lambda now: self._feed_deliver(params))

    def _feed_deliver(self, params):
        context = self.load_test_context
        if context is None:
            return
        factory = context.get('factory', {})
        window = factory.get('cassette_window')
        if (window is None or tuple(window['args'][:2]) != (params['owner'], params['run'])
            or window['state'] >= self._FEED_REACHED
            or params['state'] not in range(self._FEED_RUNNING, self._FEED_REJECTED + 1)
            or params['a'] not in (0, 1) or params['b'] not in (0, 1)):
            return
        if not window['state'] and params['state'] == self._FEED_RUNNING:
            window.update(check_at=window['deadline'] + .30, resent=False)
        window['state'] = params['state']
        factory['snapshot'].update(a=params['a'], b=params['b'],
                                   mode=window['mode'],
                                   running=int(window['state'] == self._FEED_RUNNING))

    def _feed_window_tick(self, window, *, keepalive=True):
        if window['state'] in (self._FEED_CANCELLED, self._FEED_REJECTED):
            raise ValueError('MCU %s the cassette feed phase' %
                             ('cancelled' if window['state'] == self._FEED_CANCELLED else 'rejected'))
        if not self.connected:
            raise ValueError('MCU disconnected during cassette feed')
        now = self.reactor.monotonic()
        if window['state'] <= self._FEED_RUNNING and now >= window['check_at']:
            if window['resent']:
                raise ValueError('MCU cassette feed result timed out')
            self._feed_start.send(window['args'])
            window.update(resent=True, check_at=now + .50)
        if keepalive and now >= window['keepalive_at']:
            self._feed_keepalive.send(window['args'][:2])
            window['keepalive_at'] = now + .40

    def _factory_session(self, context, mode=None, monitor=None, next_tool=None, ceiling_steps=None):
        factory = context['factory']
        window = factory.get('cassette_window')
        if window is not None and all(value is None for value in (mode, monitor, next_tool, ceiling_steps)):
            moving = factory.get('move') is not None
            self._feed_window_tick(window, keepalive=not moving)
            if moving:
                # U3 still samples the inlet at the existing E-control cadence,
                # even if the feed completion report was lost on the wire.
                row = super()._factory_session(context)
                if window['state'] == self._FEED_REACHED:
                    row['a'] = factory['snapshot']['a'] = 0
                return row
            return dict(factory['snapshot'])
        row = super()._factory_session(context, mode, monitor, next_tool, ceiling_steps)
        if window is not None and mode != window['mode']:
            factory.pop('cassette_window')
        return row

    def _feed_window_start(self, factory, phase):
        mode, condition, _, setting = phase
        window = factory.get('cassette_window')
        if window is None:
            seconds = factory['parameters'][setting] if setting else 10.
            run = factory.get('cassette_window_run', 0) + 1
            now = self.reactor.monotonic()
            window = dict(args=[factory['token'], run, condition, math.ceil(seconds * 1000.)],
                          mode=mode, state=0, deadline=now+seconds, check_at=now+.50,
                          keepalive_at=now+.40, resent=False)
            factory.update(cassette_window=window, cassette_window_run=run, mode=mode, monitor=0)
            self._feed_start.send(window['args'])
        self._feed_window_tick(window)
        if window['state']:
            return dict(factory['snapshot'], mode=mode)
        return None

    def _factory_action(self, context, action):
        factory = context['factory']
        kind = action['kind']
        phase = self._FEED_PHASES.get(factory.get('phase'))
        if (getattr(self, '_feed_start', None) is not None and phase is not None
            and factory['operation'] in ('AUTO', 'UNLOAD')):
            if kind == 'mode' and action['mode'] == phase[0]:
                return self._feed_window_start(factory, phase)
            window = factory.get('cassette_window')
            if window is not None and kind == phase[2]:
                if kind == 'wait_a' or kind == 'wait_unload':
                    if window['state'] < self._FEED_REACHED:
                        return None
                    return dict(factory['snapshot'], ok=window['state'] == self._FEED_REACHED)
                # E still follows the existing Klipper segment and stop logic.
                if window['state'] == self._FEED_EXPIRED and factory['move'] is None:
                    return dict(factory['snapshot'], ok=False, sent_steps=0)
        return super()._factory_action(context, action)
