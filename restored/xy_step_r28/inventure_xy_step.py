# SPDX-License-Identifier: GPL-3.0-or-later
# Loaded in the authenticated Inventure CoreXY module by the installer.
class InventureXYStep:
    RESPONSE = (
        "inventure_xy_step_state verified=%c enabled=%c active=%c blocks_a=%u"
        " blocks_b=%u steps_a=%u steps_b=%u aborted=%u errors=%u peak_late=%u"
    )

    def __init__(self, config, rails, configured_velocity):
        printer = config.get_printer()
        self._motors = [rail.get_steppers() for rail in rails]
        self._mcu = self._motors[0][0].get_mcu()
        self._configured_velocity = configured_velocity
        self._safe_velocity = None
        self._motor_velocity = None
        self._query = None
        self._native = self._native_wiring(config)
        self._mcu.register_config_callback(self._configure)
        printer.register_event_handler('klippy:connect', self._connect)
        printer.lookup_object('gcode').register_command(
            'INVENTURE_XY_STEP_STATUS', self._status,
            when_not_ready=True,
            desc='Report finite STEP trains and startup verification')

    def _native_wiring(self, config):
        expected = (('x', 'PA7', 'PB2'), ('y', 'PA6', 'PB1'))
        for motors, (axis, step, direction) in zip(self._motors, expected):
            if len(motors) != 1 or motors[0].get_mcu() is not self._mcu:
                return False
            section = config.getsection('stepper_' + axis)
            if (section.get('step_pin') != step
                or section.get('dir_pin').lstrip('!') != direction):
                return False
        return True

    def _configure(self):
        constants = self._mcu.get_constants()
        if str(constants.get('INVENTURE_XY_STEP_TIMER_ABI')) != '1' or not self._native:
            return
        clock = float(constants['CLOCK_FREQ'])
        period = float(constants['INVENTURE_XY_STEP_MIN_TICKS']) / clock
        pulse_widths = [motors[0].get_pulse_duration()[0] for motors in self._motors]
        # Retain the configured pulse width for both rising and falling
        # phases, including a software fallback for a changed configuration.
        self._motor_velocity = [
            motors[0].get_step_dist() / max(period, 2. * pulse)
            for motors, pulse in zip(self._motors, pulse_widths)]
        if pulse_widths == [4.e-6, 4.e-6]:
            self._mcu.add_config_cmd('inventure_xy_step_config')
        self._query = self._mcu.lookup_query_command(
            'inventure_xy_step_query', self.RESPONSE)

    def _connect(self):
        if self._query is None or self._mcu.is_fileoutput():
            return
        state = self._query.send()
        if state['verified'] != 3 or not state['enabled']:
            self._safe_velocity = self._configured_velocity
        logging.info("Inventure XY STEP startup %s", state)

    def check_move(self, move):
        if self._motor_velocity is None:
            return
        dx, dy = move.axes_r[:2]
        a, b = abs(dx + dy), abs(dx - dy)
        speed = min(self._motor_velocity[0] / a if a else math.inf,
                    self._motor_velocity[1] / b if b else math.inf)
        if self._safe_velocity is not None and (a or b):
            speed = min(speed, self._safe_velocity)
        if speed * speed < move.max_cruise_v2:
            move.limit_speed(speed, move.accel)

    def _status(self, gcmd):
        if self._query is None or self._mcu.is_fileoutput():
            gcmd.respond_info("Inventure XY STEP timer unavailable on this MCU or wiring")
            return
        state = self._query.send()
        fields = ('verified', 'enabled', 'active', 'blocks_a', 'blocks_b',
                  'steps_a', 'steps_b', 'aborted', 'errors', 'peak_late')
        gcmd.respond_info("Inventure XY STEP | " + " ".join(
            "%s=%s" % (field, state[field]) for field in fields))
