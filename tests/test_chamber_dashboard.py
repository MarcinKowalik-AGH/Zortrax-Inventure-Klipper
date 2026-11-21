import base64
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
import zlib

from orange_pi.inventure_chamber import InventureChamber
from orange_pi.inventure_oled_preview import OledPreview
from fluidd_web import deploy, exchange, MARKER, TAG


class Zone:
    def __init__(self, name):
        self.name = name
        self.min_temp, self.max_temp = -10, 120
        self.target_temp = self.power = 0
        self.temperature = 21

    def set_temp(self, target):
        self.target_temp = target

    def get_temp(self, now):
        return self.temperature, self.target_temp

    def get_status(self, now):
        return dict(target=self.target_temp, power=self.power)


class ChamberTests(unittest.TestCase):
    def setUp(self):
        self.ch = c = InventureChamber.__new__(InventureChamber)
        c.board = MagicMock()
        c.board._control_query.return_value = dict(armed=1,mode='NATIVE',fault=0,latched=0)
        self.samples = {2:29., 3:35., 4:31.}
        c.board.panel_temperature.side_effect = lambda i,t: (self.samples[i], '')
        c.heaters = MagicMock()
        c.heaters.set_temperature.side_effect = lambda z,t: z.set_temp(t)
        c.reactor = MagicMock()
        c.printer = MagicMock()
        c.names = ('chamber_l','chamber_r')
        c.zones = (Zone('left'), Zone('right'))
        c.max_temp = 90
        self.g = MagicMock()
        self.g.error.side_effect = ValueError

    def test_set_both_display_middle_and_actual_power(self):
        self.ch.set_target(self.g, 45)
        left, right = self.ch.zones
        left.power, right.power = .2, .8
        status = self.ch.get_status(1)
        self.assertEqual((status['left'],status['temperature'],status['right']), (29.,31.,35.))
        self.assertEqual((status['target'],status['zone_targets'],status['power']), (45,[45,45],.5))
        right.set_temp(0)
        self.assertTrue(self.ch.get_status(1)['split_targets'])
        self.assertIsNone(self.ch.get_status(1)['target'])

    def test_wait_both_sides_and_unconditional_zero(self):
        self.ch.set_target(self.g, 45)
        self.ch.zones[0].temperature = 45
        self.assertTrue(self.ch.check_busy(1))
        self.ch.zones[1].temperature = 44
        self.assertFalse(self.ch.check_busy(1))
        self.ch.board._control_query.side_effect = OSError('offline')
        self.samples[4] = None
        self.ch.set_target(self.g, 0)
        self.assertEqual(self.ch.get_status(1)['zone_targets'], [0,0])

    def test_invalid_and_disarmed_never_start_a_zone(self):
        for t in (-1,91,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                self.ch.set_target(self.g, t)
        self.ch.board._control_query.return_value['mode'] = 'SERVICE'
        with self.assertRaises(ValueError):
            self.ch.set_target(self.g, 45)
        self.ch.heaters.set_temperature.assert_not_called()

    def test_guard_all_three_and_clear_both_targets(self):
        for channel in self.samples:
            self.ch.set_target(self.g, 45)
            saved=self.samples[channel]
            self.samples[channel]=None
            self.ch.monitor(2)
            self.assertEqual(self.ch.get_status(2)['zone_targets'],[0,0])
            with self.assertRaises(ValueError):
                self.ch.set_target(self.g,45)
            self.samples[channel]=saved
        self.assertEqual(self.ch.printer.invoke_shutdown.call_count,3)

    def test_second_output_failure_clears_first(self):
        left,right = self.ch.zones
        def fail_second(zone,t):
            if zone is right:
                raise RuntimeError('write failed')
            zone.set_temp(t)
        self.ch.heaters.set_temperature.side_effect = fail_second
        with self.assertRaises(RuntimeError):
            self.ch.set_target(self.g,45)
        self.assertEqual(left.target_temp,0)


class OledTests(unittest.TestCase):
    def test_acknowledged_image_pixels_offline_and_wake(self):
        wire = bytes(range(256))*32
        owner = MagicMock()
        owner.stream = SimpleNamespace(ready=True, previous=wire, latest=bytes(8192), display_mode='awake')
        owner._base_live.return_value = owner._wire_live.return_value = True
        preview = OledPreview(owner)
        def get():
            return preview.get_status(1)['image_base64']
        first = get()
        self.assertIs(first,get())
        png = base64.b64decode(first)
        p, raw = 8, b''
        while p<len(png):
            size, name = struct.unpack_from('>I4s',png,p)
            chunk = png[p+8:p+8+size]
            if name==b'IDAT':
                raw += zlib.decompress(chunk)
            p += size+12
        self.assertEqual(raw,b''.join(b'\0'+wire[j:j+128] for j in range(0,8192,128)))
        owner.stream.display_mode='off'
        self.assertEqual(get(),'')
        owner.stream.display_mode='awake'
        self.assertEqual(get(),first)
        owner._wire_live.return_value = False
        self.assertEqual(preview.get_status(2),{'display':'offline','image_base64':''})


class WebInstallTests(unittest.TestCase):
    def test_atomic_upgrade_preserves_config_and_old_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'site'
            dist=Path(tmp)/'dist'
            for path, name in ((root,'old'),(dist,'new')):
                (path/'assets').mkdir(parents=True)
                (path/'index.html').write_text('<title>fluidd</title>assets/'+name)
                (path/'assets'/name).write_text(name)
                (path/'config.json').write_text(name)
            (dist/MARKER).write_text(TAG)
            backup = deploy(dist,root)
            self.assertEqual((root/'config.json').read_text(),'old')
            self.assertEqual((root/'assets/old').read_text(),'old')
            self.assertTrue((root/'index.html').read_text().endswith('new'))
            self.assertIsNone(deploy(dist,root))
            exchange(root,backup)
            self.assertTrue((root/'index.html').read_text().endswith('old'))

    def test_symlink_cannot_redirect_a_staged_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'site'
            dist = Path(tmp)/'dist'
            for path in (root,dist):
                path.mkdir()
                (path/'index.html').write_text('<title>fluidd</title>assets/')
            (dist/MARKER).write_text(TAG)
            (dist/'assets').mkdir()
            (dist/'assets/a').write_text('new')
            out = Path(tmp)/'elsewhere'
            out.mkdir()
            (root/'assets').symlink_to(out, target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'symlink'):
                deploy(dist,root)
            self.assertEqual(list(out.iterdir()),[])
