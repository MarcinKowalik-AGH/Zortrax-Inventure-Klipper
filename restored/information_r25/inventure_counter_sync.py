# Marcin Kowalik <mkowalik@agh.edu.pl>
# inventure_counter_sync.py - Rozlicza pełne minuty druku między dziennikiem hosta a fabrycznym licznikiem MCU.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable host intent for the Inventure Classic factory print-minute counter."""

import hashlib
import json
import math

from .inventure_info_store import _canonical, _read_regular, _sync_directory, _unique_pairs


STATE_FORMAT = ('inventure_factory_counter_state serial=%u state=%c source=%c'
                ' print_minutes=%u settings_crc=%u')
COMMIT_FORMAT = ('inventure_factory_counter_commit serial=%u source=%c expected=%u'
                 ' target=%u settings_crc=%u')
RESULT_FORMAT = 'inventure_factory_counter_result status=%c'
RUNTIME_COMMIT_FORMAT = ('inventure_factory_counter_commit_runtime serial=%u source=%c expected=%u'
                         ' target=%u settings_crc=%u deadline=%u')
RUNTIME_RESULT_FORMAT = 'inventure_factory_counter_runtime_result deadline=%u status=%c'
RUNTIME_QUERY_FORMAT = 'inventure_factory_counter_query_runtime token=%u'
RUNTIME_STATE_FORMAT = ('inventure_factory_counter_runtime_state token=%u serial=%u state=%c'
                        ' source=%c print_minutes=%u settings_crc=%u')
MAX_UINT = 0xffffffff
JOURNAL_KEYS = {'serial', 'machine_id', 'committed', 'last_minutes', 'pending'}
PENDING_KEYS = {'source', 'expected', 'target', 'settings_crc'}


class CounterRecoveryError(ValueError):
    """The pending Flash update needs another safe window."""


def _uint(value, limit=MAX_UINT):
    return type(value) is int and 0 <= value <= limit


def _validate_journal(data):
    if (not isinstance(data, dict) or data.keys() != JOURNAL_KEYS
            or not isinstance(data['serial'], str)
            or not isinstance(data['machine_id'], str)
            or not _uint(data['committed'], MAX_UINT - 1)
            or not _uint(data['last_minutes'], MAX_UINT - 1)
            or data['committed'] > data['last_minutes']):
        raise ValueError('invalid factory counter journal')
    pending = data['pending']
    if pending is not None and (
            not isinstance(pending, dict) or pending.keys() != PENDING_KEYS
            or not _uint(pending['source'], 1)
            or not _uint(pending['expected'], MAX_UINT - 1)
            or not _uint(pending['target'], MAX_UINT - 1)
            or not _uint(pending['settings_crc'])
            or not data['last_minutes'] <= pending['expected'] < pending['target']):
        raise ValueError('invalid pending factory counter transaction')
    return data


def _can_follow(older, newer):
    """Compare valid records in one printer's forward-only accounting history."""
    if older == newer:
        return True
    if (older['serial'], older['machine_id']) != (newer['serial'], newer['machine_id']):
        return False
    gained = newer['committed'] - older['committed']
    if not 0 <= gained <= newer['last_minutes'] - older['last_minutes']:
        return False
    pending = older['pending']
    if pending is not None:
        # A different transaction cannot supersede unacknowledged minutes.
        return (gained >= pending['target'] - pending['expected']
                and newer['last_minutes'] >= pending['target'])
    return True


