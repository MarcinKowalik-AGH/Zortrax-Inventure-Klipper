# Experimental V9 ABI3 board diagnostic/transport adapter, NOT complete printer logic.
# SPDX-License-Identifier: GPL-3.0-or-later
import bisect
import collections
import json
import logging
import math
from pathlib import Path
from .inventure_fans_core import validate_report

class InventureMCU:
    ADC_PINS = ('PC0', 'PF8', 'PC1', 'PC2', 'PA4', 'PF3', 'PF4', 'PC4')
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

        table_path = Path(__file__).with_name('inventure_lut.json')
        self.lut = json.loads(table_path.read_text())
        self.thresholds = [row[0] for row in self.lut]
        if self.diag_adc:
            pins = self.printer.lookup_object('pins')
            for i, pin in enumerate(self.ADC_PINS):
                adc = pins.setup_pin('adc', self.mcu_name + ':' + pin)
                adc.setup_adc_sample(0.1, sample_time=0.001, sample_count=3,
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
                               ('INVENTURE_VERSION',self.cmd_version)]:
            self.gcode.register_command(name, callback)
        self.timer = self.reactor.register_timer(self._tick)
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
        self.gcode.register_command('INVENTURE_COOLING_STATUS',self.cmd_cooling_status,when_not_ready=True)
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
            raw = int(normalized*4095+.5) if valid else None
            self.raw_adc[index] = raw
            self.adc_received[index] = received
            if not valid: temperature = float('nan')
            elif index < 2: temperature = normalized*660.
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
        bootloader=('InventureBoot 1.4 P4-PERSIST-r%d'%revision
                    if revision in (1,2) else 'unknown')
        gcmd.respond_info('INVENTURE_VERSION '+json.dumps({
            'mcu_release':self.mcu_release, 'bootloader':bootloader,
            'p4_baud':self.p4_boot.get('baud'),
            'host_baud':self.p4_boot.get('host_baud'),
            'baud_match':self.p4_boot.get('match'),
            'boot_identification':('P4-PERSIST marker recognized' if revision in (1,2)
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
