# SPDX-License-Identifier: GPL-3.0-or-later
# Original pinned ToolHead/ToolHeadGCode methods; full source SHA256:
# 911d675604003f9cffc1356562de17c85c9bead3d744e53922a0d77d5dee1e3b
class PinnedToolHeadDwell:
    def dwell(self, delay):
        self._flush_lookahead()
        next_print_time = self.get_last_move_time() + max(0., delay)
        self._advance_move_time(next_print_time)
        self._check_pause()

    def cmd_G4(self, gcmd):
        # Dwell
        delay = gcmd.get_float('P', 0., minval=0.) / 1000.
        self.toolhead.dwell(delay)
