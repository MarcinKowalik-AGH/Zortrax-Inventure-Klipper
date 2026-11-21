#!/usr/bin/env python3
# Adapted from the M300 RC34 OLED navigation check, Marcin Kowalik.
"""Exercise event waits with the pinned real Klipper reactor/API request parser.

No serial port, physical MCU, printer motion, heater, or network is involved.
Recorded timings cover host event delivery only, NOT browser/OLED latency.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.klipper.resolve() / 'klippy'))
    import reactor
    import webhooks
    path = args.klipper / 'klippy/extras/inventure_oled_preview.py'
    spec = importlib.util.spec_from_file_location('navigation_preview', path)
    preview_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview_module)
    results = []
    for name in ('one_detent', 'first_to_last', 'cancel_hidden_widget'):
        loop = reactor.Reactor()
        endpoints = {}
        server = SimpleNamespace(register_endpoint=lambda key, cb: endpoints.__setitem__(key, cb))
        printer = SimpleNamespace(lookup_object=lambda key, default=None: server if key == 'webhooks' else default)
        owner = SimpleNamespace(printer=printer, reactor=loop, sleep_phase='awake',
                                stream=SimpleNamespace(ready=True), _render_wire=bytes(8192),
                                _base_live=lambda now: True, _wire_live=lambda now: True)
        preview = preview_module.OledPreview(owner)
        preview.bind()
        preview.observe(loop.monotonic(), ('menu', 0), owner._render_wire)
        client = SimpleNamespace(is_closed=lambda: False)
        request = webhooks.WebRequest(client, json.dumps({'id': 1,
            'method': 'inventure/oled_preview/wait', 'params': {'client': name,
            'epoch': preview.epoch, 'navigation_revision': preview.revision}}).encode())
        marks = {}
        failures = []

        def wait(now):
            try:
                endpoints['inventure/oled_preview/wait'](request)
                marks['returned'] = loop.monotonic()
            except Exception as exc:
                failures.append(str(exc))
            finally:
                loop.end()

        def navigate(now):
            marks['event'] = loop.monotonic()
            if name == 'cancel_hidden_widget':
                cancel = webhooks.WebRequest(client, json.dumps({'method':
                    'inventure/oled_preview/cancel_wait', 'params': {'client': name}}).encode())
                endpoints['inventure/oled_preview/cancel_wait'](cancel)
            else:
                for selection in range(1, 16 if name == 'first_to_last' else 2):
                    owner._render_wire = bytes([selection]) * 8192
                    preview.observe(now, ('menu', selection), owner._render_wire)

        def fail(now):
            failures.append('event wait did not complete')
            loop.end()

        start = loop.monotonic()
        loop.register_callback(wait)
        loop.register_callback(navigate, start + .020)
        loop.register_callback(fail, start + 2.)
        try:
            loop.run()
        finally:
            loop.finalize()
        assert not failures, (name, failures)
        assert not preview.waiters, (name, 'leaked waiter')
        reply = request.finish()['result']
        assert reply['changed'] == (name != 'cancel_hidden_widget'), (name, reply)
        if reply['changed']:
            assert reply['frame']['online'] is True
            assert reply['frame']['navigation_revision'] == (16 if name == 'first_to_last' else 2)
        results.append({'case': name, 'result': 'PASS',
                        'event_to_reply_ms': round(1000. * (marks['returned'] - marks['event']), 4)})
    output = {'status': 'PASS_PINNED_KLIPPER_NAVIGATION_EVENTS', 'cases': results,
              'physical_hardware': False, 'browser_latency_measured': False,
              'global_status_interval_changed': False, 'animations_changed': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output))


if __name__ == '__main__':
    main()
