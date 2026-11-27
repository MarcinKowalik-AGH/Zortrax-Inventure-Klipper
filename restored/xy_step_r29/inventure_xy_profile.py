# SPDX-License-Identifier: GPL-3.0-or-later
class InventureXYStep(InventureXYStep):
    # r0323 reads the 200 mm/s entries after the CoreXY transform. The
    # mixed XY move obtains 1500 mm/s² from initialized planner state.
    OEM_MOTOR_VELOCITY = 200.
    OEM_XY_ACCELERATION = 1500.
    FALLBACK_VELOCITY = 30.
    FALLBACK_ACCELERATION = 200.

    def __init__(self, config, rails, configured_velocity):
        super().__init__(config, rails, configured_velocity)
        self._fast_ready = False
        self._motor_velocity = [self.OEM_MOTOR_VELOCITY] * 2

    def _configure(self):
        constants = self._mcu.get_constants()
        clock = constants.get('CLOCK_FREQ')
        period = constants.get('INVENTURE_XY_STEP_MIN_TICKS')
        pulse_ticks = constants.get('INVENTURE_XY_STEP_HIGH_TICKS')
        abi = constants.get('INVENTURE_XY_STEP_TIMER_ABI')
        if (not self._native or str(abi) != '2'
            or (str(clock), str(period), str(pulse_ticks)) != ('120000000', '660', '294')):
            return
        self._motor_velocity = [
            min(self.OEM_MOTOR_VELOCITY, motors[0].get_step_dist() * float(clock) / float(period))
            for motors in self._motors]
        if [motors[0].get_pulse_duration()[0] for motors in self._motors] == [4.e-6, 4.e-6]:
            self._mcu.add_config_cmd('inventure_xy_step_config')
        self._query = self._mcu.lookup_query_command('inventure_xy_step_query', self.RESPONSE)

    def _connect(self):
        if self._query is None or self._mcu.is_fileoutput():
            return
        state = self._query.send()
        self._fast_ready = state['verified'] == 3 and state['enabled'] == 1
        logging.info("Inventure XY STEP startup %s", state)

    def check_move(self, move):
        dx, dy = move.axes_r[:2]
        a, b = abs(dx + dy), abs(dx - dy)
        if not (a or b):
            return
        if self._fast_ready:
            speed = min(self._motor_velocity[0] / a if a else math.inf,
                        self._motor_velocity[1] / b if b else math.inf)
            accel = self.OEM_XY_ACCELERATION
        else:
            speed = min(self._configured_velocity, self.FALLBACK_VELOCITY)
            accel = self.FALLBACK_ACCELERATION
        if speed * speed < move.max_cruise_v2 or accel < move.accel:
            move.limit_speed(speed, accel)
