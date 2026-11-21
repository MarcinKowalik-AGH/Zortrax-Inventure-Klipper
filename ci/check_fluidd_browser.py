#!/usr/bin/env python3
"""Exercise the compiled production cards with simulated Moonraker updates."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
from threading import Thread

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orange_pi.inventure_oled_preview import encode_frame


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def check(dist):
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=dist))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            chrome = shutil.which('chromium') or shutil.which('google-chrome')
            browser = pw.chromium.launch(executable_path=chrome, args=['--no-sandbox'])
            page = browser.new_page(service_workers='block', viewport={'width': 1440, 'height': 1050}, locale='pl-PL')
            page.route_web_socket('**/websocket', lambda ws: None)
            page.goto('http://127.0.0.1:%d' % server.server_port)
            page.wait_for_function("document.querySelector('#app')?.__vue__?.$store")
            dashboard = './assets/'+next((dist/'assets').glob('Dashboard-*.js')).name
            frame = encode_frame(bytes(range(256))*32)
            page.evaluate('''async ([url, image]) => {
              const app = document.querySelector('#app').__vue__.$root
              const {components} = (await import(url)).default.options
              if (app.$i18n.locale !== 'en') throw Error('Unexpected default locale: '+app.$i18n.locale)
              if (components.PrinterStatusCard.options.components.InventureOledPreview) throw Error('OLED in print history')
              if (!components.TemperatureCard.options.components.InventureOledPreview) throw Error('OLED outside Thermals')
              const store = app.$store
              window.notify = (key, payload) => store.commit('printer/setSocketNotify', {key, payload})
              window.probes = []
              window.pendingWaits = new Map()
              window.cursor = 1
              window.remoteFrame = {display:'awake',online:true,sequence:1,source:'host-rendered',format:'indexed4-png',
                width:256,height:64,epoch:'a'.repeat(32), navigation_api:'NAV-EVENT-1',navigation_revision:1,image_base64:image}
              const original = app.$socket.emit.bind(app.$socket)
              app.$socket.emit = (method, options) => {
                if (method === 'printer.inventure.oled_preview.wait') {
                  probes.push(method)
                  if (options.params.navigation_revision !== cursor) return Promise.resolve({changed:true,frame:{...remoteFrame}})
                  return new Promise(resolve => { pendingWaits.set(options.params.client, resolve) })
                }
                if (method === 'printer.inventure.oled_preview.cancel_wait') {
                  probes.push(method); pendingWaits.get(options.params.client)?.({changed:false}); pendingWaits.delete(options.params.client)
                  return Promise.resolve({cancelled:true})
                }
                if (method === 'printer.objects.query') {
                  probes.push(method); return Promise.resolve({status:{inventure_oled_preview:{...remoteFrame}}})
                }
                return original(method,options)
              }
              const socketState = store.state.socket
              socketState.status='ready'
              socketState.acceptingNotifications=true
              store.state.charts.ready=true
              store.state.server.info.klippy_state = 'ready'
              store.state.server.info.klippy_connected = true
              const chamber = 'inventure_chamber chamber'
              const zones = ['heater_generic chamber_l', 'heater_generic chamber_r']
              const t1 = 'heater_generic inventure_t1'
              const keys = ['extruder',t1,...zones]
              const settings = {...store.state.printer.printer.configfile.settings,
                inventure_oled: {}, [chamber]: {max_temp:90},
                extruder: {heater_pin:'inventure_r5:HEATER_T0',max_temp:310,min_temp:0},
                [t1]: {heater_pin:'inventure_r5:HEATER_T1',max_temp:310,min_temp:0}}
              notify('configfile', {settings})
              notify('heaters',{available_heaters:keys,available_sensors:[...keys,chamber]})
              keys.forEach((key,i) => notify(key,{temperature:31+i,target:0,power:0}))
              notify(chamber,{temperature:27.5,left:28,right:32,target:45,power:.25,sensors_valid:true,
                zone_objects:zones,zone_targets:[45,45],split_targets:false})
              notify('inventure_oled_preview', {...remoteFrame})
              window.sent=[]
              const card = new components.TemperatureCard({parent:app})
              card.$mount()
              const section=document.createElement('article')
              section.id='test-thermal'; section.appendChild(card.$el)
              section.style.cssText='position:relative;z-index:20000;width:780px;background:#28292a'
              document.body.prepend(section)
              const findTargets = vm => typeof vm.handleHeaterRowClick === 'function' ? vm :
                vm.$children.map(findTargets).find(Boolean)
              const targets = findTargets(card)
              targets.sendGcode = command => window.sent.push(command)
              window.thermalCard = card
            }''', [dashboard,frame])
            thermal = page.locator('#test-thermal')
            labels = thermal.locator('td.temp-name .legend-item')
            expect(labels).to_have_text(['Extruder 0','Extruder 1','Chamber'])
            row = thermal.locator('tr').filter(has_text='Chamber')
            expect(row).to_contain_text('27.5')
            expect(row).to_contain_text('L 28.0° · R 32.0°')
            target = row.locator('input[type=number]')
            expect(target).to_have_value('45')
            target.fill('40')
            target.press('Enter')
            page.wait_for_function("window.sent.some(c => /^SET_HEATER_TEMPERATURE HEATER=[\x22']?chamber[\x22']? TARGET=40$/.test(c))")
            oled = thermal.get_by_role('region', name='OLED INVENTURE')
            expect(oled).to_be_visible()
            expect(oled.get_by_role('button', name='Set OLED zoom to 2 times')).to_have_attribute('aria-pressed','true')
            canvas = oled.get_by_role('img')
            expect(canvas).to_have_attribute('width','516')
            def first_pixel():
                return canvas.evaluate("c => Array.from(c.getContext('2d').getImageData(2,2,1,1).data).slice(0,3)")
            page.wait_for_function("document.querySelector('#test-thermal canvas.inventure-oled-frame')?.getContext('2d').getImageData(2,2,1,1).data[2] === 16")
            assert first_pixel() == [2,6,16]
            metrics = oled.evaluate("""e => {
              const c=e.querySelector('canvas').getBoundingClientRect(), p=e.getBoundingClientRect()
              const z=[...e.querySelectorAll('button[aria-pressed]')].map(b=>b.getBoundingClientRect())
              const chart=e.closest('article').querySelector('.chart')
              return [Math.abs((c.left+c.right)/2-(p.left+p.right)/2),z.every(b=>b.left>=c.right),
                z[0].top<z[1].top && z[1].top<z[2].top, chart && chart.getBoundingClientRect().bottom <= p.top+2]
            }""")
            assert metrics[0] <= 1 and all(metrics[1:]), metrics
            expect(oled).to_have_attribute('data-navigation','event')
            second = encode_frame(bytes([0xff])*8192)
            page.wait_for_function('pendingWaits.size > 0')
            page.evaluate("""img => {
                window.cursor++; Object.assign(remoteFrame, {navigation_revision:cursor,sequence:cursor,image_base64:img})
                const listeners = [...pendingWaits.values()]; pendingWaits.clear()
                for (const resolve of listeners) resolve({changed:true,frame:{...remoteFrame}})
            }""", second)
            page.wait_for_function("document.querySelector('#test-thermal canvas.inventure-oled-frame')?.getContext('2d').getImageData(2,2,1,1).data[2] === 254")
            assert first_pixel() == [22,161,254]
            oled.get_by_role('button', name='Set OLED zoom to 3 times').click()
            expect(canvas).to_have_attribute('width','772')
            assert page.evaluate("localStorage.getItem('inventure-oled-preview-zoom')") == '3'
            oled.get_by_role('button', name='Hide OLED preview').click()
            expect(canvas).to_have_count(0)
            page.wait_for_function("probes.includes('printer.inventure.oled_preview.cancel_wait')")
            page.evaluate("""() => { window.cursor++; Object.assign(remoteFrame, {display:'off',online:false,
                navigation_revision:cursor,sequence:cursor,image_base64:''}); notify('inventure_oled_preview',{...remoteFrame}) }""")
            oled.get_by_role('button', name='Show OLED preview').click()
            expect(oled).to_contain_text('Display asleep')
            page.evaluate("""() => { window.cursor++; Object.assign(remoteFrame,{display:'offline',sequence:cursor,navigation_revision:cursor});
                notify('inventure_oled_preview',{...remoteFrame}) }""")
            expect(oled).to_contain_text('Display offline')
            tune = './assets/'+next((dist/'assets').glob('Tune-*.js')).name
            page.evaluate("""async url => {
              const app = document.querySelector('#app').__vue__.$root
              const view = (await import(url)).default
              window.testCommands = []
              const card = new view({parent:app})
              card.sendGcode = cmd => testCommands.push(cmd)
              notify('inventure_mcu', {platform_level:{phase:'measuring', next_point:4, heights_steps:{0:401}}})
              const settings = {...app.$store.state.printer.printer.configfile.settings,
                inventure_mcu:{}, 'gcode_macro inventure_home_xy':{}, 'gcode_macro inventure_home_all':{}}
              notify('configfile',{settings})
              card.$mount()
              const section = document.createElement('article'); section.id = 'test-tune'
              section.appendChild(card.$el); document.body.prepend(section)
              window.testTune = card
            }""", tune)
            level = page.locator('#test-tune').get_by_label('Inventure platform leveling')
            expect(level).to_contain_text('Five-point platform leveling')
            expect(level.locator('[aria-current="step"]')).to_contain_text('Rear left')
            expect(level).to_contain_text('0.500 mm')
            assert page.evaluate('testCommands') == []
            page.evaluate("testTune.homeAxes('XY','test-home')")
            page.evaluate("testTune.homeAll()")
            assert page.evaluate('testCommands') == ['INVENTURE_HOME_XY', 'INVENTURE_HOME_ALL']
            page.evaluate("notify('inventure_mcu',{platform_level:{phase:'saved', candidate_depth_um:138349,center_error_steps:25}})")
            expect(level).to_contain_text('Platform depth 138.349 mm')
            expect(level).to_contain_text('Center deviation 0.031 mm')
            page.evaluate("notify('inventure_chamber chamber',{temperature:null,left:null,sensors_valid:false})")
            expect(row.locator('.temp-actual')).to_have_text('-')
            browser.close()
            print('PASS compiled Fluidd: Thermal card order, both nozzles and chamber, target, centered pixels, zoom toolbar, live navigation before subscription, cancel, sleep, offline, Inventure five-pad Tune view and configured home control')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('dist', type=Path)
    check(p.parse_args().dist.resolve())
