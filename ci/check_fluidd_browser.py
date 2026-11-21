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
            page = browser.new_page(service_workers='block')
            page.route_web_socket('**/websocket', lambda ws: None)
            page.goto('http://127.0.0.1:%d' % server.server_port)
            page.wait_for_function("document.querySelector('#app')?.__vue__?.$store")
            dashboard = './assets/'+next((dist/'assets').glob('Dashboard-*.js')).name
            frame = encode_frame(bytes(range(256))*32)
            page.evaluate('''async ([url, image]) => {
              const app = document.querySelector('#app').__vue__.$root
              const {components} = (await import(url)).default.options
              const {TemperatureTargets} = components.TemperatureCard.options.components
              const {InventureOledPreview} = components.PrinterStatusCard.options.components
              const store = app.$store
              window.notify = (key, payload) => store.commit('printer/setSocketNotify', {key, payload})
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
              notify('inventure_oled_preview',{display:'awake',image_base64:image})
              for (const [id,Ctor] of [['thermal',TemperatureTargets],['oled',InventureOledPreview]]) {
                const card = new Ctor({parent:app})
                if (id === 'thermal') {
                  window.sent = []
                  card.sendGcode = command => window.sent.push(command)
                }
                card.$mount()
                const section=document.createElement('article')
                section.id='test-'+id; section.appendChild(card.$el)
                section.style.cssText='position:relative;z-index:20000;background:white;color:black'
                document.body.prepend(section)
              }
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
            oled = page.locator('#test-oled')
            expect(oled).to_contain_text('OLED INVENTURE')
            expect(oled.locator('img')).to_have_attribute('src','data:image/png;base64,'+frame)
            page.wait_for_function("document.querySelector('#test-oled img')?.naturalWidth === 256")
            assert oled.locator('img').evaluate('(img) => img.naturalHeight === 64')
            oled.get_by_role('button',name='OLED zoom 1 times').click()
            expect(oled.locator('img')).to_have_attribute('width','512')
            page.evaluate("notify('inventure_oled_preview',{display:'off',image_base64:''})")
            expect(oled).to_contain_text('Display asleep')
            page.evaluate("notify('inventure_oled_preview',{display:'offline',image_base64:''})")
            expect(oled).to_contain_text('Display offline')
            page.evaluate("notify('inventure_chamber chamber',{temperature:null,left:null,sensors_valid:false})")
            expect(row.locator('.temp-actual')).to_have_text('-')
            browser.close()
            print('PASS compiled Fluidd: three rows, chamber target command, stale readings, OLED pixels, zoom, sleep and offline')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('dist', type=Path)
    check(p.parse_args().dist.resolve())
