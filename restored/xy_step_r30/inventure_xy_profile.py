# SPDX-License-Identifier: GPL-3.0-or-later
class InventureXYStep(InventureXYStep):
    # The experimental travel ceiling is a request through
    # SET_VELOCITY_LIMIT. Keep the configured commissioning limits at startup.
    TRAVEL_TEST_VELOCITY = 296.
    TIMER_LIMITS = {
        'CLOCK_FREQ': 120000000,
        'INVENTURE_XY_STEP_TIMER_ABI': 3,
        'INVENTURE_XY_STEP_MIN_TICKS': 448,
        'INVENTURE_XY_STEP_HIGH_TICKS': 294,
        'INVENTURE_XY_STEP_LOW_FLOOR_TICKS': 90,
    }

    def _configure(self):
        constants = self._mcu.get_constants()
        if (not self._native or any(str(constants.get(key)) != str(expected)
                for key, expected in self.TIMER_LIMITS.items())
            or any(motors[0].get_pulse_duration()[0] != 4.e-6 for motors in self._motors)):
            return
        steps_per_second = (self.TIMER_LIMITS['CLOCK_FREQ']
                            / self.TIMER_LIMITS['INVENTURE_XY_STEP_MIN_TICKS'])
        self._motor_velocity = [
            motors[0].get_step_dist() * steps_per_second for motors in self._motors]
        self._mcu.add_config_cmd('inventure_xy_step_config')
        self._query = self._mcu.lookup_query_command('inventure_xy_step_query', self.RESPONSE)

    def check_move(self, move):
        super().check_move(move)
        if self._fast_ready:
            xy_fraction = math.hypot(*move.axes_r[:2])
            if xy_fraction:
                speed = self.TRAVEL_TEST_VELOCITY / xy_fraction
                if speed * speed < move.max_cruise_v2:
                    move.limit_speed(speed, move.accel)
