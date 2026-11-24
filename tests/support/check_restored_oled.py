#!/usr/bin/env python3
"""Check the published OLED pixels in the actual, offline Klipper runtime.

All temperatures, heater targets, link state and acknowledgements below are
simulated in memory.  No reactor, G-code or MCU connection is started.  The
reference renderer is the authenticated, unmodified DUAL_THERMAL_V2 source;
the R33.1 footer and current OLED/Fluidd transport remain production code.
"""
import argparse
import base64
from contextlib import redirect_stdout
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import struct
import sys
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'ci'))
from inventure_oled_restore import SOURCE, SOURCE_SHA256
import check_native_nozzle_startup as startup


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def crop(wire, rectangle):
    """Extract whole pixel pairs from SSD1322 rows, without rendering glyphs."""
    x, y, width, height = rectangle
    require(x % 2 == width % 2 == 0, 'Crop must contain whole pixel pairs')
    return b''.join(wire[row * 128 + x // 2:row * 128 + (x + width) // 2]
                    for row in range(y, y + height))


def png_wire(encoded):
    """Independently decode the indexed PNG returned to Fluidd."""
    png = base64.b64decode(encoded, validate=True)
    require(png.startswith(b'\x89PNG\r\n\x1a\n'), 'Preview is not a PNG')
    offset, chunks = 8, {}
    while offset < len(png):
        size = struct.unpack('>I', png[offset:offset + 4])[0]
        name = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', png[offset + 8 + size:offset + 12 + size])[0]
        require(crc == zlib.crc32(name + data) & 0xffffffff, 'PNG checksum differs')
        chunks.setdefault(name, []).append(data)
        offset += 12 + size
    require(struct.unpack('>IIBBBBB', chunks[b'IHDR'][0]) == (256, 64, 4, 3, 0, 0, 0),
            'Preview dimensions or pixel format changed')
    rows = zlib.decompress(b''.join(chunks[b'IDAT']))
    require(len(rows) == 64 * 129, 'Unexpected preview scanline length')
    require(all(rows[y * 129] == 0 for y in range(64)), 'Unexpected PNG row filter')
    return b''.join(rows[y * 129 + 1:(y + 1) * 129] for y in range(64))


def inspect_pixels(printer, config, output_png=None):
    original = ROOT / SOURCE
    require(sha256(original.read_bytes()).hexdigest() == SOURCE_SHA256,
            'The published renderer oracle changed')
    spec = importlib.util.spec_from_file_location('extras.oled_published_v2_reference', original)
    reference_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference_module)
    real_config = printer.lookup_object('configfile').read_main_config()
    reference = reference_module.KlipperStatus(real_config.getsection('inventure_oled'))

    board = printer.lookup_object('inventure_mcu')
    physical = printer.lookup_object('inventure_oled')
    native = physical.native_status
    heaters = printer.lookup_object('heaters')
    names = ('extruder', 'inventure_t1', 'chamber_l', 'chamber_r')
    fields = ((0, 0, 80, 16), (80, 0, 80, 16),
              (0, 16, 80, 16), (80, 16, 80, 16))
    originals = {name: heaters.lookup_heater(name) for name in names}
    display = printer.lookup_object('display_status')
    display.message = 'Ready'
    checks = []
    comparisons = 0

    def seed(now):
        # These are test samples, not ADC conversions or live printer readings.
        board.connected = True
        board.raw_adc[:4] = [1000] * 4
        board.temperatures[:4] = [91., 34., 28., 32.]
        board.adc_received[:4] = [now - .1] * 4
        board.fan_received[1] = now - .1
        board.fans[1]['duty'] = 0

    def targets(active=()):
        for index, name in enumerate(names):
            # Assign only the memory used by get_temp(); never issue heat G-code.
            originals[name].target_temp = (200. if index < 2 else 60.) if index in active else 0.

    def rendered(now=100.25):
        nonlocal comparisons
        result = native.render(now, board)
        saved = heaters.heaters.get('extruder1')
        exists = 'extruder1' in heaters.heaters
        try:
            # The published V2 understood the old T1 name.  Its pixels serve as
            # the oracle while its sensor object is the actual current heater.
            if 'inventure_t1' in heaters.heaters:
                heaters.heaters['extruder1'] = heaters.heaters['inventure_t1']
            expected = reference.render(now, board)
        finally:
            if exists:
                heaters.heaters['extruder1'] = saved
            else:
                heaters.heaters.pop('extruder1', None)
        require(result[:6144] == expected[:6144],
                'Top 48 OLED rows differ from published DUAL_THERMAL_V2')
        comparisons += 1
        return result

    seed(100.25)
    targets()
    idle = rendered()
    require(len(idle) == 8192, 'Wrong SSD1322 frame size')
    require(len({crop(idle, field) for field in fields}) == 4,
            'Four independent temperatures were not rendered')
    checks.append('published_top_48_rows')

    seed(101.25)
    idle_odd = rendered(101.25)
    require(idle_odd == idle, 'Inactive thermal icons must not animate')
    for active in range(4):
        targets((active,))
        frames = []
        for now in (100.25, 101.25):
            seed(now)
            frames.append(rendered(now))
        require(crop(frames[0], fields[active]) != crop(frames[1], fields[active]),
                names[active] + ' does not animate from its own target')
        x, y, _, height = fields[active]
        allowed = {row * 128 + col for row in range(y, y + height)
                   for col in range(x // 2, x // 2 + 10)}
        for frame in frames:
            changes = {index for index, values in enumerate(zip(frame, idle))
                       if values[0] != values[1]}
            require(bool(changes) and changes <= allowed,
                    names[active] + ' target changed another field or layout')
    targets()
    checks.append('four_independent_target_animations')

    # Native T1 takes priority even when an old alias also exists and disagrees.
    heaters.heaters['extruder1'] = originals['chamber_l']
    try:
        for native_active in (True, False):
            targets((1,) if native_active else (2,))
            seed(100.25)
            frame = rendered()
            require((crop(frame, fields[1]) != crop(idle, fields[1])) == native_active,
                    'Legacy alias overrode the native T1 heater target')
    finally:
        heaters.heaters.pop('extruder1')
    targets((1,))
    seed(100.25)
    modern = rendered()
    old_t1 = heaters.heaters.pop('inventure_t1')
    heaters.heaters['extruder1'] = old_t1
    try:
        require(rendered() == modern, 'Legacy extruder1 fallback changed the pixels')
    finally:
        heaters.heaters['inventure_t1'] = old_t1
        heaters.heaters.pop('extruder1')
    targets()
    checks.append('native_t1_priority_and_legacy_fallback')

    invalid_cases = (
        ('missing', lambda i, now: board.temperatures.__setitem__(i, None)),
        ('nan', lambda i, now: board.temperatures.__setitem__(i, float('nan'))),
        ('ground', lambda i, now: board.raw_adc.__setitem__(i, 0)),
        ('adc_range', lambda i, now: board.raw_adc.__setitem__(i, board.ADC_MAX)),
        ('temp_range', lambda i, now: board.temperatures.__setitem__(i, 400.)),
        ('stale', lambda i, now: board.adc_received.__setitem__(i, now - 1.01)),
        ('future', lambda i, now: board.adc_received.__setitem__(i, now + .1)),
    )
    for channel, field in enumerate(fields):
        seed(100.25)
        board.temperatures[channel] = None
        unavailable = crop(rendered(), field)
        require(unavailable != crop(idle, field), 'Unavailable temperature retained a number')
        for label, invalidate in invalid_cases:
            seed(100.25)
            invalidate(channel, 100.25)
            frame = rendered()
            require(board.panel_temperature(channel, 100.25)[0] is None,
                    'Test input was not rejected: ' + label)
            require(crop(frame, field) == unavailable,
                    names[channel] + ' invalid/stale value did not render --: ' + label)
            for other in range(4):
                if other != channel:
                    require(crop(frame, fields[other]) == crop(idle, fields[other]),
                            'An invalid sample changed another temperature')
    checks.append('invalid_and_stale_samples_render_dashes')

    seed(100.25)
    from extras.inventure_oled_graphics import status_footer
    wire = rendered()
    require(wire[6144:] == status_footer('Ready'), 'R33.1 compact footer was lost')
    require(wire[6144:] != reference.render(100.25, board)[6144:],
            'Large upstream status footer was restored accidentally')
    lit = []
    for row in range(48, 64):
        for x in range(256):
            value = wire[row * 128 + x // 2]
            if (value >> 4 if x % 2 == 0 else value & 15):
                lit.append((x, row))
    bounds = [min(x for x, y in lit), min(y for x, y in lit),
              max(x for x, y in lit), max(y for x, y in lit)]
    require(bounds == [2, 52, 30, 58], 'Ready is no longer the existing 5x7 footer')
    require(native.render(100.25, board) is wire, 'Unchanged frame cache was lost')
    checks.append('compact_ready_footer_and_frame_cache')

    # The actual panel renderer feeds one frame to transport and Fluidd.  Link
    # readiness is simulated; no connect/ready event or serial call is made.
    physical.connected = True
    physical.stream.enabled = True
    physical.stream.generation_frames = 1
    physical.stream.display_mode = 'awake'
    physical.state_received = 100.25
    generation = physical.stream.generation
    physical.wire_state = dict(configured=1, active=1, fault=0, latched=0, reset=1,
                               generation_hi=generation >> 32,
                               generation_lo=generation & 0xffffffff)
    board.status_received = 100.25
    board.status['fault'] = 0
    physical._render(100.25)
    require(physical._render_wire == wire == physical.stream.latest,
            'Physical OLED did not receive the restored frame')
    preview = physical.preview.get_status(100.25)
    require(preview['online'] and preview['display'] == 'awake', 'Preview did not become live')
    require(png_wire(preview['image_base64']) == physical.stream.latest,
            'Fluidd pixels differ from the physical OLED frame')
    checks.append('one_frame_for_physical_oled_and_fluidd')

    # Reuse the existing SSD1322 addressing model against the installed,
    # optimized transport, including its actual PackBits packet encoder.
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_orange_pi_oled_fast import Panel, run_frame
    from extras.inventure_oled_uifast_transport import FastOledStream
    stream = FastOledStream()
    stream.request(wire)
    stream.start(0.)
    _, panel, operations = run_frame(stream, now=0., panel=Panel())
    require(bytes(panel.ram) == wire, 'Optimized transport changed restored pixels')
    require(bool(operations), 'No transport operations were exercised')
    checks.append('actual_optimized_transport_reconstructs_frame')

    if output_png is not None:
        output_png = Path(output_png)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        output_png.write_bytes(base64.b64decode(preview['image_base64']))
    return dict(status='VERIFIED_RESTORED_OLED', checks=checks,
                original_sha256=SOURCE_SHA256, pixel_oracle_comparisons=comparisons,
                sample_temperatures_c=[91, 34, 28, 32], simulated_data=True,
                ready_pixel_bounds=bounds, wire_sha256=sha256(wire).hexdigest(),
                top_48_rows_sha256=sha256(wire[:6144]).hexdigest(),
                transport_operations=len(operations), hardware_tested=False,
                reactor_run=False, gcode_executed=False)


def check(klipper, config, output_png=None):
    klipper, config = Path(klipper).resolve(), Path(config).resolve()
    sys.path.insert(0, str(klipper / 'klippy'))
    import reactor
    import mcu
    import gcode
    state = {}

    def loaded(printer, source):
        state['reactor'] = printer.get_reactor()
        state['result'] = inspect_pixels(printer, source, output_png)

    try:
        with patch.object(startup, 'check_loaded', loaded), \
             patch.object(reactor.Reactor, 'run', side_effect=AssertionError('Reactor started')), \
             patch.object(mcu.MCUConnectHelper, '_attach', side_effect=AssertionError('MCU attached')), \
             patch.object(mcu.MCUConfigHelper, '_connect', side_effect=AssertionError('MCU configured')), \
             patch.object(gcode.GCodeDispatch, 'run_script_from_command',
                          side_effect=AssertionError('G-code executed')):
            startup.load_config(klipper, config)
        return state['result']
    finally:
        if 'reactor' in state:
            state['reactor'].finalize()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output-png', type=Path)
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            result = check(args.klipper, args.config, args.output_png)
    except Exception as exc:
        print(json.dumps({'status': 'INVALID_RESTORED_OLED', 'error': str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
