# Experimental V9 ABI3 board diagnostic/transport adapter, NOT complete printer logic.
# SPDX-License-Identifier: GPL-3.0-or-later
import bisect
import collections
import json
import logging
import math
from contextlib import nullcontext
from pathlib import Path
import struct
from .inventure_fans_core import fan_test_result, normalized_tach, validate_report

HOST_REVISION = 'RC8-CONTROL-R7-UIFAST-HOME-MACROS'
AXIS_MOTION_RELEASE = 'RC8-CONTROL-R7-ALL-AXIS-SPEED'
AXIS_MOTION_COMMAND = ('inventure_axis_motion action=%c directions=%c a=%u b=%u z=%u e=%u '
    'rate=%u pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u tool=%c')
AXIS_MOTION_LIMITS = (('axis_motion_max_hz','INVENTURE_AXIS_MOTION_MAX_HZ',125000),
    ('axis_motion_z_max_hz','INVENTURE_AXIS_MOTION_Z_MAX_HZ',3200),
    ('axis_motion_e_max_hz','INVENTURE_AXIS_MOTION_E_MAX_HZ',9600))
FACTORY_FEED_RELEASE = 'RC8-CONTROL-R7-FACTORY-FEED'
FACTORY_MOTION_RELEASE = 'RC8-CONTROL-R7-FACTORY-SPEED'
FACTORY_DUAL_RELEASE = 'RC8-CONTROL-R7-FACTORY-DUAL'
FACTORY_TIMING_QUERY = 'inventure_factory_motion_timing_query'
FACTORY_TIMING_RESPONSE = 'inventure_factory_motion_timing peak_callback_ticks=%u peak_late_ticks=%u'
FACTORY_COMMAND = ('inventure_factory_session action=%c tool=%c mode=%c monitor=%c '
    'duration_ms=%hu budget_steps=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u')
FACTORY_RESPONSE = ('inventure_factory_state token=%u active=%c reason=%c reject=%c '
    'tool=%c mode=%c running=%c a=%c b=%c pulses=%u pulse_age_ms=%u starts=%u '
    'fault=%c sequence=%u remaining_ms=%u')
Z_EDGE_COMMAND = ('inventure_z_edge action=%c owner=%u token=%u operation=%c '
    'steps=%hu interval_us=%hu')
Z_EDGE_RESPONSE = ('inventure_z_edge_state owner=%u token=%u operation=%c active=%c '
    'status=%c reject=%c sent=%u end_raw=%c')
Z_EDGE_ERRORS = {4:'z_contact_mcu_guard', 5:'z_contact_mcu_time_limit', 6:'z_contact_mcu_stopped'}
FEEDER_RUN_MAX_S = 50
# r0323 nozzle controller at 0x08036D6C / algorithm 0x0801C96E.
# Keep the original 0..255 output units here, not Klipper's PID parameters.
LOAD_PID_KP, LOAD_PID_KI, LOAD_PID_KD = 25., .0005, 230.
LOAD_PID_ALPHA, LOAD_PID_DT = .95, .1
LOAD_HOLD_MAX_S = 900
LOAD_E_STEPS_PER_MM = 960.
LOAD_JOG_WINDOW_S = .5
# Provisional cassette speed: the operator observed ~80 mm in 50 motor seconds.
# This is a rate-matching estimate, not a calibrated distance measurement.
LOAD_CASSETTE_MM_S = 1.6
LOAD_CASSETTE_RATE = round(LOAD_E_STEPS_PER_MM*LOAD_CASSETTE_MM_S)
LOAD_STREAM_COMMAND = ('inventure_load_stream action=%c steps=%u rate=%u max_ms=%u '
    'lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u '
    'tool=%c channel=%c e_direction=%c feeder_direction=%c')
LOAD_STREAM_RESPONSE = ('inventure_load_stream_state token=%u active=%c reason=%c reject=%c '
    'planned=%u sent=%u elapsed_ms=%u remaining_ms=%u sequence=%u feeder=%c')
LOAD_STREAM_REASONS = ('none','completed','operator_stop','lease_expired',
    'temperature_or_heater_guard','motion_ownership_lost','heartbeat_or_cable_lost','mcu_shutdown')

INPUT_RESPONSE = 'inventure_inputs clock=%u raw=%hu buttons=%c sequence=%u'
BOARD_RESPONSE = ('inventure_status armed=%c fault=%c seen=%c cable=%c '
    'p0=%u p1=%u p2=%u p3=%u p4=%u p5=%u')
UART_RESPONSE = 'inventure_uart_status channel=%c errors=%u received=%u sent=%u'
DEBUG_RESPONSE = 'debug_result val=%u'
GPIO_BASE = 0x40020000
GPIO_PORTS = 'ABCDEFG'
CASSETTE_PIN_NAMES = {
    'PB11':'x_endstop', 'PC15':'y_endstop', 'PE12':'z_bottom',
    'PC14':'nozzle_contact', 'PC13':'tray', 'PG2':'bin',
    'PB10':'front_door', 'PE15':'lid', 'PD10':'t0_a', 'PF1':'t0_b',
    'PD15':'t1_a', 'PG10':'t1_b', 'PD7':'up', 'PD6':'down',
    'PD5':'ok', 'PA15':'power', 'PB12':'t0_pulse_level',
    'PD14':'t1_pulse_level', 'PD11':'extruder_cable',
    'PE14':'head_front_tach', 'PE13':'model_bottom_tach',
    'PE9':'chamber_left_tach', 'PE11':'chamber_right_tach'}

CONTROL_RESPONSE = ('inventure_control_state mode=%c armed=%c active=%c latched=%c '
    'fault=%c guard_mask=%c reject=%c')
MOTION_COMMAND = ('inventure_motion action=%c directions=%c a=%u b=%u z=%u e=%u '
    'rate=%u pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u')
MOTION_RESPONSE = ('inventure_motion_state run=%u token=%u phase=%c reason=%c reject=%c '
    'active=%c events=%u elapsed_ms=%u hit=%hu')
MOTION_DRIVER_RESPONSE = 'inventure_motion_driver run=%u channel=%c planned=%u sent=%u direction=%c'
OUTPUT_RESPONSE = ('inventure_output_state channel=%c duty=%hu remaining_ms=%u token=%u '
    'reject=%c reason=%c')
GUARD_RESPONSE = 'inventure_guard_state sensor=%c enabled=%c min_c=%hi max_c=%hi reject=%c'
CONTROL_MODES = ('OFF','NATIVE','SERVICE')
CONTROL_REJECTS = (None,'invalid_request','another_owner_is_busy','mcu_not_ready',
    'FIRMWARE_RESTART_required_after_native_stop','required_sensor_or_door_interlock')
MOTION_PHASES = ('idle','running','holding','done','stopped','failed')
MOTION_REASONS = ('none','completed','stop_input','operator_stop','heartbeat_or_cable_lost',
    'door_open','maximum_time','mcu_shutdown','timing_fault','thermal_guard')
OUTPUT_REASONS = ('idle','applied','operator_off','duration_finished',
    'temperature_or_sensor_guard','heartbeat_or_cable_lost','mcu_shutdown')
OUTPUT_NAMES = ('F0','F1','F2','F3','F4','F5','T0','T1','PC9','PC8')

XY_SEEK_RESPONSE = ('inventure_xy_seek_state run=%u token=%u axis=%c phase=%c reason=%c '
    'reject=%c steps=%u elapsed_ms=%u enabled=%c endstop_mask=%c hit_mask=%c door_lock_enabled=%c')
XY_SEEK_PHASES = ('idle','seeking','hit','stopped','failed')
XY_SEEK_REASONS = ('none','endstop_hit','endstop_already_active','operator_stop',
    'heartbeat_lost','mcu_shutdown','door_open','heat_busy','maximum_time',
    'endstop_not_found_within_travel','unexpected_other_endstop',
    'endstop_did_not_release','contact_before_minimum_slow_travel')
XY_SEEK_REJECTS = (None,'another_test_running','mcu_not_ready',
    'close_front_door_and_lid','heat_or_cooldown_running','invalid_request')
XY_REPEAT_RESPONSE = ('inventure_xy_repeat_state run=%u token=%u axis=%c phase=%c stage=%c '
    'reason=%c reject=%c first_steps=%u backoff_steps=%hu slow_steps=%hu elapsed_ms=%u '
    'enabled=%c endstop_mask=%c hit_mask=%c released=%c repeat=%c')

MOTOR_TEST_RESPONSE = ('inventure_motor_state run=%u token=%u motor=%c direction=%c '
    'phase=%c reason=%c reject=%c forward=%hu returned=%hu planned=%hu enabled=%c')
MOTOR_TEST_R2_RESPONSE = MOTOR_TEST_RESPONSE + ' door_lock_enabled=%c door_closed_mask=%c'
DOOR_POLICY_RESPONSE = ('inventure_door_policy_state enabled=%c disable_bit=%c '
    'closed_mask=%c lock_command_mask=%c motor_busy=%c reject=%c')
DOOR_POLICY_REJECTS = (None, 'invalid_request', 'finish_or_stop_the_active_test_first',
                       'mcu_not_ready')
MOTOR_PHASES = ('idle', 'forward', 'reversing', 'return', 'done', 'aborted')
MOTOR_REASONS = ('none', 'completed', 'operator_stop', 'heartbeat_lost', 'door_open',
                 'endstop_active', 'shutdown', 'maximum_test_time', 'heat_busy')
MOTOR_REJECTS = (None, 'another_test_running', 'mcu_not_ready', 'close_front_door_and_lid',
                 'endstop_active', 'invalid_request', 'heat_or_cooldown_running')

FAN_DIAG_PHASES = ('idle', 'full', 'off', 'minimum', 'completed', 'cancelled',
                   'aborted_safety', 'timeout')
FAN_DIAG_REJECTIONS = ('', 'test already active', 'heater output active or temperature limit exceeded',
                      'MCU armed, in fault, missing heartbeat or head cable open',
                      'missing, stale or unsuitable diagnostic ADC samples')

FAN_MANUAL_ACTIONS = {'STATUS': 0, 'ON': 1, 'OFF': 2, 'PASS': 3, 'FAIL': 4, 'CANCEL': 5}
FAN_MANUAL_PHASES = ('idle', 'on', 'off', 'pass', 'fail', 'aborted', 'timeout')
FAN_MANUAL_ERRORS = (
    '', 'invalid action or channel', 'another fan test is active',
    'MCU interlock: armed, heater output, cable or heartbeat',
    'local thermal protection requires this fan',
    'use ON, then OFF, then PASS or FAIL for this channel',
    'wait for the fan to stop and the measurement window to finish',
    'tach did not confirm ON and OFF; record FAIL or repeat',
)
FAN_MANUAL_RESPONSE = (
    'inventure_fan_manual_state run=%u channel=%c phase=%c reject=%c reason=%c '
    'required=%c applied=%c on_edges=%hu off_edges=%hu pcb_adc=%hu pcb_temp=%hi '
    'passed=%c failed=%c'
)
T0_HEAT_RESPONSE = (
    'inventure_t0_heat_state run=%u phase=%c reason=%c reject=%c elapsed_ms=%u '
    'remaining_ms=%u target_c=%hi duty=%c applied=%c cooling_mask=%c '
    't0_adc=%hu t1_adc=%hu peak0_adc=%hu peak1_adc=%hu start0_adc=%hu start1_adc=%hu'
)
T0_HEAT_PHASES = ('idle', 'heating', 'cooling', 'done')
T0_HEAT_REASONS = ('', '20_second_window_finished', 'operator_stop',
                   'heartbeat_lost', 'required_adc_invalid_or_stale',
                   '50C_limit', 'mcu_shutdown')
T0_HEAT_ERRORS = ('', 'another test or cooldown is active',
                  'MCU is not ready for a test', 'required ADC is missing or stale',
                  'a monitored sensor is already at the 50C limit', 'invalid test action')
HEAT_TEST_RESPONSE = (
    'inventure_heat_state run=%u heater=%c phase=%c reason=%c reject=%c elapsed_ms=%u '
    'remaining_ms=%u target_c=%hi duty=%c applied=%c cooling_mask=%c'
)
HEAT_SENSOR_RESPONSE = (
    'inventure_heat_sensor run=%u sensor=%c adc=%hu start_adc=%hu peak_adc=%hu '
    'heating_peak_adc=%hu age_ms=%u'
)
HEAT_TEST_NAMES = ('T0', 'T1', 'PC9', 'PC8')
HEAT_TEST_PINS = ('PA0', 'PE5', 'PC9', 'PC8')


class R5SharedSensor:
    """Use the board's existing eight ADC readers; never allocate a second ADC."""
    def __init__(self,config,board):
        self.printer=config.get_printer();self.board=board
        self.channel=config.getint('sensor_channel',minval=0,maxval=7)
        self.minimum=self.maximum=None;self.callback=None;self.failed=False
        board.adc_subscribers[self.channel].append(self._sample)
    def setup_minmax(self,minimum,maximum):self.minimum,self.maximum=minimum,maximum
    def setup_callback(self,callback):self.callback=callback
    def get_report_time_delta(self):return .2
    def _sample(self,read_time,temperature):
        if self.failed:return
        if (not math.isfinite(temperature) or self.minimum is None
                or not self.minimum<=temperature<=self.maximum):
            self.failed=True
            self.printer.invoke_async_shutdown('Inventure R5 ADC %d out of range'%self.channel)
        elif self.callback is not None:self.callback(read_time,temperature)


class R5FixedPWM:
    """Native Klipper heater/fan algorithms with the fixed OEM timer backend."""
    def __init__(self,board,name,physical,ticks,hardware):
        self.board,self.name,self.physical=board,name,physical
        self.ticks,self.hardware=ticks,hardware
    def get_mcu(self):return self.physical.get_mcu()
    def setup_cycle_time(self,cycle_time,hardware_pwm=False):
        if not math.isfinite(cycle_time) or abs(cycle_time*120000000-self.ticks)>2:
            raise self.board.printer.config_error('%s requires pwm_cycle_time %.12g'%(self.name,self.ticks/120000000.))
        self.physical.setup_cycle_time(self.ticks/120000000.,hardware_pwm=self.hardware)
    def setup_max_duration(self,value):self.physical.setup_max_duration(value)
    def setup_start_value(self,start,shutdown):
        if self.name.startswith('HEATER_') and (start or shutdown):
            raise self.board.printer.config_error('Inventure heaters require start/shutdown OFF')
        self.physical.setup_start_value(start,shutdown)
    def next_aligned_print_time(self,print_time,allow_early=0.):
        return self.physical.next_aligned_print_time(print_time,allow_early)
    def set_pwm(self,print_time,value):
        if not math.isfinite(value) or not 0<=value<=1:
            raise self.board.printer.command_error('Invalid Inventure native PWM')
        self.physical.set_pwm(print_time,value)