class CounterJournal:
    def __init__(self, store):
        self.path = store.path + '.runtime-sync'
        self.checkpoint = self.path + '.checkpoint'
        self.atomic_replace = store._atomic_replace

    @staticmethod
    def _read(path):
        try:
            raw = _read_regular(path, 4096)
        except FileNotFoundError:
            return None
        document = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (not isinstance(document, dict) or document.keys() != {'data', 'sha256'}
                or hashlib.sha256(_canonical(document['data'])).hexdigest()
                != document['sha256']):
            raise ValueError('factory counter journal checksum mismatch')
        return _validate_journal(document['data'])

    @staticmethod
    def _latest(first, second):
        if first is None:
            return second
        if second is None or first == second:
            return first
        if _can_follow(first, second):
            return second
        if _can_follow(second, first):
            return first
        raise ValueError('factory counter journal and checkpoint have conflicting histories')

    def load(self):
        return self._latest(self._read(self.path), self._read(self.checkpoint))

    def _write(self, path, data):
        checksum = hashlib.sha256(_canonical(data)).hexdigest()
        self.atomic_replace(path, _canonical(dict(data=data, sha256=checksum)))

    def load_for_sync(self, was_pending):
        first, second = self._read(self.path), self._read(self.checkpoint)
        data = self._latest(first, second)
        if data is not None:
            # Rebuild a missing or stale copy before any MCU write. This also
            # enrolls an existing RC7–16 journal on first connection.
            if first != data:
                self._write(self.path, data)
            if second != data:
                self._write(self.checkpoint, data)
            if was_pending or data['pending'] is not None:
                # An earlier rename may have succeeded before directory fsync failed.
                _sync_directory(self.path)
        return data

    def reconcile(self, serial, machine, minutes, total, committed):
        if not _uint(committed, MAX_UINT - 1) or committed > min(total // 60, minutes):
            raise ValueError('COMMITTED_MINUTES exceeds the host or factory total')
        previous = self.load()
        if previous is not None and (
                previous['pending'] is not None or previous['serial'] != serial
                or previous['machine_id'] != machine or previous['last_minutes'] >= minutes
                or previous['committed'] >= total // 60 or committed < previous['committed']
                or committed - previous['committed'] > minutes - previous['last_minutes']):
            raise ValueError('existing factory counter transaction is incompatible with reconciliation')
        self.save(dict(serial=serial, machine_id=machine, last_minutes=minutes,
                       committed=committed, pending=None))

    def save(self, data):
        _validate_journal(data)
        previous = self.load()
        if previous is not None and not _can_follow(previous, data):
            raise ValueError('factory counter journal update would discard accounted minutes')
        # Both atomic replacements and directory syncs finish before commit.
        # A cut between them leaves comparable records with an absolute target.
        self._write(self.path, data)
        self._write(self.checkpoint, data)


def decode_state(result):
    serial, state, source, minutes, crc = (result[key] for key in
        ('serial', 'state', 'source', 'print_minutes', 'settings_crc'))
    if (not _uint(serial, MAX_UINT - 1) or not _uint(state, 2)
            or not _uint(source, 255) or not _uint(minutes, MAX_UINT - 1)
            or not _uint(crc)
            or state == 1 and (not serial or source > 1)
            or state != 1 and (source != 255 or minutes or crc)):
        raise ValueError('invalid factory counter state response')
    return serial, state, source, minutes, crc


class CounterSync:
    def __init__(self):
        self.status = 'DISABLED'
        self.error = None
        self.committed_seconds = 0
        self.pending = False
        self.flash_written = False
        self.serial = self.minutes = None
        self.banks = 'NOT_CHECKED'
        self.active_bank = None
        self.display_blocked = False

    def observe(self, result):
        packet = decode_state(result)
        serial, state, source, minutes, _ = packet
        self.serial = 'Z%08X' % serial if serial else None
        self.minutes = minutes if state == 1 else None
        self.banks = ('UNAVAILABLE', 'NORMALIZED', 'RECOVERY_REQUIRED')[state]
        self.active_bank = 'AB'[source] if state == 1 else None
        return packet

    def _history_required(self, message):
        self.status = 'RECONCILIATION_REQUIRED'
        self.display_blocked = True
        self.error = message + '; verify previously credited minutes before INVENTURE_COUNTER_RECONCILE'

    def run(self, snapshot, load, save, query, commit, *, runtime=False, allow_new=True):
        self.status = 'CHECKING'
        self.error = None
        serial, state, source, minutes, crc = self.observe(query())
        try:
            data = load()
        except (OSError, ValueError) as exc:
            self.display_blocked = True
            if state == 2:
                raise CounterRecoveryError(str(exc)) from exc
            raise
        if data is None and state == 2:
            raise CounterRecoveryError('interrupted MCU factory banks require the original host journal')
        if state == 0 and (data is None or data['pending'] is None):
            self.status = 'MCU_COUNTER_UNAVAILABLE'
            return
        if not serial:
            error = CounterRecoveryError if state == 2 else ValueError
            raise error('MCU factory serial is unavailable')
        machine = snapshot['identity']['machine_id']
        total = snapshot['counters']['print_seconds']
        if (type(total) not in (int, float) or not math.isfinite(total)
                or total < 0):
            raise ValueError('invalid host model-print counter')

        fresh = data is None
        if fresh:
            if total >= 60:
                self._history_required('missing factory counter receipts for existing host print time')
                return
            data = dict(serial=self.serial, machine_id=machine, committed=0,
                        last_minutes=minutes, pending=None)
            # Establish a zero-credit baseline before the first minute accrues.
            save(data)
        elif data['serial'] != self.serial or data['machine_id'] != machine:
            self.display_blocked = True
            error = CounterRecoveryError if state == 2 else ValueError
            raise error('factory counter journal belongs to another printer')
        self.committed_seconds = 60 * data['committed']
        pending = data['pending']
        self.pending = pending is not None
        if self.pending:
            before = (pending['source'], pending['expected'], pending['settings_crc'])
            after = (1 - pending['source'], pending['target'], pending['settings_crc'])
            self.display_blocked = state != 1 or (source, minutes, crc) != before
        elif state == 1:
            self.display_blocked = minutes < data['last_minutes'] or total < self.committed_seconds
            if self.display_blocked:
                raise ValueError('factory or host counter went backwards')
        if state == 2 and not self.pending:
            raise CounterRecoveryError('interrupted MCU factory banks have no matching host transaction')
        if pending is None:
            whole_minutes = int(total // 60) - data['committed']
            if minutes > data['last_minutes']:
                if whole_minutes:
                    # Even two matching receipts can be an old directory restore.
                    # ID0 alone cannot separate stock work from prior host credits.
                    self._history_required('MCU and host advanced beyond the recorded baseline')
                    return
                # Nothing from this host remains to charge. Capture stock work
                # at startup, before a subsequent print accumulates more time.
                save(dict(data, last_minutes=minutes))
            if not allow_new:
                self.status = 'AWAITING_IDLE' if whole_minutes else 'CURRENT'
                return
            if not whole_minutes:
                self.status = 'CURRENT'
                return
            target = minutes + whole_minutes
            if not _uint(target, MAX_UINT - 1):
                raise ValueError('factory counter target exceeds its uint32 range')
            pending = dict(source=source, expected=minutes, target=target,
                           settings_crc=crc)
            data = dict(data, last_minutes=minutes, pending=pending)
            # No command is sent until the exact absolute intent is durable.
            save(data)
            self.pending = True
        elif state == 1 and (source, minutes, crc) not in (before, after):
            raise ValueError('factory counter changed during a pending transaction')
        elif state == 0:
            raise CounterRecoveryError('pending transaction has no recoverable MCU bank')

        try:
            observed_banks = self.banks, self.active_bank
            previous_display_blocked = self.display_blocked
            self.banks, self.active_bank = 'RECOVERY_REQUIRED', None
            self.display_blocked = True
            result = commit([serial, pending['source'], pending['expected'],
                             pending['target'], pending['settings_crc']])
            status = result['status']
            if type(status) is int and status == 7:
                # Firmware rejected the target before entering the writer.
                if state != 1:
                    raise ValueError('unsupported MCU target with interrupted factory banks')
                self.banks, self.active_bank = observed_banks
                self.display_blocked = previous_display_blocked
                self.status = 'UNSUPPORTED_MCU_TARGET'
                self.error = 'MCU rejected the Flash geometry, processor or factory hardware identity'
                return
            if type(status) is int and status == 2:
                if runtime:
                    observed = self.observe(query())
                    before = (serial, 1, pending['source'], pending['expected'],
                              pending['settings_crc'])
                    self.display_blocked = observed != before
                    if observed[0] != serial or observed[1] == 0:
                        raise ValueError('MCU bank read-back unavailable after deferral')
                    self.status = 'AWAITING_IDLE' if observed == before else 'RECOVERY_REQUIRED'
                    return
                if state == 1:
                    self.banks, self.active_bank = observed_banks
                    self.display_blocked = previous_display_blocked
                    self.status = 'AWAITING_FIRMWARE_RESTART'
                    return
            if type(status) is not int or status not in (0, 1):
                raise ValueError('MCU refused factory counter transaction (status %r)' % status)
            self.flash_written = status == 0
            expected = (serial, 1, 1 - pending['source'], pending['target'],
                        pending['settings_crc'])
            if self.observe(query()) != expected:
                raise ValueError('MCU factory counter read-back did not match the pending transaction')
        except Exception as exc:
            raise CounterRecoveryError(str(exc)) from exc

        data = dict(data, pending=None, last_minutes=pending['target'],
                    committed=data['committed'] + pending['target'] - pending['expected'])
        try:
            save(data)
        except (OSError, ValueError) as exc:
            raise CounterRecoveryError(str(exc)) from exc
        self.committed_seconds = 60 * data['committed']
        self.minutes = pending['target']
        self.pending = False
        self.display_blocked = False
        self.status = 'SYNCED'
