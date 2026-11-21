#!/usr/bin/env python3
"""Exercise the Inventure wait API with Moonraker's dispatcher and KlippyConnection.

A real WebSocket and a connected Unix socket pair carry the normal protocols.
The hardware button and framebuffer are supplied by a fixture. No printer I/O.
"""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import sys
from threading import Thread
from time import perf_counter
from types import SimpleNamespace


async def check(klipper):
    sys.path.insert(0, str(klipper / 'klippy'))
    import reactor
    import webhooks
    from extras.inventure_oem_menu import OEMMenu
    from extras.inventure_oled_graphics import menu_wire
    from extras.inventure_oled_preview import OledPreview, encode_frame
    from moonraker.common import APIDefinition, APITransport, JsonRPC, RequestType, TransportType
    from moonraker.components.klippy_connection import KlippyConnection
    from tornado.httpserver import HTTPServer
    from tornado.netutil import bind_sockets
    from tornado.web import Application
    from tornado.websocket import WebSocketHandler, websocket_connect

    loop = reactor.Reactor()
    klippy_sock, moonraker_sock = socket.socketpair()
    klippy_sock.setblocking(False)
    printer = SimpleNamespace(get_reactor=lambda: loop, get_start_args=lambda: {},
                              set_rollover_info=lambda *args, **kwargs: None, command_error=ValueError)
    web = webhooks.WebHooks(printer)
    printer.lookup_object = lambda *args: web
    client = webhooks.ClientConnection(web.sconn, klippy_sock)
    web.sconn.clients[client.uid] = client
    menu = OEMMenu('en', operational=True)
    owner = SimpleNamespace(printer=printer, reactor=loop, sleep_phase='awake', stream=SimpleNamespace(ready=True),
                            _base_live=lambda _: True, _wire_live=lambda _: True, _render_wire=menu_wire(menu.view()))
    preview = OledPreview(owner)
    preview.bind()
    preview.observe(loop.monotonic(), menu.selected, owner._render_wire)
    thread = Thread(target=loop.run, daemon=True)
    thread.start()

    connection = KlippyConnection.__new__(KlippyConnection)
    reader, connection.writer = await asyncio.open_connection(sock=moonraker_sock)
    connection.closing = False
    connection.pending_requests = {}
    connection.event_loop = SimpleNamespace(register_callback=lambda cb, *a: asyncio.create_task(cb(*a)))
    read_loop = asyncio.create_task(connection._read_stream(reader))
    rpc = JsonRPC(SimpleNamespace(is_verbose_enabled=lambda: False))
    for path in web._endpoints:
        if path.startswith('inventure/oled_preview/'):
            api = APIDefinition.create(path, RequestType.GET | RequestType.POST, connection.request, is_remote=True)
            for method in api.rpc_methods:
                rpc.register_method(method, api.request_types, api)

    class WebSocket(WebSocketHandler, APITransport):
        transport_type = TransportType.WEBSOCKET

        def on_message(self, data):
            asyncio.create_task(self.reply(data))

        async def reply(self, data):
            response = await rpc.dispatch(data, self)
            await self.write_message(response.decode())

    sockets = bind_sockets(0, address='127.0.0.1')
    port = sockets[0].getsockname()[1]
    server = HTTPServer(Application([(r'/websocket', WebSocket)]))
    server.add_sockets(sockets)
    ws = await websocket_connect('ws://127.0.0.1:%d/websocket' % port)
    timings = []

    async def send(method, req_id, params):
        await ws.write_message(json.dumps(dict(jsonrpc='2.0', method='printer.inventure.oled_preview.' + method,
                                               params=params, id=req_id)))

    async def receive():
        response = json.loads(await asyncio.wait_for(ws.read_message(), 3))
        assert 'error' not in response, response
        return response['id'], response['result']

    try:
        await send('wait', 1, {'client': 'bridge', 'epoch': '', 'navigation_revision': -1})
        _, reply = await receive()
        frame = reply['frame']
        expected = OEMMenu('en', operational=True)
        assert frame['image_base64'] == encode_frame(menu_wire(expected.view()))
        for index in range(20):
            direction = 1 if (index // 4) % 2 == 0 else -1
            await send('wait', index + 2, dict(client='bridge', epoch=frame['epoch'],
                       navigation_revision=frame['navigation_revision']))
            stamp = []
            def press(now):
                if not preview.waiters:
                    loop.register_callback(press, now + .002)
                    return
                stamp.append(perf_counter())
                menu.move(direction)
                owner._render_wire = menu_wire(menu.view())
                preview.observe(now, menu.selected, owner._render_wire)
            loop.register_async_callback(press)
            req_id, reply = await receive()
            timings.append((perf_counter() - stamp[0]) * 1000)
            assert req_id == index + 2 and reply['changed']
            next_frame = reply['frame']
            expected.move(direction)
            assert next_frame['navigation_revision'] > frame['navigation_revision']
            assert next_frame['image_base64'] == encode_frame(menu_wire(expected.view()))
            frame = next_frame
        await send('wait', 30, dict(client='bridge', epoch=frame['epoch'],
                   navigation_revision=frame['navigation_revision']))
        await send('cancel_wait', 31, {'client': 'bridge'})
        replies = dict([await receive(), await receive()])
        assert replies == {30: {'changed': False}, 31: {'cancelled': True}}, replies
        gallery = dict(labels=['Back', 'Heat Nozzle', 'Previous', 'Next'], selected=3,
                       icon='project:heat_nozzle', animated=True)
        for index in range(4):
            wire = menu_wire(gallery, index=index)
            await send('wait', 40+index, dict(client='bridge', epoch=frame['epoch'],
                       navigation_revision=frame['navigation_revision']))
            def advance(now):
                if not preview.waiters or now < preview.next_animation or now < preview.encode_after:
                    loop.register_callback(advance, max(now+.005, preview.next_animation, preview.encode_after))
                    return
                owner._render_wire = wire
                preview.observe(now, 'gallery', wire)
            loop.register_async_callback(advance)
            req_id, reply = await receive()
            frame = reply['frame']
            assert req_id == 40+index and reply['changed'] and frame['image_base64'] == encode_frame(wire)
        print(json.dumps(dict(events=len(timings), gallery_frames=4, cancelled=True, bitmaps='identical', physical_hardware=False,
                              host_event_to_websocket_p95_ms=round(sorted(timings)[18], 3))))
    finally:
        ws.close()
        server.stop()
        connection.closing = True
        connection.writer.close()
        await connection.writer.wait_closed()
        await read_loop
        loop.register_async_callback(lambda _: loop.end())
        thread.join(timeout=3)
        klippy_sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', required=True, type=Path)
    asyncio.run(check(parser.parse_args().klipper.resolve()))
