"""Expose one planner operation under the pinned, physically paused print owner."""
import hashlib

BASE_SHA256 = 'c2d2572baedc6460020664806557a3532efddd13fbafcf77c383428ece71beec'
PATCHED_SHA256 = 'acb94e9984f430a688d4eee246f3d13319a475e5408d47a9932d5b0949bbf2de'

METHODS = '''    def _check_move_lease(self, lease):
        if getattr(self, '_paused_move_caller', None) is getcurrent():
            self._core(self.state.check, lease, ('paused',))
        else:
            self._core(self.state.check, lease)

    def resume_filament_recovery(self, lease):
        # Use P02's native RESUME validation and the optional P17 policy
        # wrapper with Klipper's stock pause handler, irrespective of macros.
        self._core(self.state.check, lease, ('paused',))
        cycle = self.printer.lookup_object('inventure_cycle', None)
        checked_cycle = cycle is not None and cycle.enabled
        if checked_cycle:
            cycle._allow_resume = True
        try:
            cmd = self.gcode.create_gcode_command('RESUME', 'RESUME', {})
            return self._dispatch('RESUME', self.pause.cmd_RESUME, cmd, cmd)
        finally:
            if checked_cycle:
                cycle._allow_resume = False

    def paused_print_move(self, lease, position, speed):
        # Keep the source and the arbiter paused, including while the planner
        # yields. Only this greenlet may issue this native move on this lease.
        self._core(self.state.check, lease, ('paused',))
        if (lease.kind != 'print' or self.vsd is None or self.vsd.is_active()
                or self.vsd.current_file is None or self.pause is None
                or not self.pause.is_paused or not self.pause.sd_paused
                or self.state.pause_reason not in ('source', 'material')
                or getattr(self, '_paused_move_caller', None) is not None):
            raise self.printer.command_error('No stationary paused filament recovery owner')
        caller = getcurrent()
        if self.contexts.get(caller):
            raise self.printer.command_error('Cannot nest paused recovery inside another procedure')
        self.contexts[caller] = [lease]
        self._paused_move_caller = caller
        try:
            self._require_print_reference()
            self.require_motion()
            self.th.move(position, speed)
            self.th.wait_moves()
            self._check_move_lease(lease)
        finally:
            self._paused_move_caller = None
            self.contexts.pop(caller, None)

'''


def render(before):
    digest = hashlib.sha256(before).hexdigest()
    if digest == PATCHED_SHA256:
        return before
    if digest != BASE_SHA256:
        raise ValueError('Unrecognized Inventure native print ownership module')
    code = before.decode('utf-8')
    code = code.replace('    def current_lease(self):', METHODS + '    def current_lease(self):')
    code = code.replace('        self._core(self.state.check, lease)\n        return lease\n\n    @contextmanager',
                        '        self._check_move_lease(lease)\n        return lease\n\n    @contextmanager', 1)
    code = code.replace('if lease is not None: self._core(self.state.check, lease)\n            motion =',
                        'if lease is not None: self._check_move_lease(lease)\n            motion =', 1)
    return code.encode('utf-8')
