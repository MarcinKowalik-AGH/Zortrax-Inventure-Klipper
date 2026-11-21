# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Moonraker mirror of frames rendered by the Inventure host."""
import base64
import struct
import uuid
import zlib

PALETTE = bytes.fromhex('020610 030b15 030f1a 041423 051b2d 08395f 0a436c 0c588e '
                        '0d619a 0e6ba8 0f75b7 1081cb 128cdc 1497eb 159df5 16a1fe')


def png_chunk(name, data):
    return (struct.pack('>I', len(data)) + name + data
            + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff))


def encode_frame(wire):
    if len(wire) != 8192:
        raise ValueError('Expected a 256 by 64 SSD1322 framebuffer')
    header = struct.pack('>IIBBBBB', 256, 64, 4, 3, 0, 0, 0)
    rows = b''.join(b'\0' + wire[i:i+128] for i in range(0, len(wire), 128))
    png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', header) + png_chunk(b'PLTE', PALETTE)
           + png_chunk(b'IDAT', zlib.compress(rows, 1)) + png_chunk(b'IEND', b''))
    return base64.b64encode(png).decode('ascii')


class OledPreview:
    """Share one PNG across clients and wake a bounded wait on menu changes."""

    interval = .125
    wait_seconds = 10.
    max_waiters = 8

    def __init__(self, owner):
        self.owner = owner
        self.waiters = {}
        self.bound = False
        self.reset()

    def bind(self):
        web = self.owner.printer.lookup_object('webhooks', None)
        if web is not None and not self.bound:
            web.register_endpoint('inventure/oled_preview/wait', self.wait)
            web.register_endpoint('inventure/oled_preview/cancel_wait', self.cancel)
            self.bound = True

    def reset(self):
        self.epoch = uuid.uuid4().hex
        self.revision = self.sequence = 0
        self.view = self.notified_wire = self.encoded_wire = None
        self.encoded_view = None
        self.encode_after = 0.
        self.display = 'offline'
        self.image = ''
        self.next_animation = 0.
        self.wake_waiters()

    def wake_waiters(self):
        for completion, client in tuple(self.waiters.values()):
            if not completion.test():
                completion.complete(not client.is_closed())

    def observe(self, now, view, wire):
        # Only compare references/bytes here. PNG encoding is on reader demand.
        if view != self.view or (wire != self.notified_wire and now >= self.next_animation):
            self.view, self.notified_wire = view, wire
            self.next_animation = now + self.interval
            self.revision += 1
            self.wake_waiters()

    def get_status(self, now):
        owner = self.owner
        stream = owner.stream
        live = stream.ready and owner._base_live(now) and owner._wire_live(now)
        mode = owner.sleep_phase if live else 'offline'
        display = 'dim' if mode == 'notice' else mode
        wire = owner._render_wire if display in ('awake', 'dim') else None
        if not isinstance(wire, bytes) or len(wire) != 8192:
            wire = None
        # A busy status subscriber cannot make animations encode faster.
        # Navigation, sleep, disconnection and wake are visible immediately.
        if (display != self.display or wire is None or self.encoded_wire is None
                or self.view != self.encoded_view or now >= self.encode_after) and (
                    wire != self.encoded_wire or display != self.display):
            if wire != self.encoded_wire:
                self.image = encode_frame(wire) if wire is not None else ''
                self.encoded_wire = wire
            self.encoded_view = self.view
            self.encode_after = now + self.interval
            self.sequence += 1
            self.display = display
        return {'display': display, 'online': bool(self.image), 'sequence': self.sequence,
                'source': 'host-rendered', 'format': 'indexed4-png', 'width': 256, 'height': 64,
                'image_base64': self.image, 'epoch': self.epoch, 'navigation_revision': self.revision,
                'navigation_api': 'NAV-EVENT-1' if self.bound else ''}

    @staticmethod
    def client_id(request):
        key = request.get_str('client')
        if not 1 <= len(key) <= 64 or not all(c.isalnum() or c in '-_' for c in key):
            raise request.error('Invalid OLED preview client')
        return key

    def wait(self, request):
        key = self.client_id(request)
        epoch = request.get_str('epoch', '')
        revision = request.get_int('navigation_revision', -1)
        if len(epoch) > 64 or revision < -1:
            raise request.error('Invalid OLED preview cursor')
        reactor = self.owner.reactor
        changed = (epoch, revision) != (self.epoch, self.revision)
        if not changed:
            if key in self.waiters or len(self.waiters) >= self.max_waiters:
                raise request.error('OLED preview wait is already pending or all slots are in use')
            completion = reactor.completion()
            self.waiters[key] = (completion, request.get_client_connection())
            try:
                changed = completion.wait(reactor.monotonic() + self.wait_seconds, False)
            finally:
                del self.waiters[key]
        reply = {'changed': changed}
        if changed:
            reply['frame'] = self.get_status(reactor.monotonic())
        request.send(reply)

    def cancel(self, request):
        key = self.client_id(request)
        record = self.waiters.get(key)
        matched = record is not None and record[1] is request.get_client_connection()
        if matched and not record[0].test():
            record[0].complete(False)
        request.send({'cancelled': matched})
