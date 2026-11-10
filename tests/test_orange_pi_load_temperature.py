"""Real log replay plus delayed thermal plant tests of the service regulator."""
import bisect
import importlib.util
import json
import math
from pathlib import Path
import unittest
import test_orange_pi_load_test as load_support
from test_orange_pi_door_policy import adapter as mod, Command

FIXTURES=Path(__file__).with_name('fixtures')

def previous_class():
    spec=importlib.util.spec_from_file_location('door_test_host.previous_load230_r2',
        FIXTURES/'inventure_mcu_control_r5_load230_r2.py')
    previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
    return previous.InventureMCU

class ThermalPlant:
    """Model variants, not measured calibration of the physical printer.

    dT/dt = heater_gain * duty - heat_loss * (T-ambient), then sensor lag.
    ADC is quantized only at the feedback boundary, never in the plant state.
    """
    def __init__(self,h,gain=9.8,loss=.0125,lag=3.,ambient=26.75):
        self.h=h;self.gain=gain;self.loss=loss;self.lag=lag;self.ambient=ambient
        self.physical=self.measured=ambient;self.last=h.reactor.now;self.rows=[]
        self.original=h.refresh;h.refresh=self.refresh;self.refresh()
    def refresh(self):
        h=self.h;now=h.reactor.now;dt=now-self.last
        power=h.device.output(6)['duty']/256.
        steps=max(1,math.ceil(dt/.025));step=dt/steps
        for _ in range(steps):
            self.physical+=step*(self.gain*power-self.loss*(self.physical-self.ambient))
            self.measured+=step*(self.physical-self.measured)/self.lag
        raw=round(self.measured*4095./660.)
        h.raw_adc[0]=raw;h.temperatures[0]=raw*660./4095.
        self.last=now;self.original()
        self.rows.append((now,h.temperatures[0],power))