class InventureMCU:
    ADC_MAX = 4095
    ADC_SAMPLES = 3
    ADC_REFERENCE_MV = 3300
    NOZZLE_MV_PER_C = 5
    ADC_PINS = ('PC0', 'PF8', 'PC1', 'PC2', 'PA4', 'PF3', 'PF4', 'PC4')
    ADC_NAMES = ('t0','t1','chamber_left','chamber_right','chamber_center',
                 'psu','mainboard','tool_pcb')
    ADC_CEILINGS = (310,310,150,150,150,100,100,100)
    FAN_NAMES = ('head_front','model_bottom','chamber_left','chamber_right',
                 'rear','transfer')
    FAN_PWM_PINS = ('PB15','PB14','PC7','PC6','PA1','PD13')
    FAN_TACH_PINS = ('PE14','PE13','PE9','PE11',None,None)
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.mcu_name = config.get('mcu', 'mcu')
        self.mcu = self.printer.lookup_object('mcu' if self.mcu_name == 'mcu' else 'mcu ' + self.mcu_name)
        self.gcode = self.printer.lookup_object('gcode')
        self.allow_actuators = config.getboolean('allow_actuators', False)
        self.diag_adc = config.getboolean('diagnostic_adc', True)
        self._configure_procedures(config)
        self.status = {}
        self.status_received = None
        self.inputs_received = None
        self.temperatures = [None] * 8
        self.raw_adc = [None] * 8
        self.adc_received = [None] * 8
        self.adc_generation = 0
        self.adc_accepting = True
        self.rfid_rx = [collections.deque(maxlen=2048), collections.deque(maxlen=2048)]
        self.rfid_overruns = [0, 0]
        self.inputs = {}
        self.uart_status = [{}, {}]
        self.rfid_received = [None,None]
        self.adcs = []
        self.adc_subscribers = [[] for _ in range(8)]
        self.fans=[{} for _ in range(6)]
        self.fan_received=[None]*6
        self.fan_window_received=[None]*6
        self.fan_window_valid=[False]*6
        self.fan_rejected=0
        self.commands = {}
        self.query_mutex = self.reactor.mutex()
        self.motion_mutex = self.reactor.mutex()
        self.cassette_gpio_baseline = None
        self.feeder_test_pending_until = None
        self.feeder_run_context = None
        self.feeder_run_last = {'phase':'idle'}
        self.load_test_context = None
        self.load_test_last = {'phase':'idle','load_completed':False}
        self.connected = False
        self.fan_group = 0
        self.fan_batch = False
        self.uart_buffer_stats = {}
        self.next_uart_stats = 0.
        self.p4_boot = {}
        self.p4_abi = False
        self.mcu_release = 'unknown'
        self.local_oled_state = {}
        self.local_cooling_state = {}
        self.local_cooling_received = None
        self.local_oled_received = None
        self.next_local_query = 0.
        self.t0_heat_last = {}
        self.t0_heat_peak_key = None
        self.t0_heat_peak_adc = [0, 0]
        self.sensor_test_last = {'phase': 'idle'}
        self.sensor_test_context = None
        self.thermal_display_held = False
        self.heat_test_abi = 0
        self.fan_manual_last = {}
        self.motor_test_abi = 0
        self.motor_test_last = {}
        self.motor_test_token = int(self.reactor.monotonic()*1000000) & 0xffffffff
        self.door_policy_abi = 0
        self.door_policy_global = False
        self.door_policy_default_enabled = True
        self.door_policy_last = {}
        self.xy_seek_abi = 0
        self.xy_seek_token = self.motor_test_token
        self.xy_seek_last = {}
        self.xy_repeat_abi = 0
        self.xy_repeat_last = {}
        self.control_abi = 0
        self.control_last = {}
        self.motion_last = {}
        self.motion_request = {}
        self.output_last = {}
        self.guard_last = {}
        self.control_token = self.motor_test_token
        self.native_outputs = {}

        table_path = Path(__file__).with_name('inventure_lut.json')
        self.lut = json.loads(table_path.read_text())
        self.thresholds = [row[0] for row in self.lut]
        if self.diag_adc:
            pins = self.printer.lookup_object('pins')
            for i, pin in enumerate(self.ADC_PINS):
                adc = pins.setup_pin('adc', self.mcu_name + ':' + pin)
                adc.setup_adc_sample(0.1, sample_time=0.001, sample_count=self.ADC_SAMPLES,
                                     minval=0., maxval=1., range_check_count=0)
                adc.setup_adc_callback(lambda samples, i=i: self._adc(i, samples))
                self.adcs.append(adc)
        # A new chip name prevents obsolete virtual heater polarities from
        # entering the R5 native path. Existing diagnostic cfg needs no edits.
        self.printer.lookup_object('pins').register_chip('inventure_r5',self)
        self.printer.load_object(config,'heaters').add_sensor_factory(
            'Inventure R5 ADC',lambda c:R5SharedSensor(c,self))
        self.mcu.register_config_callback(self._build_config)
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        for name, callback in [('INVENTURE_STATUS',self.cmd_status),('INVENTURE_ARM',self.cmd_arm),
                               ('INVENTURE_RFID_SEND',self.cmd_uart),('INVENTURE_FEEDER',self.cmd_feeder),
                               ('INVENTURE_LOCK',self.cmd_lock),('INVENTURE_INPUTS',self.cmd_inputs),
                               ('INVENTURE_RGB',self.cmd_rgb),('INVENTURE_RFID_STATUS',self.cmd_uart_status),
                               ('INVENTURE_PANEL_IO_STATUS',self.cmd_panel_io),
                               ('INVENTURE_VERSION',self.cmd_version)]:
            self.gcode.register_command(name, callback)
        self.gcode.register_command('INVENTURE_FAN_DIAG_START',self.cmd_fan_diag_start)
        self.gcode.register_command('INVENTURE_FAN_DIAG_STATUS',self.cmd_fan_diag_status,when_not_ready=True)
        self.gcode.register_command('INVENTURE_FAN_DIAG_STOP',self.cmd_fan_diag_stop,when_not_ready=True)
        self.gcode.register_command('INVENTURE_FAN_MANUAL', self.cmd_fan_manual)
        self.gcode.register_command('INVENTURE_FAN_MANUAL_STATUS', self.cmd_fan_manual_status,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_FAN_MANUAL_CANCEL', self.cmd_fan_manual_cancel,
                                    when_not_ready=True)
        self.timer = self.reactor.register_timer(self._tick)
        self.t0_heat_timer = self.reactor.register_timer(self._t0_heat_tick)
        self.sensor_test_timer = self.reactor.register_timer(self._sensor_test_tick)
        self.motor_test_timer = self.reactor.register_timer(self._motor_test_tick)
        self.xy_seek_timer = self.reactor.register_timer(self._xy_seek_tick)
        self.xy_repeat_timer = self.reactor.register_timer(self._xy_repeat_tick)
        self.control_timer = self.reactor.register_timer(self._control_poll)
        for name, callback in (
                ('INVENTURE_CONTROL',self.cmd_control),
                ('INVENTURE_CONTROL_STATUS',self.cmd_control_status),
                ('INVENTURE_STOP',self.cmd_control_stop),
                ('INVENTURE_MOTION',self.cmd_motion),
                ('INVENTURE_MOVE',self.cmd_move),
                ('INVENTURE_OUTPUT',self.cmd_output),
                ('INVENTURE_GUARD',self.cmd_guard),
                ('INVENTURE_SENSORS',self.cmd_sensors),
                ('INVENTURE_CASSETTE_DIAG',self.cmd_cassette_diag)):
            self.gcode.register_command(name,callback,when_not_ready=True)
        self.gcode.register_command('INVENTURE_FEEDER_TEST',self.cmd_feeder_test,when_not_ready=True)
        self.gcode.register_command('INVENTURE_FEEDER_RUN',self.cmd_feeder_run,when_not_ready=True)
        self.gcode.register_command('INVENTURE_LOAD_TEST',self.cmd_load_test,when_not_ready=True)
        self.gcode.register_command('INVENTURE_PROCEDURE',self.cmd_procedure,when_not_ready=True)
        self.gcode.register_command('INVENTURE_XY_REPEAT',self.cmd_xy_repeat,when_not_ready=True)
        self.gcode.register_command('INVENTURE_XY_REPEAT_STOP',self.cmd_xy_repeat_stop,when_not_ready=True)
        self.gcode.register_command('INVENTURE_XY_SEEK',self.cmd_xy_seek,when_not_ready=True)
        self.gcode.register_command('INVENTURE_XY_SEEK_STOP',self.cmd_xy_seek_stop,when_not_ready=True)
        self.gcode.register_command('INVENTURE_MOTOR_TEST', self.cmd_motor_test,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_MOTOR_TEST_STOP', self.cmd_motor_test_stop,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_DOOR_POLICY', self.cmd_door_policy,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_HEAT_TEST', self.cmd_t0_heat_test,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_HEAT_TEST_STATUS', self.cmd_t0_heat_status,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_HEAT_TEST_STOP', self.cmd_t0_heat_stop,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_SENSOR_TEST', self.cmd_sensor_test,
                                    when_not_ready=True)
        from .inventure_menu_service import ZortraxMenuService
        self.zortrax=ZortraxMenuService(self,config)
    def _build_config(self):
        if self.mcu.get_constant_float('INVENTURE_ABI') != 3:
            raise self.printer.config_error('Inventure MCU ABI must be 3')
        self.mcu.add_config_cmd('inventure_aux_config')
        for i in range(2):
            self.mcu.add_config_cmd('inventure_uart_config channel=%d' % i)
        for name, fmt in {
            'heartbeat':'inventure_heartbeat', 'query':'inventure_query',
            'arm':'inventure_arm acknowledge=%u',
            'uart':'inventure_uart_send channel=%c data=%*s',
            'feeder':'inventure_feeder channel=%c direction=%c duration_ms=%hu',
            'lock':'inventure_lock channel=%c value=%c',
            'inputs':'inventure_inputs_query',
            'uart_status':'inventure_uart_query channel=%c',
            'rgb':'inventure_rgb mask=%c',
        }.items(): self.commands[name] = self.mcu.lookup_command(fmt)
        if float(self.mcu.get_constants().get('INVENTURE_FANS_ABI',0))==1:
            self.commands['fans']=self.mcu.lookup_command('inventure_fan_query')
            self.fan_batch=(int(self.mcu.get_constants().get('INVENTURE_FAN_BATCH_ABI',0))==1)
            if self.fan_batch:
                self.commands['fans_batch']=self.mcu.lookup_command('inventure_fan_query_batch group=%c')
                if int(self.mcu.get_constants().get('INVENTURE_TX_RING_ABI',0))!=1:
                    raise self.printer.config_error('P06 batch requires paired MCU UART ring ABI')
            fmt='inventure_fan_status clock=%u window_clock=%u sequence=%u channel=%c duty=%c window_duty=%c edges=%u normalized=%u bad=%u peak=%u fault_mask=%c mixed=%c required_mask=%c'
            if not self.mcu.check_valid_response(fmt):
                raise self.printer.config_error('P06 fan status protocol mismatch')
            self.mcu.register_serial_response(self._fan_report,fmt)
        if int(self.mcu.get_constants().get('INVENTURE_TX_RING_ABI',0))==1:
            self.commands['uart_buffers']=self.mcu.lookup_command('inventure_uart_buffers_query')
            self.mcu.register_serial_response(self._uart_buffers,
                'inventure_uart_buffers pending=%hu highwater=%hu dropped=%u rxpending=%c')
        # New RC6 MCU reports local startup state. Older MCU versions are
        # supported for diagnosis, but cannot claim to have the local splash.
        self.local_oled_abi = (int(self.mcu.get_constants().get(
            'INVENTURE_LOCAL_OLED_ABI',0))==1)
        self.mcu_release=self.mcu.get_constants().get('INVENTURE_MCU_RELEASE','unknown')
        if int(self.mcu.get_constants().get('INVENTURE_LOCAL_COOLING_ABI',0))==1:
            self.commands['cooling_query']=self.mcu.lookup_command('inventure_cooling_query')
            fmt='inventure_cooling_state clock=%u mode=%c reason=%c valid=%c mask=%c max_temp=%hi native_owner=%c adc_failures=%u gpio_updates=%u'
            if not self.mcu.check_valid_response(fmt):
                raise self.printer.config_error('RC8 cooling response ABI mismatch')
            self.mcu.register_serial_response(self._cooling_response,fmt)
        if int(self.mcu.get_constants().get('INVENTURE_FAN_DIAG_ABI',0))==1:
            self.commands['fan_diag']=self.mcu.lookup_query_command('inventure_fan_diag action=%c',
                'inventure_fan_diag_state run=%u phase=%c reject=%c full=%c low=%c unverified=%c')
            self.commands['fan_diag_row']=[self.mcu.lookup_query_command(
                'inventure_fan_diag_row_query run=%u oid=%c',
                'inventure_fan_diag_row run=%u oid=%c data=%*s',oid=oid) for oid in range(8)]
        if int(self.mcu.get_constants().get('INVENTURE_FAN_DIAG_DETAIL_ABI',0))==1:
            self.commands['fan_diag_detail']=self.mcu.lookup_query_command(
                'inventure_fan_diag_detail_query rejected=%c',
                'inventure_fan_diag_detail run=%u reason=%c channel=%c value=%hi full_gpio=%c')
        if int(self.mcu.get_constants().get('INVENTURE_FAN_MANUAL_ABI', 0)) == 1:
            self.commands['fan_manual'] = self.mcu.lookup_query_command(
                'inventure_fan_manual action=%c channel=%c', FAN_MANUAL_RESPONSE)
        self.gcode.register_command('INVENTURE_COOLING_STATUS',self.cmd_cooling_status,when_not_ready=True)
        if int(self.mcu.get_constants().get('INVENTURE_T0_HEAT_TEST_ABI', 0)) == 1:
            self.commands['t0_heat'] = self.mcu.lookup_query_command(
                'inventure_t0_heat_test action=%c', T0_HEAT_RESPONSE)
        self.heat_test_abi = int(self.mcu.get_constants().get('INVENTURE_HEAT_TEST_ABI', 0))
        self.control_abi = int(self.mcu.get_constants().get('INVENTURE_CONTROL_ABI',0))
        if getattr(self,'native_outputs',{}) and self.control_abi!=1:
            raise self.printer.config_error('inventure_r5 native PWM requires the CONTROL-R5 MCU')
        if self.control_abi == 1:
            expected_control={'INVENTURE_MOTION_ABI':1,'INVENTURE_OUTPUT_ABI':1,
                'INVENTURE_CONTROL_MAX_HZ':20000,'INVENTURE_CONTROL_MAX_STEPS':2000000,
                'INVENTURE_CONTROL_MAX_MS':600000,'INVENTURE_HEAT_BENCH_ONLY':0}
            if any(int(self.mcu.get_constants().get(k,-1))!=v for k,v in expected_control.items()):
                raise self.printer.config_error('R5 control MCU limits do not match this host')
            for key,command,response in (
                ('control','inventure_control action=%c mode=%c',CONTROL_RESPONSE),
                ('motion',MOTION_COMMAND,MOTION_RESPONSE),
                ('motion_driver','inventure_motion_driver_query channel=%c',MOTION_DRIVER_RESPONSE),
                ('output','inventure_output action=%c channel=%c duty=%hu duration_ms=%u limit_c=%hi token=%u',OUTPUT_RESPONSE),
                ('guard','inventure_control_guard sensor=%c enabled=%c min_c=%hi max_c=%hi',GUARD_RESPONSE)):
                self.commands[key]=self.mcu.lookup_query_command(command,response)
        self.load_stream_abi=int(self.mcu.get_constants().get('INVENTURE_LOAD_STREAM_ABI',0))
        if self.load_stream_abi==1:
            if self.control_abi!=1:raise self.printer.config_error('Smooth load requires CONTROL ABI 1')
            self.commands['load_stream']=self.mcu.lookup_query_command(LOAD_STREAM_COMMAND,LOAD_STREAM_RESPONSE)
        self.factory_abi=int(self.mcu.get_constants().get('INVENTURE_FACTORY_ABI',0))
        self.factory_feed_clock=int(self.mcu.get_constants().get('INVENTURE_FACTORY_FEED_CLOCK',0))
        self.factory_motion_abi=int(self.mcu.get_constants().get('INVENTURE_FACTORY_MOTION_ABI',0))
        self.factory_motion_max_hz=int(self.mcu.get_constants().get('INVENTURE_FACTORY_MOTION_MAX_HZ',0))
        legacy_tools=1 if self.factory_motion_abi==1 else 0
        self.factory_motion_tools=int(self.mcu.get_constants().get('INVENTURE_FACTORY_MOTION_TOOLS',legacy_tools))
        if self.factory_abi==1:
            self.commands['factory_session']=self.mcu.lookup_query_command(FACTORY_COMMAND,FACTORY_RESPONSE)
        self.z_home_abi=int(self.mcu.get_constants().get('INVENTURE_Z_HOME_ABI',0))
        self.z_edge_abi=int(self.mcu.get_constants().get('INVENTURE_Z_EDGE_ABI',0))
        self.native_z_guard_abi=int(self.mcu.get_constants().get('INVENTURE_NATIVE_Z_GUARD_ABI',0))
        self.native_z_refine_abi=int(self.mcu.get_constants().get('INVENTURE_NATIVE_Z_REFINE_ABI',0))
        if self.z_edge_abi==1:
            if self.control_abi!=1 or self.factory_abi!=1:
                raise self.printer.config_error('MCU Z edge probing requires CONTROL and FACTORY ABI 1')
            self.commands['z_edge']=self.mcu.lookup_query_command(Z_EDGE_COMMAND,Z_EDGE_RESPONSE)
        if self.factory_motion_abi==1:
            self.commands['factory_motion_timing']=self.mcu.lookup_query_command(
                FACTORY_TIMING_QUERY,FACTORY_TIMING_RESPONSE)
        self.axis_motion_abi=int(self.mcu.get_constants().get('INVENTURE_AXIS_MOTION_ABI',0))
        for attr,key,_ in AXIS_MOTION_LIMITS:
            setattr(self,attr,int(self.mcu.get_constants().get(key,0)))
        if self.axis_motion_abi==1:
            if self.control_abi!=1 or any(getattr(self,attr)!=limit for attr,_,limit in AXIS_MOTION_LIMITS):
                raise self.printer.config_error('Unexpected axis timer MCU limits')
            self.commands['axis_motion']=self.mcu.lookup_query_command(AXIS_MOTION_COMMAND,MOTION_RESPONSE)
        self.motor_test_abi = int(self.mcu.get_constants().get('INVENTURE_MOTOR_TEST_ABI', 0))
        self.door_policy_abi = int(self.mcu.get_constants().get('INVENTURE_DOOR_POLICY_ABI', 0))
        self.xy_seek_abi = int(self.mcu.get_constants().get('INVENTURE_XY_SEEK_ABI', 0))
        if self.xy_seek_abi == 1:
            expected_seek={'INVENTURE_XY_SEEK_MAX_STEPS':102400,'INVENTURE_XY_SEEK_MAX_HZ':3200,
                           'INVENTURE_XY_SEEK_MAX_MS':40000,'INVENTURE_XY_SEEK_X_DIR_MASK':0,
                           'INVENTURE_XY_SEEK_Y_DIR_MASK':2}
            if (self.motor_test_abi != 2 or self.door_policy_abi != 1
                or any(int(self.mcu.get_constants().get(k,-1))!=v for k,v in expected_seek.items())):
                raise self.printer.config_error('XY seek MCU limits/directions do not match this host')
            self.commands['xy_seek'] = self.mcu.lookup_query_command(
                'inventure_xy_seek action=%c axis=%c token=%u',XY_SEEK_RESPONSE)
        self.xy_repeat_abi = int(self.mcu.get_constants().get('INVENTURE_XY_REPEAT_ABI', 0))
        if self.xy_repeat_abi == 1:
            expected_repeat={'INVENTURE_XY_REPEAT_BACKOFF_STEPS':1280,
                'INVENTURE_XY_REPEAT_BACKOFF_HZ':1280,'INVENTURE_XY_REPEAT_SLOW_STEPS':2560,
                'INVENTURE_XY_REPEAT_SLOW_HZ':640}
            if self.xy_seek_abi != 1 or any(int(self.mcu.get_constants().get(k,-1))!=v for k,v in expected_repeat.items()):
                raise self.printer.config_error('XY repeat MCU limits do not match this host')
            self.commands['xy_repeat'] = self.mcu.lookup_query_command(
                'inventure_xy_repeat action=%c axis=%c token=%u',XY_REPEAT_RESPONSE)
        if self.door_policy_abi == 1:
            if int(self.mcu.get_constants().get('INVENTURE_DOOR_POLICY_DISABLE_BIT', -1)) != 3:
                raise self.printer.config_error('Door policy MCU flag does not match this host')
            self.door_policy_global = int(self.mcu.get_constants().get('INVENTURE_DOOR_POLICY_GLOBAL', 0)) == 1
            default_enabled = int(self.mcu.get_constants().get('INVENTURE_DOOR_POLICY_DEFAULT_ENABLED', 1))
            if default_enabled not in (0,1):
                raise self.printer.config_error('Invalid MCU door policy default')
            self.door_policy_default_enabled = bool(default_enabled)
            self.commands['door_policy'] = self.mcu.lookup_query_command(
                'inventure_door_policy action=%c enabled=%c', DOOR_POLICY_RESPONSE)
        if self.motor_test_abi in (1, 2):
            if (int(self.mcu.get_constants().get('INVENTURE_MOTOR_TEST_HZ', 0)) != 400
                or int(self.mcu.get_constants().get('INVENTURE_MOTOR_TEST_PULSE_US', 0)) != 100
                or int(self.mcu.get_constants().get('INVENTURE_MOTOR_ENABLE_ON', 0)) != 1):
                raise self.printer.config_error('Motor test MCU limits do not match this host')
            self.commands['motor_test'] = self.mcu.lookup_query_command(
                'inventure_motor_test action=%c motor=%c direction=%c token=%u',
                MOTOR_TEST_R2_RESPONSE if self.motor_test_abi == 2 else MOTOR_TEST_RESPONSE)
            if self.motor_test_abi == 2 and self.door_policy_abi != 1:
                raise self.printer.config_error('R2 motor test requires door policy ABI 1')
        if self.heat_test_abi == 3:
            expected={'INVENTURE_CHAMBER_HEAT_ABI':1,'INVENTURE_CHAMBER_DUTY_PCT':75,
                      'INVENTURE_CHAMBER_DURATION_MS':180000,'INVENTURE_CHAMBER_OVERHEAT_C':50}
            if any(int(self.mcu.get_constants().get(key,0))!=value for key,value in expected.items()):
                raise self.printer.config_error('R3 chamber test limits do not match this host')
        if self.heat_test_abi in (2, 3):
            self.commands['heat_test'] = self.mcu.lookup_query_command(
                'inventure_heat_test action=%c heater=%c', HEAT_TEST_RESPONSE)
            self.commands['heat_sensor'] = self.mcu.lookup_query_command(
                'inventure_heat_sensor_query run=%u sensor=%c', HEAT_SENSOR_RESPONSE)
        if self.local_oled_abi:
            self.commands['local_oled_query']=self.mcu.lookup_command('inventure_local_oled_query')
            fmt='inventure_local_oled_status state=%c boot_revision=%c baud=%u'
            if not self.mcu.check_valid_response(fmt):
                raise self.printer.config_error('RC6 local OLED response ABI mismatch')
            self.mcu.register_serial_response(self._local_oled_response,fmt)
        self.p4_abi = (int(self.mcu.get_constants().get('INVENTURE_P4_BOOT_JOURNAL_ABI',0))==1)
        if self.p4_abi:
            self.commands['p4_baud']=self.mcu.lookup_command('inventure_p4_baud_query')
            self.mcu.register_serial_response(self._p4_baud_received,
                'inventure_p4_baud baud=%u revision=%c saved=%c index=%c slots=%hu invalid=%hu')
        self.mcu.register_serial_response(self._status,
            'inventure_status armed=%c fault=%c seen=%c cable=%c p0=%u p1=%u p2=%u p3=%u p4=%u p5=%u')
        self.mcu.register_serial_response(self._arm_result,
            'inventure_arm_result armed=%c fault=%c')
        self.mcu.register_serial_response(self._uart_rx,
            'inventure_uart_rx channel=%c data=%*s')
        self.mcu.register_serial_response(self._inputs,
            'inventure_inputs clock=%u raw=%hu buttons=%c sequence=%u')
        self.mcu.register_serial_response(self._uart_status,
            'inventure_uart_status channel=%c errors=%u received=%u sent=%u')
        for key, command, response in (
                ('sensor_inputs','inventure_inputs_query',INPUT_RESPONSE),
                ('sensor_board','inventure_query',BOARD_RESPONSE),
                ('sensor_uart','inventure_uart_query channel=%c',UART_RESPONSE)):
            self.commands[key] = self.mcu.lookup_query_command(command,response)
        if self.control_abi == 1 and self.mcu.check_valid_response(DEBUG_RESPONSE):
            self.commands['gpio_read'] = self.mcu.lookup_query_command(
                'debug_read order=%c addr=%u',DEBUG_RESPONSE)
    def _adc(self, index, samples):
        # MCU_adc invokes this from serialhdl's background thread. Printer
        # state, thermal procedures and shutdown handlers belong to the reactor.
        if not self.adc_accepting: return
        received = self.reactor.monotonic()
        generation = self.adc_generation
        samples = tuple(tuple(sample) for sample in samples)
        def deliver(eventtime):
            if self.adc_accepting and generation == self.adc_generation:
                self._deliver_adc(index, samples, received)
        self.reactor.register_async_callback(deliver)

    def _deliver_adc(self, index, samples, received):
        for read_time, normalized in samples:
            if not self.adc_accepting: break
            valid = (math.isfinite(read_time) and math.isfinite(normalized)
                     and 0. <= normalized <= 1.)
            # Klipper reports the normalized sum; r0323 divides the
            # original three-conversion sum as an unsigned integer first.
            total = round(normalized * self.ADC_MAX * self.ADC_SAMPLES) if valid else None
            raw = total // self.ADC_SAMPLES if valid else None
            self.raw_adc[index] = raw
            self.adc_received[index] = received
            if not valid: temperature = float('nan')
            elif index < 2:
                temperature = raw*self.ADC_REFERENCE_MV/self.ADC_MAX/self.NOZZLE_MV_PER_C
            else:
                pos = bisect.bisect_left(self.thresholds, raw)
                temperature = self.lut[pos][1] if pos < len(self.lut) else -46
            self.temperatures[index] = temperature if valid else None
            for cb in self.adc_subscribers[index]:
                if not self.adc_accepting: break
                cb(read_time + .003, temperature)
    def _ready(self):
        self.connected = True
        if self.p4_abi:
            self.commands['p4_baud'].send([])
        if 'local_oled_query' in self.commands:
            self.commands['local_oled_query'].send([])
        self.reactor.update_timer(self.timer, self.reactor.NOW)
    def _shutdown(self):
        if self.connected and 'fans' in self.commands:
            self.commands['fans'].send([])  # HF_IN_SHUTDOWN: retain MCU fan cause
        self.adc_accepting = False
        self.adc_generation += 1
        self.connected = False
        self._load_test_cancel('mcu_shutdown',phase='failed')
        self._feeder_run_cancel('mcu_shutdown',phase='failed',stop=False)
        self.feeder_test_pending_until = None
        self.cassette_gpio_baseline = None
        self.door_policy_last = {}
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
    def _tick(self, eventtime):
        if not self.connected: return self.reactor.NEVER
        self.commands['heartbeat'].send([])
        self.commands['query'].send([])
        self.commands['inputs'].send([])
        if 'fans_batch' in self.commands:
            self.commands['fans_batch'].send([self.fan_group])
            self.fan_group=(self.fan_group+1)%3
        elif 'fans' in self.commands:self.commands['fans'].send([])
        if 'uart_buffers' in self.commands and eventtime>=self.next_uart_stats:
            self.commands['uart_buffers'].send([])
            self.next_uart_stats=eventtime+1.0
        if eventtime>=self.next_local_query:
            if 'local_oled_query' in self.commands:
                self.commands['local_oled_query'].send([])
            self.next_local_query=eventtime+2.0
        return eventtime + 0.25
    def _local_oled_response(self,params):
        received = self.reactor.monotonic()
        sample = {k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(
            lambda now:self._deliver_local_oled(sample,received))
    def _deliver_local_oled(self,sample,received):
        self.local_oled_state=dict(sample)
        self.local_oled_received=received

    def _cooling_response(self,params):
        received=self.reactor.monotonic()
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(lambda now:self._deliver_cooling(sample,received))
    def _deliver_cooling(self,sample,received):
        limits={'clock':0xffffffff,'mode':3,'reason':3,'valid':1,'mask':63,
                'native_owner':1,'adc_failures':0xffffffff,'gpio_updates':0xffffffff}
        if any(type(sample.get(k)) is not int or not 0<=sample[k]<=v for k,v in limits.items()):return
        if sample['mask']&2 or type(sample.get('max_temp')) is not int or not -32768<=sample['max_temp']<=32767:return
        if self.local_cooling_state:
            delta=(sample['clock']-self.local_cooling_state['clock'])&0xffffffff
            if not 0<delta<0x80000000:return
        self.local_cooling_state=dict(sample);self.local_cooling_received=received
    def cmd_cooling_status(self,gcmd):
        supported='cooling_query' in self.commands
        if supported:self.commands['cooling_query'].send([])
        now=self.reactor.monotonic()
        gcmd.respond_info('V9_COOLING '+json.dumps({'supported':supported,
            'query_sent':supported,'last_received':dict(self.local_cooling_state),
            'age':None if self.local_cooling_received is None else max(0.,now-self.local_cooling_received)},sort_keys=True))

    def _t0_heat_query(self, action):
        cmd = self.commands.get('t0_heat')
        if cmd is None:
            raise ValueError('Install the matching T0-HEAT-R1 MCU application first')
        if action == 1 and not self.connected:
            raise ValueError('MCU is disconnected')
        p = self._thermal_query_send(cmd, [action], start=action == 1)
        limits = {'run': 0xffffffff, 'phase': 3, 'reason': 6, 'reject': 5,
                  'elapsed_ms': 20000, 'remaining_ms': 20000, 'target_c': 45,
                  'duty': 25, 'applied': 1, 'cooling_mask': 63,
                  't0_adc': 65535, 't1_adc': 65535,
                  'peak0_adc': 65535, 'peak1_adc': 65535,
                  'start0_adc': 65535, 'start1_adc': 65535}
        if any(type(p.get(k)) is not int or not 0 <= p[k] <= high
               for k, high in limits.items()):
            raise ValueError('Invalid MCU T0 heat response')
        if p['target_c'] != 45 or p['duty'] not in (0, 25):
            raise ValueError('Unexpected MCU test limits')
        if p['phase'] in (1, 2) and p['cooling_mask'] != 33:
            raise ValueError('Unexpected MCU test cooling channels')
        remaining = 20000 - p['elapsed_ms'] if p['phase'] == 1 else 0
        if p['remaining_ms'] != remaining:
            raise ValueError('Invalid MCU test timing')
        def temp(raw):
            return round(raw * 660. / 4095., 2) if 8 < raw < 4095 else None
        def rise(raw, start):
            return round((raw-start)*660./4095., 2) if temp(raw) is not None and temp(start) is not None else None
        # MCU peaks stop at the heater cutoff. Keep the later observed
        # cooling samples too, without claiming continuous MCU-rate coverage.
        peak_key = (p['run'], p['start0_adc'], p['start1_adc'])
        if p['phase'] == 0 or peak_key != getattr(self, 't0_heat_peak_key', None):
            self.t0_heat_peak_key = peak_key
            self.t0_heat_peak_adc = [0, 0]
        if p['phase'] != 0:
            for i in range(2):
                for raw in (p['peak%d_adc' % i], p['t%d_adc' % i]):
                    if temp(raw) is not None:
                        self.t0_heat_peak_adc[i] = max(self.t0_heat_peak_adc[i], raw)
        peak0, peak1 = self.t0_heat_peak_adc
        self.t0_heat_last = {
            'run': p['run'], 'phase': T0_HEAT_PHASES[p['phase']],
            'reason': T0_HEAT_REASONS[p['reason']],
            'reject': T0_HEAT_ERRORS[p['reject']],
            'elapsed_s': p['elapsed_ms'] / 1000.,
            'remaining_s': p['remaining_ms'] / 1000.,
            'target_c': 45, 'maximum_duty_pct': 25,
            'duty_pct': p['duty'], 'heater_on': bool(p['applied']),
            'cooling_channels': [i for i in range(6) if p['cooling_mask'] & (1 << i)],
            't0_adc': p['t0_adc'], 't1_adc': p['t1_adc'],
            't0_c': temp(p['t0_adc']), 't1_c': temp(p['t1_adc']),
            'peak_t0_c': temp(peak0), 'peak_t1_c': temp(peak1),
            'peak_scope': 'mcu_heating_plus_host_observations',
            'start_t0_c': temp(p['start0_adc']), 'start_t1_c': temp(p['start1_adc']),
            'peak_rise_t0_c': rise(peak0, p['start0_adc']),
            'peak_rise_t1_c': rise(peak1, p['start1_adc']),
            'heating_peak_t0_c': temp(p['peak0_adc']),
            'heating_peak_t1_c': temp(p['peak1_adc']),
            'heating_peak_rise_t0_c': rise(p['peak0_adc'], p['start0_adc']),
            'heating_peak_rise_t1_c': rise(p['peak1_adc'], p['start1_adc']),
        }
        return self.t0_heat_last

    def _thermal_test_temperature(self, index, raw):
        if type(raw) is not int or not 0 < raw < self.ADC_MAX:
            return None, None, 'invalid'
        if index < 2:
            if raw <= 8:
                return None, round(raw * 660. / self.ADC_MAX, 2), 'near_ground'
            stock = raw * 660. / self.ADC_MAX
        else:
            pos = bisect.bisect_left(self.thresholds, raw)
            stock = self.lut[pos][1] if pos < len(self.lut) else -46
        valid = -30. < stock < self.ADC_CEILINGS[index]
        stock = round(stock, 2)
        return stock if valid else None, stock, 'valid' if valid else 'out_of_range'

    def _thermal_display_hold(self, hold):
        printer=getattr(self, 'printer', None)
        oled=printer.lookup_object('inventure_oled', None) if printer is not None else None
        if oled is not None:
            setter=getattr(oled, 'set_thermal_test_hold', None)
            if setter is None:
                if hold:
                    raise ValueError('Install the matching R2-UART host OLED module first')
            else:
                setter(hold)
        self.thermal_display_held=bool(hold)

    def _thermal_query_send(self, command, data, start=False, owner=None):
        # Timer jobs and console commands must not replace each other's
        # SerialRetryCommand callback while either waits for the same reply.
        mutex=getattr(self,'query_mutex',None)
        with mutex if mutex is not None else nullcontext():
            context=getattr(self,'load_test_context',None)
            if start and context is not None and owner is not context:
                raise ValueError('Load test owns the actuators; use INVENTURE_LOAD_TEST or INVENTURE_STOP')
            if owner is not None:
                if context is not owner or not self.connected:
                    raise ValueError('Load test was stopped before the actuator request')
                if start and self.reactor.monotonic()>=owner['deadline']:
                    raise ValueError('Load test duration finished before the actuator request')
            heartbeat=self.commands.get('heartbeat')
            if self.connected and heartbeat is not None:
                # Preface and read share a queue; no actuator window is renewed.
                return command.send_with_preface(heartbeat, [], data, retry=not start)
            return command.send(data, retry=not start)

    def _thermal_heat_query(self, action, heater=0):
        query_started=self.reactor.monotonic()
        cmd = self.commands.get('heat_test')
        if cmd is None:
            if action == 1 and heater != 0:
                raise ValueError('T1/PC9/PC8 tests require the THERMAL-TEST-R2 MCU application')
            return self._t0_heat_query(action)
        if action == 1 and not self.connected:
            raise ValueError('MCU is disconnected')
        heat_abi = getattr(self, 'heat_test_abi', 2)
        if action == 1 and heater >= 2 and heat_abi != 3:
            raise ValueError('The 75% chamber test requires the matching R3 MCU application')
        if action == 1:
            self._thermal_display_hold(True)
        # A missing response must never trigger a new START after an aborted
        # trial has already cooled down. Read queries may use normal retries.
        wire_action = 3 if action == 1 and heater >= 2 else action
        p = self._thermal_query_send(cmd, [wire_action, heater], start=action == 1)
        limits = {'run': 0xffffffff, 'heater': 3, 'phase': 3, 'reason': 6,
                  'reject': 5, 'elapsed_ms': 180000, 'remaining_ms': 180000,
                  'target_c': 45, 'duty': 75, 'applied': 1, 'cooling_mask': 63}
        if any(type(p.get(k)) is not int or not 0 <= p[k] <= high
               for k, high in limits.items()):
            raise ValueError('Invalid MCU thermal test response')
        chamber = heat_abi == 3 and p['heater'] >= 2
        duration, duty_limit, target = (180000, 75, 0) if chamber else (20000, 25, 45)
        if (p['target_c'] != target or p['duty'] not in (0, duty_limit)
                or p['elapsed_ms'] > duration or p['remaining_ms'] > duration
                or (chamber and p['phase'] == 1 and p['duty'] != 75)):
            raise ValueError('Unexpected MCU thermal test limits')
        if action == 1 and not p['reject'] and p['heater'] != heater:
            raise ValueError('MCU selected a different heater than requested')
        expected_fans = 33 if p['heater'] < 2 else 12
        if p['phase'] in (1, 2) and p['cooling_mask'] != expected_fans:
            raise ValueError('Unexpected MCU thermal test cooling channels')
        remaining = duration - p['elapsed_ms'] if p['phase'] == 1 else 0
        if p['remaining_ms'] != remaining or (p['phase'] != 1 and (p['duty'] or p['applied'])):
            raise ValueError('Invalid MCU test timing or output state')
        self._thermal_display_hold(p['phase'] in (1, 2))
        thermal = {}
        for i, name in enumerate(self.ADC_NAMES):
            row = self._thermal_query_send(self.commands['heat_sensor'], [p['run'], i])
            row_limits = {'run': 0xffffffff, 'sensor': 7, 'adc': 65535,
                          'start_adc': 65535, 'peak_adc': 65535,
                          'heating_peak_adc': 65535, 'age_ms': 0xffffffff}
            if any(type(row.get(k)) is not int or not 0 <= row[k] <= high
                   for k, high in row_limits.items()):
                raise ValueError('Invalid MCU thermal sensor response')
            if row['run'] != p['run'] or row['sensor'] != i:
                raise ValueError('Thermal run or sensor changed during read; request status again')
            current, stock, state = self._thermal_test_temperature(i, row['adc'])
            if row['age_ms'] >= 1000:
                current, state = None, 'stale'
            start, start_stock, _ = self._thermal_test_temperature(i, row['start_adc'])
            peak, peak_stock, _ = self._thermal_test_temperature(i, row['peak_adc'])
            heating_peak, _, _ = self._thermal_test_temperature(i, row['heating_peak_adc'])
            def rise(value, raw_value):
                if value is None or start is None:
                    return None
                return round((raw_value-row['start_adc'])*660./4095., 2) if i < 2 else round(value-start, 2)
            thermal[name] = {'pin': self.ADC_PINS[i], 'adc': row['adc'],
                'c': current, 'stock_c': stock, 'state': state,
                'age_s': None if row['age_ms'] == 0xffffffff else row['age_ms']/1000.,
                'start_c': start, 'start_stock_c': start_stock,
                'peak_c': peak, 'peak_stock_c': peak_stock,
                'peak_rise_c': rise(peak, row['peak_adc']),
                'heating_peak_c': heating_peak,
                'heating_peak_rise_c': rise(heating_peak, row['heating_peak_adc'])}
        selected = p['heater']
        result = {'run': p['run'], 'heater': HEAT_TEST_NAMES[selected],
            'heater_pin': HEAT_TEST_PINS[selected],
            'control_sensor': self.ADC_PINS[selected],
            'phase': T0_HEAT_PHASES[p['phase']],
            'reason': ('maximum_test_time_reached' if chamber and p['reason'] == 1
                       else T0_HEAT_REASONS[p['reason']]),
            'reject': T0_HEAT_ERRORS[p['reject']],
            'elapsed_s': p['elapsed_ms']/1000., 'remaining_s': p['remaining_ms']/1000.,
            'target_c': None if chamber else 45,
            'control_mode': 'constant_power' if chamber else 'temperature_target',
            'overheat_limit_c': 50, 'maximum_duration_s': duration/1000.,
            'maximum_duty_pct': duty_limit, 'duty_pct': p['duty'],
            'heater_on': bool(p['applied']),
            'cooling_channels': [i for i in range(6) if p['cooling_mask'] & (1 << i)],
            'peak_scope': 'mcu_samples_heating_and_cooling', 'thermal': thermal}
        for i in (0, 1):
            name = self.ADC_NAMES[i]
            row = thermal[name]
            result[name+'_adc'], result[name+'_c'] = row['adc'], row['c']
            result['start_'+name+'_c'] = row['start_c']
            result['peak_'+name+'_c'] = row['peak_c']
            result['peak_rise_'+name+'_c'] = row['peak_rise_c']
            result['heating_peak_'+name+'_c'] = row['heating_peak_c']
            result['heating_peak_rise_'+name+'_c'] = row['heating_peak_rise_c']
        uart=getattr(self, 'uart_buffer_stats', {})
        result['comm']={'host_revision':HOST_REVISION,
            'query_ms':round(max(0.,self.reactor.monotonic()-query_started)*1000.,2),
            'heartbeat_preface':self.connected and 'heartbeat' in self.commands,
            'oled_bulk_hold':self.thermal_display_held,
            'uart_dropped':uart.get('dropped'), 'uart_pending':uart.get('pending'),
            'uart_highwater':uart.get('highwater'), 'uart_rxpending':uart.get('rxpending')}
        self.t0_heat_last = result
        return result

    def _sensor_test_snapshot(self, now):
        result = {}
        for i, name in enumerate(self.ADC_NAMES):
            raw, received = self.raw_adc[i], self.adc_received[i]
            current, stock, state = self._thermal_test_temperature(i, raw)
            age = None if received is None else max(0., now-received)
            if not self.connected or age is None or not 0. <= now-received < 1.:
                current, state = None, 'stale'
            result[name] = {'pin': self.ADC_PINS[i], 'adc': raw,
                            'c': current, 'stock_c': stock, 'state': state,
                            'age_s': None if age is None else round(age, 3)}
        return result

    def _sensor_test_update(self, now, stop=False):
        context = self.sensor_test_context
        if context is None:
            return self.sensor_test_last
        samples = self._sensor_test_snapshot(now)
        for name, row in samples.items():
            value = row['c']
            if value is not None:
                context['minimum'][name] = min(context['minimum'].get(name, value), value)
                context['maximum'][name] = max(context['maximum'].get(name, value), value)
            start = context['start'][name]['c']
            row['start_c'] = start
            row['minimum_c'] = context['minimum'].get(name)
            row['peak_c'] = context['maximum'].get(name)
            row['peak_rise_c'] = None if start is None or row['peak_c'] is None else round(row['peak_c']-start, 2)
        elapsed = max(0., now-context['started'])
        phase = 'stopped' if stop else 'done' if elapsed >= 20. else 'observing'
        result = {'phase': phase, 'sensor': context['selected'],
            'elapsed_s': round(min(20., elapsed), 3),
            'remaining_s': round(max(0., 20.-elapsed), 3),
            'mode': 'read_only', 'result': 'observation_only', 'thermal': samples}
        self.sensor_test_last = result
        if phase != 'observing':
            self.sensor_test_context = None
        return result

    def cmd_sensor_test(self, gcmd):
        action = gcmd.get('ACTION', 'START').upper()
        if action not in ('START', 'STATUS', 'STOP'):
            raise gcmd.error('Use ACTION=START, STATUS or STOP')
        now = self.reactor.monotonic()
        if action == 'START':
            if self.sensor_test_context is not None:
                raise gcmd.error('A sensor observation is already active; use STATUS or STOP')
            aliases = {name.upper(): name for name in self.ADC_NAMES}
            aliases.update({pin: name for pin, name in zip(self.ADC_PINS, self.ADC_NAMES)})
            aliases['ALL'] = 'all'
            selected = gcmd.get('SENSOR', 'ALL').upper()
            if selected not in aliases:
                raise gcmd.error('Use SENSOR=T0, T1, PC1, PC2, PA4, PF3, PF4, PC4 or ALL')
            self.sensor_test_context = {'started': now, 'selected': aliases[selected],
                'start': self._sensor_test_snapshot(now), 'minimum': {}, 'maximum': {}}
        result = self._sensor_test_update(now, action == 'STOP')
        gcmd.respond_info('V9_SENSOR_TEST ' + json.dumps(result, sort_keys=True))
        self.reactor.update_timer(self.sensor_test_timer,
            now+1. if result['phase'] == 'observing' else self.reactor.NEVER)

    def _sensor_test_tick(self, eventtime):
        result = self._sensor_test_update(eventtime)
        self.gcode.respond_info('V9_SENSOR_TEST ' + json.dumps(result, sort_keys=True))
        return eventtime+1. if result['phase'] == 'observing' else self.reactor.NEVER

    def _t0_heat_command(self, gcmd, action, heater=0):
        try:
            state = self._thermal_heat_query(action, heater)
        except Exception as exc:
            raise gcmd.error(str(exc))
        gcmd.respond_info(('V9_HEAT_TEST ' if 'heater' in state else 'V9_T0_HEAT ') + json.dumps(state, sort_keys=True))
        if state['phase'] in ('heating', 'cooling'):
            self.reactor.update_timer(self.t0_heat_timer, self.reactor.monotonic() + 1.)
        else:
            self.reactor.update_timer(self.t0_heat_timer, self.reactor.NEVER)
        if state['reject']:
            raise gcmd.error(state['reject'])

    def cmd_t0_heat_test(self, gcmd):
        action = gcmd.get('ACTION', 'START').upper()
        if action not in ('START', 'STATUS', 'STOP'):
            raise gcmd.error('Use ACTION=START, STATUS or STOP')
        heater = gcmd.get('HEATER', 'T0').upper()
        if heater not in HEAT_TEST_NAMES:
            raise gcmd.error('Use HEATER=T0, T1, PC9 or PC8')
        self._t0_heat_command(gcmd, {'STATUS': 0, 'START': 1, 'STOP': 2}[action],
                              HEAT_TEST_NAMES.index(heater))

    def cmd_t0_heat_status(self, gcmd):
        self._t0_heat_command(gcmd, 0)

    def cmd_t0_heat_stop(self, gcmd):
        self._t0_heat_command(gcmd, 2)

    def _t0_heat_tick(self, eventtime):
        if not self.connected:
            return self.reactor.NEVER
        try:
            state = self._thermal_heat_query(0)  # read-only; never renews the MCU window
            if 'heater' in state and state['phase'] in ('heating', 'cooling'):
                # Full baseline/MCU peaks remain in START, explicit STATUS,
                # and the final record. Avoid 2.6 KB of JSON every second.
                compact={key:state[key] for key in ('run','heater','phase','elapsed_s',
                    'remaining_s','reason','reject','duty_pct','heater_on','cooling_channels','comm')}
                for key in ('control_mode','overheat_limit_c','maximum_duration_s','maximum_duty_pct'):
                    compact[key]=state[key]
                compact['detail']='periodic_compact'
                compact['temperatures_c']={row['pin']:row['c'] for row in state['thermal'].values()}
                self.gcode.respond_info('V9_HEAT_TEST ' + json.dumps(compact, sort_keys=True))
            else:
                prefix='V9_HEAT_TEST ' if 'heater' in state else 'V9_T0_HEAT '
                self.gcode.respond_info(prefix + json.dumps(state, sort_keys=True))
        except Exception:
            logging.exception('T0 heat diagnostic query failed; MCU owns cutoff')
            return self.reactor.NEVER
        return eventtime + 1. if state['phase'] in ('heating', 'cooling') else self.reactor.NEVER

    def _xy_repeat_query(self,action=0,axis=0):
        wire=self.commands.get('xy_repeat')
        if wire is None:raise ValueError('Backoff and slow approach require the RC8-MOTOR-R4-XY-REPEAT MCU application')
        if action==1:
            if not self.connected:raise ValueError('MCU is not connected')
            # Shared with the single-seek command: a token cannot collide
            # when changing between diagnostic procedures on this host.
            self.xy_seek_token=(self.xy_seek_token+1)&0xffffffff or 1
        token=self.xy_seek_token if action==1 else 0
        p=self._thermal_query_send(wire,[action,axis,token],start=action==1)
        bounds={'run':0xffffffff,'token':0xffffffff,'axis':1,'phase':4,'stage':2,'reason':12,
                'reject':5,'first_steps':102400,'backoff_steps':1280,'slow_steps':2560,
                'elapsed_ms':0xffffffff,'enabled':1,'endstop_mask':3,'hit_mask':3,'released':1,'repeat':1}
        if any(type(p.get(k)) is not int or not 0<=p[k]<=v for k,v in bounds.items()):
            raise ValueError('Invalid MCU XY repeat response')
        if (p['enabled'] != int(p['phase']==1)
            or (p['repeat'] and p['phase']==2 and (p['reason']!=1 or p['stage']!=2
                or p['backoff_steps']!=1280 or not p['released'] or p['slow_steps']<64
                or not p['hit_mask']&(1<<p['axis'])))
            or (action==1 and not p['reject'] and (p['token']!=token or p['axis']!=axis or not p['repeat']))):
            raise ValueError('Inconsistent MCU XY repeat result or token')
        state=dict(run=p['run'],token=p['token'],axis=('X','Y')[p['axis']],
            phase=XY_SEEK_PHASES[p['phase']],stage=('first_approach','backoff','slow_approach')[p['stage']],
            reason=XY_SEEK_REASONS[p['reason']],reject=XY_SEEK_REJECTS[p['reject']],
            repeat_mode=bool(p['repeat']),driver_enabled=bool(p['enabled']),
            first_approach_steps=p['first_steps'],backoff_steps=p['backoff_steps'],
            slow_approach_steps=p['slow_steps'],switch_release_confirmed=bool(p['released']),
            elapsed_s=round(p['elapsed_ms']/1000.,3),hit_mask=p['hit_mask'],
            x_endstop=bool(p['endstop_mask']&1),y_endstop=bool(p['endstop_mask']&2),
            backoff_nominal_mm=2,backoff_hz=1280,slow_hz=640,
            slow_approach_nominal_mm=round(p['slow_steps']/640.,4),
            maximum_slow_nominal_mm=4,maximum_duration_s=40,
            establishes_position=False,pulse_high_us=100)
        self.xy_repeat_last=state;return state

    def cmd_xy_repeat(self,gcmd):
        action=gcmd.get('ACTION','START').upper();name=gcmd.get('AXIS','X').upper()
        if action not in ('START','STOP','STATUS') or name not in ('X','Y'):
            raise gcmd.error('Use AXIS=X or Y and ACTION=START, STOP or STATUS')
        try:state=self._xy_repeat_query({'STATUS':0,'START':1,'STOP':2}[action],int(name=='Y'))
        except Exception as exc:
            if action=='START' and 'xy_repeat' in self.commands:
                try:self.commands['xy_repeat'].send([2,0,0])
                except Exception:logging.exception('XY repeat STOP failed; MCU owns stopping')
            raise gcmd.error(str(exc))
        gcmd.respond_info('V9_XY_REPEAT '+json.dumps(state,sort_keys=True))
        self.reactor.update_timer(self.xy_repeat_timer,self.reactor.monotonic()+.5
            if state['driver_enabled'] else self.reactor.NEVER)
        if state['reject']:raise gcmd.error(state['reject'])

    def cmd_xy_repeat_stop(self,gcmd):
        try:state=self._xy_repeat_query(2)
        except Exception as exc:raise gcmd.error(str(exc))
        self.reactor.update_timer(self.xy_repeat_timer,self.reactor.NEVER)
        gcmd.respond_info('V9_XY_REPEAT '+json.dumps(state,sort_keys=True))

    def _xy_repeat_tick(self,eventtime):
        if not self.connected:return self.reactor.NEVER
        try:
            state=self._xy_repeat_query()
            self.gcode.respond_info('V9_XY_REPEAT '+json.dumps(state,sort_keys=True))
        except Exception:
            logging.exception('XY repeat read failed; requesting STOP')
            try:self.commands['xy_repeat'].send([2,0,0])
            except Exception:logging.exception('XY repeat STOP failed; MCU owns stopping')
            return self.reactor.NEVER
        return eventtime+.5 if state['driver_enabled'] else self.reactor.NEVER

    def _xy_seek_query(self,action=0,axis=0):
        wire=self.commands.get('xy_seek')
        if wire is None:raise ValueError('XY approach requires the RC8-MOTOR-R3-XY-SEEK MCU application')
        if action == 1:
            if not self.connected:raise ValueError('MCU is not connected')
            self.xy_seek_token=(self.xy_seek_token+1)&0xffffffff or 1
        token=self.xy_seek_token if action==1 else 0
        p=self._thermal_query_send(wire,[action,axis,token],start=action==1)
        bounds={'run':0xffffffff,'token':0xffffffff,'axis':1,'phase':4,'reason':12,'reject':5,
                'steps':102400,'elapsed_ms':0xffffffff,'enabled':1,'endstop_mask':3,'hit_mask':3,
                'door_lock_enabled':1}
        if any(type(p.get(k)) is not int or not 0<=p[k]<=v for k,v in bounds.items()):
            raise ValueError('Invalid MCU XY approach response')
        if (p['enabled'] != int(p['phase']==1)
            or (p['phase']==2 and (p['reason'] not in (1,2) or not p['hit_mask']&(1<<p['axis'])))
            or (action==1 and not p['reject'] and (p['token']!=token or p['axis']!=axis))):
            raise ValueError('Inconsistent MCU XY approach result or token')
        state=dict(run=p['run'],token=p['token'],axis=('X','Y')[p['axis']],
            phase=XY_SEEK_PHASES[p['phase']],reason=XY_SEEK_REASONS[p['reason']],
            reject=XY_SEEK_REJECTS[p['reject']],steps_each_motor=p['steps'],
            elapsed_s=round(p['elapsed_ms']/1000.,3),driver_enabled=bool(p['enabled']),
            x_endstop=bool(p['endstop_mask']&1),y_endstop=bool(p['endstop_mask']&2),
            hit_mask=p['hit_mask'],door_lock_enabled=bool(p['door_lock_enabled']),
            endstop_pin=('PB11','PC15')[p['axis']],dir_a=0,dir_b=p['axis'],
            step_pins=['PA7','PA6'],pulse_hz_max=3200,pulse_high_us=100,
            maximum_steps_each_motor=102400,maximum_duration_s=40,
            oem_scale_steps_per_mm=640,maximum_travel_nominal_mm=160,
            distance_nominal_mm=round(p['steps']/640.,4),
            establishes_position=False,automatic_retract=False)
        self.xy_seek_last=state
        return state

    def cmd_xy_seek(self,gcmd):
        action=gcmd.get('ACTION','START').upper();name=gcmd.get('AXIS','X').upper()
        if action not in ('START','STOP','STATUS') or name not in ('X','Y'):
            raise gcmd.error('Use AXIS=X or Y and ACTION=START, STOP or STATUS')
        try:state=self._xy_seek_query({'STATUS':0,'START':1,'STOP':2}[action],int(name=='Y'))
        except Exception as exc:
            if action=='START' and 'xy_seek' in self.commands:
                try:self.commands['xy_seek'].send([2,0,0])
                except Exception:logging.exception('XY STOP failed; MCU heartbeat and limits own stopping')
            raise gcmd.error(str(exc))
        gcmd.respond_info('V9_XY_SEEK '+json.dumps(state,sort_keys=True))
        self.reactor.update_timer(self.xy_seek_timer,self.reactor.monotonic()+.5
                                  if state['driver_enabled'] else self.reactor.NEVER)
        if state['reject']:raise gcmd.error(state['reject'])

    def cmd_xy_seek_stop(self,gcmd):
        try:state=self._xy_seek_query(2)
        except Exception as exc:raise gcmd.error(str(exc))
        self.reactor.update_timer(self.xy_seek_timer,self.reactor.NEVER)
        gcmd.respond_info('V9_XY_SEEK '+json.dumps(state,sort_keys=True))

    def _xy_seek_tick(self,eventtime):
        if not self.connected:return self.reactor.NEVER
        try:
            state=self._xy_seek_query()
            self.gcode.respond_info('V9_XY_SEEK '+json.dumps(state,sort_keys=True))
        except Exception:
            logging.exception('XY approach read failed; requesting STOP')
            try:self.commands['xy_seek'].send([2,0,0])
            except Exception:logging.exception('XY STOP failed; MCU owns stopping')
            return self.reactor.NEVER
        return eventtime+.5 if state['driver_enabled'] else self.reactor.NEVER

    def _door_policy_query(self, enabled=None):
        wire=self.commands.get('door_policy')
        if wire is None:
            raise ValueError('Door policy requires the RC8-MOTOR-R2-DOOR-POLICY MCU application')
        if enabled is not None and not self.connected:
            raise ValueError('MCU is not connected')
        action=int(enabled is not None)
        self.door_policy_last={}
        p=self._thermal_query_send(wire,[action,1 if enabled is None else enabled],start=bool(action))
        global_policy=getattr(self,'door_policy_global',False)
        bounds={'enabled':1,'disable_bit':1,'closed_mask':3,'lock_command_mask':3,
                'motor_busy':1,'reject':3}
        if (any(type(p.get(k)) is not int or not 0<=p[k]<=v for k,v in bounds.items())
            or p['enabled']+p['disable_bit'] != 1
            or (action and not p['reject'] and p['enabled'] != enabled)
            or (global_policy and not p['enabled'] and p['lock_command_mask'])):
            raise ValueError('Invalid MCU door policy response')
        state=dict(enabled=bool(p['enabled']),oem_disable_bit=p['disable_bit'],
            oem_disable_bit_index=3,front_door_closed=bool(p['closed_mask']&2),
            lid_closed=bool(p['closed_mask']&1),lock_command_mask=p['lock_command_mask'],
            physical_lock_position_verified=False,motor_busy=bool(p['motor_busy']),
            reject=DOOR_POLICY_REJECTS[p['reject']],scope='global' if global_policy else 'bounded_motor_tests',
            default_enabled=bool(getattr(self,'door_policy_default_enabled',True)),
            resets_on_mcu_restart=True)
        self.door_policy_last=state
        return state

    def cmd_door_policy(self,gcmd):
        enabled=None if gcmd.get('ENABLE',None) is None else gcmd.get_int('ENABLE',minval=0,maxval=1)
        try:state=self._door_policy_query(enabled)
        except Exception as exc:
            if enabled == 0 and 'door_policy' in self.commands and not getattr(self,'door_policy_global',False):
                try:self.commands['door_policy'].send([1,1])
                except Exception:logging.exception('Could not confirm restoring door policy')
            raise gcmd.error(str(exc))
        gcmd.respond_info('V9_DOOR_POLICY '+json.dumps(state,sort_keys=True))
        if state['reject']:raise gcmd.error(state['reject'])
        if enabled == 0:
            gcmd.respond_info('Door lock disabled for both doors. Be careful of moving printer parts.')

    def _motor_test_query(self, action=0, motor=0, direction=1):
        wire = self.commands.get('motor_test')
        if wire is None:
            raise ValueError('Motor tests require an RC8-MOTOR-R1 or R2 MCU application')
        if action == 1:
            if not self.connected: raise ValueError('MCU is not connected')
            self.motor_test_token = (self.motor_test_token+1) & 0xffffffff or 1
        token = self.motor_test_token if action == 1 else 0
        p = self._thermal_query_send(wire, [action, motor, direction, token], start=action == 1)
        bounds = {'run':0xffffffff,'token':0xffffffff,'motor':3,'direction':1,
                  'phase':5,'reason':8,'reject':6,'forward':640,'returned':640,
                  'planned':640,'enabled':1}
        if getattr(self,'motor_test_abi',1) == 2:
            bounds.update(door_lock_enabled=1,door_closed_mask=3)
        if any(type(p.get(k)) is not int or not 0 <= p[k] <= v for k,v in bounds.items()):
            raise ValueError('Invalid MCU motor test response')
        planned = (640,640,401,96)[p['motor']]
        if (p['planned'] != planned or p['forward'] > planned or p['returned'] > planned
            or p['enabled'] != int(p['phase'] in (1,2,3))
            or (p['phase'] == 4 and (p['forward'] != planned or p['returned'] != planned))
            or (action == 1 and not p['reject'] and p['token'] != token)):
            raise ValueError('Inconsistent MCU motor test counts or run token')
        state = dict(run=p['run'], token=p['token'], motor=('A','B','Z','E')[p['motor']],
            direction_first=p['direction'], phase=MOTOR_PHASES[p['phase']],
            reason=MOTOR_REASONS[p['reason']], reject=MOTOR_REJECTS[p['reject']],
            forward_pulses=p['forward'], return_pulses=p['returned'], pulses_each_way=planned,
            driver_enabled=bool(p['enabled']), pulse_hz=400, pulse_high_us=100,
            step_pin=('PA7','PA6','PB8','PB9')[p['motor']],
            dir_pin=('PB2','PB1','PE7','PE8')[p['motor']],
            establishes_position=False, maximum_duration_s=5)
        if getattr(self,'motor_test_abi',1) == 2:
            state.update(door_lock_enabled=bool(p['door_lock_enabled']),
                front_door_closed=bool(p['door_closed_mask']&2),
                lid_closed=bool(p['door_closed_mask']&1))
        self.motor_test_last=state
        return state

    def cmd_motor_test(self, gcmd):
        action=gcmd.get('ACTION','START').upper()
        name=gcmd.get('MOTOR','A').upper()
        aliases={'A':0,'X':0,'B':1,'Y':1,'Z':2,'E':3}
        if action not in ('START','STOP','STATUS') or name not in aliases:
            raise gcmd.error('Use MOTOR=A, B, Z or E and ACTION=START, STOP or STATUS')
        direction=gcmd.get_int('DIRECTION',1,minval=0,maxval=1)
        if action == 'START' and name == 'E' and gcmd.get_int('FILAMENT_CLEAR',0,minval=0,maxval=1) != 1:
            raise gcmd.error('E test: remove filament from the drive, then use FILAMENT_CLEAR=1')
        try:
            state=self._motor_test_query({'STATUS':0,'START':1,'STOP':2}[action],aliases[name],direction)
        except Exception as exc:
            # A lost/malformed response to START must not leave a blind test
            # running until its timeout. STOP is unconditional on the MCU.
            if action == 'START' and 'motor_test' in self.commands:
                try:self.commands['motor_test'].send([2,0,0,0])
                except Exception:logging.exception('Motor STOP failed; MCU heartbeat/5s cutoff owns stop')
            raise gcmd.error(str(exc))
        gcmd.respond_info('V9_MOTOR_TEST '+json.dumps(state,sort_keys=True))
        self.reactor.update_timer(self.motor_test_timer,self.reactor.monotonic()+.5
            if state['driver_enabled'] else self.reactor.NEVER)
        if state['reject']: raise gcmd.error(state['reject'])

    def cmd_motor_test_stop(self, gcmd):
        try:state=self._motor_test_query(2)
        except Exception as exc:raise gcmd.error(str(exc))
        self.reactor.update_timer(self.motor_test_timer,self.reactor.NEVER)
        gcmd.respond_info('V9_MOTOR_TEST '+json.dumps(state,sort_keys=True))

    def _motor_test_tick(self, eventtime):
        if not self.connected:return self.reactor.NEVER
        try:
            state=self._motor_test_query(0)
            self.gcode.respond_info('V9_MOTOR_TEST '+json.dumps(state,sort_keys=True))
        except Exception:
            logging.exception('Motor test query failed; requesting STOP')
            try:self.commands['motor_test'].send([2,0,0,0])
            except Exception:logging.exception('Motor STOP failed; MCU owns the deadline')
            return self.reactor.NEVER
        return eventtime+.5 if state['driver_enabled'] else self.reactor.NEVER

    def _fan_diag_state(self,gcmd,action):
        cmd=self.commands.get('fan_diag')
        if cmd is None:
            raise gcmd.error('Batch diagnostic requires R5. On R6 use INVENTURE_FAN_MANUAL.')
        state=cmd.send([action])
        if state['phase']>=len(FAN_DIAG_PHASES) or state['reject']>=len(FAN_DIAG_REJECTIONS):
            raise gcmd.error('Invalid MCU fan diagnostic state')
        if state['reject']:
            if state['reject']!=1:
                self._fan_diag_detail(gcmd,state['run'],rejected=True)
            raise gcmd.error('Fan diagnostic refused: '+FAN_DIAG_REJECTIONS[state['reject']])
        unknown=','.join(name for i,name in enumerate(self.ADC_NAMES)
                         if state['unverified']&(1<<i)) or 'none'
        gcmd.respond_info('FAN DIAG run=%d phase=%s windows=%d/20 at 100%%, %d/20 at 37.6%%; '
                          'temperature unverified=%s' % (state['run'],FAN_DIAG_PHASES[state['phase']],
                            state['full'],state['low'],unknown))
        return state

    def _fan_diag_detail(self,gcmd,run,rejected=False):
        query=self.commands.get('fan_diag_detail')
        if query is None:
            return
        detail=query.send([int(rejected)])
        if detail['run']!=run:
            raise gcmd.error('Fan diagnostic run changed; request status again')
        reason,channel,value=(detail[k] for k in ('reason','channel','value'))
        if reason:
            if reason==1:
                names=('armed','fault','sampler unavailable','heartbeat lost','PD11 cable open')
                message='link/safety '+', '.join(n for i,n in enumerate(names) if value&(1<<i))
            elif reason==2:
                pins=('PA0','PE5','PC9','PC8','PE10')
                message='heater/output '+', '.join(p for i,p in enumerate(pins) if value&(1<<i))
            elif reason in (3,6):
                why='stale ADC' if reason==3 else 'ADC became unverified'
                message=why+' '+', '.join(n for i,n in enumerate(self.ADC_NAMES) if value&(1<<i))
            elif reason in (4,5) and 0<=channel<len(self.ADC_NAMES):
                reading='%d C'%value if reason==5 else 'raw ADC %d'%value
                message='%s (%s): %s'%(self.ADC_NAMES[channel],self.ADC_PINS[channel],reading)
            elif reason==7:
                message='MCU shutdown fault=%d'%value
            else:
                message='reason=%d channel=%d value=%d'%(reason,channel,value)
            gcmd.respond_info('FAN DIAG safety: '+message)
        if not rejected and detail['full_gpio']!=255:
            levels=' '.join('FAN%d/%s=%d'%(i,self.FAN_PWM_PINS[i],bool(detail['full_gpio']&(1<<i)))
                            for i in (0,1,2,3,5))
            gcmd.respond_info('FAN DIAG 100%% MCU pin levels: '+levels)

    def _require_cold_idle_fan_test(self, gcmd):
        if gcmd.get('ACK', '') != 'COLD':
            raise gcmd.error('Confirm a cold printer and heaters OFF with ACK=COLD')
        if self.allow_actuators:
            raise gcmd.error('Fan diagnostic requires [inventure_mcu] allow_actuators: False')
        sd=self.printer.lookup_object('virtual_sdcard',None)
        stats=self.printer.lookup_object('print_stats',None)
        if ((sd is not None and sd.is_active()) or
                (stats is not None and stats.state in ('printing','paused'))):
            raise gcmd.error('Fan diagnostic requires an idle printer')
        heaters=self.printer.lookup_object('heaters',None)
        if heaters is not None and any(h.target_temp or h.last_pwm_value for h in heaters.heaters.values()):
            raise gcmd.error('Fan diagnostic requires all heater targets and outputs OFF')
    def cmd_fan_diag_start(self,gcmd):
        self._require_cold_idle_fan_test(gcmd)
        self._fan_diag_state(gcmd,1)

    def _fan_manual(self, gcmd, action, channel=255):
        query = self.commands.get('fan_manual')
        if query is None:
            raise gcmd.error('Manual fan diagnostics require the R6 MCU application')
        state = query.send([FAN_MANUAL_ACTIONS[action], channel])
        phase, reject, reason = (state[k] for k in ('phase', 'reject', 'reason'))
        index = state['channel']
        if (not 0 <= phase < len(FAN_MANUAL_PHASES) or not 0 <= reject < len(FAN_MANUAL_ERRORS)
                or not 0 <= reason < len(FAN_MANUAL_ERRORS) or index not in (*range(6), 255)):
            raise gcmd.error('Invalid MCU fan diagnostic response')
        def channels(mask):
            return [i for i in range(6) if mask & (1 << i)]
        def reading(key, missing=65535):
            return None if state[key] == missing else state[key]
        result = {
            'run': state['run'], 'channel': None if index == 255 else index,
            'phase': FAN_MANUAL_PHASES[phase],
            'reject': FAN_MANUAL_ERRORS[reject], 'reason': FAN_MANUAL_ERRORS[reason],
            'cooling_required': channels(state['required']), 'on': channels(state['applied']),
            'tach_on_edges': reading('on_edges'), 'tach_off_edges': reading('off_edges'),
            'pc4_mcu_adc': reading('pcb_adc'), 'pc4_mcu_c': reading('pcb_temp', -32768),
            'pc4_host_avg_adc': self.raw_adc[7] if self.connected and self.adc_received[7] is not None
                and 0 <= self.reactor.monotonic() - self.adc_received[7] < 1 else None,
            'passed': channels(state['passed']), 'failed': channels(state['failed']),
        }
        self.fan_manual_last = result
        gcmd.respond_info('V9_FAN_MANUAL ' + json.dumps(result, sort_keys=True))
        if reject:
            raise gcmd.error(FAN_MANUAL_ERRORS[reject])

    def cmd_fan_manual(self, gcmd):
        channel = gcmd.get_int('CHANNEL', minval=0, maxval=5)
        action = gcmd.get('ACTION', '').upper()
        if action not in ('ON', 'OFF', 'PASS', 'FAIL'):
            raise gcmd.error('Use ACTION=ON, OFF, PASS or FAIL')
        if action == 'ON':
            self._require_cold_idle_fan_test(gcmd)
        if action in ('PASS', 'FAIL') and gcmd.get('ACK', '') != 'OBSERVED':
            raise gcmd.error('Confirm the visual observation with ACK=OBSERVED')
        self._fan_manual(gcmd, action, channel)

    def cmd_fan_manual_status(self, gcmd):
        self._fan_manual(gcmd, 'STATUS')

    def cmd_fan_manual_cancel(self, gcmd):
        self._fan_manual(gcmd, 'CANCEL')

    def cmd_fan_diag_stop(self,gcmd):
        self._fan_diag_state(gcmd,2)

    def cmd_fan_diag_status(self,gcmd):
        state=self._fan_diag_state(gcmd,0)
        self._fan_diag_detail(gcmd,state['run'])
        if state['phase']<4 or not (state['full'] or state['low']):
            return
        row_commands=self.commands['fan_diag_row']
        gcmd.respond_info('Mean gated edges/1 s [min..max]; score compared with factory thresholds')
        for channel in range(4):
            phases=[]
            for phase,duty,key in ((0,255,'full'),(1,96,'low')):
                count=state[key]
                if not count:
                    phases.append('--');continue
                oid=4*phase+channel
                row=row_commands[oid].send([state['run'],oid])
                if (row['run']!=state['run'] or row['oid']!=oid or not 1<=count<=20
                        or len(row['data'])!=2*count):
                    raise gcmd.error('Invalid MCU fan diagnostic row; request status again')
                edges=struct.unpack('<%dH'%count,bytes(row['data']))
                value='%.1f [%d..%d]'%(sum(edges)/count,min(edges),max(edges))
                if count==20:
                    scores=[normalized_tach(sample,duty) for sample in edges]
                    result=fan_test_result(scores,channel,minimum=bool(phase))
                    value+=' score=%d %s'%(result['value'],'PASS' if result['pass'] else 'OUTSIDE')
                phases.append(value)
            gcmd.respond_info('FAN%d %s/%s | 100%% %s | 37.6%% %s' % (channel,
                self.FAN_NAMES[channel],self.FAN_TACH_PINS[channel],*phases))

    def _p4_baud_received(self,params):
        received=self.reactor.monotonic()
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(
            lambda eventtime:self._deliver_p4_baud(sample,received))

    def _deliver_p4_baud(self,sample,received):
        # No attempt to change baud mid-print and absolutely no Flash writes.
        # With an actual mismatch, the UART normally cannot even identify.
        configured=self.mcu._conn_helper.get_serialport()[1]
        same=(configured==sample.get('baud'))
        forced=int(self.mcu.get_constants().get('INVENTURE_P4_FORCED_BAUD',0))
        self.p4_boot=dict(sample,host_baud=configured,match=same,
                          received_at=received,
                          source='CONFIG_SERIAL_BAUD_forced' if forced else 'InventureBoot_Flash_read_only',
                          forced_baud=forced)
        if not same:
            logging.error('Inventure P4 baud mismatch: boot=%s host=%s',
                          sample.get('baud'),configured)

    def _uart_buffers(self,params):
        received=self.reactor.monotonic()
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        self.reactor.register_async_callback(lambda now:self._deliver_uart_buffers(sample,received))

    def _deliver_uart_buffers(self,sample,received):
        previous=self.uart_buffer_stats.get('dropped',0)
        self.uart_buffer_stats=dict(sample,received_at=received)
        if sample.get('dropped',0)>previous:
            logging.error('Inventure UART TX dropped frames: %d (was %d)',
                          sample['dropped'],previous)

    def _fan_report(self,params):
        received=self.reactor.monotonic()
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        # Same serial background thread boundary as ADC, receipt time retained.
        self.reactor.register_async_callback(lambda now:self._deliver_fan(sample,received))

    def _deliver_fan(self,sample,received):
        i=sample.get('channel',-1)
        if type(i) is not int or i not in range(6):self.fan_rejected+=1;return
        def invalid(reason):
            self.fan_rejected+=1
            self.printer.send_event('inventure:fan_invalid',i,str(reason),received)
        try:validate_report(sample)
        except ValueError as exc:invalid(exc);return
        old=self.fans[i]
        if old:
            delta=(sample['clock']-old['clock'])&0xffffffff
            if not 0 < delta < 0x80000000:
                self.fan_rejected+=1;return
        if not old or sample['sequence']!=old['sequence']:
            if old:
                delta=(sample['sequence']-old['sequence'])&0xffffffff
                if not 0<delta<0x80000000:self.fan_rejected+=1;return
                window_delta=(sample['window_clock']-old['window_clock'])&0xffffffff
                if not 0<window_delta<0x80000000:
                    invalid('Fan sequence changed without a new window');return
            self.fan_window_received[i]=received
            self.fan_window_valid[i]=bool(sample['sequence'] or sample['window_clock'] or old)
        elif any(sample[k]!=old[k] for k in ('window_clock','window_duty','edges','normalized','mixed')):
            invalid('Fan window payload changed without a new sequence');return
        self.fans[i]=sample;self.fan_received[i]=received
        self.printer.send_event('inventure:fan_report',i,sample,received)

    def _status(self, params):
        self.status = {k:v for k,v in params.items() if not k.startswith('#')}
        self.status_received = self.reactor.monotonic()
    def _arm_result(self, params):
        self.status.update(armed=params['armed'], fault=params['fault'])
        self.gcode.respond_info('V9 ARM: armed=%d fault=%d' % (params['armed'], params['fault']))
    def _uart_rx(self, params):
        if not self.adc_accepting:return
        i=params['channel']
        if i not in (0,1):return
        data=bytes(params['data']);received=self.reactor.monotonic();generation=self.adc_generation
        def deliver(now):
            if not self.adc_accepting or generation!=self.adc_generation:return
            self.rfid_received[i]=received
            room=self.rfid_rx[i].maxlen-len(self.rfid_rx[i])
            if len(data)>room:
                self.rfid_overruns[i]+=len(data)-room
                logging.warning('Inventure RFID host RX overflow, channel %d',i)
            self.rfid_rx[i].extend(data)
            self.printer.send_event('inventure:rfid_rx',i,data)
        self.reactor.register_async_callback(deliver)
    def get_status(self, eventtime):
        return dict(self.status, temperatures=list(self.temperatures),
                    host_revision=HOST_REVISION, host_module=str(Path(__file__).resolve()),
                    heat_test_abi=self.heat_test_abi,
                    motor_test_abi=getattr(self,'motor_test_abi',0),
                    door_policy_abi=getattr(self,'door_policy_abi',0),
                    xy_seek_abi=getattr(self,'xy_seek_abi',0),
                    xy_repeat_abi=getattr(self,'xy_repeat_abi',0),
                    door_policy=dict(getattr(self,'door_policy_last',{})),
                    raw_adc=list(self.raw_adc), rfid_host_overruns=list(self.rfid_overruns),
                    adc_dispatch='reactor',fans=[dict(f) for f in self.fans],
                    fan_ages=[None if t is None else max(0.,eventtime-t) for t in self.fan_received],
                    fan_reports_rejected=self.fan_rejected,
                    adc_ages=[None if t is None else max(0.,eventtime-t) for t in self.adc_received],
                    diagnostic_adc=self.diag_adc, allow_actuators=self.allow_actuators,
                    inputs=dict(self.inputs), uart_status=list(self.uart_status),
                    uart_buffer_stats=dict(self.uart_buffer_stats),
                    p4_boot=dict(self.p4_boot), p4_journal_abi=self.p4_abi,
                    mcu_release=self.mcu_release, local_oled=dict(self.local_oled_state),
                    local_cooling=dict(self.local_cooling_state),
                    local_oled_age=(None if self.local_oled_received is None else
                                    max(0.,eventtime-self.local_oled_received)),
                    fan_batch=self.fan_batch, connected=self.connected,
                    status_age=99. if self.status_received is None else eventtime-self.status_received,
                    inputs_age=99. if self.inputs_received is None else eventtime-self.inputs_received)

    def panel_temperature(self,index,now):
        raw, temp, received = (self.raw_adc[index],self.temperatures[index],
                               self.adc_received[index])
        if not self.connected or received is None or not 0.<=now-received<1.:
            return None,'stale'
        if raw is None or temp is None or not math.isfinite(temp):
            return None,'invalid'
        # On the active 5 mV/°C nozzle inputs a few ADC counts can be a
        # near-ground signal. Label it in diagnostics, never synthesize a
        # room temperature or change the thermal protection path.
        if index<2 and raw<=8:
            return None,'near_ground'
        if not 0<raw<self.ADC_MAX or not -30.<temp<self.ADC_CEILINGS[index]:
            return None,'out_of_range'
        return temp,'valid'

    def panel_fan_duty(self,index,now):
        received=self.fan_received[index]
        if self.connected and received is not None and 0.<=now-received<1.5:
            return self.fans[index]['duty']
        return None

    def cmd_panel_io(self,gcmd):
        now=self.reactor.monotonic()
        thermal={}
        fans={}
        for i,name in enumerate(self.ADC_NAMES):
            value, state=self.panel_temperature(i,now)
            received=self.adc_received[i]
            raw=self.raw_adc[i]
            converted=self.temperatures[i]
            thermal[name]={'pin':self.ADC_PINS[i],'adc':raw,
                'mv':None if raw is None else round(raw*self.ADC_REFERENCE_MV/self.ADC_MAX,2),
                'stock_c':None if converted is None else round(converted,2),
                'c':None if value is None else round(value,1),'state':state,
                'age_s':None if received is None else round(max(0.,now-received),3)}
        for i,name in enumerate(self.FAN_NAMES):
            duty=self.panel_fan_duty(i,now)
            tach=self.FAN_TACH_PINS[i]
            sample=self.fans[i]
            received=self.fan_window_received[i]
            valid=(tach is not None and duty is not None and self.fan_window_valid[i]
                   and received is not None and 0.<=now-received<2.
                   and not (sample['mixed'] & (1 << i)))
            fans[name]={'pwm_pin':self.FAN_PWM_PINS[i],'tach_pin':tach,
                'duty_pct':None if duty is None else round(100*duty/255),
                'tach_edges_window':sample['edges'] if valid else None}
        fresh=(self.connected and self.inputs_received is not None
               and 0.<=now-self.inputs_received<.75)
        board_fresh=(self.connected and self.status_received is not None
                     and 0.<=now-self.status_received<.75)
        io=self.printer.lookup_object('inventure_io',None)
        gcmd.respond_info('V9_PANEL_IO '+json.dumps({
            'thermal':thermal,'fans':fans,'gpio_raw':self.inputs.get('raw') if fresh else None,
            'extruder_cable_pd11_raw':self.status.get('cable') if board_fresh else None,
            'filament_pulses':{'t0_pb12':self.status.get('p4') if board_fresh else None,
                               't1_pd14':self.status.get('p5') if board_fresh else None},
            'armed':self.status.get('armed') if board_fresh else None,
            'configured_outputs':io.get_status(now)['outputs'] if io is not None else {}},
            sort_keys=True))
    INPUT_NAMES = ('x_endstop','y_endstop','z_bottom','nozzle_contact',
                   'tray','bin','front_door','lid','t0_a','t0_b','t1_a','t1_b',
                   'up','down','ok','power')
    def _inputs(self, params):
        self.inputs_received = self.reactor.monotonic()
        received_at=params.get('#receive_time',self.inputs_received)
        receipt=getattr(self,'_oled_input_receipt',None)
        generation=self.adc_generation
        self.inputs = {'raw_mask': params['raw'], 'buttons': params['buttons'],
                       'sequence': params['sequence'],
                       'raw': {name: (params['raw'] >> i) & 1
                               for i, name in enumerate(self.INPUT_NAMES)}}
        buttons, sequence = params['buttons'], params['sequence']
        def deliver(eventtime):
            if generation!=self.adc_generation:return
            menu=getattr(self,'zortrax',None)
            if menu is not None:
                self.printer.send_event('inventure:menu_buttons',eventtime,buttons,sequence,
                                        received_at,receipt)
                legacy=menu.legacy_buttons(buttons)
            else:legacy=buttons
            self.printer.send_event('inventure:buttons',eventtime,legacy,sequence)
        self.reactor.register_async_callback(deliver)
    def _uart_status(self, params):
        channel=params['channel'];generation=self.adc_generation
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        def deliver(now):
            if not self.adc_accepting or generation!=self.adc_generation or channel not in (0,1):return
            self.uart_status[channel]=sample
            self.printer.send_event('inventure:rfid_uart_status',channel,sample['errors'])
        self.reactor.register_async_callback(deliver)
    def cmd_inputs(self, gcmd):
        try:
            self._fresh_inputs()
        except Exception as exc:
            raise gcmd.error(str(exc))
        gcmd.respond_info(json.dumps(dict(self.inputs,fresh=True,
            age_s=max(0.,self.reactor.monotonic()-self.inputs_received)),sort_keys=True))
    def cmd_uart_status(self, gcmd):
        if self.connected:
            for i in range(2): self.commands['uart_status'].send([i])
        gcmd.respond_info(json.dumps(self.uart_status, sort_keys=True))
    def cmd_rgb(self, gcmd):
        if not self.connected: raise gcmd.error('V9 MCU not ready')
        self.commands['rgb'].send([gcmd.get_int('MASK',minval=0,maxval=7)])

    def cmd_status(self, gcmd):
        gcmd.respond_info(json.dumps(self.get_status(self.reactor.monotonic()), sort_keys=True))
    def cmd_version(self, gcmd):
        revision=self.p4_boot.get('revision')
        bootloader={1:'InventureBoot 1.4 P4-PERSIST-r1',2:'InventureBoot 1.4 P4-PERSIST-r2',
                    3:'InventureBoot 1.6-RC2',15:'InventureBoot 1.5-HW2',
                    17:'InventureBoot 1.7'}.get(revision,'unknown')
        gcmd.respond_info('INVENTURE_VERSION '+json.dumps({
            'host_revision':HOST_REVISION, 'host_module':str(Path(__file__).resolve()),
            'heat_test_abi':getattr(self,'heat_test_abi',0),
            'motor_test_abi':getattr(self,'motor_test_abi',0),
            'door_policy_abi':getattr(self,'door_policy_abi',0),
            'door_policy_global':getattr(self,'door_policy_global',False),
            'door_policy_default_enabled':bool(getattr(self,'door_policy_default_enabled',True)),
            'xy_seek_abi':getattr(self,'xy_seek_abi',0),
            'xy_repeat_abi':getattr(self,'xy_repeat_abi',0),
            'control_abi':getattr(self,'control_abi',0),
            'motion_abi':1 if getattr(self,'control_abi',0)==1 else 0,
            'output_abi':1 if getattr(self,'control_abi',0)==1 else 0,
            'load_stream_abi':getattr(self,'load_stream_abi',0),
            'factory_abi':getattr(self,'factory_abi',0), 'procedure_api':1, 'zortrax_menu_api':1,
            'factory_feed_clock':getattr(self,'factory_feed_clock',0),
            'factory_motion_abi':getattr(self,'factory_motion_abi',0),
            'factory_motion_max_hz':getattr(self,'factory_motion_max_hz',0),
            'factory_motion_tools':getattr(self,'factory_motion_tools',0),
            'z_home_abi':getattr(self,'z_home_abi',0),
            'z_edge_abi':getattr(self,'z_edge_abi',0),
            'native_z_guard_abi':getattr(self,'native_z_guard_abi',0),
            'native_z_refine_abi':getattr(self,'native_z_refine_abi',0),
            'axis_motion_abi':getattr(self,'axis_motion_abi',0),
            **{attr:getattr(self,attr,0) for attr,_,_ in AXIS_MOTION_LIMITS},
            'control_state':getattr(self,'control_last',{}),
            'mcu_release':self.mcu_release, 'bootloader':bootloader,
            'p4_baud':self.p4_boot.get('baud'),
            'host_baud':self.p4_boot.get('host_baud'),
            'baud_match':self.p4_boot.get('match'),
            'p4_forced_baud':self.p4_boot.get('forced_baud'),
            'p4_selection_source':self.p4_boot.get('source'),
            'boot_identification':('P4-PERSIST marker recognized' if revision in (1,2,3,15,17)
                                   else 'unrecognized'),
            'local_oled':dict(self.local_oled_state),
            'connected':self.connected,
        },sort_keys=True))
    def setup_pin(self,pin_type,pin_params):
        name=pin_params['pin']
        mapping={
            'HEATER_T0':('PA0',True,5632,True),
            'HEATER_T1':('PE5',True,12800,True),
            'HEATER_PC9':('PC9',True,16776960,True),
            'HEATER_PC8':('PC8',True,16776960,True),
            'F0':('PB15',False,12000000,False),'F1':('PB14',False,12000000,False),
            'F2':('PC7',False,12000000,False),'F3':('PC6',False,12000000,False),
            'F4':('PA1',False,5632,True),'F5':('PD13',False,2096640,True)}
        if pin_type!='pwm' or name not in mapping or pin_params['invert']:
            raise self.printer.config_error('Use a noninverted inventure_r5 PWM name')
        if name in self.native_outputs:raise self.printer.config_error('Duplicate Inventure R5 output '+name)
        pin,invert,ticks,hardware=mapping[name]
        desc=('!' if invert else '')+self.mcu_name+':'+pin
        physical=self.printer.lookup_object('pins').setup_pin('pwm',desc)
        result=R5FixedPWM(self,name,physical,ticks,hardware)
        self.native_outputs[name]=result
        return result

    def _native_preflight(self):
        printer=getattr(self,'printer',None)
        if printer is None:return
        for name,obj in printer.lookup_objects():
            out=getattr(obj,'mcu_pwm',None)
            physical=getattr(out,'physical',out)
            pin=getattr(physical,'_pin',None)
            if pin in HEAT_TEST_PINS and physical.get_mcu() is self.mcu:
                if not getattr(physical,'_invert',False) or not getattr(physical,'_hardware_pwm',False):
                    raise ValueError('Native heater '+name+' requires inventure_r5 fixed active-LOW hardware PWM')

    @staticmethod
    def _control_validate(p, limits):
        if any(type(p.get(k)) is not int or not low<=p[k]<=high
               for k,(low,high) in limits.items()):
            raise ValueError('Invalid R5 MCU response')
        return p

    def _control_send(self, key, args, mutate=False, owner=None):
        command=self.commands.get(key)
        if command is None:
            raise ValueError('Install the RC8-CONTROL-R5 MCU application first')
        if mutate and not self.connected:
            raise ValueError('MCU is disconnected')
        return self._thermal_query_send(command,args,start=mutate,owner=owner)

    def _control_next_token(self):
        self.control_token=(getattr(self,'control_token',0)+1)&0xffffffff or 1
        return self.control_token

    def _control_query(self, action=0, mode=0):
        was_armed=bool(getattr(self,'control_last',{}).get('armed'))
        stopping=was_armed and (action==2 or action==1 and mode==0)
        if stopping:self.printer.send_event('inventure:native_motion_lost')
        p=self._control_send('control',[action,mode],mutate=action==1 and mode!=0)
        self._control_validate(p,{'mode':(0,2),'armed':(0,1),'active':(0,1),
            'latched':(0,1),'fault':(0,255),'guard_mask':(0,255),'reject':(0,5)})
        if bool(p['armed']) != (p['mode']==1 and not p['latched'] and not p['fault']):
            raise ValueError('Invalid R5 MCU ARM response')
        self.control_last=dict(p,mode=CONTROL_MODES[p['mode']],reject=CONTROL_REJECTS[p['reject']])
        if was_armed and not p['armed'] and not stopping:
            self.printer.send_event('inventure:native_motion_lost')
        return self.control_last

    def _control_service(self):
        if getattr(self,'load_test_context',None) is not None:
            raise ValueError('Load test is active; use INVENTURE_LOAD_TEST or INVENTURE_STOP')
        if getattr(self,'feeder_run_context',None) is not None:
            raise ValueError('Feeder run is active; use INVENTURE_STOP first')
        state=self._control_query()
        if state['mode']!='SERVICE' or state['latched'] or state['fault']:
            raise ValueError('Select INVENTURE_CONTROL MODE=SERVICE first')

    def _control_abort(self):
        self._load_test_cancel('control_abort',phase='failed')
        self._feeder_run_cancel('control_abort')
        self._control_stop_outputs()

    def _control_stop_outputs(self):
        try: self._control_query(2)
        except Exception: logging.exception('R5 STOP response unavailable; MCU heartbeat remains authoritative')

    def _control_schedule(self):
        self.reactor.update_timer(self.control_timer,self.reactor.monotonic()+.5)

    def cmd_control(self,gcmd):
        mode=gcmd.get('MODE',None)
        if mode is not None:
            mode=str(mode).upper()
            if mode not in CONTROL_MODES: raise gcmd.error('MODE must be OFF, SERVICE or NATIVE')
        if mode is not None and getattr(self,'load_test_context',None) is not None:
            if mode!='OFF':raise gcmd.error('Load test is active; use INVENTURE_STOP first')
            self._load_test_cancel('mode_off')
        if mode is not None and getattr(self,'feeder_run_context',None) is not None:
            if mode!='OFF':raise gcmd.error('Feeder run is active; use INVENTURE_STOP first')
            self._feeder_run_cancel('mode_off')
        try:
            if mode=='NATIVE':self._native_preflight()
            state=self._control_query(0 if mode is None else 1,0 if mode is None else CONTROL_MODES.index(mode))
        except Exception as exc:
            if mode is not None: self._control_abort()
            raise gcmd.error(str(exc))
        if state['reject']: raise gcmd.error(state['reject'])
        gcmd.respond_info('V9_CONTROL '+json.dumps(state,sort_keys=True))
        if state['mode']!='OFF': self._control_schedule()

    def cmd_control_status(self,gcmd):
        try: state=self._control_query()
        except Exception as exc: raise gcmd.error(str(exc))
        gcmd.respond_info('V9_CONTROL '+json.dumps(dict(state,
            motion=getattr(self,'motion_last',{}),outputs=getattr(self,'output_last',{}),
            guards=getattr(self,'guard_last',{})),sort_keys=True))

    def cmd_control_stop(self,gcmd):
        self._load_test_cancel('operator_stop')
        self._feeder_run_cancel('operator_stop')
        self.feeder_test_pending_until = None
        try: state=self._control_query(2)
        except Exception as exc: raise gcmd.error(str(exc))
        self.motion_last={};self.output_last={}
        self.reactor.update_timer(self.control_timer,self.reactor.NEVER)
        gcmd.respond_info('V9_CONTROL '+json.dumps(state,sort_keys=True))

    def _motion_query(self, action=0, counts=(0,0,0,0), rate=400, pulse=10,
                      max_ms=30000, stop_mask=0, stop_value=0, hold_ms=0, owner=None,
                      timer_tool=None):
        # State plus four driver replies must describe one run. In particular,
        # the next E window cannot start halfway through another reader's poll.
        mutex=getattr(self,'motion_mutex',None)
        with mutex if mutex is not None else nullcontext():
            return self._motion_query_locked(action,counts,rate,pulse,max_ms,stop_mask,stop_value,
                                             hold_ms,owner,timer_tool)

    def _motion_query_locked(self, action=0, counts=(0,0,0,0), rate=400, pulse=10,
                      max_ms=30000, stop_mask=0, stop_value=0, hold_ms=0, owner=None,
                      timer_tool=None):
        directions=sum(1<<i for i,n in enumerate(counts) if n>0)
        token=self._control_next_token() if action==1 else 0
        args=[action,directions,*[abs(n) for n in counts],
              rate,pulse,max_ms,stop_mask,stop_value,hold_ms,token]
        key='motion'
        if timer_tool is not None:
            key='axis_motion'
            args.append(timer_tool)
        p=self._control_send(key,args,mutate=action==1,owner=owner)
        self._control_validate(p,{'run':(0,0xffffffff),'token':(0,0xffffffff),'phase':(0,5),
            'reason':(0,len(MOTION_REASONS)-1),'reject':(0,5),'active':(0,1),'events':(0,2000000),
            'elapsed_ms':(0,600100),'hit':(0,65535)})
        if p['reject']:
            return dict(p,reject=CONTROL_REJECTS[p['reject']])
        if bool(p['active'])!=(p['phase'] in (1,2)) or (action==1 and p['token']!=token):
            raise ValueError('Invalid R5 motion ownership response')
        drivers={}
        for i,name in enumerate(('A','B','Z','E')):
            row=self._control_send('motion_driver',[i])
            self._control_validate(row,{'run':(0,0xffffffff),'channel':(0,3),
                'planned':(0,2000000),'sent':(0,2000000),'direction':(0,1)})
            if row['run']!=p['run'] or row['channel']!=i or row['sent']>row['planned']:
                raise ValueError('R5 motion driver response belongs to a different run')
            if action==1 and (row['planned']!=abs(counts[i]) or row['direction']!=int(counts[i]>0)):
                raise ValueError('R5 motion driver parameters differ from the request')
            drivers[name]={k:row[k] for k in ('planned','sent','direction')}
        if action==1:
            self.motion_request=dict(run=p['run'],token=p['token'],stop_mask=stop_mask,
                stop_value=stop_value,timer=timer_tool is not None)
        request=getattr(self,'motion_request',{})
        if (request.get('run'),request.get('token'))!=(p['run'],p['token']):request={}
        snapshot=self._motion_input_snapshot()
        self.motion_last=dict(p,phase=MOTION_PHASES[p['phase']],reason=MOTION_REASONS[p['reason']],
            reject=None,drivers=drivers,elapsed_s=round(p['elapsed_ms']/1000.,3),
            establishes_position=False,stop_mask=request.get('stop_mask'),stop_value=request.get('stop_value'),
            axis_timer=request.get('timer'),
            hit_inputs=[name for i,name in enumerate(self.INPUT_NAMES) if p['hit']&(1<<i)],
            hit_scope='selected_stop_inputs_latched_by_mcu',
            z_direction=('table_up' if drivers['Z']['direction'] else 'table_down') if drivers['Z']['planned'] else None,
            **snapshot)
        return self.motion_last

    def _motion_timing(self):
        if 'factory_motion_timing' not in self.commands:
            return None
        limits={'peak_callback_ticks':(0,0xffffffff),'peak_late_ticks':(0,0xffffffff)}
        ticks=self._control_validate(self._control_send('factory_motion_timing',[]),limits)
        return {key:ticks[key] for key in limits}

    def _motion_input_snapshot(self):
        now=self.reactor.monotonic();received=getattr(self,'inputs_received',None)
        age=None if received is None else now-received
        fresh=self.connected and age is not None and 0<=age<.75
        raw=getattr(self,'inputs',{}).get('raw',{})
        names=('x_endstop','y_endstop','z_bottom','nozzle_contact')
        observed={name:raw.get(name) if fresh and type(raw.get(name)) is int and raw[name] in (0,1) else None for name in names}
        active={name:(None if value is None else bool(value if name!='nozzle_contact' else not value))
                for name,value in observed.items()}
        return dict(input_raw=observed,endstop_active=active,
            inputs_age_s=None if age is None else round(age,3),inputs_scope='latest_periodic_sample')

    def _axis_timer_rate_limit(self,gcmd,counts):
        if getattr(self,'axis_motion_abi',0)!=1 or 'axis_motion' not in self.commands:
            raise gcmd.error('TIMER=1 requires '+AXIS_MOTION_RELEASE)
        a,b,z,e=counts
        if (z and (a or b or e)) or (e and (a or b)):
            raise gcmd.error('TIMER=1 requires a standalone Z or E move')
        if z:
            return self.axis_motion_z_max_hz
        if e:
            return self.axis_motion_e_max_hz
        return self.axis_motion_max_hz

    def _motion_execute(self,gcmd,action,counts=(0,0,0,0),rate=400,stop_mask=0,stop_value=0,
                        axis_timer=False):
        pulse=gcmd.get_int('PULSE_US',2 if axis_timer else 10,minval=2,maxval=100)
        max_ms=gcmd.get_int('MAX_MS',30000,minval=1,maxval=600000)
        hold=gcmd.get_int('HOLD_MS',0,minval=0,maxval=60000)
        stop_mask=gcmd.get_int('STOP_MASK',stop_mask,minval=0,maxval=65535)
        stop_value=gcmd.get_int('STOP_VALUE',stop_value,minval=0,maxval=65535)
        timer_tool=None
        if axis_timer:
            if pulse!=2 or hold:
                raise gcmd.error('TIMER=1 requires PULSE_US=2 and HOLD_MS=0')
            tool=str(gcmd.get('TOOL','' if counts[3] else 'T0')).upper()
            if tool not in ('T0','T1'):
                raise gcmd.error('Select TOOL=T0 or TOOL=T1 for the timer extrusion move')
            timer_tool=int(tool[1])
            a,b,_,_=counts
            xy_minimum=int(a+b<0) | (int(a-b<0)<<1)
            if xy_minimum&stop_mask&~stop_value:
                raise gcmd.error('X/Y minimum limit polarity conflicts with STOP_VALUE')
            stop_mask|=xy_minimum
            stop_value|=xy_minimum
        if action==1 and counts[2]:
            # Physical test: DIR=1 raises the table; DIR=0 lowers it.
            # Require the limit ahead, while allowing release of the one behind.
            required=8 if counts[2]>0 else 4
            level=0 if counts[2]>0 else 4
            if stop_mask&required and (stop_value^level)&required:
                raise gcmd.error('Z limit polarity conflicts: PE12 bottom active HIGH; PC14 contact active LOW')
            stop_mask|=required;stop_value=(stop_value&~required)|level
        if axis_timer and stop_value&~stop_mask:
            raise gcmd.error('STOP_VALUE contains bits outside STOP_MASK')
        if action==1 and (not any(counts) or max(map(abs,counts))>2000000 or pulse+2>1000000//rate):
            raise gcmd.error('Invalid counts or pulse width for the requested RATE')
        attempted=False
        try:
            if action==1:self._control_service()
            attempted=action==1
            state=self._motion_query(action,counts,rate,pulse,max_ms,stop_mask,stop_value,hold,
                                     timer_tool=timer_tool)
            if action==0:
                ticks=self._motion_timing()
                if ticks is not None:state=dict(state,last_fast_segment=ticks)
        except Exception as exc:
            if attempted:self._control_abort()
            raise gcmd.error(str(exc))
        if state['reject']:raise gcmd.error(state['reject'])
        gcmd.respond_info('V9_MOTION '+json.dumps(state,sort_keys=True))
        if state['active']:self._control_schedule()

    @staticmethod
    def _control_action(gcmd):
        action=str(gcmd.get('ACTION','START')).upper()
        if action not in ('START','STATUS','STOP'):raise gcmd.error('ACTION must be START, STATUS or STOP')
        return {'STATUS':0,'START':1,'STOP':2}[action]

    def cmd_motion(self,gcmd):
        action=self._control_action(gcmd)
        counts=tuple(gcmd.get_int(n,0,minval=-2000000,maxval=2000000) for n in ('A','B','Z','E'))
        axis_timer=action==1 and gcmd.get_int('TIMER',0,minval=0,maxval=1)==1
        cap=self._axis_timer_rate_limit(gcmd,counts) if axis_timer else 20000
        rate=gcmd.get_int('RATE',400,minval=1,maxval=cap)
        self._motion_execute(gcmd,action,counts,rate,axis_timer=axis_timer)

    def cmd_move(self,gcmd):
        action=self._control_action(gcmd)
        if action!=1:return self._motion_execute(gcmd,action)
        axis=str(gcmd.get('AXIS','X')).upper()
        if axis not in ('X','Y','Z','E','A','B'):raise gcmd.error('AXIS must be X, Y, Z, E, A or B')
        scale={'X':640,'Y':640,'A':640,'B':640,'Z':802,'E':960}[axis]
        try:
            distance=float(gcmd.get('DISTANCE',1.))
            speed=float(gcmd.get('SPEED',1.))
            if not math.isfinite(distance) or not math.isfinite(speed) or speed<=0:raise ValueError()
            distance_steps=round(distance*scale)
            default_rate=max(1,round(speed*scale))
        except (TypeError,ValueError,OverflowError):raise gcmd.error('DISTANCE and positive SPEED must be finite numbers')
        steps=gcmd.get_int('STEPS',distance_steps,minval=-2000000,maxval=2000000)
        if gcmd.get('DIR',None) is not None:
            if axis in ('X','Y'):raise gcmd.error('DIR override is for A, B, Z or E; use signed DISTANCE for X/Y')
            steps=abs(steps)*(1 if gcmd.get_int('DIR',minval=0,maxval=1) else -1)
        counts={'X':(steps,steps,0,0),'Y':(steps,-steps,0,0),'Z':(0,0,steps,0),
                'E':(0,0,0,steps),'A':(steps,0,0,0),'B':(0,steps,0,0)}[axis]
        axis_timer=gcmd.get_int('TIMER',0,minval=0,maxval=1)==1
        cap=self._axis_timer_rate_limit(gcmd,counts) if axis_timer else 20000
        rate=gcmd.get_int('RATE',default_rate if axis_timer else min(cap,default_rate),minval=1,maxval=cap)
        if axis_timer and rate>cap:
            raise gcmd.error('SPEED exceeds the timer limit of %d steps/s'%cap)
        # Negative X/Y heads toward its minimum. Directional Z limits are
        # merged in _motion_execute for both MOVE and raw vector MOTION.
        mask=(1 if axis=='X' else 2) if axis in ('X','Y') and steps<0 else 0
        self._motion_execute(gcmd,action,counts,rate,mask,mask,axis_timer)

    def _output_query(self,channel,action=0,duty=0,duration=0,limit=0,owner=None):
        token=self._control_next_token() if action==1 else 0
        p=self._control_send('output',[action,channel,duty,duration,limit,token],mutate=action==1,owner=owner)
        self._control_validate(p,{'channel':(0,9),'duty':(0,256),'remaining_ms':(0,600000),
            'token':(0,0xffffffff),'reject':(0,5),'reason':(0,6)})
        if p['channel']!=channel:raise ValueError('R5 output channel mismatch')
        state=dict(p,channel=OUTPUT_NAMES[channel],power_pct=round(p['duty']*100/256.,2),
            reason=OUTPUT_REASONS[p['reason']],reject=CONTROL_REJECTS[p['reject']])
        if not state['reject'] and action==1 and p['token']!=token:
            raise ValueError('R5 output token mismatch')
        self.output_last[OUTPUT_NAMES[channel]]=state
        return state

    def cmd_output(self,gcmd):
        name=str(gcmd.get('CHANNEL','')).upper()
        if name not in OUTPUT_NAMES:raise gcmd.error('CHANNEL must be F0..F5, T0, T1, PC9 or PC8')
        channel=OUTPUT_NAMES.index(name);act=str(gcmd.get('ACTION','STATUS')).upper()
        if act not in ('SET','ON','OFF','STOP','STATUS'):raise gcmd.error('ACTION must be SET, OFF or STATUS')
        action={'SET':1,'ON':1,'OFF':2,'STOP':2,'STATUS':0}[act]
        try:
            power=float(gcmd.get('POWER',0.))
            duration_s=float(gcmd.get('DURATION',20. if channel<8 else 180.))
            if not math.isfinite(power) or not 0<=power<=100 or not math.isfinite(duration_s) or not .001<=duration_s<=600:
                raise ValueError()
        except (TypeError,ValueError):raise gcmd.error('POWER must be 0..100 and DURATION .001..600 seconds')
        limit=gcmd.get_int('LIMIT',45 if channel<8 else 50,minval=0,maxval=310)
        if action==1 and power==0:action=2
        if action==1 and channel>=6 and limit<40:raise gcmd.error('Heaters require LIMIT>=40')
        load_context=getattr(self,'load_test_context',None)
        if action==2 and load_context is not None and channel==load_context['tool']+6:
            self._load_test_cancel('heater_off')
            self._control_abort()
        attempted=False
        try:
            if action==1:self._control_service()
            attempted=action==1
            state=self._output_query(channel,action,round(power*256/100.),round(duration_s*1000),limit)
        except Exception as exc:
            if attempted:self._control_abort()
            raise gcmd.error(str(exc))
        if state['reject']:raise gcmd.error(state['reject'])
        gcmd.respond_info('V9_OUTPUT '+json.dumps(state,sort_keys=True))
        if state['duty']:self._control_schedule()

    def cmd_guard(self,gcmd):
        name=str(gcmd.get('SENSOR','')).upper()
        aliases={s.upper():i for i,s in enumerate(self.ADC_NAMES)}
        aliases.update({pin:i for i,pin in enumerate(self.ADC_PINS)})
        if name not in aliases:raise gcmd.error('SENSOR must be an ADC pin or sensor name')
        if gcmd.get('ENABLE',None) is None:
            raise gcmd.error('Use explicit ENABLE=0/1 MIN=-20 MAX=...; INVENTURE_CONTROL_STATUS shows cached guards')
        i=aliases[name];enabled=gcmd.get_int('ENABLE',minval=0,maxval=1)
        lo=gcmd.get_int('MIN',-20,minval=-30,maxval=309)
        hi=gcmd.get_int('MAX',(310,310,150,150,150,100,100,190)[i],minval=-29,maxval=310)
        if hi<=lo:raise gcmd.error('MAX must exceed MIN')
        try:
            p=self._control_send('guard',[i,enabled,lo,hi],mutate=True)
            self._control_validate(p,{'sensor':(0,7),'enabled':(0,1),'min_c':(-30,309),
                'max_c':(-29,310),'reject':(0,2)})
            if p['sensor']!=i:raise ValueError('R5 guard sensor mismatch')
        except Exception as exc:raise gcmd.error(str(exc))
        if p['reject']:raise gcmd.error(('invalid_guard_limits','stop_active_outputs_before_changing_guards')[p['reject']-1])
        self.guard_last[self.ADC_PINS[i]]=p
        gcmd.respond_info('V9_GUARD '+json.dumps(dict(p,pin=self.ADC_PINS[i],
            mandatory_for_heating=i<5),sort_keys=True))

    def _sensor_readback(self,key,args,response,callback):
        if not self.connected:
            raise ValueError('MCU is disconnected; no fresh sensor readback')
        command=self.commands.get(key)
        if command is None:
            raise ValueError('MCU sensor readback is not configured')
        try:
            return self._thermal_query_send(command,args)
        finally:
            # The pinned SerialRetryCommand temporarily replaces the single
            # serial response callback and unregisters it on completion/error.
            # Restore the original periodic subscription in every exit path.
            self.mcu.register_serial_response(callback,response)

    def _fresh_inputs(self):
        p=self._sensor_readback('sensor_inputs',[],INPUT_RESPONSE,self._inputs)
        self._control_validate(p,{'clock':(0,0xffffffff),'raw':(0,65535),
            'buttons':(0,15),'sequence':(0,0xffffffff)})
        if not self.connected:raise ValueError('MCU disconnected during input readback')
        self._inputs(p)
        return dict(self.inputs)

    def _fresh_sensor_snapshot(self):
        started=self.reactor.monotonic()
        p=self._sensor_readback('sensor_board',[],BOARD_RESPONSE,self._status)
        limits={'armed':(0,1),'fault':(0,255),'seen':(0,255),'cable':(0,1)}
        limits.update({'p%d'%i:(0,0xffffffff) for i in range(6)})
        self._control_validate(p,limits)
        self._status(p)
        inputs=self._fresh_inputs()
        uart=[]
        for i in range(2):
            p=self._sensor_readback('sensor_uart',[i],UART_RESPONSE,self._uart_status)
            self._control_validate(p,{'channel':(0,1),'errors':(0,0xffffffff),
                'received':(0,0xffffffff),'sent':(0,0xffffffff)})
            if p['channel']!=i:raise ValueError('RFID UART readback channel mismatch')
            uart.append({k:v for k,v in p.items() if not k.startswith('#')})
            self._uart_status(p)
        if not self.connected:raise ValueError('MCU disconnected during sensor readback')
        return dict(inputs=inputs,rfid_uart=uart,
            refresh_ms=round((self.reactor.monotonic()-started)*1000.,3))

    def _gpio_read_word(self,port,offset):
        # Fixed read-only register allowlist. No caller-supplied addresses,
        # pin reconfiguration, pulls, ODR/BSRR access or debug_write command.
        if port not in GPIO_PORTS or len(port)!=1 or offset not in (0,0x10):
            raise ValueError('GPIO diagnostic register outside read-only allowlist')
        p=self._control_send('gpio_read',[2,GPIO_BASE+GPIO_PORTS.index(port)*0x400+offset])
        self._control_validate(p,{'val':(0,0xffffffff)})
        return p['val']

    def _cassette_gpio_snapshot(self):
        if not self.connected:raise ValueError('MCU is disconnected')
        state=self._control_query()
        pending=getattr(self,'feeder_test_pending_until',None)
        if pending is not None:
            # G4 queues print time and does not necessarily wait in wall time.
            # Finish a recent short feeder window before reading the final
            # inputs, and confirm the MCU has actually released its owner.
            deadline=max(pending,self.reactor.monotonic())+.75
            while (state['mode']=='SERVICE' and not state['fault'] and not state['latched']
                    and (self.reactor.monotonic()<pending or state['active'])
                    and self.reactor.monotonic()<deadline):
                self.reactor.pause(min(deadline,self.reactor.monotonic()+.05))
                state=self._control_query()
            if self.reactor.monotonic()>=pending and not state['active']:
                self.feeder_test_pending_until=None
        if state['mode']!='SERVICE' or state['active'] or state['fault'] or state['latched']:
            raise ValueError('Cassette input comparison requires idle SERVICE mode')
        sensor=self._fresh_sensor_snapshot()
        started=self.reactor.monotonic();ports={};levels={}
        for port in GPIO_PORTS:
            mode=self._gpio_read_word(port,0)
            value=self._gpio_read_word(port,0x10)&0xffff
            mask=sum(1<<i for i in range(16) if ((mode>>(2*i))&3)==0)
            ports[port]=dict(moder=mode,idr=value,input_mask=mask)
            levels.update({'P%s%d'%(port,i):(value>>i)&1 for i in range(16) if mask&(1<<i)})
        if not self.connected:raise ValueError('MCU disconnected during GPIO readback')
        return dict(ports=ports,input_levels=levels,
            gpio_read_ms=round((self.reactor.monotonic()-started)*1000.,3),
            filament=dict(t0_a=sensor['inputs']['raw']['t0_a'],
                t0_b=sensor['inputs']['raw']['t0_b'],t1_a=sensor['inputs']['raw']['t1_a'],
                t1_b=sensor['inputs']['raw']['t1_b'],
                t0_pb12=self.status['p4'],t1_pd14=self.status['p5']),
            rfid_uart=sensor['rfid_uart'])

    @staticmethod
    def _cassette_difference(baseline,current):
        changes=[];mode_changes=[]
        if baseline is not None:
            for port in GPIO_PORTS:
                before=baseline['ports'][port];after=current['ports'][port]
                for i in range(16):
                    pin='P%s%d'%(port,i)
                    if ((before['moder']^after['moder'])>>(2*i))&3:
                        mode_changes.append(pin)
                    if (before['input_mask']&after['input_mask']&(1<<i)
                            and (before['idr']^after['idr'])&(1<<i)):
                        changes.append(dict(pin=pin,name=CASSETTE_PIN_NAMES.get(pin,'unmapped_input'),
                            before=(before['idr']>>i)&1,after=(after['idr']>>i)&1))
        pulse_delta=None
        if baseline is not None:
            pulse_delta={k:(current['filament'][k]-baseline['filament'][k])&0xffffffff
                         for k in ('t0_pb12','t1_pd14')}
        return dict(changes=changes,mode_changes=mode_changes,pulse_delta=pulse_delta)

    def cmd_cassette_diag(self,gcmd):
        action=gcmd.get('ACTION','STATUS').upper()
        if action not in ('BASELINE','COMPARE','STATUS'):
            raise gcmd.error('Use ACTION=BASELINE, COMPARE or STATUS')
        if getattr(self,'feeder_run_context',None) is not None:
            raise gcmd.error('Wait for V9_FEEDER_RUN phase=done or use INVENTURE_STOP')
        baseline=getattr(self,'cassette_gpio_baseline',None)
        if action=='COMPARE' and baseline is None:
            raise gcmd.error('Run INVENTURE_CASSETTE_DIAG ACTION=BASELINE first')
        try:
            current=self._cassette_gpio_snapshot()
        except Exception as exc:
            raise gcmd.error(str(exc))
        difference=self._cassette_difference(None if action=='BASELINE' else baseline,current)
        if action=='BASELINE':self.cassette_gpio_baseline=current
        gcmd.respond_info('V9_CASSETTE_DIAG '+json.dumps(dict(current,action=action,
            **difference,
            baseline_available=self.cassette_gpio_baseline is not None,
            scope='sequential_GPIOA_to_GPIOG_input_register_readbacks',
            physical_function_confirmed=False),sort_keys=True))

    def cmd_sensors(self,gcmd):
        try:
            snapshot=self._fresh_sensor_snapshot()
        except Exception as exc:
            raise gcmd.error(str(exc))
        self.cmd_panel_io(gcmd)
        now=self.reactor.monotonic()
        fresh=self.connected and self.status_received is not None and 0<=now-self.status_received<.75
        gcmd.respond_info('V9_SENSORS '+json.dumps({'board_age_s':None if self.status_received is None else now-self.status_received,
            'counters':{str(i):self.status.get('p%d'%i) if fresh else None for i in range(6)},
            'rfid_uart':snapshot['rfid_uart'],'rfid_rx_overruns':self.rfid_overruns,
            'snapshot_fresh':True,'refresh_ms':snapshot['refresh_ms'],
            'inputs_age_s':max(0.,now-self.inputs_received),
            'unqualified_temperature_pin':'PC4','z_inputs_physically_qualified':False},sort_keys=True))

    def _control_poll(self,eventtime):
        if not self.connected:return self.reactor.NEVER
        try:
            state=self._control_query()
            if getattr(self,'motion_last',{}).get('active'):
                motion=self._motion_query()
                self.gcode.respond_info('V9_MOTION '+json.dumps(motion,sort_keys=True))
            for name,row in list(getattr(self,'output_last',{}).items()):
                if row.get('duty'):
                    updated=self._output_query(OUTPUT_NAMES.index(name))
                    if not updated['duty']:self.gcode.respond_info('V9_OUTPUT '+json.dumps(updated,sort_keys=True))
            return eventtime+.5 if state['mode']!='OFF' else self.reactor.NEVER
        except Exception:
            self._control_abort();logging.exception('R5 control polling failed; outputs stopped')
            return self.reactor.NEVER

    def _load_test_report(self,context,phase=None,reason=None):
        now=self.reactor.monotonic()
        temperature,state=self.panel_temperature(context['tool'],now)
        result=dict(phase=phase or context['phase'],reason=reason,
            stage='cassette_transport_and_flow_check' if context.get('cassette_load')
                else 'extruder_capture_and_flow_check',tool='T%d'%context['tool'],
            target_c=context['target'],temperature_c=temperature,temperature_state=state,
            maximum_power_pct=context['power'],duty_pct=round(context['duty']*100/256.,2),
            overheat_limit_c=context['limit'],elapsed_s=round(max(0.,now-context['start']),2),
            remaining_s=round(max(0.,context['deadline']-now),2),
            timer_phase='heatup' if context['hold_started'] is None else 'hold',
            hold_duration_s=context['hold_duration'],
            hold_remaining_s=round(context['hold_duration'] if context['hold_started'] is None
                else max(0.,context['deadline']-now),2),
            ready_for_jog=self._load_test_jog_ready(context,temperature,state),
            control_mode='pid_r0323_nozzle',
            jog_min_c=max(180.,context['target']-15.),jog_max_c=context['target']+8.,
            flow_confirmed_by_operator=context['flow_confirmed'],
            head_filament_sensor_identified=False,load_completed=False,
            cleaning_pending=True,position_established=False,
            heater_lease_ms=1500,heater_off_during_jog=False,
            jog_window_max_s=LOAD_JOG_WINDOW_S,
            measurement=self._load_test_measurement(context))
        result['motion_scope']='latest_MCU_window; last_jog and measurement contain totals'
        if context.get('comparison') is not None:result.update(context['comparison'])
        if context.get('motion') is not None:result['motion']=context['motion']
        if context.get('jog') is not None:
            result['jog']={k:context['jog'][k] for k in ('steps','sent','direction','rate','windows')}
        elif context.get('last_jog') is not None:result['last_jog']=context['last_jog']
        if context.get('cassette_load') is not None:
            plan=context['cassette_load'];sent=plan['sent']
            stage=('complete' if plan['feed_completed'] else 'heating' if not plan['started']
                else 'tube' if sent<plan['tube_steps']
                else 'head' if sent<plan['tube_steps']+plan['head_steps'] else 'purge')
            result['cassette_load']=dict(plan,stage=stage,
                e_direction=plan['direction'],cassette_direction=plan['cassette_direction'],channel=plan['channel'],
                nominal_steps_per_mm=LOAD_E_STEPS_PER_MM,
                nominal_E_mm=round(plan['total_steps']/LOAD_E_STEPS_PER_MM,3),
                nominal_E_sent_mm=round(sent/LOAD_E_STEPS_PER_MM,3),
                feeder_speed_estimate_mm_s=LOAD_CASSETTE_MM_S,
                distance_calibrated=False,head_observed_mm_approx=90,
                head_observation_steps=95000,head_reference_tool='T0',
                automatic_tool_switch=False,mechanical_tool_selection_verified=False,
                feeder_time_basis='requested MCU timer windows; not measured travel')
            if plan.get('operation')=='retract':
                result['stage']='heated_cassette_retraction'
                result['cassette_load'].update(stage='complete' if plan['feed_completed']
                    else 'retracting' if plan['started'] else 'heating',
                    percent_of_reference=round(100.*plan['total_steps']/171800.,3),
                    reference_steps=171800,heater_off_on_completion=True)
            if plan.get('smooth'):
                result['cassette_load'].update(continuous_motion=True,
                    feeder_windows_requested=None,feeder_ms_requested=None,
                    feeder_elapsed_ms=(context.get('last_stream') or {}).get('elapsed_ms',0),
                    feeder_time_basis='continuous MCU-owned run; elapsed time is not measured filament travel')
        if context.get('last_stream') is not None:result['load_stream']=context['last_stream']
        if context.get('factory') is not None:
            f=context['factory']
            result['factory']={k:f[k] for k in ('parameters','phase','history','position',
                'reference_valid','snapshot','result','e_sent_steps','e_steps_by_phase','fallback_started',
                'operation','module_stack','module_history','selection_commanded','motion_legs',
                'cleaning_done')}
            result['stage']='factory_cartridge_load' if f['operation']=='AUTO' else 'factory_procedure'
            if 'z_home_settings' in f:
                result['stage']='factory_z_reference'
                result['factory']['z_home_settings']=f['z_home_settings']
                result['factory']['last_z_move']=f.get('last_z_move')
                result['factory']['z_home_progress']=f.get('z_home_progress')
                if 'z_mcu_state' in f:result['factory']['z_mcu_state']=f['z_mcu_state']
            cleaning_planned=(f['operation'] in ('AUTO','CLEAN')
                or f['operation']=='PURGE' and f['parameters']['clean_after'])
            result['cleaning_pending']=bool(cleaning_planned and not f['cleaning_done']
                and not (f.get('result') or {}).get('success'))
            result['heater_off_on_completion']=True
            result['factory_transport_completed']=(f['operation']=='AUTO'
                and bool((f.get('result') or {}).get('success')))
        self.load_test_last=result
        return result

    def _load_test_feeder_window(self,context,milliseconds):
        # A queued STOP must invalidate the owner before another raw feeder
        # packet. Feeder writes and the heartbeat use the same serial queue.
        mutex=getattr(self,'query_mutex',None)
        with mutex if mutex is not None else nullcontext():
            if self.load_test_context is not context or not self.connected:
                raise ValueError('Load test was stopped before the cassette request')
            if milliseconds and self.reactor.monotonic()>=context['deadline']:
                raise ValueError('Load test duration finished before the cassette request')
            plan=context['cassette_load']
            self.commands['heartbeat'].send([])
            self.commands['feeder'].send([plan['channel'],plan['cassette_direction'],milliseconds])
            if milliseconds:
                plan['feeder_windows_requested']+=1
                plan['feeder_ms_requested']+=milliseconds

    def _load_test_begin_cassette(self,context,now):
        plan=context['cassette_load']
        self._load_test_capture(context,baseline=True)
        self._load_test_safety(context)
        if self.load_test_context is not context:return
        steps=plan['total_steps'];rate=plan['rate']
        if plan.get('smooth'):
            context['stream']=dict(token=self._control_next_token(),sequence=0,sent=0,
                steps=steps,rate=rate,started=False,start=now,
                max_ms=math.ceil(steps*1000./rate)+2000)
            plan['started']=True;context['phase']='loading'
            return
        windows=math.ceil(steps/(rate*LOAD_JOG_WINDOW_S))+2
        context['jog']=dict(identity=None,steps=steps,sent=0,direction=plan['direction'],rate=rate,
            started=now,windows=0,deadline=now+steps/rate+windows*.5+30.,coupled=True)
        plan['started']=True
        context['phase']='loading'

    def _load_test_stream_step(self,context,temperature,now):
        stream=context['stream'];plan=context['cassette_load']
        if temperature<max(180.,context['target']-25.):raise ValueError('Nozzle cooled below the extrusion limit')
        if now-stream['start']>=stream['max_ms']/1000.+1.:
            raise ValueError('Continuous load exceeded its bounded duration')
        starting=not stream['started'];sequence=0 if starting else stream['sequence']+1
        if starting:
            duty=self._load_test_pid(context,temperature,now)
            if duty or context['duty']:self._load_test_output(context,duty)
        row=self._control_send('load_stream',[1 if starting else 2,
            stream['steps'] if starting else 0,stream['rate'] if starting else 0,
            stream['max_ms'] if starting else 0,1500,
            round(max(180.,context['target']-25.)),round(context['target']+10.),
            stream['token'],sequence,context['tool'],plan['channel'],plan['direction'],
            plan['cassette_direction']],mutate=True,owner=context)
        if self.load_test_context is not context:return
        self._control_validate(row,{'token':(0,0xffffffff),'active':(0,1),'reason':(0,7),
            'reject':(0,4),'planned':(0,400000),'sent':(0,400000),'elapsed_ms':(0,600100),
            'remaining_ms':(0,1500),'sequence':(0,0xffffffff),'feeder':(0,1)})
        if row['token']!=stream['token'] or row['planned']!=stream['steps']:
            raise ValueError('Continuous load ownership changed')
        if not stream['sent']<=row['sent']<=stream['steps']:
            raise ValueError('Invalid continuous extruder step accounting')
        delta=row['sent']-stream['sent'];stream['sent']=row['sent']
        context['measurement']['dir%d_steps'%plan.get('direction',0)]+=delta;plan['sent']=row['sent']
        context['last_stream']=dict(row,reason=LOAD_STREAM_REASONS[row['reason']])
        completed=not row['active'] and row['reason']==1 and row['sent']==stream['steps']
        # The last steps can finish between a renewal request and its reply.
        # A completed token is accepted, but can never renew or restart motion.
        if row['reject'] and not (completed and row['reject']==4):
            raise ValueError('MCU refused continuous load: '+str(row['reject']))
        if completed:
            if row['feeder']:raise ValueError('MCU did not stop the cassette at completion')
            plan['feed_completed']=True
            context['last_jog']=dict(steps=stream['steps'],sent=stream['sent'],direction=plan.get('direction',0),
                rate=stream['rate'],windows=1,elapsed_s=round(now-stream['start'],3),continuous=True)
            context['measurement']['completed_jogs']+=1;context['stream']=None
            self._load_test_capture(context)
            if self.load_test_context is not context:return
            if plan.get('operation')=='retract':
                self._load_test_emit(context,'retract_done')
                self._load_test_cancel('retraction_finished',phase='done')
                self._control_abort()
                return
            context['phase']='awaiting_flow_confirmation';self._load_test_emit(context,'feed_done')
            return
        if (not row['active'] or row['reason'] or not row['remaining_ms'] or not row['feeder']
                or row['sequence']!=sequence):
            raise ValueError('Continuous load stopped: '+LOAD_STREAM_REASONS[row['reason']])
        stream['started']=True;stream['sequence']=sequence
        if not starting:
            duty=self._load_test_pid(context,temperature,now)
            if duty or context['duty']:self._load_test_output(context,duty)
        if self.load_test_context is not context:return
        context['phase']='retracting' if plan.get('operation')=='retract' else 'loading'
        if now>=context['next_report']:
            context['next_report']=now+5.;self._load_test_emit(context)

    def _load_test_jog_ready(self,context,temperature=None,state=None):
        now=self.reactor.monotonic()
        if state is None:temperature,state=self.panel_temperature(context['tool'],now)
        return (getattr(self,'load_test_context',None) is context and self.connected
            and context.get('factory') is None
            and now<context['deadline'] and context.get('ready_latched',False)
            and context.get('jog') is None and context.get('stream') is None and state=='valid'
            and max(180.,context['target']-15.)<=temperature<=context['target']+8.)

    def _load_test_readiness(self,context,temperature,now):
        # Acquire close to target once; release only outside the wider hot-jog
        # band. A thermal ripple must not restart the two-second warm-up gate.
        if not max(180.,context['target']-15.)<=temperature<=context['target']+8.:
            context['ready_latched']=False;context['ready_since']=None
        if not context['ready_latched']:
            if abs(temperature-context['target'])<=5.:
                if context['ready_since'] is None:context['ready_since']=now
                if now-context['ready_since']>=2.:
                    context['ready_latched']=True;context['reached_target']=True
                    if context['hold_started'] is None:
                        context['hold_started']=now;context['deadline']=now+context['hold_duration']
            else:context['ready_since']=None

    @staticmethod
    def _load_test_pid(context,temperature,now,integrate=True):
        pid=context['pid'];dt=now-pid['time']
        if not math.isfinite(dt) or dt<0:raise ValueError('Invalid load regulator clock')
        if dt>0:
            # .95 at 100 ms is preserved for the actual elapsed interval.
            alpha=LOAD_PID_ALPHA**(dt/LOAD_PID_DT)
            slope=(temperature-pid['temperature'])/dt
            pid['derivative']=alpha*pid['derivative']+(1.-alpha)*slope
            pid['temperature']=temperature;pid['time']=now
        error=context['target']-temperature
        maximum=context['power']*255./100.
        raw=LOAD_PID_KP*error+pid['integral']-LOAD_PID_KD*pid['derivative']
        # Bounded conditional integration; don't integrate while E has the
        # heater intentionally OFF, or farther into either output saturation.
        if integrate and dt>0 and (0<=raw<=maximum or raw>maximum and error<0
                                  or raw<0 and error>0):
            pid['integral']=min(maximum,max(0.,pid['integral']+LOAD_PID_KI*error*dt))
        raw=LOAD_PID_KP*error+pid['integral']-LOAD_PID_KD*pid['derivative']
        raw=min(maximum,max(0.,raw))
        if temperature>=context['target']+2.:raw=0.
        return round(raw*256./255.)

    def _load_test_emit(self,context,phase=None,reason=None):
        result=self._load_test_report(context,phase,reason)
        self.gcode.respond_info('V9_LOAD_TEST '+json.dumps(result,sort_keys=True))
        return result

    def _load_test_cancel(self,reason,phase='stopped'):
        # Invalidate before any serial wait. A queued timer cannot renew power
        # or issue another STEP request after the common STOP takes ownership.
        context=getattr(self,'load_test_context',None)
        if context is None:return
        self.load_test_context=None
        self.reactor.unregister_timer(context['timer'])
        if context.get('factory') is not None:context['factory']['reference_valid']=False
        context['duty']=0
        self._load_test_emit(context,phase,reason)

    def _load_test_safety(self,context):
        self._feeder_service_preflight(allow_active=True,allow_load=True)
        if self.load_test_context is not context:raise ValueError('Load test was stopped')
        now=self.reactor.monotonic()
        if now>=context['deadline']:
            raise ValueError('Nozzle did not reach target within 300 seconds'
                             if context['hold_started'] is None else 'hold_duration_finished')
        received=getattr(self,'status_received',None)
        if received is None or not 0<=now-received<.75:
            raise ValueError('Board/cable status is stale')
        if self.status.get('cable')!=0 or self.status.get('fault')!=0:
            raise ValueError('Extruder cable or MCU fault')
        values=[]
        for i in range(7):
            value,state=self.panel_temperature(i,now)
            maximum=(310,310,150,150,150,100,100)[i]
            if state!='valid' or not -20<=value<maximum:
                raise ValueError('Required temperature '+self.ADC_PINS[i]+' is '+state)
            values.append(value)
        temperature=values[context['tool']]
        if temperature>=min(context['limit'],context['target']+10.):
            raise ValueError('Nozzle overtemperature')
        counter=self.status.get('p0')
        if type(counter) is not int:raise ValueError('Missing F0 hotend fan tachometer')
        if counter!=context['fan_counter']:
            context['fan_counter']=counter;context['fan_deadline']=now+10.
        if max(values[:2])>40 or context['duty']:
            if now>=context['fan_deadline']:raise ValueError('No F0 hotend cooling fan pulses for 10 seconds')
        else:context['fan_deadline']=now+10.
        if not context['reached_target'] and not context.get('factory_preparing',False):
            if now-context['start']>=300.:raise ValueError('Nozzle did not reach target within 300 seconds')
            if now-context['start']>=60. and temperature<context['start_temperature']+5.:
                raise ValueError('Nozzle temperature did not rise by 5C within 60 seconds')
        return temperature

    def _load_test_output(self,context,duty):
        state=self._output_query(context['tool']+6,1 if duty else 2,duty,
            1500 if duty else 0,context['limit'],owner=context)
        if state['reject']:raise ValueError(state['reject'])
        if state['duty']!=duty:raise ValueError('MCU did not apply the requested heater power')
        if not duty and (state['reason']!='operator_off' or state['remaining_ms']):
            raise ValueError('MCU did not confirm a fresh heater OFF')
        context['duty']=duty;context['output_token']=state['token']
        return state

    def _load_test_capture(self,context,baseline=False):
        self._load_test_output(context,0)
        if self.load_test_context is not context:raise ValueError('Load test was stopped')
        current=self._cassette_gpio_snapshot()
        if self.load_test_context is not context:raise ValueError('Load test was stopped')
        if baseline:context['baseline']=current
        comparison=self._cassette_difference(context['baseline'],current)
        comparison.update(filament_before=context['baseline']['filament'],
            filament_after=current['filament'],gpio_snapshot_fresh=True)
        context['comparison']=comparison

    @staticmethod
    def _load_test_measurement(context):
        m=context['measurement'];net=m['dir0_steps']-m['dir1_steps']
        feed_direction=context['tool'];feed=m['dir%d_steps'%feed_direction]
        retract=m['dir%d_steps'%(1-feed_direction)]
        return dict(m,net_dir0_steps=net,nominal_steps_per_mm=LOAD_E_STEPS_PER_MM,
            dir0_mm=round(m['dir0_steps']/LOAD_E_STEPS_PER_MM,3),
            dir1_mm=round(m['dir1_steps']/LOAD_E_STEPS_PER_MM,3),
            net_dir0_mm=round(net/LOAD_E_STEPS_PER_MM,3),
            basis='MCU-confirmed steps; not measured filament travel',
            T0_dir0_is_feed=True,selected_tool='T%d'%context['tool'],feed_direction=feed_direction,
            retract_direction=1-feed_direction,feed_steps=feed,retract_steps=retract,
            net_feed_steps=feed-retract,net_feed_mm=round((feed-retract)/LOAD_E_STEPS_PER_MM,3),
            has_unfinished_jog=context.get('jog') is not None or context.get('stream') is not None,
            flow_mark=context.get('flow_mark'))

    def _load_test_check_output(self,context):
        output=self._output_query(context['tool']+6)
        if self.load_test_context is not context:raise ValueError('Load test was stopped')
        if output['reject'] or output['reason'] in ('temperature_or_sensor_guard','heartbeat_or_cable_lost','mcu_shutdown'):
            raise ValueError('MCU heater guard: '+str(output['reason']))
        if context['duty'] and (output['duty']!=context['duty'] or not output['remaining_ms']
                or output['token']!=context['output_token']):
            raise ValueError('Heater power lease expired or changed; restart the test explicitly')

    def _load_test_start_window(self,context):
        jog=context['jog'];steps=min(jog['steps']-jog['sent'],max(1,int(jog['rate']*LOAD_JOG_WINDOW_S)))
        if jog.get('coupled'):
            plan=context['cassette_load']
            boundaries=(plan['tube_steps'],plan['tube_steps']+plan['head_steps'],plan['total_steps'])
            steps=min(steps,next(n for n in boundaries if n>jog['sent'])-jog['sent'])
        max_ms=math.ceil(steps*1000./jog['rate'])+250
        counts=(0,0,0,steps if jog['direction'] else -steps)
        timer_tool=context['tool'] if jog.get('axis_timer') else None
        motion=self._motion_query(1,counts,jog['rate'],2 if timer_tool is not None else 10,
                                  max_ms,owner=context,timer_tool=timer_tool)
        if self.load_test_context is not context:return
        if motion['reject']:raise ValueError(motion['reject'])
        context['motion']=motion
        jog.update(identity=(motion['run'],motion['token']),window_steps=steps,window_accounted=0,
            window_deadline=self.reactor.monotonic()+max_ms/1000.+.25)
        if jog.get('coupled'):
            # Start the cassette only after the E START and driver parameters
            # have been acknowledged. T0 directions were physically checked;
            # T1 E polarity follows r0323 0x08012458, awaiting its first trial.
            self._load_test_feeder_window(context,math.ceil(steps*1000./LOAD_CASSETTE_RATE))

    def _load_test_account_window(self,context,motion):
        jog=context['jog']
        if motion['reject'] or (motion['run'],motion['token'])!=jog['identity']:
            raise ValueError('Extruder motion ownership changed')
        sent=motion['drivers']['E']['sent']
        if sent<jog['window_accounted'] or sent>jog['window_steps']:
            raise ValueError('Invalid extruder step accounting')
        delta=sent-jog['window_accounted'];jog['window_accounted']=sent;jog['sent']+=delta
        context['measurement']['dir%d_steps'%jog['direction']]+=delta
        if jog.get('coupled'):context['cassette_load']['sent']=jog['sent']

    def _load_test_step(self,context):
        temperature=self._load_test_safety(context)
        now=self.reactor.monotonic()
        self._load_test_readiness(context,temperature,now)
        starting=context['output_token'] is None
        if starting:self._load_test_output(context,0)
        self._load_test_check_output(context)
        plan=context.get('cassette_load')
        if (plan is not None and not plan['started']
                and self._load_test_jog_ready(context,temperature,'valid')):
            self._load_test_begin_cassette(context,now)
        if context.get('stream') is not None:
            self._load_test_stream_step(context,temperature,now)
            return
        jog=context.get('jog')
        if jog is not None:
            if temperature<max(170.,context['target']-25.):
                raise ValueError('Nozzle cooled below the extrusion limit during the jog')
            if now>=jog['deadline']:raise ValueError('Extruder jog exceeded its bounded duration')
            if jog['identity'] is not None:
                motion=self._motion_query()
                if self.load_test_context is not context:return
                self._load_test_account_window(context,motion);context['motion']=motion
                if now>=jog['window_deadline'] and motion['active']:
                    raise ValueError('Extruder window exceeded its bounded duration')
                if motion['active']:
                    self._load_test_pid(context,temperature,now,integrate=False)
                    return
                if motion['reason']!='completed' or motion['drivers']['E']['sent']!=jog['window_steps']:
                    raise ValueError('Extruder jog stopped before completion: '+motion['reason'])
                if jog.get('coupled'):self._load_test_feeder_window(context,0)
                jog['windows']+=1;jog['identity']=None
            if jog['sent']==jog['steps']:
                context['last_jog']=dict(steps=jog['steps'],sent=jog['sent'],direction=jog['direction'],
                    axis_timer=bool(jog.get('axis_timer')),
                    rate=jog['rate'],windows=jog['windows'],elapsed_s=round(now-jog['started'],3))
                context['measurement']['completed_jogs']+=1;context['jog']=None
                if jog.get('coupled'):plan['feed_completed']=True
                self._load_test_capture(context)
                if self.load_test_context is not context:return
                self._load_test_emit(context,'feed_done' if jog.get('coupled') else 'jog_done')
        duty=self._load_test_pid(context,temperature,now)
        if duty or context['duty']:self._load_test_output(context,duty)
        if self.load_test_context is not context:return
        if context.get('jog') is not None:
            if context['ready_latched'] and max(180.,context['target']-15.)<=temperature<=context['target']+8.:
                self._load_test_start_window(context)
                if self.load_test_context is not context:return
                context['phase']='loading' if context['jog'].get('coupled') else 'jogging'
            else:context['phase']='reheating'
            if now>=context['next_report']:
                context['next_report']=now+5.;self._load_test_emit(context)
            return
        ready=self._load_test_jog_ready(context,temperature,'valid')
        phase='ready' if ready else 'heating'
        if plan is not None and plan['feed_completed']:phase='awaiting_flow_confirmation'
        if context['flow_confirmed']:phase='flow_confirmed_cleaning_pending'
        if starting or phase!=context['phase'] or now>=context['next_report']:
            context['phase']=phase;context['next_report']=now+5.
            self._load_test_emit(context)

    def _load_test_tick(self,context,eventtime):
        if getattr(self,'load_test_context',None) is not context:return self.reactor.NEVER
        if context['busy']:return self.reactor.monotonic()+.25
        context['busy']=True
        try:
            if context.get('factory') is not None:self._factory_tick(context)
            else:self._load_test_step(context)
        except Exception as exc:
            if self.load_test_context is context:
                factory=context.get('factory')
                edge=factory is not None and factory.get('z_edge_run') is not None
                stopped=factory is not None and (factory['move'] is not None or edge)
                reason=str(exc);details=dict(getattr(exc,'details',{}))
                if stopped:
                    # The MCU retains an already terminal motion across STOP.
                    # Keep ownership until readback finishes, with no renewal.
                    self._control_stop_outputs()
                    if self.load_test_context is not context:return self.reactor.NEVER
                    if edge:
                        reason=self._factory_failed_z_edge(context,reason,details)
                    else:
                        reason=self._factory_failed_motion(context,reason,details)
                    if self.load_test_context is not context:return self.reactor.NEVER
                if factory is not None:
                    factory['result']=dict(success=False,reason=reason,details=details)
                finished_hold=factory is None and reason=='hold_duration_finished'
                self._load_test_cancel(reason,phase='done' if finished_hold else 'failed')
                if not stopped:self._control_abort()
        finally:context['busy']=False
        return (self.reactor.monotonic()+(.1 if context.get('jog') is not None or context.get('factory') is not None else .25)
                if self.load_test_context is context else self.reactor.NEVER)

    def _configure_procedures(self,config):
        from .inventure_factory_core import profiles
        try:
            self.procedure_profiles=profiles(config.get('procedure_defaults','{}'),
                config.get('procedure_t0','{}'),config.get('procedure_t1','{}'))
        except ValueError as exc:raise config.error(str(exc))

    def _factory_motion_available(self,tool=0):
        from .inventure_factory_core import FACTORY_MOTION_MAX_RATE
        return (getattr(self,'factory_motion_abi',0)==1
                and getattr(self,'factory_motion_max_hz',0)>=FACTORY_MOTION_MAX_RATE
                and bool(getattr(self,'factory_motion_tools',1)&(1<<tool)))

    def _z_home_mcu_available(self):
        return getattr(self,'z_edge_abi',0)==1 and 'z_edge' in self.commands

    def cmd_procedure(self,gcmd):
        from .inventure_factory_core import PROCEDURES, procedure_modules
        from .inventure_z_home import Z_HOME_PROCEDURES
        action=str(gcmd.get('ACTION','STATUS')).upper()
        if action in ('STATUS','STOP'):return self.cmd_load_test(gcmd)
        if action=='LIST':
            tool=str(gcmd.get('TOOL','T0')).upper()
            if tool not in ('T0','T1'):
                raise gcmd.error('TOOL must be T0 or T1')
            profile=str(gcmd.get('SPEED_PROFILE','SERVICE')).upper()
            if profile not in ('SERVICE','OEM'):
                raise gcmd.error('SPEED_PROFILE must be SERVICE or OEM')
            tool_index=int(tool[-1])
            modules={name:procedure_modules(name,tool_index,profile) for name in PROCEDURES}
            if tool_index==0:modules.update(Z_HOME_PROCEDURES)
            gcmd.respond_info('V9_PROCEDURES '+json.dumps(dict(api=1,tool=tool,procedures=modules,
                precedence=['builtin','procedure_defaults','procedure_t0_or_t1','command'],
                standalone_homes=True,MCU_required='RC8-CONTROL-R7-FACTORY',
                MCU_required_for_feeder=FACTORY_FEED_RELEASE,
                MCU_required_for_oem_speed=FACTORY_DUAL_RELEASE if tool_index else FACTORY_MOTION_RELEASE,
                speed_profile=profile,speed_profiles=['SERVICE','OEM'],
                factory_motion_available=self._factory_motion_available(tool_index),
                z_home_mcu_available=tool_index==0 and self._z_home_mcu_available()),
                sort_keys=True));return
        operation=str(gcmd.get('PROCEDURE','AUTO')).upper() if action=='PARAMS' else action
        if operation not in PROCEDURES and operation not in Z_HOME_PROCEDURES:
            raise gcmd.error('Use ACTION=LIST, PARAMS, STATUS, STOP or '+
                ', '.join((*PROCEDURES,*Z_HOME_PROCEDURES)))
        return self._factory_start(gcmd,read_only=action=='PARAMS',operation=operation)

    def _factory_start(self,gcmd,read_only=False,operation='AUTO'):
        from .inventure_factory_core import (
            parameters, FactoryProgram, PROCEDURES, procedure_modules, require_heated_target,
        )
        from .inventure_z_home import Z_HOME_PROCEDURES, ZHomeProgram, z_home_parameters
        z_home=operation in Z_HOME_PROCEDURES
        z_settings=None
        try:
            if not z_home and operation not in PROCEDURES:raise ValueError('Unknown procedure '+operation)
            p=parameters(gcmd,getattr(self,'procedure_profiles',None),operation)
            z_edges=operation=='HOME_Z' and p['speed_profile']=='OEM'
            if z_home:
                if p['tool']!=0:raise ValueError('Z reference uses the model nozzle TOOL=T0')
                if gcmd.get('TARGET',None) is None:p['target']=200.
                p['target_requires_override']=False
                z_settings=z_home_parameters(gcmd,p,required=operation=='HOME_Z' and not read_only)
            timeout=p['procedure_timeout']
            fast_available=self._factory_motion_available(p['tool'])
            if read_only:
                modules=(Z_HOME_PROCEDURES[operation] if z_home
                         else procedure_modules(operation,p['tool'],p['speed_profile']))
                settings={} if z_settings is None else dict(z_home_settings=z_settings)
                gcmd.respond_info('V9_FACTORY_PARAMS '+json.dumps(dict(p,operation=operation,
                    target=None if not z_home and p['target_requires_override'] else p['target'],
                    factory_motion_available=fast_available,
                    z_home_mcu_available=self._z_home_mcu_available(),
                    modules=modules,**settings),sort_keys=True));return
            if not z_home:require_heated_target(p,operation)
            if z_edges and not self._z_home_mcu_available():
                raise ValueError('OEM HOME_Z requires RC8-CONTROL-R7-Z-HOST (MCU Z edge ABI 1)')
            if p['speed_profile']=='OEM' and not fast_available:
                release=FACTORY_DUAL_RELEASE if p['tool'] else FACTORY_MOTION_RELEASE
                raise ValueError('SPEED_PROFILE=OEM for T%d requires the %s MCU application'%(p['tool'],release))
            if 'factory_session' not in self.commands:
                raise ValueError('Factory procedures require the RC8-CONTROL-R7-FACTORY MCU application')
            needs_feeder = operation in ('AUTO','UNLOAD') or (
                operation in ('MANUAL','PURGE') and p['feeder'])
            if needs_feeder and not getattr(self,'factory_feed_clock',0):
                raise ValueError('Factory cassette transport requires the '+FACTORY_FEED_RELEASE+' MCU application')
            if any(gcmd.get(k,None) is not None for k in ('STEPS','HEAD_STEPS','TUBE_SECONDS','PURGE_STEPS','SMOOTH','RATE')):
                raise ValueError('AUTO uses L1/L3/L4 parameters; remove fixed-distance LOAD/RETRACT parameters')
            if getattr(self,'load_test_context',None) is not None:
                raise ValueError('Load test already active; use STATUS or STOP')
            if z_home:
                self._z_home_inputs({'nozzle_contact':1})
                program=ZHomeProgram(p,z_settings).run(operation)
            else:program=FactoryProgram(p).run(operation)
        except (ValueError,TypeError) as exc:raise gcmd.error(str(exc))
        class HeatCommand:
            def get(self,key,default=None):
                if key=='ACTION':return 'HEAT'
                if key=='TOOL':return 'T%d'%p['tool']
                if key in ('TARGET','MAX_POWER','DURATION'):return p[key.lower()]
                return gcmd.get(key,default)
            def get_int(self,*args,**kw):return gcmd.get_int(*args,**kw)
            def error(self,message):return gcmd.error(message)
            def respond_info(self,message):return gcmd.respond_info(message)
        self.cmd_load_test(HeatCommand())
        context=self.load_test_context;now=self.reactor.monotonic()
        context['factory_preparing']=True;context['deadline']=now+timeout
        context['factory']=dict(parameters=p,program=program,action=None,move=None,
            resume=None,phase='HOME',history=[],position=[0.,0.,0.],reference_valid=False,
            homed=[],snapshot={},result=None,e_sent_steps=0,fallback_started=False,
            e_steps_by_phase={},motion_legs={},
            cleaning_done=False,session=False,token=self._control_next_token(),sequence=-1,
            mode=0,monitor=0,total_deadline=now+timeout,heat_started=False,
            operation=operation,started=now,module_stack=[],module_history=[],selection_commanded=False)
        if z_home:context['factory']['z_home_settings']=z_settings
        self._load_test_emit(context)

    def _z_home_inputs(self,expected):
        raw=self._fresh_inputs()['raw']
        if raw.get('tray')!=0:raise ValueError('Z reference requires a seated tray (PC13=0)')
        for name,level in expected.items():
            if raw.get(name)!=level:
                raise ValueError('Unexpected %s before Z reference move (expected %d)'%(name,level))
        return raw

    def _factory_z_edge_read(self,context,run,starting=False):
        operation,steps,interval=run['operation'],run['steps'],run['interval_us']
        args=[int(starting),run['owner'],run['token'],
              operation if starting else 0,steps if starting else 0,interval if starting else 0]
        row=self._control_send('z_edge',args,mutate=starting,
                               owner=context if starting else None)
        if self.load_test_context is not context:raise ValueError('Z reference was stopped')
        self._control_validate(row,{'owner':(0,0xffffffff),'token':(0,0xffffffff),
            'operation':(0,2),'active':(0,1),'status':(0,6),'reject':(0,255),
            'sent':(0,steps),'end_raw':(0,1)})
        if row['reject']:
            raise ValueError('MCU Z edge rejected (%d)' % row['reject'])
        if (row['owner'],row['token'],row['operation'])!=(run['owner'],run['token'],operation):
            raise ValueError('MCU Z edge ownership changed')
        previous=run.get('last')
        status=row['status'];sent=row['sent']
        if (bool(row['active'])!=(status==0)
                or status==3
                or previous is not None and (sent<previous['sent']
                    or not previous['active'] and (status,sent,row['end_raw'])!=(
                        previous['status'],previous['sent'],previous['end_raw']))
                or status==1 and (row['end_raw']!=int(operation!=0)
                    or operation==2 and sent!=steps)
                or status==2 and (operation==2 or sent!=steps)):
            raise ValueError('Inconsistent MCU Z edge response')
        run['last']=row
        f=context['factory']
        from .inventure_z_home import Z_STEPS_PER_MM
        signed=sent if operation==0 else -sent
        f['position'][2]=(run['start_steps']+signed)/Z_STEPS_PER_MM
        f['z_mcu_state']=dict(row)
        return row

    def _factory_z_edge(self,context,action):
        from .inventure_factory_core import FactoryError
        from .inventure_z_home import Z_STEPS_PER_MM
        f=context['factory'];now=self.reactor.monotonic();run=f.get('z_edge_run')
        if run is None:
            if (not f['reference_valid'] or not f['selection_commanded'] or not context['ready_latched']
                    or not f['session'] or f['mode'] or context['tool']!=0
                    or round(f['position'][2]*Z_STEPS_PER_MM)!=action['start_steps']):
                raise ValueError('MCU Z edge requires the heated, selected nozzle and its owned approach')
            run=dict(owner=f['token'],token=self._control_next_token(),
                operation=action['operation'],steps=action['steps'],interval_us=action['interval_us'],
                start_steps=action['start_steps'],
                deadline=now+action['steps']*action['interval_us']/1_000_000.+2.)
            # A lost START reply must still leave enough context to stop and
            # identify the exact leg. Never reissue a motion request on timeout.
            f['z_edge_run']=run
            row=self._factory_z_edge_read(context,run,starting=True)
        else:
            row=self._factory_z_edge_read(context,run)
        if row['active']:
            if self.reactor.monotonic()>=run['deadline']:raise ValueError('MCU Z edge deadline exceeded')
            return None
        if row['status'] in Z_EDGE_ERRORS:
            raise FactoryError(Z_EDGE_ERRORS[row['status']],z_mcu_state=dict(row))
        f['z_edge_run']=None
        return dict(status=row['status'],sent_steps=row['sent'],end_raw=row['end_raw'])

    def _factory_failed_z_edge(self,context,reason,details):
        run=context['factory']['z_edge_run'];row=run.get('last')
        try:
            if row is None or row['active']:row=self._factory_z_edge_read(context,run)
        except Exception:
            logging.debug('MCU Z edge terminal readback unavailable',exc_info=True)
        if self.load_test_context is not context or row is None:
            return reason
        details['z_mcu_state']=dict(row)
        if row['status'] in (4,5):
            primary=Z_EDGE_ERRORS[row['status']]
            if reason!=primary:details['secondary_error']=reason
            return primary
        return reason

    def _factory_session(self,context,mode=None,monitor=None):
        f=context['factory'];p=f['parameters'];starting=not f['session']
        if mode is not None:f['mode']=mode
        if monitor is not None:f['monitor']=monitor
        seq=f['sequence']+1
        from .inventure_factory_core import feeder_budget
        budget=feeder_budget(p)
        row=self._control_send('factory_session',[1 if starting else 2,context['tool'],
            f['mode'],f['monitor'],p['feeder_ms'],budget,1500,
            round(max(180.,context['target']-25.)),round(context['target']+10.),f['token'],seq],
            mutate=True,owner=context)
        if self.load_test_context is not context:raise ValueError('Factory load was stopped')
        self._control_validate(row,{'token':(0,0xffffffff),'active':(0,1),'reason':(0,7),
            'reject':(0,5),'tool':(0,1),'mode':(0,4),'running':(0,1),'a':(0,1),'b':(0,1),
            'pulses':(0,0xffffffff),'pulse_age_ms':(0,0xffffffff),'starts':(0,0xffffffff),
            'fault':(0,255),'sequence':(0,0xffffffff),'remaining_ms':(0,1500)})
        if (row['token']!=f['token'] or not row['active'] or row['reject'] or row['reason']
                or row['tool']!=context['tool'] or row['mode']!=f['mode']
                or row['fault'] or row['sequence']!=seq or not row['remaining_ms']):
            raise ValueError('MCU factory guard/ownership rejected: '+json.dumps(row,sort_keys=True))
        if row['mode']==2 and not row['running']:
            raise ValueError('MCU did not start forward cassette feed: '+json.dumps(row,sort_keys=True))
        if f['snapshot']:
            from .inventure_factory_core import delta32
            delta32(row['pulses'],f['snapshot']['pulses']);delta32(row['starts'],f['snapshot']['starts'])
        f.update(session=True,sequence=seq,snapshot=dict(row))
        return dict(row)

    def _factory_begin_move(self,context,action):
        from .inventure_factory_core import MAX_MASTER_RATE,FACTORY_MOTION_MAX_RATE
        f=context['factory'];p=f['parameters'];pos=f['position']
        releasing=action['kind']=='home_release'
        home=action['kind']=='home' or releasing
        probe=action['kind']=='z_contact_move'
        e=action.get('e',0.);x=action.get('x',pos[0]);y=action.get('y',pos[1])
        z=pos[2];mask=0
        if probe:
            from .inventure_z_home import Z_STEPS_PER_MM
            if not f['reference_valid'] or not f['selection_commanded'] or not context['ready_latched']:
                raise ValueError('Z probe requires the heated, selected nozzle and a valid lower reference')
            self._z_home_inputs(action['initial_raw'])
            if self.load_test_context is not context:raise ValueError('Z reference was stopped')
            dx=dy=0.
            dz=action['steps']/Z_STEPS_PER_MM
            length=abs(dz)
            mask=action['stop_mask']|16
            stop_value=action['stop_value']|16
        elif home:
            axis=action['axis'];distance=action['max_mm'] if releasing else -action['max_mm']
            dx=distance if axis=='X' else 0.;dy=distance if axis=='Y' else 0.
            dz=distance if axis=='Z' else 0.;length=abs(distance)
            mask={'X':1,'Y':2,'Z':4}[axis]
            stop_value=0 if releasing else mask
        else:
            if not f['reference_valid']:raise ValueError('Factory motion has no owned homing reference')
            if not p['min_x']<=x<=p['max_x'] or not p['min_y']<=y<=p['max_y']:
                raise ValueError('Factory motion outside service envelope')
            dx=x-pos[0];dy=y-pos[1];dz=0.;length=math.hypot(dx,dy) or abs(e)
            stop_value=0
        counts=(round((dx+dy)*640),round((dx-dy)*640),action['steps'] if probe else round(dz*802),
                round(e*960)*(1 if context['tool'] else -1))
        master=max(map(abs,counts))
        if not master:return dict(f['snapshot'],ok=True,sent_steps=0)
        speed=action['speed']
        requested_rate=max(1,math.ceil(Z_STEPS_PER_MM*speed if probe else master*speed/length))
        fast_xy=(p['speed_profile']=='OEM' and not home and (counts[0] or counts[1])
                 and requested_rate>MAX_MASTER_RATE)
        if fast_xy and (not f['session'] or requested_rate>FACTORY_MOTION_MAX_RATE):
            raise ValueError('OEM XY segment exceeds the owned MCU motion envelope')
        rate=requested_rate if fast_xy else min(MAX_MASTER_RATE,requested_rate)
        maximum=math.ceil(master*1000./rate)+5000
        if maximum>600000:raise ValueError('Factory segment exceeds 600 seconds')
        row=self._motion_query(1,counts,rate,2 if fast_xy else 10,
            maximum,mask,stop_value,owner=context)
        if self.load_test_context is not context:raise ValueError('Factory load was stopped')
        if row['reject']:raise ValueError(row['reject'])
        f['move']=dict(action=action,identity=(row['run'],row['token']),counts=counts,
            accounted=0,deadline=self.reactor.monotonic()+maximum/1000.+.5,
            target=[x,y,z],home=home,mask=mask,start=self.reactor.monotonic())
        if action['kind']=='feed_until_edges':
            f['move'].update(pulse_base=action['pulse_base'],
                last_pulse=action['pulse_base'],last_edge=None)
        leg=action.get('leg')
        if leg:
            f['motion_legs'][leg]=dict(start=pos[:2],target=[x,y],
                requested_speed=action['speed'],effective_speed=round(rate*length/master,6),
                rate=rate,reason=row['reason'],drivers=row['drivers'])
        if action['kind']=='retract_until_quiet':
            if action.get('reset_quiet') or 'unload_quiet' not in f:
                f['unload_quiet']=[f['snapshot']['pulses'],self.reactor.monotonic()]
        return None

    def _factory_account(self,context,row):
        f=context['factory'];m=f['move']
        if row['reject'] or (row['run'],row['token'])!=m['identity']:
            raise ValueError('Factory motion ownership changed')
        for i,name in enumerate(('A','B','Z','E')):
            if row['drivers'][name]['planned']!=abs(m['counts'][i]):
                raise ValueError('Factory driver plan changed')
        sent=row['drivers']['E']['sent'];delta=sent-m['accounted']
        if delta<0:raise ValueError('Factory E counter moved backward')
        m['accounted']=sent
        if delta:
            direction=int(m['counts'][3]>0)
            context['measurement']['dir%d_steps'%direction]+=delta
            steps=delta if direction==context['tool'] else -delta
            f['e_sent_steps']+=steps
            phase=f['phase'];by_phase=f['e_steps_by_phase']
            by_phase[phase]=by_phase.get(phase,0)+steps
        context['motion']=row
        leg=m['action'].get('leg')
        if leg:
            f['motion_legs'][leg].update(reason=row['reason'],drivers=row['drivers'])

    def _factory_failed_motion(self,context,reason,details):
        # A factory motion fault clears the heater output before the next
        # host tick. Read after STOP so a fault between polls is visible too.
        move=context['factory']['move']
        try:
            row=context.get('motion') or {}
            if (row.get('run'),row.get('token'))!=move['identity'] or row.get('phase')!='failed':
                row=self._motion_query()
            if (self.load_test_context is not context or row['reject']
                    or (row['run'],row['token'])!=move['identity'] or row['phase']!='failed'):
                return reason
            self._factory_account(context,row)
        except Exception:
            logging.debug('Factory terminal motion readback unavailable',exc_info=True)
            return reason
        if row['reason']=='timing_fault':
            try:
                timing=self._motion_timing()
                if timing is not None:
                    row=dict(row,last_fast_segment=timing)
                    context['motion']=row
            except Exception:
                logging.debug('Factory motion timing readback unavailable',exc_info=True)
        details['motion']=row
        primary='Factory motion stopped: '+row['reason']
        if reason!=primary:details['secondary_error']=reason
        return primary

    def _factory_poll_move(self,context):
        from .inventure_factory_core import UINT32, delta32, uses_r0323_sequence
        f=context['factory'];m=f['move'];a=m['action']
        row=self._motion_query();self._factory_account(context,row)
        if row['phase']=='failed':raise ValueError('Factory motion stopped: '+row['reason'])
        if a['kind']=='z_contact_move':
            from .inventure_z_home import Z_STEPS_PER_MM
            if row['active']:
                if self.reactor.monotonic()>=m['deadline']:raise ValueError('Z motion deadline exceeded')
                return None
            sent=row['drivers']['Z']['sent']
            f['position'][2]+=(sent if m['counts'][2]>0 else -sent)/Z_STEPS_PER_MM
            result=dict(reason=row['reason'],hit=row['hit'],sent_steps=sent)
            f['last_z_move']=result
            f['move']=None
            if row['hit']&16:raise ValueError('Tray input opened during Z reference')
            result['end_raw']=self._z_home_inputs({})
            if self.load_test_context is not context:raise ValueError('Z reference was stopped')
            return result
        # Sample after a completed move. An edge at the last E step can
        # arrive after the tick's initial lease renewal.
        snap=self._factory_session(context) if not row['active'] and not m['home'] else f['snapshot']
        now=self.reactor.monotonic()
        stop=False;ok=False;kind=a['kind']
        if kind=='feed_until_edges':
            pulses=snap['pulses'];age=snap['pulse_age_ms']/1000.
            if age>a['quiet_s']:
                m.update(pulse_base=pulses,last_edge=None)
            elif pulses!=m['last_pulse']:
                edge=(snap.get('#receive_time') or now)-age
                if m['last_edge'] is not None and edge-m['last_edge']>a['quiet_s']:
                    # A gap can fall between two polls. Credit the newest
                    # edge and start a new series without assuming where the
                    # other edges in this sample occurred.
                    m['pulse_base']=(pulses-1)&UINT32
                m['last_edge']=edge
            m['last_pulse']=pulses
            ok=delta32(pulses,m['pulse_base'])>=a['count'];stop=ok
        elif kind=='retract_until_quiet':
            quiet=f['unload_quiet']
            if snap['pulses']!=quiet[0]:quiet[:]=[snap['pulses'],now-snap['pulse_age_ms']/1000.]
            ok=m['accounted']>=a.get('min_steps',0) and now-quiet[1]>=a['quiet_s'];stop=ok
        elif kind=='retract_until_a':
            ok=not snap['a'];stop=ok or now-m['start']>=a['timeout_s']
        if stop and row['active']:
            row=self._motion_query(2,owner=context);self._factory_account(context,row)
        if row['active']:
            if now>=m['deadline']:raise ValueError('Factory motion deadline exceeded')
            return None
        if m['home']:
            if row['reason']!='stop_input' or not row['hit']&m['mask']:
                message=('failed to release its limit' if kind=='home_release'
                         else 'failed to reach its limit')
                raise ValueError('Factory '+a['axis']+' homing '+message)
            released_xy=uses_r0323_sequence(context['tool'],f['parameters']['speed_profile'])
            if not released_xy or a['axis']=='Z' or kind=='home_release':
                f['homed'].append(a['axis']);f['position']['XYZ'.index(a['axis'])]=0.
                f['reference_valid']=len(set(f['homed']))==3
            ok=True
        elif row['reason']=='completed':
            if any(row['drivers'][n]['sent']!=abs(m['counts'][i]) for i,n in enumerate(('A','B','Z','E'))):
                raise ValueError('Factory move completed with missing steps')
            f['position'][:]=m['target']
            if kind=='move':ok=True
        elif not stop or row['reason']!='operator_stop':
            raise ValueError('Factory motion stopped: '+row['reason'])
        f['move']=None
        return dict(snap,ok=ok,sent_steps=m['accounted'])

    def _factory_action(self,context,action):
        f=context['factory'];now=self.reactor.monotonic();kind=action['kind']
        if kind=='module':
            name=action['name']
            if action['state']=='start':f['module_stack'].append(name)
            elif action['state']=='done' and f['module_stack'] and f['module_stack'][-1]==name:
                f['module_stack'].pop()
                if name=='select_tool':f['selection_commanded']=True
                if name=='clean':f['cleaning_done']=True
            else:raise ValueError('Factory module sequence is invalid')
            f['module_history'].append(dict(name=name,state=action['state'],time_s=round(now-f['started'],3)))
            f['module_history'][:]=f['module_history'][-64:]
            return dict(f['snapshot'])
        if kind=='phase':
            f['phase']=action['name'];f['history'].append(action['name'])
            if f['operation']=='AUTO' and action['name'].startswith('U'):f['fallback_started']=True
            self._load_test_emit(context);return dict(f['snapshot'])
        if kind=='z_progress':
            f['z_home_progress']={key:value for key,value in action.items() if key!='kind'}
            return dict(f['snapshot'])
        if kind=='z_contact_edge':return self._factory_z_edge(context,action)
        if kind=='z_verify_open':
            if now<action.setdefault('until',now+action['delay_s']):return None
            return self._z_home_inputs({'z_bottom':0})
        if kind=='heat':
            if not f['heat_started']:
                temperature,state=self.panel_temperature(context['tool'],now)
                context.update(factory_preparing=False,start=now,start_temperature=temperature,
                    deadline=min(now+300.,f['total_deadline']))
                context['pid'].update(time=now,temperature=temperature,derivative=0.,integral=0.)
                f['heat_started']=True
            self._load_test_readiness(context,self.panel_temperature(context['tool'],now)[0],now)
            return dict(f['snapshot']) if context['ready_latched'] else None
        if kind=='mode':return self._factory_session(context,action['mode'],action['monitor'])
        if kind in ('home','home_release','move','z_contact_move'):
            return self._factory_poll_move(context) if f['move'] is not None else self._factory_begin_move(context,action)
        if kind in ('wait_a','wait_unload'):
            if 'until' not in action:action['until']=now+action['timeout_s']
            snap=f['snapshot'];expired=now>=action['until']
            ok=(bool(snap['a']) and not expired) if kind=='wait_a' else not snap['a'] or bool(snap['b'])
            return dict(snap,ok=ok) if ok or expired else None
        if kind in ('feed_until_edges','retract_until_quiet','retract_until_a'):
            if f['move'] is not None:return self._factory_poll_move(context)
            if kind=='retract_until_a' and not f['snapshot']['a']:
                return dict(f['snapshot'],ok=True,sent_steps=0)
            mm=action.get('mm',action.get('timeout_s',0)*action['speed'])
            action=dict(action,e=mm if kind=='feed_until_edges' else -mm)
            return self._factory_begin_move(context,action)
        if kind=='done':
            if f['module_stack']:raise ValueError('Factory procedure left a module unfinished')
            if 'z_home_settings' in f:
                self._z_home_inputs({'nozzle_contact':1,
                    'z_bottom':int(f['operation']=='Z_PREPARE')})
                if self.load_test_context is not context:return None
            f['result']=dict(action);f['cleaning_done']=bool(action.get('cleaning_done'))
            self._load_test_cancel('factory_load_completed' if f['operation']=='AUTO'
                else 'factory_procedure_completed',phase='done');self._control_abort()
            return None
        raise ValueError('Unknown factory action '+kind)

    def _factory_tick(self,context):
        f=context['factory'];now=self.reactor.monotonic()
        if now>=f['total_deadline']:raise ValueError('Factory procedure timeout')
        temperature=self._load_test_safety(context)
        if 'z_home_settings' in f and context['ready_latched']:
            if not context['target']-10.<=temperature<=context['target']+8.:
                raise ValueError('Nozzle left the Z reference temperature band')
        if context['output_token'] is None:self._load_test_output(context,0)
        self._load_test_check_output(context)
        self._factory_session(context)
        if f['heat_started']:
            duty=self._load_test_pid(context,temperature,now)
            if duty or context['duty']:self._load_test_output(context,duty)
        for _ in range(12):
            if self.load_test_context is not context:return
            if f['action'] is None:
                try:f['action']=f['program'].send(f['resume'])
                except StopIteration:raise ValueError('Factory program ended without terminal result')
                f['resume']=None
            result=self._factory_action(context,f['action'])
            if self.load_test_context is not context:return
            if result is None:break
            f['resume']=result;f['action']=None
        if now>=context['next_report']:
            context['next_report']=now+5.;self._load_test_emit(context)

    def cmd_load_test(self,gcmd):
        action=str(gcmd.get('ACTION','STATUS')).upper()
        if action in ('AUTO','PARAMS'):
            return self._factory_start(gcmd,read_only=action=='PARAMS')
        if action not in ('HEAT','LOAD','RETRACT','JOG','ZERO','BASELINE','COMPARE','CONFIRM','STATUS','STOP'):
            raise gcmd.error('Use ACTION=AUTO, PARAMS, HEAT, LOAD, RETRACT, JOG, ZERO, BASELINE, COMPARE, CONFIRM, STATUS or STOP')
        context=getattr(self,'load_test_context',None)
        if action=='STOP':
            self._load_test_cancel('operator_stop')
            self._control_abort()
            gcmd.respond_info('V9_LOAD_TEST '+json.dumps(self.load_test_last,sort_keys=True));return
        if action=='STATUS':
            result=self._load_test_report(context) if context is not None else getattr(self,'load_test_last',{'phase':'idle'})
            gcmd.respond_info('V9_LOAD_TEST '+json.dumps(result,sort_keys=True));return
        if action in ('HEAT','LOAD','RETRACT'):
            if context is not None:raise gcmd.error('Load test already active; use STATUS or STOP')
            tool=str(gcmd.get('TOOL','T0')).upper()
            if tool not in ('T0','T1'):raise gcmd.error('TOOL must be T0 or T1')
            plan=None
            if action in ('LOAD','RETRACT'):
                if action=='RETRACT' and any(gcmd.get(k,None) is not None
                        for k in ('TUBE_SECONDS','HEAD_STEPS','PURGE_STEPS')):
                    raise gcmd.error('RETRACT uses STEPS; remove TUBE_SECONDS, HEAD_STEPS and PURGE_STEPS')
                try:
                    tube_seconds=0. if action=='RETRACT' else float(gcmd.get('TUBE_SECONDS',50.))
                    if not math.isfinite(tube_seconds) or not 0<=tube_seconds<=FEEDER_RUN_MAX_S:raise ValueError()
                except (TypeError,ValueError):raise gcmd.error('TUBE_SECONDS must be 0..50 motor seconds')
                head_steps=(gcmd.get_int('STEPS',128850,minval=1,maxval=192000) if action=='RETRACT'
                    else gcmd.get_int('HEAD_STEPS',95000,minval=1,maxval=192000))
                purge_steps=0 if action=='RETRACT' else gcmd.get_int('PURGE_STEPS',0,minval=0,maxval=96000)
                rate=gcmd.get_int('RATE',LOAD_CASSETTE_RATE,minval=960,maxval=LOAD_CASSETTE_RATE)
                smooth=gcmd.get_int('SMOOTH',1 if action=='RETRACT' else int('load_stream' in self.commands),minval=0,maxval=1)
                if action=='RETRACT' and not smooth:raise gcmd.error('RETRACT requires SMOOTH=1 and the R6 MCU')
                if smooth and 'load_stream' not in self.commands:
                    raise gcmd.error('SMOOTH=1 requires the RC8-CONTROL-R6-SMOOTH MCU application')
                if smooth and rate!=LOAD_CASSETTE_RATE:
                    raise gcmd.error('Continuous paired loading uses RATE=1536 to match the fixed cassette speed')
                tube_steps=round(tube_seconds*LOAD_CASSETTE_RATE)
                plan=dict(tube_seconds=tube_seconds,tube_steps=tube_steps,head_steps=head_steps,
                    purge_steps=purge_steps,total_steps=tube_steps+head_steps+purge_steps,sent=0,
                    rate=rate,smooth=bool(smooth),operation='retract' if action=='RETRACT' else 'load',
                    # r0323 0x08012458/0x08012474 invert PE8 for T1.
                    # Cassette DIR1 is feed for either channel, independently.
                    direction=int(tool[1])^int(action=='RETRACT'),
                    channel=int(tool[1]),cassette_direction=int(action!='RETRACT'),
                    started=False,feed_completed=False,
                    feeder_windows_requested=0,feeder_ms_requested=0)
            try:
                target=float(gcmd.get('TARGET',230.));duration=float(gcmd.get('DURATION',LOAD_HOLD_MAX_S))
                power=float(gcmd.get('MAX_POWER',40.))
                if not math.isfinite(target) or not 180<=target<=240:raise ValueError()
                if not math.isfinite(duration) or not 10<=duration<=LOAD_HOLD_MAX_S:raise ValueError()
                if not math.isfinite(power) or not 1<=power<=50:raise ValueError()
            except (TypeError,ValueError):
                raise gcmd.error('TARGET must be 180..240C, MAX_POWER 1..50 percent, DURATION 10..900 seconds of hold')
            if plan is not None:
                windows=math.ceil(plan['total_steps']/(plan['rate']*LOAD_JOG_WINDOW_S))+2
                margin=5. if plan['smooth'] else windows*.5+30.
                if duration<plan['total_steps']/plan['rate']+margin:
                    raise gcmd.error('DURATION is too short for this cassette feed; use DURATION=900')
            try:
                self._feeder_service_preflight()
                baseline=self._cassette_gpio_snapshot()
                temperature,state=self.panel_temperature(int(tool[1]),self.reactor.monotonic())
                if state!='valid' or temperature>=target+10:raise ValueError('Nozzle is not ready for controlled heating')
            except Exception as exc:raise gcmd.error(str(exc))
            now=self.reactor.monotonic()
            context=dict(tool=int(tool[1]),target=target,power=power,limit=round(target+20),
                start=now,deadline=now+300.,hold_duration=duration,hold_started=None,start_temperature=temperature,
                duty=0,output_token=None,ready_since=None,ready_latched=False,reached_target=False,
                pid=dict(time=now,temperature=temperature,derivative=0.,integral=0.),
                fan_counter=self.status['p0'],fan_deadline=now+10.,phase='heating',
                flow_confirmed=False,baseline=baseline,comparison=None,motion=None,
                jog=None,last_jog=None,next_report=now+5.,busy=False,flow_mark=None,
                measurement=dict(dir0_steps=0,dir1_steps=0,completed_jogs=0,zeroed_by_operator=False),
                cassette_load=plan,stream=None,last_stream=None)
            self.load_test_context=context
            context['timer']=self.reactor.register_timer(lambda t:self._load_test_tick(context,t),now+.25)
            self._load_test_emit(context);return
        if context is None:raise gcmd.error('Start INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 or TOOL=T1 TARGET=230 first')
        if context.get('factory') is not None:
            raise gcmd.error('Factory AUTO owns the printer; use STATUS, PARAMS or STOP')
        deadline=self.reactor.monotonic()+1.
        while context['busy'] and self.load_test_context is context and self.reactor.monotonic()<deadline:
            self.reactor.pause(min(deadline,self.reactor.monotonic()+.025))
        if self.load_test_context is not context:raise gcmd.error('Load test was stopped')
        if context['busy']:raise gcmd.error('Load test communication is busy; use STATUS or STOP')
        if context.get('cassette_load') is not None and not context['cassette_load']['feed_completed']:
            raise gcmd.error('Cassette load is heating or feeding; use STATUS or STOP')
        if context.get('jog') is not None:raise gcmd.error('Wait for jog_done before another load-test action')
        if action=='JOG':
            steps=gcmd.get_int('STEPS',960,minval=1,maxval=19200)
            direction=gcmd.get_int('DIR',minval=0,maxval=1)
            axis_timer=gcmd.get_int('TIMER',0,minval=0,maxval=1)==1
            cap=self._axis_timer_rate_limit(gcmd,(0,0,0,steps)) if axis_timer else 4800
            rate=gcmd.get_int('RATE',960,minval=160,maxval=cap)
            if steps/rate>10.:raise gcmd.error('One extruder jog must take at most 10 seconds')
            temperature,state=self.panel_temperature(context['tool'],self.reactor.monotonic())
            if not self._load_test_jog_ready(context,temperature,state):
                raise gcmd.error('Wait for ready_for_jog=true at the requested nozzle temperature')
        elif action=='CONFIRM' and gcmd.get_int('FLOW',minval=1,maxval=1)!=1:
            raise gcmd.error('Use FLOW=1 only after observing material leave the selected nozzle')
        context['busy']=True
        try:
            temperature=self._load_test_safety(context)
            if action=='JOG':
                if not self._load_test_jog_ready(context,temperature,'valid'):
                    raise gcmd.error('Wait for ready_for_jog=true at the requested nozzle temperature')
                self._load_test_capture(context,baseline=True)
                self._load_test_safety(context)
                now=self.reactor.monotonic();windows=math.ceil(steps/max(1,int(rate*LOAD_JOG_WINDOW_S)))
                context['jog']=dict(identity=None,steps=steps,sent=0,direction=direction,rate=rate,
                    started=now,windows=0,deadline=now+steps/rate+windows*.5+30.,axis_timer=axis_timer)
                context['phase']='jogging'
                self._load_test_step(context)
                self._control_schedule()
            elif action in ('BASELINE','COMPARE'):
                self._load_test_capture(context,baseline=action=='BASELINE')
            elif action=='ZERO':
                context['measurement']=dict(dir0_steps=0,dir1_steps=0,completed_jogs=0,zeroed_by_operator=True)
                context['flow_mark']=None;context['flow_confirmed']=False;context['last_jog']=None
            else:
                context['flow_mark']={k:v for k,v in self._load_test_measurement(context).items() if k!='flow_mark'}
                context['flow_confirmed']=True
                context['phase']='flow_confirmed_cleaning_pending'
            if self.load_test_context is context:self._load_test_emit(context)
        except Exception as exc:
            if self.load_test_context is context:
                self._load_test_cancel(str(exc),phase='failed');self._control_abort()
            raise gcmd.error(str(exc))
        finally:context['busy']=False

    def _require_permission(self, gcmd):
        if getattr(self,'load_test_context',None) is not None:
            raise gcmd.error('Load test owns the actuators; use INVENTURE_STOP first')
        if 'control' in self.commands:
            try:
                state=self._control_query()
                if state['mode'] in ('SERVICE','NATIVE') and not state['fault'] and not state['latched']:return
            except Exception as exc:raise gcmd.error(str(exc))
            raise gcmd.error('Select INVENTURE_CONTROL MODE=SERVICE or NATIVE first')
        if not self.allow_actuators:
            raise gcmd.error('Actuator tests disabled: allow_actuators=False. Bench qualification is required.')
    def cmd_arm(self, gcmd):
        self._require_permission(gcmd)
        if gcmd.get('ACK','') != 'V9_BENCH_VERIFIED':
            raise gcmd.error('Explicit ACK=V9_BENCH_VERIFIED required')
        self.commands['heartbeat'].send([])
        self.commands['arm'].send([0x56394c42])
    def cmd_uart(self, gcmd):
        rfid=self.printer.lookup_object('inventure_rfid',None)
        if rfid is not None and rfid.enabled:raise gcmd.error('RFID UARTs belong to P12; raw sends refused')
        i = gcmd.get_int('CHANNEL', minval=0, maxval=1)
        try: data = bytes.fromhex(gcmd.get('HEX'))
        except ValueError: raise gcmd.error('HEX must be hexadecimal bytes')
        if len(data)>48: raise gcmd.error('At most 48 bytes per request; higher-level framing is separate')
        self.commands['uart'].send([i,data])
    def cmd_feeder(self, gcmd):
        i=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        direction=gcmd.get_int('DIRECTION',1,minval=0,maxval=1)
        duration=gcmd.get_int('DURATION_MS',0,minval=0,maxval=1500)
        if not duration and self._load_test_feeder_stop(i):return
        if duration:self._require_permission(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.enabled:raise gcmd.error('P13 owns feeder transport; use dedicated filament commands')
        if self.connected:self.commands['heartbeat'].send([])
        self.commands['feeder'].send([i,direction,duration])

    def _feeder_service_preflight(self,allow_run=False,allow_active=False,allow_load=False):
        if not self.connected:raise ValueError('MCU is disconnected')
        sd=self.printer.lookup_object('virtual_sdcard',None)
        stats=self.printer.lookup_object('print_stats',None)
        if (sd is not None and sd.is_active() or stats is not None
                and stats.get_status(self.reactor.monotonic()).get('state') in ('printing','paused')):
            raise ValueError('Service transport requires an idle printer; finish the native print first')
        if getattr(self,'load_test_context',None) is not None and not allow_load:
            raise ValueError('Load test is active; use INVENTURE_STOP first')
        if getattr(self,'feeder_run_context',None) is not None and not allow_run:
            raise ValueError('Feeder run is active; use INVENTURE_STOP first')
        state=self._control_query()
        if state['mode']!='SERVICE' or state['latched'] or state['fault'] or state['reject']:
            raise ValueError('Select INVENTURE_CONTROL MODE=SERVICE first')
        if state['active'] and not allow_active:
            raise ValueError('Stop the active service output or motion before testing a feeder')
        ops=self.printer.lookup_object('inventure_operations',None)
        if ops is not None:
            operation=getattr(ops,'state',None)
            if (operation is None or getattr(operation,'mode',None)!='idle'
                    or getattr(operation,'owner',None) is not None
                    or getattr(operation,'fatal',None) is not None
                    or getattr(ops,'cancel_in_progress',False)):
                raise ValueError('Feeder service test requires idle operations with no owner or fault')
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and (getattr(filament,'running',False)
                or getattr(filament,'assisting',False) or getattr(filament,'configuring',False)
                or getattr(filament,'jog_until',0)>self.reactor.monotonic()):
            raise ValueError('Finish or cancel the active filament procedure before a service test')
        return state

    def _load_test_feeder_stop(self,channel=None):
        context=getattr(self,'load_test_context',None)
        if context is not None and context.get('factory') is not None:
            if channel is not None and channel!=context['tool']:return False
            self._load_test_cancel('operator_feeder_stop');self._control_abort();return True
        if context is None or context.get('cassette_load') is None:return False
        if channel is not None and channel!=context['cassette_load']['channel']:return False
        self._load_test_cancel('operator_feeder_stop')
        self._control_abort()
        return True

    def cmd_feeder_test(self,gcmd):
        # The legacy INVENTURE_FEEDER command is intercepted by the global
        # operation owner. This bounded service entrypoint owns its own checks
        # and never enters load/unload or the native automatic P13 transport.
        channel=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        action=str(gcmd.get('ACTION','START')).upper()
        if action not in ('START','STOP'):raise gcmd.error('ACTION must be START or STOP')
        direction=0 if action=='STOP' else gcmd.get_int('DIRECTION',0,minval=0,maxval=1)
        duration=0 if action=='STOP' else gcmd.get_int('DURATION_MS',100,minval=0,maxval=1500)
        if not duration and self._load_test_feeder_stop(channel):return
        if duration:
            try:self._feeder_service_preflight()
            except Exception as exc:raise gcmd.error(str(exc))
        else:
            context=getattr(self,'feeder_run_context',None)
            if context is not None and context['channel']==channel:
                self._feeder_run_cancel('operator_stop')
        if self.connected:self.commands['heartbeat'].send([])
        self.commands['feeder'].send([channel,direction,duration])
        self.feeder_test_pending_until=self.reactor.monotonic()+duration/1000. if duration else None
        gcmd.respond_info('V9_FEEDER_TEST '+json.dumps({'channel':channel,'direction':direction,
            'duration_ms':duration,'phase':'requested' if duration else 'stop_requested',
            'physical_motion_confirmed':False},sort_keys=True))

    def _feeder_run_report(self,context,phase='running',reason=None):
        return dict(phase=phase,reason=reason,channel=context['channel'],direction=context['direction'],
            elapsed_s=round(max(0.,self.reactor.monotonic()-context['started']),3),
            requested_motor_s=context['total_ms']/1000.,
            completed_windows_s=context['completed_ms']/1000.,
            remaining_requested_s=(context['total_ms']-context['completed_ms'])/1000.,
            windows_sent=context['windows'],maximum_mcu_window_ms=1500,
            maximum_duration_s=FEEDER_RUN_MAX_S,
            filament_before=context['initial']['filament'],
            physical_motion_confirmed=False)

    def _feeder_run_cancel(self,reason,phase='stopped',stop=True):
        context=getattr(self,'feeder_run_context',None)
        if context is None:return
        self.feeder_run_context=None
        self.reactor.unregister_timer(context['timer'])
        if stop and self.connected:
            try:self.commands['feeder'].send([context['channel'],0,0])
            except Exception:logging.exception('Feeder STOP unavailable; MCU timer remains bounded')
        self.feeder_run_last=self._feeder_run_report(context,phase,reason)
        self.gcode.respond_info('V9_FEEDER_RUN '+json.dumps(self.feeder_run_last,sort_keys=True))

    def _feeder_run_start_window(self,context):
        self._feeder_service_preflight(allow_run=True)
        if self.feeder_run_context is not context:return
        ms=min(1500,context['total_ms']-context['completed_ms'])
        self.commands['heartbeat'].send([])
        self.commands['feeder'].send([context['channel'],context['direction'],ms])
        context['window_ms']=ms;context['windows']+=1
        context['window_end']=self.reactor.monotonic()+ms/1000.

    def _feeder_run_tick(self,context,eventtime):
        if self.feeder_run_context is not context:return self.reactor.NEVER
        try:
            state=self._feeder_service_preflight(allow_run=True,allow_active=True)
            if self.feeder_run_context is not context:return self.reactor.NEVER
            now=self.reactor.monotonic()
            if now>=context['window_end'] and not state['active']:
                context['completed_ms']+=context['window_ms']
                if context['completed_ms']>=context['total_ms']:
                    current=self._cassette_gpio_snapshot()
                    if self.feeder_run_context is not context:return self.reactor.NEVER
                    report=self._feeder_run_report(context,'done','completed')
                    report.update(filament_before=context['initial']['filament'],
                        filament_after=current['filament'],
                        **self._cassette_difference(context['initial'],current))
                    self.feeder_run_context=None;self.feeder_run_last=report
                    self.reactor.unregister_timer(context['timer'])
                    self.gcode.respond_info('V9_FEEDER_RUN '+json.dumps(report,sort_keys=True))
                    return self.reactor.NEVER
                self._feeder_run_start_window(context)
            elif now>context['window_end']+.75:
                raise ValueError('MCU feeder owner did not become idle after its window')
            if self.feeder_run_context is not context:return self.reactor.NEVER
            if now>=context['next_progress']:
                self.feeder_run_last=self._feeder_run_report(context)
                self.gcode.respond_info('V9_FEEDER_RUN '+json.dumps(self.feeder_run_last,sort_keys=True))
                context['next_progress']=now+5.
            return self.reactor.monotonic()+.1
        except Exception as exc:
            if self.feeder_run_context is context:self._feeder_run_cancel(str(exc),phase='failed')
            return self.reactor.NEVER

    def cmd_feeder_run(self,gcmd):
        action=str(gcmd.get('ACTION','START')).upper()
        if action not in ('START','STATUS','STOP'):
            raise gcmd.error('ACTION must be START, STATUS or STOP')
        if action=='STOP' and self._load_test_feeder_stop():return
        if action=='STOP' and getattr(self,'feeder_run_context',None) is not None:
            self._feeder_run_cancel('operator_stop');return
        if action!='START':
            context=getattr(self,'feeder_run_context',None)
            result=self._feeder_run_report(context) if context is not None else getattr(self,'feeder_run_last',{'phase':'idle'})
            gcmd.respond_info('V9_FEEDER_RUN '+json.dumps(result,sort_keys=True));return
        channel=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        direction=gcmd.get_int('DIRECTION',1,minval=0,maxval=1)
        try:
            seconds=float(gcmd.get('DURATION',10.))
            if not math.isfinite(seconds) or not .001<=seconds<=FEEDER_RUN_MAX_S:
                raise ValueError('DURATION must be .001..50 seconds')
            self._feeder_service_preflight()
            initial=self._cassette_gpio_snapshot()
        except Exception as exc:raise gcmd.error(str(exc))
        now=self.reactor.monotonic()
        context=dict(channel=channel,direction=direction,total_ms=max(1,round(seconds*1000.)),
            completed_ms=0,windows=0,started=now,next_progress=now+5.,initial=initial)
        context['timer']=self.reactor.register_timer(lambda eventtime:self._feeder_run_tick(context,eventtime))
        self.feeder_run_context=context
        try:self._feeder_run_start_window(context)
        except Exception as exc:
            self._feeder_run_cancel(str(exc),phase='failed');raise gcmd.error(str(exc))
        if self.feeder_run_context is not context:return
        self.reactor.update_timer(context['timer'],self.reactor.monotonic()+.1)
        self.feeder_run_last=self._feeder_run_report(context)
        gcmd.respond_info('V9_FEEDER_RUN '+json.dumps(self.feeder_run_last,sort_keys=True))
    def cmd_lock(self, gcmd):
        i=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        value=gcmd.get_int('VALUE',minval=0,maxval=1)
        if value:self._require_permission(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.running and not filament.in_scope():raise gcmd.error('P13 owns lock state during filament procedure')
        if value and getattr(self,'door_policy_global',False):
            try: policy=self._door_policy_query()
            except Exception as exc:raise gcmd.error(str(exc))
            if policy['reject'] or not policy['enabled']:
                raise gcmd.error('Door locks are disabled by INVENTURE_DOOR_POLICY')
        if self.connected:self.commands['heartbeat'].send([])
        self.commands['lock'].send([i,value])

def load_config(config): return InventureMCU(config)
