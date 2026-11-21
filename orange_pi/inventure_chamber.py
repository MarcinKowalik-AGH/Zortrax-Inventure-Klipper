# SPDX-License-Identifier: GPL-3.0-or-later
"""One operator setpoint for the two native Inventure chamber PID loops."""
import math


class InventureChamber:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.board = self.printer.load_object(config, 'inventure_mcu')
        self.heaters = self.printer.load_object(config, 'heaters')
        self.reactor = self.printer.get_reactor()
        self.names = (config.get('left_heater'), config.get('right_heater'))
        self.max_temp = config.getfloat('max_temp', 90., above=0., maxval=90.)
        self.zones = ()
        self.heaters.register_sensor(config, self)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command('SET_HEATER_TEMPERATURE', 'HEATER', 'chamber',
                                   self.cmd_set, desc='Set both chamber zones')
        gcode.register_command('INVENTURE_CHAMBER_STATUS', self.cmd_status,
                               desc='Show the center and both chamber zones')
        gcode.register_command('INVENTURE_CHAMBER_PID', self.cmd_pid)
        self.printer.register_event_handler('klippy:connect', self.connect)
        self.printer.register_event_handler('inventure:native_motion_lost', self.off)
        self.printer.register_event_handler('klippy:shutdown', self.off)
        self.reactor.register_timer(self.monitor, self.reactor.NOW)

    def connect(self):
        zones = tuple(self.heaters.lookup_heater(name) for name in self.names)
        for heater, channel, pin in zip(zones, (2, 3), ('HEATER_PC9', 'HEATER_PC8')):
            if (getattr(heater.sensor, 'channel', None) != channel or
                    getattr(heater.mcu_pwm, 'name', None) != pin):
                raise self.printer.config_error('Chamber heater and shared ADC do not match '+pin)
        self.zones = zones

    def off(self, *unused):
        for zone in self.zones:
            zone.set_temp(0.)

    def readings(self, now):
        return tuple(self.board.panel_temperature(i, now)[0] for i in (2, 3, 4))

    def monitor(self, now):
        if self.zones and any(zone.target_temp > 0 for zone in self.zones):
            if None in self.readings(now):
                self.off()
                self.printer.invoke_shutdown('Inventure chamber sensor missing, stale, or out of range')
        return now + .25

    def get_status(self, now):
        left, right, center = self.readings(now)
        zones = [zone.get_status(now) for zone in self.zones]
        targets = [z['target'] for z in zones]
        return dict(temperature=center, left=left, right=right,
                    target=targets[0] if len(targets) == 2 and targets[0] == targets[1] else None,
                    split_targets=len(targets) == 2 and targets[0] != targets[1],
                    power=sum(z['power'] for z in zones) / 2.,
                    zone_targets=targets, zone_power=[z['power'] for z in zones],
                    zone_objects=['heater_generic '+name for name in self.names],
                    sensors_valid=None not in (left, right, center))

    def get_temp(self, now):
        status = self.get_status(now)
        # TEMPERATURE_WAIT cannot treat a missing central sample as a cold chamber.
        return (float('nan') if status['temperature'] is None else status['temperature'],
                status['target'])

    def set_target(self, gcmd, target):
        if target == 0:
            self.off()
            return
        if (not math.isfinite(target) or not self.zones or not 0 < target <= self.max_temp
                or any(not zone.min_temp <= target <= zone.max_temp for zone in self.zones)):
            raise gcmd.error('Chamber target is outside the configured range')
        if None in self.readings(self.reactor.monotonic()):
            raise gcmd.error('A fresh reading is required from each of the three chamber sensors')
        try:
            from .inventure_fluidd import native_control_state, select_native_control
            select_native_control(self.board, native_control_state(self.board))
        except ValueError as exc:
            raise gcmd.error(str(exc)) from exc
        try:
            for zone in self.zones:
                self.heaters.set_temperature(zone, target)
        except Exception:
            self.off()
            raise

    def cmd_set(self, gcmd):
        wait = gcmd.get_int('WAIT', 0, minval=0, maxval=1)
        self.set_target(gcmd, gcmd.get_float('TARGET', 0.))
        if wait and any(z.target_temp for z in self.zones):
            self.heaters._wait_for_temperature(self)

    def check_busy(self, now):
        return any(zone.target_temp and zone.get_temp(now)[0] < zone.target_temp - 1
                   for zone in self.zones)

    def cmd_pid(self, gcmd):
        from .inventure_fluidd import require_idle, native_control_state, select_native_control
        side = gcmd.get('SIDE', 'LEFT').upper()
        if side not in ('LEFT', 'RIGHT'):
            raise gcmd.error('Choose SIDE=LEFT or RIGHT')
        target = gcmd.get_float('TARGET', 45., above=0., maxval=self.max_temp)
        if not math.isfinite(target):
            raise gcmd.error('Chamber PID target must be finite')
        try:
            require_idle(self.board)
            if None in self.readings(self.reactor.monotonic()) or any(z.target_temp for z in self.zones):
                raise ValueError('PID tuning requires three valid chamber sensors and both zone targets off')
            select_native_control(self.board, native_control_state(self.board))
        except ValueError as exc:
            raise gcmd.error(str(exc)) from exc
        name = self.names[side == 'RIGHT']
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command('PID_CALIBRATE HEATER=%s TARGET=%g' % (name, target))

    def cmd_status(self, gcmd):
        import json
        gcmd.respond_info('INVENTURE_CHAMBER '+json.dumps(self.get_status(self.reactor.monotonic())))


def load_config_prefix(config):
    if config.get_name() != 'inventure_chamber chamber':
        raise config.error('Use [inventure_chamber chamber]')
    return InventureChamber(config)
