# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared M300-style Info, Inventure OTP/NVM transport and durable print clock."""
import json
import logging
import math
from pathlib import Path
from secrets import randbits
from .inventure_info_store import InfoStore
from .inventure_runtime import PrintClock, CHECKPOINT_SECONDS, DRAIN_POLL_SECONDS, deposited
from .inventure_counter_sync import (CounterJournal, CounterSync, RUNTIME_QUERY_FORMAT,
    RUNTIME_STATE_FORMAT, RUNTIME_COMMIT_FORMAT, RUNTIME_RESULT_FORMAT)

RELEASE = 'RC8-INFORMATION-R25.1'
INFO_FORMAT = 'inventure_factory_info token=%u valid=%c serial=%u hardware=%hi uid0=%u uid1=%u uid2=%u'
PROJECT_URL = 'https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper'


def duration(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return 'Unavailable'
    minutes = int(seconds)//60
    return '%d h %02d min' % (minutes//60, minutes%60)


class Transport:
    def __init__(self, mcu, reactor):
        self.mcu, self.reactor, self.pending, self.token = mcu, reactor, None, randbits(32)
        self.commands = {}
        for kind, command, response in (
                ('identity', 'inventure_factory_info_query token=%u', INFO_FORMAT),
                ('state', RUNTIME_QUERY_FORMAT, RUNTIME_STATE_FORMAT),
                ('result', RUNTIME_COMMIT_FORMAT, RUNTIME_RESULT_FORMAT)):
            self.commands[kind] = mcu.lookup_command(command)
            mcu.register_serial_response(lambda packet, kind=kind:self.receive(kind, packet), response)

    def receive(self, kind, packet):
        pending = self.pending
        if pending is None:
            return
        expected_kind, token, started, completion = pending
        key = 'deadline' if kind == 'result' else 'token'
        if kind == expected_kind and packet.get(key) == token and packet.get('#receive_time', -1) >= started:
            self.reactor.async_complete(completion, packet)

    def request(self, kind, args, token):
        now = self.reactor.monotonic()
        completion = self.reactor.completion()
        if self.pending is not None:
            raise ValueError('Information request already pending')
        self.pending = kind, token, now, completion
        try:
            self.commands[kind].send(args)
            result = completion.wait(now+3.)
            if result is None:
                raise TimeoutError('Inventure information '+kind+' response timed out')
            return result
        finally:
            self.pending = None

    def query(self, kind='state'):
        self.token = (self.token+1)&0xffffffff
        return self.request(kind, [self.token], self.token)

    def commit(self, args):
        now = self.reactor.monotonic()
        deadline = self.mcu.print_time_to_clock(self.mcu.estimated_print_time(now)+.350)&0xffffffff
        return self.request('result', [*args, deadline], deadline)


class InventureInformation:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.board = self.printer.load_object(config, 'inventure_mcu')
        self.mcu = self.board.mcu
        root = Path(self.printer.get_start_args()['config_file']).parent
        try:
            self.store = InfoStore(config.get('path', str(root/'inventure_information.json')))
        except (OSError, ValueError) as exc:
            raise config.error('Inventure information: '+str(exc))
        self.executor = self.printer.load_object(config, 'aio_executor').allocate_executor('inventure-information')
        self.sync = CounterSync()
        self.journal = CounterJournal(self.store)
        self.factory_verified = False
        try:
            receipt = self.journal.load()
            identity = self.store.snapshot()['identity']
            if receipt is not None:
                if receipt['machine_id'] != identity['uid'] or receipt['serial'] != identity['serial']:
                    raise ValueError('Factory receipt belongs to another stored printer identity')
                self.sync.serial = receipt['serial']
                self.sync.minutes = receipt['last_minutes']
                self.sync.committed_seconds = 60*receipt['committed']
                self.sync.pending = receipt['pending'] is not None
                self.sync.display_blocked = self.sync.pending
                self.sync.status = 'RECOVERY_REQUIRED' if self.sync.pending else 'STORED_FACTORY_RECEIPT'
        except (OSError, ValueError) as exc:
            self.sync.error = str(exc)
            self.sync.display_blocked = True
            self.sync.status = 'RECOVERY_REQUIRED'
        self.clock = PrintClock()
        self.saved = (0., 0)
        self.target = None
        self.error = self.wait_reason = None
        self.transport = None
        self.verified_identity = self.writing = self.ready = False
        self.synchronizing = False
        self.job_deposited = self.job_completed = False
        self.observers_installed = False
        self.runtime_timer = self.reactor.register_timer(self.checkpoint)
        self.sync_timer = self.reactor.register_timer(self.synchronize)
        self.mcu.register_config_callback(self.configure)
        self.printer.register_event_handler('klippy:ready', self.connect)
        for event in ('klippy:shutdown', 'gcode:request_restart'):
            self.printer.register_event_handler(event, self.stop)
        self.printer.register_event_handler('klippy:disconnect', self.disconnect)
        self.gcode.register_command('INVENTURE_INFO', self.cmd_info, when_not_ready=True)
        self.gcode.register_command('INVENTURE_COUNTER_RECONCILE', self.cmd_reconcile)

    def configure(self):
        constants = self.mcu.get_constants()
        if all(str(constants.get(key)) == '1' for key in ('INVENTURE_FACTORY_INFO_ABI','INVENTURE_FACTORY_COUNTER_ABI')):
            self.transport = Transport(self.mcu, self.reactor)
        else:
            self.sync.status = 'MCU_INFO_UPDATE_REQUIRED'

    def connect(self):
        self.ready = True
        self.install_observers()
        if self.transport is not None:
            self.reactor.update_timer(self.sync_timer, self.reactor.NOW)

    def install_observers(self):
        if self.observers_installed:
            return
        self.observers_installed = True
        # Observe successful native G0/G1 and print lifecycle; never replace
        # a movement, transform, macro, Home method or ownership decision.
        move = self.printer.lookup_object('gcode_move', None)
        if move is not None:
            for name in ('G0', 'G1'):
                original = self.gcode.register_command(name, None)
                if original is None:
                    continue
                def command(gcmd, original=original):
                    before = tuple(move.last_position)
                    result = original(gcmd)
                    if self.printing() and deposited(before, tuple(move.last_position)):
                        self.job_deposited = True
                        if self.clock.since is None or self.clock.drain_since is not None:
                            try:
                                self.clock.start(self.reactor.monotonic(), self.printer.lookup_object('toolhead', None))
                                self.reactor.update_timer(self.runtime_timer, self.reactor.NOW)
                            except Exception as exc:
                                self.error = 'Printing-time observer: '+str(exc)
                                logging.warning(self.error)
                    return result
                self.gcode.register_command(name, command)
        stats = self.printer.lookup_object('print_stats', None)
        if stats is None:
            return
        for name in ('note_pause', 'note_complete', 'note_error', 'note_cancel', 'reset'):
            original = getattr(stats, name, None)
            if original is None:
                continue
            def observe(*args, original=original, name=name, **kwargs):
                result = original(*args, **kwargs)
                now = self.reactor.monotonic()
                if name == 'reset':
                    self.clock.stop(now)
                    self.job_deposited = self.job_completed = False
                elif name == 'note_pause':
                    self.clock.request_stop(now)
                else:
                    self.clock.request_stop(now)
                    if name == 'note_complete' and self.job_deposited and not self.job_completed:
                        self.clock.completed += 1
                        self.job_completed = True
                self.reactor.update_timer(self.runtime_timer, self.reactor.NOW)
                return result
            setattr(stats, name, observe)

    def printing(self):
        stats = self.printer.lookup_object('print_stats', None)
        sd = self.printer.lookup_object('virtual_sdcard', None)
        now = self.reactor.monotonic()
        return bool(self.verified_identity and stats is not None and stats.get_status(now).get('state') == 'printing'
                    and sd is not None and sd.is_active())

    def stop(self, *unused):
        now = self.reactor.monotonic()
        self.clock.stop(now)
        self.ready = False
        self.reactor.update_timer(self.sync_timer, self.reactor.NEVER)
        self.reactor.update_timer(self.runtime_timer, self.reactor.NOW)

    def disconnect(self, *unused):
        self.stop()
        self.checkpoint(self.reactor.monotonic(), disconnected=True)

    def checkpoint(self, now, disconnected=False):
        if self.writing and not disconnected:
            return now+DRAIN_POLL_SECONDS
        self.clock.drain(now, self.printer.lookup_object('toolhead', None))
        sample = self.clock.snapshot(now)
        delta = tuple(a-b for a,b in zip(sample, self.saved))
        if any(delta):
            if self.target is None:
                counters = self.store.snapshot()['counters']
                self.target = (counters['print_seconds']+delta[0], counters['completed_jobs']+delta[1], sample)
            seconds, jobs, receipt = self.target
            def mutate(data):
                data['counters']['print_seconds'] = max(data['counters']['print_seconds'], seconds)
                data['counters']['completed_jobs'] = max(data['counters']['completed_jobs'], jobs)
            self.writing = True
            try:
                if disconnected:
                    self.store.update(mutate)
                else:
                    self.executor.submit(self.store.update, mutate)
                self.saved, self.target, self.error = receipt, None, None
                if self.ready and self.transport is not None:
                    self.reactor.update_timer(self.sync_timer, self.reactor.NOW)
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                logging.warning('Inventure printing-time checkpoint: %s', exc)
            finally:
                self.writing = False
            if disconnected and self.saved == receipt and sample != receipt:
                return self.checkpoint(now, disconnected=True)
        if disconnected:
            return self.reactor.NEVER
        if self.clock.drain_since is not None:
            return self.reactor.monotonic()+DRAIN_POLL_SECONDS
        return self.reactor.monotonic()+CHECKPOINT_SECONDS if self.clock.since is not None or self.error else self.reactor.NEVER

    def blocker(self, now):
        if not self.ready or self.printer.is_shutdown() or not self.verified_identity:
            return 'board_not_ready'
        if self.writing or self.target is not None or self.clock.since is not None or self.clock.drain_since is not None:
            return 'printing_checkpoint'
        stats = self.printer.lookup_object('print_stats', None)
        if stats is not None and stats.get_status(now).get('state') in ('printing', 'paused'):
            return 'print_stats'
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is not None and sd.is_active():
            return 'virtual_sdcard'
        queued, estimated, empty = self.printer.lookup_object('toolhead').check_busy(now)
        if not empty or not all(math.isfinite(v) for v in (queued, estimated)) or queued > estimated:
            return 'toolhead'
        for name in ('inventure_native_z', 'inventure_operations', 'inventure_cycle', 'inventure_filament'):
            obj = self.printer.lookup_object(name, None)
            if obj is not None:
                status = obj.get_status(now)
                if status.get('running') or status.get('active') or status.get('busy'):
                    return name
        menu = getattr(self.board, 'zortrax', None)
        if self.board.load_test_context is not None or menu is not None and menu.active is not None:
            return 'service'
        heaters = self.printer.lookup_object('heaters')
        for name in heaters.get_all_heaters():
            status = heaters.lookup_heater(name).get_status(now)
            heater = heaters.lookup_heater(name)
            pwm = getattr(heater, 'mcu_pwm', None)
            native = getattr(pwm, 'native', getattr(pwm, 'physical', pwm))
            current = self.mcu.print_time_to_clock(self.mcu.estimated_print_time(now))
            if status.get('target', 0) or status.get('power', 0) or getattr(native, '_last_clock', 0) > current:
                return name
        control = self.board._control_query()
        if control.get('mode') != 'OFF' or control.get('armed') or control.get('latched') or control.get('fault'):
            return 'mcu_control_or_held_drives'
        return None

    def synchronize(self, now):
        if not self.ready or self.transport is None or self.printer.is_shutdown():
            return self.reactor.NEVER
        if self.synchronizing:
            return now+DRAIN_POLL_SECONDS
        self.synchronizing = True
        try:
            if not self.verified_identity:
                packet = self.transport.query('identity')
                if packet.get('valid') != 1:
                    raise ValueError('Factory OTP identity is unavailable')
                serial = 'Z%08X' % packet['serial']
                uid = ''.join('%08X' % packet['uid%d' % i] for i in range(3))
                hardware = packet['hardware'] if 0 <= packet['hardware'] <= 15 else None
                self.executor.submit(self.store.bind_identity, uid, serial, hardware)
                self.verified_identity = True
            snapshot = self.store.snapshot()
            snapshot['identity'] = dict(machine_id=snapshot['identity']['uid'])
            def commit(args):
                mutex = self.gcode.get_mutex()
                if mutex.test():
                    self.wait_reason = 'gcode_mutex'
                    return dict(status=2)
                with mutex:
                    self.wait_reason = self.blocker(self.reactor.monotonic())
                    return dict(status=2) if self.wait_reason else self.transport.commit(args)
            def query():
                result = self.transport.query()
                from .inventure_counter_sync import decode_state
                decode_state(result)
                self.factory_verified = True
                return result
            self.wait_reason = self.blocker(self.reactor.monotonic())
            self.sync.run(snapshot,
                lambda:self.executor.submit(self.journal.load_for_sync, self.sync.pending),
                lambda data:self.executor.submit(self.journal.save, data),
                query, commit, runtime=True,
                allow_new=self.wait_reason is None)
        except (OSError, ValueError, TimeoutError, KeyError) as exc:
            self.sync.error = str(exc)
            self.sync.status = 'RECOVERY_REQUIRED' if self.sync.pending else 'READ_ERROR'
            logging.warning('Inventure factory information: %s', exc)
        finally:
            self.synchronizing = False
        return self.reactor.monotonic()+5.

    def get_status(self, now):
        data = self.store.snapshot()
        counters = data['counters']
        # M300 displays the last published durable checkpoint. This avoids
        # double-counting the brief executor/receipt handover and disk waits.
        klipper = counters['print_seconds']
        pending = max(0., klipper-self.sync.committed_seconds)
        minutes = self.sync.minutes
        total = None if minutes is None or self.sync.display_blocked else minutes*60+pending
        from .inventure_mcu import HOST_REVISION
        return dict(information_release=RELEASE, printer_model='Zortrax Inventure',
            host_module=__file__, identity_verified=self.verified_identity,
            hardware_version=data['identity']['hardware'], serial_number=data['identity']['serial'],
            serial_source='Factory OTP' if self.verified_identity else 'Stored factory OTP' if data['identity']['serial'] else 'Unavailable',
            mcu_uid=data['identity']['uid'], host_version=HOST_REVISION,
            display_diagnostics=dict(getattr(self.board, 'local_oled_diag', {})),
            mcu_release=getattr(self.board, 'mcu_release', None), oem_reference='1.6.1 r0323',
            factory_print_minutes=minutes, klipper_print_seconds=klipper,
            klipper_completed_jobs=counters['completed_jobs'],
            klipper_unsynced_print_seconds=pending,total_print_seconds=total,total_printing_time=duration(total),
            time_source=(('Factory NVM' if getattr(self,'factory_verified',False) else 'Stored factory NVM receipt')
                         +' + unsynced Klipper printing time') if total is not None else 'Unavailable',
            factory_counter_sync=self.sync.status, factory_counter_pending=self.sync.pending,
            factory_counter_error=self.sync.error, factory_counter_wait_reason=self.wait_reason,
            factory_counter_banks=self.sync.banks, factory_counter_active_bank=self.sync.active_bank,
            factory_counter_committed_seconds=self.sync.committed_seconds,
            runtime_checkpoint_error=self.error, runtime_checkpoint_interval=CHECKPOINT_SECONDS,
            runtime_file=self.store.path, project_url=PROJECT_URL, hardware_tested=False)

    def cmd_info(self, gcmd):
        gcmd.respond_info('INVENTURE_INFO '+json.dumps(self.get_status(self.reactor.monotonic()), sort_keys=True))

    def cmd_reconcile(self, gcmd):
        if self.transport is None or not self.verified_identity or self.blocker(self.reactor.monotonic()) is not None:
            raise gcmd.error('Counter reconciliation requires an identified, idle printer with heaters and drives off')
        committed = gcmd.get_int('COMMITTED_MINUTES', minval=0, maxval=0xfffffffe)
        observed = self.transport.query()
        from .inventure_counter_sync import decode_state
        serial, state, source, minutes, crc = decode_state(observed)
        if state != 1:
            raise gcmd.error('Counter reconciliation requires one complete factory bank')
        snapshot = self.store.snapshot()
        self.executor.submit(self.journal.reconcile, 'Z%08X' % serial,
                             snapshot['identity']['uid'], minutes,
                             snapshot['counters']['print_seconds'], committed)
        self.reactor.update_timer(self.sync_timer, self.reactor.NOW)
        gcmd.respond_info('Inventure counter receipt reconciled; factory writes retain the idle checks')


def load_config(config):
    return InventureInformation(config)
