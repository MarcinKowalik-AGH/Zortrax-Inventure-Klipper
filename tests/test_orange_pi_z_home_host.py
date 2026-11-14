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
        self.transient_closures = 0
        self.transient_closure_at = 1
        self.closures = 0
        self.reclose_before_retreat = False
        self.reclose_before_fine = False
        self.reclose_before_open_verification = False
        self.contact_stays_closed_after_retreat = False
        self.release_edges = []
        self.z_moves = []
        self.input_reads = []

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
        row = self.motion
        self.input_reads.append((self.run, self.now(), self.z, bool(raw & self.CONTACT)))
        if row and self.now() >= row['until'] and row.pop('reclose_after_read', False):
            row['contact_at_end'] = True
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
            reclose_after_read = False
            if not hit:
                for step in range(1, z + 1):
                    position = self.z + step * direction
                    if self.contact_at is not None and (direction > 0 or self.contact):
                        contact_at_end = position >= self.contact_at
                    raw = self._raw_z(position, contact_at_end)
                    if self.contact and direction < 0 and self.release_fault is not None:
                        raw |= self.release_fault
                    hit = mask & ~(raw ^ value)
                    if hit:
                        distance = step
                        break
            if direction > 0 and hit == self.CONTACT:
                self.closures += 1
                if self.transient_closures and self.closures >= self.transient_closure_at:
                    self.transient_closures -= 1
                    # The MCU saw a close, but the first host read is open.
                    contact_at_end = False
                    reclose_after_read = self.reclose_before_retreat
            if (direction < 0 and mask & value & self.CONTACT and self.contact
                    and self.release_edges):
                # Script the MCU's open-edge stop independently of the
                # subsequent raw sample (which may already be closed again).
                distance, contact_at_end = self.release_edges.pop(0)
                if not 0 <= distance <= z:
                    raise AssertionError('Modeled release edge exceeds move')
                hit = self.CONTACT
                if not distance and not contact_at_end and self.reclose_before_open_verification:
                    reclose_after_read = True
                    self.reclose_before_open_verification = False
            if (direction < 0 and hit == self.CONTACT and distance
                    and not contact_at_end and self.reclose_before_fine):
                self.reclose_before_fine = False
                reclose_after_read = True
            if (direction < 0 and mask == value == (self.BOTTOM | self.TRAY)
                    and self.contact_stays_closed_after_retreat):
                contact_at_end = True
            duration = distance / rate + .02 if distance else .0
            self.motion = dict(
                token=token, counts=(a, b, z, e), directions=directions, z_start=self.z,
                direction=direction, z_steps=distance, contact_at_end=contact_at_end,
                until=self.now() + duration, started=self.now(), duration=max(.001, duration),
                stopped=False, hit=hit, home=False, reclose_after_read=reclose_after_read)
            if not distance:
                self.motion['fraction'] = 1.
            self.z_moves.append(dict(
                run=self.run, direction=direction, planned=z, sent=distance, rate=rate,
                max_ms=ms, mask=mask, value=value, hit=hit,
                contact_at_start=self.contact, contact_at_end=contact_at_end))
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

    def test_oem_host_preserves_logged_probe_envelope_and_uses_audited_rates(self):
        host = self.host()
        host.device.contact_at = 135 * 802 + 3471
        host.device.z = 135 * 802
        self.command(host, SPEED_PROFILE='OEM', TARGET=200, Z_APPROACH_MM=135,
                     Z_PROBE_MM=9, Z_STABLE_CYCLES=1, ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        moves = host.device.z_moves
        self.assertEqual([(m['direction'], m['rate'], m['planned'], m['hit']) for m in moves[:3]],
                         [(-1, 6645, 120300, 4), (1, 1000, 5000, 4), (1, 16040, 802, 0)])
        self.assertEqual((moves[3]['rate'], moves[3]['mask'], moves[3]['value']),
                         (16040, 24, 16))
        self.assertEqual((moves[4]['rate'], moves[4]['planned'], moves[4]['sent'], moves[4]['hit']),
                         (161, 7218, 3471, 8))
        self.assertEqual(report['factory']['motion_legs']['z_contact_pad']['target'], [82.8, 68.])
        measured = report['factory']['result']['z_home_result']
        self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at - 1)
        self.assertEqual(host.device.output(6)['duty'], 0)

    def test_transient_pc14_stop_backs_off_without_counting_a_release(self):
        for transients in (1, 2):
            with self.subTest(transients=transients):
                host = self.host()
                host.device.transient_closures = transients
                self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                             Z_TRANSIENT_RETRIES=1, ACK='PAD_AND_CLEARANCE_CHECKED')
                report = self.finish(host)
                moves = host.device.z_moves
                first = next(index for index, move in enumerate(moves)
                             if move['hit'] == host.device.CONTACT)
                self.assertFalse(moves[first]['contact_at_end'])
                backoff = moves[first + 1]
                self.assertEqual((backoff['direction'], backoff['planned'], backoff['sent']),
                                 (-1, 160, 160))
                self.assertEqual((backoff['mask'], backoff['value'], backoff['hit']),
                                 (20, 20, 0))
                self.assertFalse(backoff['contact_at_end'])
                self.assertEqual(moves[first + 2]['direction'], 1)
                self.assertEqual(moves[first + 2]['hit'], host.device.CONTACT)
                result = report['factory']['result']
                progress = report['factory']['z_home_progress']
                if transients == 1:
                    self.assertEqual(report['phase'], 'done', report)
                    measured = result['z_home_result']
                    self.assertEqual((measured['cycles'], measured['transient_closures']), (2, 1))
                    self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at - 1)
                    self.assertEqual(sum(move['direction'] < 0 and move['hit'] == host.device.CONTACT
                                         for move in moves), 2)
                    self.assertEqual(
                        (progress['qualified_cycles'], progress['transient_closures']), (2, 1))
                    self.assertEqual(progress['best_release_steps'], host.device.contact_at - 1)
                else:
                    self.assertEqual(report['phase'], 'failed', report)
                    self.assertEqual(result['reason'], 'z_contact_retry_limit')
                    self.assertEqual(result['details']['qualified_cycles'], 0)
                    self.assertEqual(len(moves), first + 3)
                    self.assertEqual(progress['qualified_cycles'], 0)
                    self.assertIsNone(progress['best_release_steps'])
                self.assertFalse(host.device.active())
                self.assertEqual(host.device.output(6)['duty'], 0)

    def test_release_retries_share_original_remaining_travel_budget(self):
        for sent in (0, 5):
            with self.subTest(sent=sent):
                host = self.host()
                host.device.release_edges = [(sent, True)]
                self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                             ACK='PAD_AND_CLEARANCE_CHECKED')
                report = self.finish(host)
                self.assertEqual(report['phase'], 'done', report)
                measured = report['factory']['result']['z_home_result']
                progress = report['factory']['z_home_progress']
                self.assertEqual((measured['cycles'], measured['transient_openings'],
                                  measured['transient_closures']), (2, 1, 0))
                self.assertEqual(progress['transient_openings'], 1)
                releases = [row for row in host.device.z_moves
                            if row['direction'] < 0 and row['hit'] == ZDevice.CONTACT]
                first, retry = releases[:2]
                self.assertEqual((first['sent'], first['contact_at_end']), (sent, True))
                self.assertEqual((retry['mask'], retry['value']), (28, 28))
                self.assertEqual(retry['planned'], first['planned'] - sent)
                self.assertGreater(retry['sent'], 0)
                self.assertFalse(retry['contact_at_end'])
                self.assertFalse(host.device.active())

    def test_stationary_open_verification_qualifies_two_zero_step_releases(self):
        host = self.host()
        host.device.release_edges = [(0, False)] * 2
        self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                     ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        factory = report['factory']
        measured = factory['result']['z_home_result']
        self.assertEqual((measured['cycles'], measured['zero_step_releases'],
                          measured['transient_openings']), (2, 2, 0))
        self.assertEqual(factory['z_home_progress']['zero_step_releases'], 2)
        self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at)
        releases = [row for row in host.device.z_moves
                    if row['direction'] < 0 and row['hit'] == ZDevice.CONTACT]
        self.assertEqual(len(releases), 2)
        for release in releases:
            self.assertEqual((release['sent'], release['contact_at_end']), (0, False))
            self.assert_stationary_samples(host, release, (True, True))
        self.assertFalse(any(row['mask'] == row['value'] == 20 and row['direction'] < 0
                             for row in host.device.z_moves))
        self.assertFalse(host.device.active())
        self.assertEqual(host.device.output(6)['duty'], 0)

    def assert_stationary_samples(self, host, release, opened):
        reads = [(when, z, is_open) for run, when, z, is_open in host.device.input_reads
                 if run == release['run']]
        self.assertGreaterEqual(len(reads), 2)
        self.assertGreaterEqual(reads[1][0] - reads[0][0], .025 - 1.e-8)
        self.assertEqual(reads[0][1], reads[1][1])
        self.assertEqual((reads[0][2], reads[1][2]), opened)

    def test_zero_step_release_reclosed_at_verification_retries_with_same_bound(self):
        host = self.host()
        host.device.release_edges = [(0, False)]
        host.device.reclose_before_open_verification = True
        self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                     ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        measured = report['factory']['result']['z_home_result']
        self.assertEqual((measured['cycles'], measured['transient_openings'],
                          measured['transient_closures'], measured['zero_step_releases']),
                         (2, 1, 0, 0))
        moves = host.device.z_moves
        first = next(index for index, row in enumerate(moves)
                     if row['direction'] < 0 and row['hit'] == ZDevice.CONTACT)
        release, retry = moves[first:first + 2]
        self.assertEqual((release['sent'], release['contact_at_end']), (0, False))
        self.assert_stationary_samples(host, release, (True, False))
        self.assertEqual((retry['direction'], retry['mask'], retry['value'],
                          retry['planned']), (-1, 28, 28, release['planned']))
        self.assertGreater(retry['sent'], 0)
        self.assertEqual(sum(row['direction'] < 0 and row['hit'] == ZDevice.CONTACT
                             and row['sent'] > 0 for row in moves), 2)
        self.assertFalse(host.device.active())

    def test_opening_retries_stop_on_shared_budget_without_qualifying_cycle(self):
        for share_close in (False, True):
            with self.subTest(share_close=share_close):
                host = self.host()
                host.device.transient_closures = int(share_close)
                host.device.release_edges = [(0, True)] * (1 if share_close else 2)
                self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                             Z_TRANSIENT_RETRIES=1, ACK='PAD_AND_CLEARANCE_CHECKED')
                report = self.finish(host)
                self.assertEqual(report['phase'], 'failed', report)
                factory = report['factory']
                self.assertEqual(factory['result']['reason'], 'z_contact_retry_limit')
                progress = factory['z_home_progress']
                self.assertEqual(progress['qualified_cycles'], 0)
                self.assertIsNone(progress['best_release_steps'])
                self.assertEqual(progress['transient_closures'], int(share_close))
                self.assertEqual(progress['transient_openings'], 1 if share_close else 2)
                self.assertEqual(host.device.z_moves[-1]['sent'], 0)
                self.assertFalse(host.device.active())
                self.assertEqual(host.device.output(6)['duty'], 0)

    def test_reclosed_input_before_retreat_moves_only_down_with_tray_and_bottom_stops(self):
        for stuck in (False, True):
            with self.subTest(stuck=stuck):
                host = self.host()
                host.device.transient_closures = 1
                host.device.transient_closure_at = 4
                host.device.reclose_before_retreat = True
                host.device.contact_stays_closed_after_retreat = stuck
                self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=3,
                             ACK='PAD_AND_CLEARANCE_CHECKED')
                report = self.finish(host)
                moves = host.device.z_moves
                approaches = [index for index, row in enumerate(moves)
                              if row['direction'] > 0 and row['hit'] == ZDevice.CONTACT]
                self.assertGreaterEqual(len(approaches), 4, report)
                event = moves[approaches[3]]
                self.assertFalse(event['contact_at_end'])
                self.assertLess(approaches[3] + 1, len(moves), report)
                retreat = moves[approaches[3] + 1]
                self.assertTrue(retreat['contact_at_start'])
                self.assertEqual((retreat['direction'], retreat['mask'], retreat['value'],
                                  retreat['planned'], retreat['sent'], retreat['hit']),
                                 (-1, 20, 20, 160, 160, 0))
                if stuck:
                    self.assertEqual(report['phase'], 'failed', report)
                    self.assertEqual(report['factory']['z_home_progress']['qualified_cycles'], 3)
                    self.assertIs(moves[-1], retreat)
                else:
                    self.assertEqual(report['phase'], 'done', report)
                    measured = report['factory']['result']['z_home_result']
                    self.assertEqual(measured['transient_closures'], 1)
                    self.assertEqual(measured['non_improvements'], 3)
                    self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at - 1)
                self.assertFalse(host.device.active())
                self.assertEqual(host.device.output(6)['duty'], 0)

    def test_already_closed_fine_seek_stops_at_zero_and_retries_after_retreat(self):
        host = self.host()
        host.device.reclose_before_fine = True
        self.command(host, Z_APPROACH_MM=3, Z_STABLE_CYCLES=1,
                     ACK='PAD_AND_CLEARANCE_CHECKED')
        report = self.finish(host)
        self.assertEqual(report['phase'], 'done', report)
        moves = host.device.z_moves
        zero_seek = next(index for index, row in enumerate(moves)
                         if row['direction'] > 0 and row['hit'] == ZDevice.CONTACT
                         and row['sent'] == 0)
        self.assertTrue(moves[zero_seek]['contact_at_start'])
        self.assertEqual((moves[zero_seek]['mask'], moves[zero_seek]['value']), (24, 16))
        self.assertEqual((moves[zero_seek + 1]['direction'], moves[zero_seek + 1]['sent']),
                         (-1, 160))
        measured = report['factory']['result']['z_home_result']
        self.assertEqual(measured['transient_closures'], 1)
        self.assertEqual(measured['release_steps_from_bottom'], host.device.contact_at - 1)

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