class TemperatureTests(unittest.TestCase):
    def host(self,old=False):
        h,ops,p13=load_support.LoadTests().host()
        if old:h.__class__=previous_class()
        return h
    def replay(self,h):
        rows=json.loads((FIXTURES/'load230_r2_thermal_observation.json').read_text())['samples']
        times=[row['elapsed_s'] for row in rows];refresh=h.refresh
        def feed():
            elapsed=h.reactor.now-100.;idx=bisect.bisect_right(times,elapsed)-1
            idx=max(0,min(idx,len(rows)-2));a,b=rows[idx:idx+2]
            weight=max(0.,min(1.,(elapsed-a['elapsed_s'])/(b['elapsed_s']-a['elapsed_s'])))
            temperature=a['temperature_c']+(b['temperature_c']-a['temperature_c'])*weight
            h.temperatures[0]=temperature;h.raw_adc[0]=round(temperature*4095./660.)
            refresh()
        h.refresh=feed;h.refresh()
        return rows

    def test_reported_temperature_ripple_rejects_old_jog_but_new_ready_remains_latched(self):
        for old in (True,False):
            h=self.host(old);self.replay(h)
            h.cmd_load_test(Command(ACTION='HEAT'))
            h.reactor.pause(200.)
            self.assertTrue(h.load_test_last['ready_for_jog'])
            # User's 09:02:11 command, ~127 s into the heat job.
            h.reactor.pause(227.)
            self.assertLess(h.temperatures[0],225.)
            if old:
                with self.assertRaisesRegex(RuntimeError,'ready_for_jog'):
                    h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=2880,RATE=960))
                self.assertEqual(load_support.LoadTests().starts(h,'motion'),[])
            else:
                self.assertTrue(h._load_test_jog_ready(h.load_test_context))
                h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=2880,RATE=960))
                self.assertEqual(len(load_support.LoadTests().starts(h,'motion')),1)

    def test_PID_settles_in_delayed_plant_variants_and_uses_intermediate_PWM(self):
        for gain,loss,lag in ((8.,.0125,4.),(9.8,.0125,3.),(12.,.009,1.),(9.8,.016,4.)):
            h=self.host();plant=ThermalPlant(h,gain,loss,lag)
            h.cmd_load_test(Command(ACTION='HEAT'));h.reactor.pause(550.)
            self.assertIsNotNone(h.load_test_context)
            self.assertTrue(h.load_test_last['ready_for_jog'])
            tail=[t for now,t,_ in plant.rows if now>=450.]
            self.assertLess(max(tail)-min(tail),2.)
            self.assertLess(abs(sum(tail)/len(tail)-230.),5.)
            self.assertLess(max(t for _,t,_ in plant.rows),238.)
            duties=[p[3] for p in load_support.LoadTests().starts(h)]
            self.assertTrue(any(0<d<102 for d in duties));self.assertTrue(all(d<=102 for d in duties))

    def test_PID_reduces_ripple_relative_to_previous_thermostat_in_same_plant(self):
        spans=[]
        for old in (True,False):
            h=self.host(old);plant=ThermalPlant(h,lag=4.)
            h.cmd_load_test(Command(ACTION='HEAT'));h.reactor.pause(550.)
            tail=[t for now,t,_ in plant.rows if now>=450.]
            spans.append(max(tail)-min(tail))
        self.assertGreater(spans[0],5.)
        self.assertLess(spans[1],spans[0]/3.)

    def test_ready_acquires_near_target_then_releases_outside_hot_jog_band(self):
        h=self.host();load_support.LoadTests().heat(h);context=h.load_test_context
        h.temperatures[0]=220.;h.reactor.pause(101.)
        self.assertFalse(h._load_test_jog_ready(context))
        load_support.LoadTests().ready(h)
        for temperature in (215.,224.,230.,237.9):
            h.temperatures[0]=temperature;h.refresh()
            self.assertTrue(h._load_test_jog_ready(context))
        h.temperatures[0]=214.9;h.reactor.pause(h.reactor.now+.25)
        self.assertFalse(h._load_test_jog_ready(context))
        h.temperatures[0]=220.;h.reactor.pause(h.reactor.now+.25)
        self.assertFalse(h._load_test_jog_ready(context))
        load_support.LoadTests().ready(h)
        h.temperatures[0]=238.1;h.reactor.pause(h.reactor.now+.25)
        self.assertFalse(h._load_test_jog_ready(context))

    def test_hot_jogs_recover_in_the_thermal_plant_without_integral_windup(self):
        h=self.host();plant=ThermalPlant(h)
        h.cmd_load_test(Command(ACTION='HEAT'));h.reactor.pause(300.)
        context=h.load_test_context;initial=context['pid']['integral']
        h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=2880,RATE=960))
        h.reactor.pause(302.)
        self.assertEqual(context['pid']['integral'],initial)
        self.assertEqual(h.device.output(6)['duty'],0)
        h.reactor.pause(306.)
        self.assertIsNotNone(h.load_test_context);self.assertIsNone(context['jog'])
        self.assertTrue(h._load_test_jog_ready(context))
        h.reactor.pause(340.)
        self.assertLess(abs(h.temperatures[0]-230.),5.)
        self.assertLess(max(t for _,t,_ in plant.rows),238.)

    def test_sampling_wait_does_not_require_the_user_to_retry_a_ready_jog(self):
        h=self.host();load_support.LoadTests().heat(h);load_support.LoadTests().ready(h)
        context=h.load_test_context;context['busy']=True
        def release(now):context['busy']=False;return h.reactor.NEVER
        h.reactor.register_timer(release,h.reactor.now+.1)
        h.cmd_load_test(Command(ACTION='JOG',DIR=0))
        self.assertEqual(len(load_support.LoadTests().starts(h,'motion')),1)

    def test_stop_while_waiting_for_sampling_never_starts_the_queued_jog(self):
        h=self.host();load_support.LoadTests().heat(h);load_support.LoadTests().ready(h)
        h.load_test_context['busy']=True
        def stop(now):h.cmd_control_stop(Command());return h.reactor.NEVER
        h.reactor.register_timer(stop,h.reactor.now+.1)
        with self.assertRaisesRegex(RuntimeError,'was stopped'):h.cmd_load_test(Command(ACTION='JOG',DIR=0))
        self.assertEqual(load_support.LoadTests().starts(h,'motion'),[])
        self.assertIsNone(h.load_test_context)

    def test_PID_output_is_bounded_and_reduces_power_for_a_rising_measurement(self):
        def context():
            return dict(target=230.,power=40.,pid=dict(time=0.,temperature=228.,derivative=0.,integral=0.))
        steady=mod.InventureMCU._load_test_pid(context(),228.,.25)
        rising=mod.InventureMCU._load_test_pid(context(),229.,.25)
        self.assertGreater(steady,rising);self.assertLess(steady,102)
        c=context()
        for n in range(1,100):
            duty=mod.InventureMCU._load_test_pid(c,25.,n*.25)
            self.assertTrue(0<=duty<=102)
        self.assertEqual(c['pid']['integral'],0.)
        self.assertEqual(mod.InventureMCU._load_test_pid(c,233.,26.),0)

if __name__=='__main__':unittest.main(verbosity=2)
