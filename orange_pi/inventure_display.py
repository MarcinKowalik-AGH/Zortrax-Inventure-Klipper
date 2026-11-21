# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure screen preferences. The OLED transport owns the single panel."""
import math


class InventureDisplay:
    def __init__(self, config):
        self.panel = panel = config.get_printer().load_object(config, 'inventure_oled')
        # Seconds in the shared layout. Keep legacy separate minute choices,
        # including a stored DIM > SLEEP (the Inventure scheduler caps dim at sleep).
        panel.configured_dim_s = config.getfloat('timeout', panel.configured_dim_min * 60.,
                                                  minval=5., maxval=86400.)
        panel.configured_off_s = config.getfloat('sleep_timeout', panel.configured_sleep_min * 60.,
                                                  minval=1., maxval=86400.)
        panel.configured_sleep = config.getfloat('idle_timeout', panel.configured_sleep,
                                                 minval=0., maxval=86400.)
        panel.configured_notice = config.getfloat('notice_seconds', panel.configured_notice,
                                                  minval=1., maxval=60.)
        if not all(map(math.isfinite, (panel.configured_dim_s, panel.configured_off_s,
                                       panel.configured_sleep, panel.configured_notice))):
            raise config.error('OLED timers must be finite')
        # The operator edits whole minutes. The scheduler retains exact seconds
        # until an operator explicitly saves a minute preference.
        panel.configured_dim_min = min(60, max(1, round(panel.configured_dim_s / 60)))
        panel.configured_sleep_min = min(60, max(1, round(panel.configured_off_s / 60)))
        panel.configured_normal = config.getint('normal_contrast', 159, minval=1, maxval=159)
        panel.configured_dim = config.getint('dim_contrast', min(32, panel.configured_normal),
                                             minval=1, maxval=panel.configured_normal)
        panel.configured_brightness = config.getint('brightness', panel.configured_brightness,
                                                    minval=1, maxval=16)
        panel.configured_light = config.get('sleep_light', panel.configured_light).lower()
        if panel.configured_light not in ('manual', 'dynamic', 'white', 'off'):
            raise config.error('sleep_light must be manual, dynamic, white or off')
        panel.configured_policy = config.getchoice('timer_policy',
            {s:s for s in ('inventure','minutes')}, panel.configured_policy)
        panel.sleep_timeout, panel.sleep_notice = panel.configured_sleep, panel.configured_notice

    def get_status(self, eventtime):
        panel = self.panel
        return dict(timer_policy='minutes' if panel.minute_timers else 'inventure',
                    idle_timeout=panel.configured_sleep, timeout=panel.configured_dim_s,
                    sleep_timeout=panel.configured_off_s, notice_seconds=panel.configured_notice,
                    normal_contrast=panel.configured_normal, dim_contrast=panel.configured_dim,
                    brightness=panel.stream.awake_level + 1, sleep_light=panel.sleep_light,
                    active_timeout_s=panel.sleep_timeout, active_notice_s=panel.sleep_notice,
                    phase=panel.sleep_phase)


def load_config(config):
    return InventureDisplay(config)
