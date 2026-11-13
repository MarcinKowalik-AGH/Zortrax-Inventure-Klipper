"""Exercise the service Z procedure against a local-stop wire model."""
import json
import unittest

import test_orange_pi_factory_host as factory_support
import test_orange_pi_load_test as load_support
from test_orange_pi_door_policy import Command


class ZDevice(factory_support.FactoryDevice):
    BOTTOM = 4
    CONTACT = 8
    TRAY = 16

    def __init__(self, host):
        super().__init__(host)
        self.z = 0
        self.contact_at = 2486
        self.contact = False
        self.tray = True
        self.release_fault = None
        self.z_moves = []

    def _raw_z(self, position, contact):
        return ((self.BOTTOM if position == 0 else 0)
                | (0 if contact else self.CONTACT)
                | (0 if self.tray else self.TRAY))

    def _settle(self):
        row = self.motion
        if row is not None and 'z_start' in row:
            fraction = self.fraction()
            self.z = row['z_start'] + row['direction'] * round(row['z_steps'] * fraction)
            if fraction == 1. and not row['stopped']:
                self.contact = row['contact_at_end']
                if row['hit'] & self.TRAY:
                    self.tray = False

    def inputs(self):
        self._settle()
        self.h.inputs_received = self.now()
        raw = self._raw_z(self.z, self.contact)
        return dict(raw_mask=raw, raw={
            name: int(bool(raw & (1 << index)))
            for index, name in enumerate(self.h.INPUT_NAMES)})

    def move(self, args):
        action, directions, a, b, z, e, rate, pulse, ms, mask, value, hold, token = args
        self._settle()
        if action != 1:
            if self.motion is None or 'z_start' not in self.motion:
                return super().move(args)
            if action == 2:
                self.stop_motion()
        elif z:
            self.packets.append(('motion', *args))
            self.run += 1
            direction = 1 if directions & self.BOTTOM else -1
            raw = self._raw_z(self.z, self.contact)
            hit = mask & ~(raw ^ value)
            distance = 0 if hit else z
            contact_at_end = self.contact
            if not hit:
                for step in range(1, z + 1):
                    position = self.z + step * direction
                    if self.contact_at is not None:
                        contact_at_end = position >= self.contact_at
                    raw = self._raw_z(position, contact_at_end)
                    if self.contact and direction < 0 and self.release_fault is not None:
                        raw |= self.release_fault
                    hit = mask & ~(raw ^ value)
                    if hit:
                        distance = step
                        break
            duration = distance / rate + .02 if distance else .0
            self.motion = dict(
                token=token, counts=(a, b, z, e), directions=directions, z_start=self.z,
                direction=direction, z_steps=distance, contact_at_end=contact_at_end,
                until=self.now() + duration, started=self.now(), duration=max(.001, duration),
                stopped=False, hit=hit, home=False)
            if not distance:
                self.motion['fraction'] = 1.
            self.z_moves.append(dict(
                direction=direction, planned=z, sent=distance, rate=rate,
                max_ms=ms, mask=mask, value=value, hit=hit))
        else:
            return super().move(args)
        row = self.motion
        active = self.active_motion()
        stopped = row['stopped']
        hit = 0 if active or stopped else row['hit']
        return dict(
            run=self.run, token=row['token'], phase=1 if active else 4 if stopped else 3,
            reason=0 if active else 3 if stopped else 2 if hit else 1,
            reject=0, active=int(active), events=round(row['z_steps'] * self.fraction()),
            elapsed_ms=round((self.now() - row['started']) * 1000), hit=hit)

    def driver(self, args):
        if 'z_start' not in self.motion:
            return super().driver(args)
        index = args[0]
        row = self.motion
        return dict(
            run=self.run, channel=index, planned=row['counts'][index],
            sent=round(row['z_steps'] * self.fraction()) if index == 2 else 0,
            direction=(row['directions'] >> index) & 1)


