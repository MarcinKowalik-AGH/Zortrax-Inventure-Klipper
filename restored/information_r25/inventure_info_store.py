"""Atomic, checksummed, two-copy identity and printing-time checkpoints.

The counter transaction protocol is the M300 implementation. This store keeps
Inventure identity/runtime separate from calibration, UI settings and live pose.
"""
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import threading


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate information key: '+key)
        result[key] = value
    return result


def _sync_directory(path):
    descriptor = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Information state must be a regular file')
        raw = os.read(descriptor, maximum+1)
        if len(raw) > maximum:
            raise ValueError('Information state exceeds its size bound')
        return raw
    finally:
        os.close(descriptor)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1.e15


def validate(data):
    if (not isinstance(data, dict) or set(data) != {'schema', 'revision', 'identity', 'counters'}
            or data['schema'] != 1 or type(data['revision']) is not int or data['revision'] < 0):
        raise ValueError('Invalid Inventure information document')
    identity, counters = data['identity'], data['counters']
    if not isinstance(identity, dict) or set(identity) != {'uid', 'serial', 'hardware'}:
        raise ValueError('Invalid information identity')
    uid, serial, hardware = (identity[k] for k in ('uid', 'serial', 'hardware'))
    import re
    if uid is not None and (not isinstance(uid, str) or not re.fullmatch('[0-9A-F]{24}', uid)):
        raise ValueError('Invalid MCU UID')
    if serial is not None and (not isinstance(serial, str) or not re.fullmatch('Z[0-9A-F]{8}', serial)):
        raise ValueError('Invalid factory serial')
    if hardware is not None and (type(hardware) is not int or not 0 <= hardware <= 15):
        raise ValueError('Invalid factory hardware revision')
    if (not isinstance(counters, dict) or set(counters) != {'print_seconds', 'completed_jobs'}
            or not _finite(counters['print_seconds']) or type(counters['completed_jobs']) is not int
            or not 0 <= counters['completed_jobs'] < 0xffffffff):
        raise ValueError('Invalid printing counters')
    return data


class InfoStore:
    def __init__(self, path):
        self.path = str(Path(path).expanduser().absolute())
        self.checkpoint = self.path+'.checkpoint'
        self.lock = threading.RLock()
        self.data = self.load()

    def _read(self, path):
        try:
            raw = _read_regular(path, 8192)
        except FileNotFoundError:
            return None
        document = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (not isinstance(document, dict) or set(document) != {'data', 'sha256'}
                or hashlib.sha256(_canonical(document['data'])).hexdigest() != document['sha256']):
            raise ValueError('Inventure information checksum mismatch')
        return validate(document['data'])

    def load(self):
        first, second = self._read(self.path), self._read(self.checkpoint)
        if first is None and second is None:
            return dict(schema=1, revision=0, identity=dict(uid=None, serial=None, hardware=None),
                        counters=dict(print_seconds=0., completed_jobs=0))
        if first is None:
            return second
        if second is None:
            return first
        if first == second:
            return first
        older, newer = sorted((first, second), key=lambda x:x['revision'])
        if (older['revision'] == newer['revision']
                or older['identity']['uid'] is not None and older['identity'] != newer['identity']
                or any(newer['counters'][key] < value for key, value in older['counters'].items())):
            raise ValueError('Conflicting Inventure information checkpoints')
        return newer

    def _atomic_replace(self, path, raw):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError('Refusing a symlink information target')
        descriptor, temporary = tempfile.mkstemp(prefix='.'+target.name+'.', dir=target.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            _sync_directory(target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def snapshot(self):
        # Writers publish a new immutable document only after both fsyncs.
        # Like M300's UI snapshot, readers never wait for a filesystem writer.
        return deepcopy(self.data)

    def update(self, mutation):
        with self.lock:
            lock_path = self.path+'.lock'
            Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                current = self.load()
                candidate = deepcopy(current)
                mutation(candidate)
                candidate['revision'] += 1
                validate(candidate)
                if (current['identity']['uid'] is not None and current['identity'] != candidate['identity']
                        or any(candidate['counters'][key] < value for key, value in current['counters'].items())):
                    raise ValueError('Information update would replace identity or decrease lifetime')
                raw = _canonical(dict(data=candidate, sha256=hashlib.sha256(_canonical(candidate)).hexdigest()))
                self._atomic_replace(self.path, raw)
                self._atomic_replace(self.checkpoint, raw)
                self.data = candidate
            finally:
                os.close(descriptor)

    def bind_identity(self, uid, serial, hardware):
        identity = dict(uid=uid, serial=serial, hardware=hardware)
        if self.data['identity'] == identity:
            return
        def mutate(data):
            if data['identity']['uid'] is not None and data['identity'] != identity:
                raise ValueError('This printing history belongs to another MCU or factory identity')
            data['identity'] = identity
        self.update(mutate)
