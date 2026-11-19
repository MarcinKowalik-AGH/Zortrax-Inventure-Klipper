# SPDX-License-Identifier: GPL-3.0-or-later
"""Store the operator's source selection separately from tag/material records."""
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile


class JamSources:
    VALID = ('UNKNOWN', 'CASSETTE', 'EXTERNAL')

    def __init__(self, printer):
        args = printer.get_start_args() if hasattr(printer, 'get_start_args') else {}
        config = args.get('config_file')
        self.path = Path(config).with_name('inventure_jam_sources.json') if config else None
        self.values = dict(T0='UNKNOWN', T1='UNKNOWN')
        self.error = None
        if self.path is None:
            return
        try:
            if not stat.S_ISREG(self.path.lstat().st_mode):
                raise ValueError('Source settings must be a regular file')
            record = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(record, dict) or type(record.get('schema')) is not int
                    or record['schema'] != 1):
                raise ValueError('Unknown source settings format')
            values = record.get('sources')
            if (not isinstance(values, dict) or set(values) != {'T0', 'T1'}
                    or any(source not in self.VALID for source in values.values())
                    or record.get('sha256') != self._digest(values)):
                raise ValueError('Invalid or damaged filament source settings')
            self.values = values
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            self.error = str(exc)

    @staticmethod
    def _digest(values):
        return sha256(json.dumps(values, separators=(',', ':'), sort_keys=True).encode()).hexdigest()

    def set(self, tool, source):
        if tool not in self.values or source not in self.VALID:
            raise ValueError('Invalid tool or filament source')
        updated = dict(self.values, **{tool: source})
        if self.path is None:
            self.values = updated
            return
        if self.values == updated and self.error is None:
            return
        try:
            try:
                mode = self.path.lstat().st_mode
            except FileNotFoundError:
                mode = None
            if mode is not None and not stat.S_ISREG(mode):
                raise ValueError('Source settings must be a regular file')
            record = dict(schema=1, sources=updated, sha256=self._digest(updated))
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.' + self.path.name)
            temporary = Path(name)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(record, handle, sort_keys=True)
                    handle.write('\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self.values = updated
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
        except (ValueError, OSError) as exc:
            self.error = str(exc)
            raise ValueError('Could not save filament source settings: ' + self.error) from exc
        self.error = None
