# Marcin Kowalik <mkowalik@agh.edu.pl>
# inventure_runtime.py - Mierzy czas aktywnego druku zegarem monotonicznym i przygotowuje punkty zapisu.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure active model printing with a monotonic, checkpointable clock."""
import math


CHECKPOINT_SECONDS = 15.
DRAIN_POLL_SECONDS = .25


class PrintClock:
    def __init__(self):
        self.since = None
        self.drain_since = None
        self.elapsed = 0.
        self.completed = 0

    def start(self, now, toolhead):
        self.drain(now, toolhead)
        if self.since is None:
            self.since = now
        self.drain_since = None

    def request_stop(self, now):
        if self.since is not None and self.drain_since is None:
            self.drain_since = now

    def stop(self, now):
        if self.since is not None:
            self.elapsed += max(0., now - self.since)
        self.since = self.drain_since = None

    def drain(self, now, toolhead):
        if self.drain_since is None:
            return
        if toolhead is None:
            self.stop(self.drain_since)
            return
        print_time, estimated, empty = toolhead.check_busy(now)
        if empty and print_time <= estimated:
            # Convert the last scheduled move's MCU time back to host time.
            self.stop(max(self.drain_since, now - (estimated - print_time)))

    def snapshot(self, now):
        elapsed = self.elapsed
        if self.since is not None:
            elapsed += max(0., now - self.since)
        return elapsed, self.completed


def deposited(before, after):
    """True after a successfully dispatched XYZ move with positive E."""
    return (all(math.isfinite(v) for v in (*before, *after))
            and after[3] - before[3] > 1.e-7
            and any(abs(a - b) > 1.e-7 for a, b in zip(before[:3], after[:3])))
