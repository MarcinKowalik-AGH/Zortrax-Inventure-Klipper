# Experimental V9 ABI3 board diagnostic/transport adapter, NOT complete printer logic.
# SPDX-License-Identifier: GPL-3.0-or-later
import bisect
import collections
import json
import logging
import math
from pathlib import Path
import struct
from .inventure_fans_core import fan_test_result, normalized_tach, validate_report

HOST_REVISION = 'RC8-CHAMBER75-R3'

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
        self.gcode.register_command('INVENTURE_HEAT_TEST', self.cmd_t0_heat_test,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_HEAT_TEST_STATUS', self.cmd_t0_heat_status,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_HEAT_TEST_STOP', self.cmd_t0_heat_stop,
                                    when_not_ready=True)
        self.gcode.register_command('INVENTURE_SENSOR_TEST', self.cmd_sensor_test,
                                    when_not_ready=True)
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

    def _thermal_query_send(self, command, data, start=False):
        heartbeat=self.commands.get('heartbeat')
        if self.connected and heartbeat is not None:
            # The pinned Klipper wrapper enqueues this preface on the SAME
            # command queue before the read. It never restarts a heater window.
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
        self.inputs = {'raw_mask': params['raw'], 'buttons': params['buttons'],
                       'sequence': params['sequence'],
                       'raw': {name: (params['raw'] >> i) & 1
                               for i, name in enumerate(self.INPUT_NAMES)}}
        buttons, sequence = params['buttons'], params['sequence']
        self.reactor.register_async_callback(lambda eventtime:
            self.printer.send_event('inventure:buttons', eventtime, buttons, sequence))
    def _uart_status(self, params):
        channel=params['channel'];generation=self.adc_generation
        sample={k:v for k,v in params.items() if not k.startswith('#')}
        def deliver(now):
            if not self.adc_accepting or generation!=self.adc_generation or channel not in (0,1):return
            self.uart_status[channel]=sample
            self.printer.send_event('inventure:rfid_uart_status',channel,sample['errors'])
        self.reactor.register_async_callback(deliver)
    def cmd_inputs(self, gcmd):
        if self.connected: self.commands['inputs'].send([])
        gcmd.respond_info(json.dumps(self.inputs, sort_keys=True))
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
    def _require_permission(self, gcmd):
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
        self._require_permission(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.enabled:raise gcmd.error('P13 owns feeder transport; use dedicated filament commands')
        i=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        direction=gcmd.get_int('DIRECTION',1,minval=0,maxval=1)
        duration=gcmd.get_int('DURATION_MS',0,minval=0,maxval=1500)
        self.commands['feeder'].send([i,direction,duration])
    def cmd_lock(self, gcmd):
        self._require_permission(gcmd)
        filament=self.printer.lookup_object('inventure_filament',None)
        if filament is not None and filament.running and not filament.in_scope():raise gcmd.error('P13 owns lock state during filament procedure')
        i=gcmd.get_int('CHANNEL',minval=0,maxval=1)
        value=gcmd.get_int('VALUE',minval=0,maxval=1)
        self.commands['lock'].send([i,value])

def load_config(config): return InventureMCU(config)