class ZHomeHostTests(unittest.TestCase):
    def host(self):
        fixture = factory_support.FactoryHostTests()
        host, _, _ = fixture.host(fast_motion=True, factory_motion_tools=3)
        host.device = ZDevice(host)
        host._fresh_inputs = host.device.inputs
        host.commands.update(
            control=load_support.Wire(host.device.control),
            output=load_support.Wire(host.device.out),
            motion=load_support.Wire(host.device.move),
            motion_driver=load_support.Wire(host.device.driver),
            factory_session=load_support.Wire(host.device.query_factory))
        return host

    def command(self, host, action='HOME_Z', **values):
        command = Command(ACTION=action, **values)
        host.cmd_procedure(command)
        return command

    def finish(self, host):
        return factory_support.FactoryHostTests().finish(host)

    def assert_no_heat(self, host):
        self.assertEqual(
            [row for row in host.device.packets if row[:2] == ('output', 1)], [])

    def test_params_reports_z_operation_without_actuation(self):
        host = self.host()
        command = self.command(host, 'PARAMS', PROCEDURE='HOME_Z', TOOL='T0')
        report = json.loads(command.lines[0].split(' ', 1)[1])
        self.assertEqual(report['operation'], 'HOME_Z')
        self.assertEqual(report['tool'], 0)
        self.assertIsNone(host.load_test_context)
        self.assertEqual(host.device.packets, [])
        self.assertEqual(host.device.factory_packets, [])

    def test_requires_ack_and_explicit_approach_before_actuation(self):
        for values in ({}, {'ACK': 'PAD_AND_CLEARANCE_CHECKED'}, {'Z_APPROACH_MM': 3}):
            with self.subTest(values=values):
                host = self.host()
                with self.assertRaises(RuntimeError):
                    self.command(host, **values)
                self.assertIsNone(host.load_test_context)
                self.assertEqual(host.device.packets, [])
                self.assertEqual(host.device.factory_packets, [])

    def test_unseated_tray_refuses_probe(self):
        host = self.host()
        host.device.tray = False
        with self.assertRaisesRegex(RuntimeError, 'tray'):
            self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
        self.assertEqual(host.device.packets, [])
        self.assert_no_heat(host)

    def test_initial_contact_refuses_reference_before_actuation(self):
        host = self.host()
        host.device.contact = True
        with self.assertRaisesRegex(RuntimeError, 'nozzle_contact'):
            self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
        self.assertEqual(host.device.packets, [])

    def test_prepare_uses_t0_and_keeps_z_at_the_bottom(self):
        host = self.host()
        self.command(host, 'Z_PREPARE', TOOL='T0')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        self.assertEqual(report['tool'], 'T0')
        self.assertEqual(report['factory']['result']['terminal'], 'Z_PREPARE')
        self.assertTrue(host.device.z_moves)
        self.assertTrue(all(row['direction'] < 0 for row in host.device.z_moves))
        self.assertFalse(any(row['hit'] & host.device.CONTACT
                             for row in host.device.z_moves))
        self.assertEqual({row[2] for row in host.device.packets if row[:2] == ('output', 1)},
                         {6})
        self.assertTrue(host.device.factory_packets)
        self.assertEqual({row[1] for row in host.device.factory_packets}, {0})
        self.assertFalse(host.device.active())

    def test_t1_refused_before_starting_z_reference(self):
        for action in ('Z_PREPARE', 'HOME_Z'):
            with self.subTest(action=action):
                host = self.host()
                with self.assertRaisesRegex(RuntimeError, 'T0'):
                    self.command(host, action, TOOL='T1', Z_APPROACH_MM=3,
                                 ACK='PAD_AND_CLEARANCE_CHECKED')
                self.assertIsNone(host.load_test_context)
                self.assertEqual(host.device.packets, [])

    def test_home_z_uses_local_contact_and_release_masks_after_coarse_approach(self):
        host = self.host()
        self.command(host, TOOL='T0', Z_APPROACH_MM=3,
                     ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        self.assertEqual(report['factory']['result']['terminal'], 'HOME_Z')
        measured = report['factory']['result']['z_home_result']
        self.assertEqual(measured['cycles'], 102)
        self.assertEqual(measured['non_improvements'], 101)
        self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at - 1)
        self.assertFalse(measured['coordinate_saved'])
        self.assertTrue(host.device.z_moves)
        contact = [m for m in host.device.z_moves if m['hit'] == host.device.CONTACT]
        self.assertEqual(len(contact), 2 * 102)
        for move in contact:
            mask, value = (24, 16) if move['direction'] > 0 else (28, 28)
            self.assertEqual((move['mask'], move['value']), (mask, value))
            self.assertGreater(move['sent'], 0)
            self.assertLessEqual(move['sent'], move['planned'])
        self.assertEqual(host.device.z, host.device.contact_at - 1)
        self.assertFalse(host.device.contact)
        self.assertFalse(host.device.active())
        self.assertFalse(host.motion_last['establishes_position'])

    def test_release_cannot_be_satisfied_by_bottom_or_tray_stop(self):
        for hit in (ZDevice.BOTTOM, ZDevice.TRAY):
            with self.subTest(hit=hit):
                host = self.host()
                host.device.release_fault = hit
                self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
                report = self.finish(host)
                self.assertEqual(report['phase'], 'failed', report)
                self.assertTrue(host.device.z_moves[-1]['hit'] & hit)
                self.assertFalse(host.device.active())

    def test_early_contact_releases_then_stops_before_fine_probe(self):
        host = self.host()
        host.device.contact_at = 2000
        self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'failed', report)
        self.assertIn('coarse', report['reason'].lower())
        self.assertEqual([row['direction'] for row in host.device.z_moves
                          if row['hit'] == host.device.CONTACT], [1, -1])
        self.assertFalse(host.device.contact)
        self.assertFalse(host.device.active())

    def test_loss_of_temperature_stops_before_new_z_motion(self):
        host = self.host()
        self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
        deadline = host.reactor.now + 60.
        while not any(row['direction'] > 0 for row in host.device.z_moves):
            self.assertIsNotNone(host.load_test_context)
            self.assertLess(host.reactor.now, deadline)
            host.reactor.pause(host.reactor.now + .1)
        before = len(host.device.z_moves)
        warm_refresh = host.refresh

        def cold_refresh():
            warm_refresh()
            host.temperatures[0] = 160.

        host.refresh = cold_refresh
        report = self.finish(host)
        self.assertEqual(report['phase'], 'failed', report)
        self.assertIn('temperature', report['reason'].lower())
        self.assertEqual(len(host.device.z_moves), before)
        self.assertFalse(host.device.active())

    def test_missing_contact_exhausts_bounded_probe_without_establishing_position(self):
        host = self.host()
        host.device.contact_at = None
        self.command(host, Z_APPROACH_MM=3, ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'failed', report)
        move = host.device.z_moves[-1]
        self.assertEqual((move['direction'], move['mask'], move['value']), (1, 24, 16))
        self.assertEqual((move['hit'], move['sent']), (0, move['planned']))
        self.assertFalse(host.device.active())
        self.assertFalse(host.motion_last['establishes_position'])
