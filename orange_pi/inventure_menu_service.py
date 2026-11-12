# SPDX-License-Identifier: GPL-3.0-or-later
"""One Zortrax procedure owner interface for OLED and G-code/Fluidd.

The service routes intentions to existing Inventure owners. It never creates
qualification flags, native coordinates, RFID identities or print checkpoints.
"""
import json
import math
from pathlib import Path

from .inventure_menu_core import (ALL_ACTIONS, CHOICE_ACTIONS, MODEL_MATERIALS,
    NATIVE_ACTIONS, READ_ACTIONS, SERVICE_ACTIONS, SUPPORT_MATERIALS,
    MenuError, SettingsStore, View, source_identity)


class Command:
    def __init__(self, service, **values):self.service=service;self.values=values
    def get(self,key,default=None):return self.values.get(key,default)
    def get_int(self,key,default=None,minval=None,maxval=None):
        value=int(self.get(key,default))
        if minval is not None and value<minval or maxval is not None and value>maxval:raise MenuError('Invalid '+key)
        return value
    def get_float(self,key,default=None,minval=None,maxval=None,above=None,below=None):
        value=float(self.get(key,default))
        if (minval is not None and value<minval or maxval is not None and value>maxval
                or above is not None and value<=above or below is not None and value>=below):raise MenuError('Invalid '+key)
        return value
    def error(self,message):return MenuError(message)
    def respond_info(self,message):self.service.messages.append(str(message)[:1800]);self.service.messages[:]=self.service.messages[-8:]


class ZortraxMenuService:
    def __init__(self, board, config):
        self.board=board;self.printer=board.printer;self.reactor=board.reactor;self.gcode=board.gcode
        args=self.printer.get_start_args() if hasattr(self.printer,'get_start_args') else {}
        default=str(Path(args['config_file']).with_name('inventure_menu_settings.json')) if args.get('config_file') else None
        try:self.settings=SettingsStore(config.get('zortrax_settings_file',default))
        except (ValueError,OSError) as exc:raise config.error('Zortrax menu settings: '+str(exc))
        self.view=None;self.active=None;self.last=None;self.messages=[];self.stopped=False
        self.fan_session=None
        self.executing=False;self.last_buttons=0;self.last_sequence=None;self.last_native_key=None
        self.last_pause_view=None
        self.platform_step=float(config.get('zortrax_platform_step',1.))
        self.platform_speed=float(config.get('zortrax_platform_speed',1.))
        if not all(math.isfinite(v) and .1<=v<=5. for v in (self.platform_step,self.platform_speed)):
            raise config.error('Zortrax platform step/speed must be finite 0.1..5 mm and mm/s')
        self.timer=self.reactor.register_timer(self._tick)
        self.gcode.register_command('INVENTURE_ZORTRAX',self.cmd,when_not_ready=True)
        self.printer.register_event_handler('klippy:ready',self._ready)
        for event in ('klippy:shutdown','klippy:disconnect','gcode:request_restart'):
            self.printer.register_event_handler(event,self._shutdown)

    def _ready(self):
        oled=self._obj('inventure_oled')
        if oled is not None and getattr(oled,'oem_menu',None) is not None:
            oled.oem_menu.language='en' if getattr(oled,'menu_profile','m300')=='m300' else self.settings.value['language']
            oled.oem_menu.generation+=1
        self.reactor.update_timer(self.timer,self.reactor.monotonic()+.25)
    def _shutdown(self,*args):
        self.stopped=True;self.view=None;self.active=None;self.last_native_key=None;self.fan_session=None
        self.reactor.update_timer(self.timer,self.reactor.NEVER)
    def _obj(self,name):return self.printer.lookup_object(name,None)
    def _status(self,name):
        obj=self._obj(name)
        return obj.get_status(self.reactor.monotonic()) if obj is not None and hasattr(obj,'get_status') else {}
    def _enabled(self,name):
        obj=self._obj(name)
        return obj is not None and bool(getattr(obj,'enabled',False)) and not getattr(obj,'stopped',False)
    def _native_job(self):
        sd=self._obj('virtual_sdcard');stats=self._status('print_stats')
        f=getattr(sd,'current_file',None)
        return (id(f) if f is not None else None,getattr(f,'name',None),stats.get('state'))
    def _binding(self):
        ctx=getattr(self.board,'load_test_context',None)
        f={} if ctx is None else ctx.get('factory') or {}
        ops=getattr(self._obj('inventure_operations'),'state',None)
        cal=self._status('inventure_calibration')
        filament=self._obj('inventure_filament');level=self._obj('inventure_leveling');align=self._obj('inventure_alignment');pre=self._obj('inventure_preflight')
        return (id(ctx) if ctx is not None else None, f.get('token'),
            None if ctx is None else ctx.get('measurement',{}).get('completed_jogs'),
            getattr(ops,'epoch',None),getattr(self._obj('inventure_materials'),'generation',None),
            cal.get('revision'),self._native_job(),getattr(filament,'job',None),
            (getattr(filament,'prompt',None) or {}).get('token'),
            id(getattr(level,'session',None)),getattr(level,'state',None),
            getattr(getattr(level,'session',None),'candidate',None),
            id(getattr(align,'lease',None)),getattr(align,'state',None),
            repr(getattr(align,'candidate',None)),
            getattr(pre,'epoch',None),getattr(getattr(pre,'ticket',None),'token',None),
            tuple(getattr(self.board,'fan_manual_last',{}).get(k) for k in ('run','channel','phase')))
    def _set_view(self,kind,title,options,*,data=None,binding=True):
        self.view=View(self.reactor.monotonic(),kind,title,options,
            binding=self._binding() if binding else None,data=data)
        # Retain the actual file object for this view; an id() reused after
        # close/reopen of the same filename cannot resurrect old authority.
        self.view.job_reference=getattr(self._obj('virtual_sdcard'),'current_file',None)
        self.view.released=not bool(self.last_buttons&4)
        self._wake()
        return self.view
    def _wake(self):
        oled=self._obj('inventure_oled')
        if oled is not None:
            oled.message=None;oled.render_at=0.;oled._user_refresh_pending=True
            if hasattr(oled,'_display_activity'):oled._display_activity(self.reactor.monotonic())
    def _info(self,title,reason,**data):
        lines=[str(reason)[i:i+24] for i in range(0,len(str(reason)),24)] or ['']
        return self._set_view('info',title,[('Back',('close',{}))]+[(x,None) for x in lines],data=dict(data,reason=str(reason)))
    def _confirm(self,action,params=None,title=None):
        reviews=[]
        if action.startswith('load_cartridge_'):reviews=['Cassette/path checked','SERVICE: preinserted only']
        elif action=='leveling':title='Knobs and tray checked?'
        elif action=='alignment':reviews=['Geometry/materials checked']
        elif action=='check_model':title='Job + spool supply OK?'
        return self._set_view('confirm',title or action.replace('_',' ').title(),
            [('No / Back',('close',{})),('Yes / Start',('execute',dict(action=action,params=dict(params or {}))))]+
            [(line,None) for line in reviews],
            data=dict(action=action,parameters=dict(params or {})))

    def capability(self,action):
        if action not in ALL_ACTIONS and not action.startswith(('fan_','platform_','spool_')):
            return dict(available=False,backend=None,reason='Unknown menu action')
        if action in READ_ACTIONS or action in CHOICE_ACTIONS or action.startswith(('sort_','language_')) or action in ('back','go_status','cancel_no','question_no'):
            return dict(available=True,backend='host_view',reason=None)
        if action in NATIVE_ACTIONS:
            name=NATIVE_ACTIONS[action];enabled=self._enabled(name)
            if action in ('pause','resume','cancel_yes') and not enabled:
                enabled=self._obj('pause_resume') is not None and self._obj('virtual_sdcard') is not None
                if enabled:return dict(available=True,backend='native_pause_resume',reason=None)
            if action in ('filecheck_on','filecheck_off','ignore_filament'):
                method='set_file_checking' if action.startswith('filecheck_') else 'set_ignore_endstops'
                enabled=enabled and callable(getattr(self._obj(name),method,None))
            return dict(available=enabled,backend=name,reason=None if enabled else name+' is absent or its required execution contract is disabled')
        if action.startswith(('door_','fan_')):
            key='door_policy' if action.startswith('door_') else 'fan_manual'
            okay=key in self.board.commands
            return dict(available=okay,backend=key,reason=None if okay else key+' MCU ABI is unavailable')
        if action.startswith('platform_'):
            motion=self._obj('inventure_motion');th=self._obj('toolhead')
            okay=bool(motion is not None and getattr(motion,'enabled',False) and th is not None
                and callable(getattr(self._obj('inventure_operations'),'procedure',None)))
            return dict(available=okay,backend='native_platform',reason=None if okay else 'Native homed Z/contact profile is not enabled; service bottom reference is not print Z')
        if action in ('select_t0','select_t1') and self._enabled('inventure_tools'):
            return dict(available=True,backend='inventure_tools',reason=None)
        okay='factory_session' in self.board.commands
        return dict(available=okay,backend='R7_service',reason=None if okay else 'Requires MCU RC8-CONTROL-R7-FACTORY')

    def open(self,action,**params):
        if self.stopped:raise MenuError('Menu lifecycle ended; reconnect first')
        if not isinstance(action,str) or not action:raise MenuError('Specify a stable menu ACTION identifier')
        if self.active is not None:self._running_view()
        if self.active is not None:raise MenuError('A menu procedure is active; use its current controls')
        if self.fan_session is not None and action not in READ_ACTIONS and action not in ('fan_test','back','go_status') and not action.startswith('fan_'):
            return self._info('Fan test active','End the fan-test session before starting another menu operation')
        if action in ('go_status','back','cancel_no','question_no'):self.view=None;return
        if action=='question_yes':return self._info('Confirmation','No bound question is active; open the intended operation')
        if action=='job_info':return self._job_view()
        if action in READ_ACTIONS:return self._read(action)
        if action=='sd_browser':return self._browse()
        if action in ('spool_catalog_model','spool_catalog_support'):
            tool=int(action=='spool_catalog_support');catalog=SUPPORT_MATERIALS if tool else MODEL_MATERIALS
            return self._set_view('choice','Support spool' if tool else 'Model spool',
                [('Back',('close',{}))]+[(label,('request',dict(action='spool_load',tool=tool,material_id=i))) for i,label in catalog])
        if action in ('maintenance_t0','maintenance_t1'):
            tool=int(action.endswith('1'))
            return self._set_view('choice','Hotend T%d'%tool,[('Back',('close',{}))]+[
                (label,('request',dict(action=name+'_t%d'%tool))) for label,name in
                [('Select nozzle','select'),('Heat / hold','heat'),('Purge and clean','purge'),
                 ('Clean nozzle','clean'),('Feed 20mm','manual_load'),('Retract 20mm','manual_unload')]])
        if action=='heat':
            return self._set_view('choice','Heat extruder',[('Back',('close',{})),
                ('Model nozzle T0',('request',dict(action='heat_t0'))),('Support nozzle T1',('request',dict(action='heat_t1')))])
        if action=='move_platform':
            return self._set_view('choice','Move platform',[('Back',('close',{})),
                ('Up %gmm'%self.platform_step,('request',dict(action='platform_up'))),
                ('Down %gmm'%self.platform_step,('request',dict(action='platform_down')))])
        if action=='fan_test':
            return self._set_view('choice','Fan test',[('Back',('close',{}))]+[
                ('F%d %s'%(i,name),('fan_page',dict(channel=i))) for i,name in enumerate(self.board.FAN_NAMES)])
        if action.startswith('sort_'):
            self.settings.set(sorting=action[5:]);return self._info('Models sorting',self.settings.value['sorting'])
        if action.startswith('language_'):
            language=action[-2:];oled=self._obj('inventure_oled')
            if language=='pl' and getattr(oled,'menu_profile','m300')=='m300':
                return self._info('Language','M300 launcher uses English')
            self.settings.set(language=language)
            if oled is not None and oled.oem_menu is not None:oled.oem_menu.language=language;oled.oem_menu.generation+=1
            return self._info('Language',language)
        capability=self.capability(action)
        if action in ('heat_t0','heat_t1','spool_load'):capability=dict(available=True)
        if not capability['available']:return self._info('Unavailable',capability['reason'],action=action)
        title=None
        if action in SERVICE_ACTIONS or action in ('heat_t0','heat_t1'):
            from .inventure_factory_core import parameters, require_heated_target
            tool=SERVICE_ACTIONS[action][1] if action in SERVICE_ACTIONS else int(action[-1])
            if 'TOOL' in params and str(params['TOOL']).upper()!='T%d'%tool:raise MenuError('TOOL differs from the selected menu nozzle')
            params.pop('TOOL',None)
            parsed=parameters(Command(self,TOOL='T%d'%tool,**params),getattr(self.board,'procedure_profiles',None))
            native_filament=(action.startswith(('load_cartridge_','unload_'))
                and self._enabled('inventure_filament'))
            if not native_filament:
                operation=SERVICE_ACTIONS[action][0] if action in SERVICE_ACTIONS else 'HEAT'
                require_heated_target(parsed,operation)
            for key in ('MAX_POWER','DURATION'):params.setdefault(key,parsed[key.lower()])
            if not parsed['target_requires_override']:
                params.setdefault('TARGET',parsed['target'])
            if action.startswith('select_'):
                title='Select nozzle T%d?'%tool
            else:
                title='T%d %s'%(tool,action.split('_')[0])
                if not parsed['target_requires_override']:title+=' at %gC'%parsed['target']
                title+='?'
        elif action=='alignment':
            from .inventure_factory_core import parameters
            for tool in (0,1):
                p=parameters(Command(self,TOOL='T%d'%tool),getattr(self.board,'procedure_profiles',None))
                params.setdefault('T%d_TEMP'%tool,p['target'])
            title='Align %g/%gC?'%(float(params['T0_TEMP']),float(params['T1_TEMP']))
        elif action=='spool_load':
            if type(params.get('tool')) is not int or params.get('material_id') not in dict(SUPPORT_MATERIALS if params.get('tool') else MODEL_MATERIALS):
                raise MenuError('Choose a material from the current spool catalogue')
        elif action=='check_model':
            if not isinstance(params.get('identity'),dict):raise MenuError('Choose a model from the current browser preview')
        elif action=='print_model':
            token=self._status('inventure_preflight').get('token')
            if token is None:return self._info('Models','Run P15 checking for the selected manifest before starting')
            if params.get('owner_token',token)!=token:raise MenuError('Checked model token changed')
            params['owner_token']=token
        return self._confirm(action,params,title)

    def _read(self,action):
        now=self.reactor.monotonic()
        if action=='save_print':return self._info('Save print','P17 has no restart checkpoint; a paused job remains in the current host session')
        if action in ('motion_disabled','homing_disabled','heating_disabled'):
            return self._info('Native control','Use the qualified Inventure native motion/thermal profile; R7 service is separate')
        if action=='help':return self._info('Inventure help','UP/DOWN select, OK confirm, POWER back. Hold POWER stops the current operation.')
        if action in ('version','printer_info','mcu_info','uart_info'):
            rows=['Host '+str(__import__(self.board.__class__.__module__,fromlist=['HOST_REVISION']).HOST_REVISION),
                'MCU '+str(getattr(self.board,'mcu_release',None)),
                'UART '+str(getattr(self.board,'p4_boot',{}).get('baud','unknown'))]
        elif action in ('thermal_info','adc_info'):
            rows=['%s: %s'%(self.board.ADC_PINS[i],'--' if (t:=self.board.panel_temperature(i,now))[1]!='valid' else '%.1fC'%t[0]) for i in range(8)]
        elif action=='fans_info':rows=['F%d: %s'%(i,self.board.panel_fan_duty(i,now)) for i in range(6)]
        elif action=='oled_info':rows=[str(self._status('inventure_oled'))[:200]]
        elif action=='rfid_info':rows=[str(self.board.uart_status),str(self.board.inputs.get('raw',{}))]
        elif action=='settings_info':rows=[str(self.settings.value),'Host JSON only']
        elif action=='job_info':rows=[str(self._status('print_stats')),str(self._status('inventure_cycle'))]
        else:rows=[action]
        return self._set_view('info',action.replace('_',' ').title(),[('Back',('close',{}))]+[(str(row),None) for row in rows],data={'readings':rows})

    def _job_view(self):
        job=self._native_job();state=job[2]
        options=[('Back',('close',{}))]
        if state=='printing':options.append(('Pause current print',('request',dict(action='pause'))))
        elif state=='paused':
            options += [('Resume this print',('request',dict(action='resume'))),
                ('Stop this print',('request',dict(action='cancel_yes')))]
        return self._set_view('job','Print: '+str(state or 'idle'),options,
            data={'job_file':job[1],'state':state})

    def _browse(self):
        sd=self._obj('virtual_sdcard');root=getattr(sd,'sdcard_dirname',None)
        if root is None:return self._info('Models','Native virtual_sdcard is not configured; MCU card browsing needs its own reader backend')
        files=[]
        for p in Path(root).rglob('*'):
            if len(files)>=1000:break
            if not p.is_symlink() and p.is_file() and p.suffix.lower() in ('.json','.gcode','.gco','.zcode'):
                stat=p.stat();files.append((str(p.relative_to(root)),stat.st_size,stat.st_mtime_ns))
        sorting=self.settings.value['sorting']
        files.sort(key=lambda x: x[0].lower() if sorting=='name' else x[1] if sorting=='size' else x[2],reverse=sorting in ('desc','size'))
        return self._set_view('files','Models / host files',[('Back',('close',{}))]+
            [(name,('preview',dict(name=name))) for name,_,_ in files],data={'count':len(files),'source':'native_virtual_sdcard'})

    def _preview(self,name):
        sd=self._obj('virtual_sdcard');identity=source_identity(sd.sdcard_dirname,name)
        self.preview=identity
        choices=[('Back to models',('browse',{})),('File '+str(identity['size'])+' bytes',None)]
        if name.lower().endswith('.json'):
            choices.append(('Check manifest (P15)',('request',dict(action='check_model',name=name,identity=identity))))
        else:choices.append(('Needs P15 manifest',None))
        return self._set_view('preview',Path(name).name,choices,data=identity)

    def _fan_page(self,channel):
        status=getattr(self.board,'fan_manual_last',{})
        readings=[('State: '+str(status.get('phase','idle')),None),
            ('Tach ON/OFF %s/%s'%(status.get('tach_on_edges','--'),status.get('tach_off_edges','--')),None)]
        return self._set_view('fan','Fan F%d'%channel,[('Back / end test',('fan_end',{}))]+[
            (label,('request',dict(action='fan_'+verb,channel=channel))) for label,verb in
            [('Cold printer: ON','on'),('OFF','off'),('Observed PASS','pass'),('Observed FAIL','fail'),('Cancel test','cancel')]]+readings,data=dict(channel=channel,status=status))

    def _end_fan(self):
        owned=self.fan_session;self.fan_session=None
        if owned is not None:
            self._call(self.board,'cmd_fan_manual_status')
            current=getattr(self.board,'fan_manual_last',{})
            if current.get('run')==owned['run']:
                self._call(self.board,'cmd_fan_manual_cancel')
        return self._info('Fan test ended','Automatic cooling retains priority')

    def _call(self,obj,method,**params):
        fn=getattr(obj,method,None)
        if not callable(fn):raise MenuError('Required owner method is unavailable: '+method)
        return fn(Command(self,**params))
    def _service_ready(self):
        if not self.board.connected:raise MenuError('MCU disconnected')
        if self.fan_session is not None:raise MenuError('End the menu fan-test session before starting heat or motion')
        if self._native_job()[2] in ('printing','paused'):raise MenuError('R7 SERVICE requires an idle printer; the native print retains ownership')
        if getattr(self.board,'load_test_context',None) is not None:raise MenuError('Another service procedure is active')
        mode=self.board._control_query()
        if mode['mode']=='OFF' and not mode['active'] and not mode['fault'] and not mode['latched']:
            self._call(self.board,'cmd_control',MODE='SERVICE')
    def _start_service(self,action,params):
        operation,tool=SERVICE_ACTIONS[action];self._service_ready()
        args=dict(ACTION=operation,TOOL='T%d'%tool);args.update(params)
        if args['TOOL']!='T%d'%tool:raise MenuError('Menu tool differs from the requested procedure')
        if action.startswith('manual_unload_'):args['MANUAL_DIR']='UNLOAD'
        self._call(self.board,'cmd_procedure',**args)
        context=self.board.load_test_context
        if context is None:raise MenuError('Service did not establish a running owner')
        self.active=dict(backend='service',object=context,action=action)

    def _execute(self,action,params):
        if self.stopped:raise MenuError('Host lifecycle ended')
        if action in SERVICE_ACTIONS:
            # Prefer the full native P13 contract when that qualified profile is
            # installed. R7 SERVICE explicitly handles preinserted cartridges.
            filament=self._obj('inventure_filament')
            if action in ('select_t0','select_t1') and self._enabled('inventure_tools'):
                self._call(self._obj('inventure_tools'),'cmd_select',TOOL=int(action[-1]))
            elif action.startswith(('load_cartridge_','unload_')) and self._enabled('inventure_filament'):
                if action.startswith('load_cartridge_'):
                    selection=self._status('inventure_materials').get('selection')
                    if not selection or selection[int(action[-1])]['source']!='cartridge':
                        raise MenuError('The selected native material source is not a cartridge; select and verify that source first')
                tool=int(action[-1]);self._call(filament,'cmd_load' if action.startswith('load_') else 'cmd_unload',TOOL=tool,ACK='TRANSPORT_PATH_CHECKED')
                self.active=dict(backend='filament',object=filament,job=filament.job,action=action)
            else:self._start_service(action,params)
        elif action in ('heat_t0','heat_t1'):
            self._service_ready();self._call(self.board,'cmd_load_test',ACTION='HEAT',TOOL='T'+action[-1],**params)
            self.active=dict(backend='service',object=self.board.load_test_context,action=action)
        elif action=='leveling':
            obj=self._obj('inventure_leveling');self._call(obj,'cmd_start',ACK='KNOBS_AND_TRAY_CHECKED')
            self.active=dict(backend='level',object=obj,session=obj.session,action=action)
        elif action=='alignment':
            obj=self._obj('inventure_alignment');args=dict(ACK='GEOMETRY_AND_MATERIALS_CHECKED',MODE='FULL',X_MODE='stock_reference')
            args.update(params);self._call(obj,'cmd_start',**args)
            self.active=dict(backend='align',object=obj,session=getattr(obj,'lease',None),action=action)
        elif action=='spool_load':
            tool=int(params['tool']);material_id=int(params['material_id'])
            if material_id not in dict(SUPPORT_MATERIALS if tool else MODEL_MATERIALS):raise MenuError('Material is outside the audited tool catalogue')
            materials=self._obj('inventure_materials');filament=self._obj('inventure_filament')
            if not self._enabled('inventure_materials') or not self._enabled('inventure_filament'):raise MenuError('Spool menu requires enabled native P11/P13; R7 cartridge AUTO is not a spool loader')
            selected=self._status('inventure_materials').get('selection')
            if not selected or len(selected)!=2:raise MenuError('Select the model/support material pair before loading from spool')
            args={'A':selected[0]['material_id'],'B':selected[1]['material_id'],
                'A_SOURCE':selected[0]['source'],'B_SOURCE':selected[1]['source'],'ACK':'MATERIALS_AND_SOURCES_CHECKED'}
            args['B' if tool else 'A']=material_id;args['B_SOURCE' if tool else 'A_SOURCE']='spool'
            self._call(materials,'cmd_select',**args)
            self._call(filament,'cmd_load',TOOL=tool,ACK='TRANSPORT_PATH_CHECKED')
            self.active=dict(backend='filament',object=filament,job=filament.job,action=action)
        elif action in ('door_lock','door_unlock'):
            self._call(self.board,'cmd_door_policy',ENABLE=int(action=='door_lock'))
        elif action.startswith('fan_'):
            self._call(self.board,'cmd_fan_manual',CHANNEL=params.get('channel',0),ACTION=action[4:].upper(),ACK='COLD' if action=='fan_on' else 'OBSERVED')
            if action=='fan_on':self.fan_session=dict(run=self.board.fan_manual_last['run'],channel=params.get('channel',0))
            if action=='fan_cancel':self.fan_session=None
        elif action.startswith('platform_'):
            motion=self._obj('inventure_motion');th=self._obj('toolhead')
            if motion is None or not getattr(motion,'enabled',False) or 'z' not in th.get_status(self.reactor.monotonic()).get('homed_axes',''):
                raise MenuError('Platform movement requires qualified native homed Z')
            if getattr(self._obj('inventure_operations'),'state',None) is None:raise MenuError('Native operation owner unavailable')
            ops=self._obj('inventure_operations');ops.require_motion();ops.require_position('z')
            # Use the existing finite native service scope and toolhead checks.
            # XYZ/E parser state is untouched; there is no held raw Z loop.
            with ops.procedure('calibration'):
                pos=list(th.get_position());pos[2]+= -self.platform_step if action=='platform_up' else self.platform_step
                th.move(pos,self.platform_speed);th.wait_moves()
        elif action in ('pause','resume','cancel_yes'):
            obj=self._obj('inventure_cycle')
            state=self._native_job()
            expected='printing' if action=='pause' else 'paused' if action=='resume' else None
            if state[0] is None or expected is not None and state[2]!=expected:
                raise MenuError('No matching native print/paused job')
            if self._enabled('inventure_cycle'):
                self._call(obj,{'pause':'cmd_pause','resume':'cmd_resume','cancel_yes':'cmd_cancel'}[action],
                    ACK={'pause':'PAUSE_PRINT','resume':'CHECKED_CURRENT_STATE','cancel_yes':'CONFIRM_STOP'}[action])
            else:
                # Dispatch registered native G-code so P02/P17 wrappers retain
                # control; never call a raw PauseResume method past its owner.
                self.gcode.run_script_from_command({'pause':'PAUSE','resume':'RESUME','cancel_yes':'CANCEL_PRINT'}[action])
        elif action=='check_model':
            sd=self._obj('virtual_sdcard');current=source_identity(sd.sdcard_dirname,params['name'])
            if current!=params['identity']:raise MenuError('Model changed after preview')
            obj=self._obj('inventure_preflight');self._call(obj,'cmd_check',MANIFEST=params['name'],ACK='JOB_AND_SPOOL_SUPPLY_CHECKED')
            self.active=dict(backend='preflight',object=obj,epoch=obj.epoch,action=action)
        elif action=='print_model':
            obj=self._obj('inventure_preflight');status=obj.get_status(self.reactor.monotonic())
            if status.get('token')!=params.get('owner_token'):raise MenuError('P15 job token changed')
            self._call(obj,'cmd_start',TOKEN=params['owner_token'],ACK='START_CHECKED_JOB')
        elif action in ('filecheck_on','filecheck_off','ignore_filament'):
            cap=self.capability(action)
            if not cap['available']:raise MenuError(cap['reason'])
            obj=self._obj(cap['backend'])
            if action.startswith('filecheck_'):obj.set_file_checking(action=='filecheck_on')
            else:obj.set_ignore_endstops(not bool(obj.get_status(self.reactor.monotonic()).get('ignore_endstops')))
        else:raise MenuError('No procedure handler for '+action)
        self.last=dict(action=action,result='requested',physical_completion_verified=False)
        if action.startswith('fan_'):self._fan_page(params.get('channel',0))
        elif self.active is not None:self._running_view()
        else:self._info('Command accepted','Read the current owner status; no physical result was inferred',action=action)

    def _owns_active(self):
        a=self.active
        if a is None:return False
        obj=a['object'];backend=a['backend']
        if backend=='service':return getattr(self.board,'load_test_context',None) is obj
        if backend=='filament':return getattr(obj,'job',None)==a['job']
        if backend=='level':return getattr(obj,'session',None) is a['session']
        if backend=='align':return getattr(obj,'lease',None)==a['session']
        if backend=='preflight':return getattr(obj,'epoch',None)==a['epoch']
        return False

    def _running_view(self):
        active=self.active;obj=active['object'];backend=active['backend'];now=self.reactor.monotonic()
        if not self._owns_active() and backend!='service':
            self.active=None;return self._info('Owner changed','The previous procedure ended; no command was sent to its replacement')
        options=[('Stop / cancel',('cancel_request',{}))];phase='running';detail={}
        if backend=='service':
            ctx=getattr(self.board,'load_test_context',None)
            if ctx is not obj:
                self.active=None;self.last=self.board.load_test_last
                return self._info('Procedure ended',self.last.get('reason',self.last.get('phase','ended')),result=self.last)
            detail=self.board._load_test_report(ctx);phase=(detail.get('factory') or {}).get('phase',detail['phase'])
            if active['action'].startswith('heat_') and detail.get('ready_for_jog'):
                options.append(('Finish / heaters off',('cancel',{})))
                options += [('Feed 20mm',('jog',dict(reverse=False))),
                    ('Retract 20mm',('jog',dict(reverse=True)))]
            temperature=detail.get('temperature_c');target=detail.get('target_c')
            if isinstance(temperature,(int,float)) and isinstance(target,(int,float)):
                options.append(('T%d %.1f / %.0fC'%(ctx['tool'],temperature,target),None))
            if detail.get('factory'):
                steps=detail['factory'].get('e_sent_steps',0)
                options.append(('E %.1fmm nominal'%(steps/960.),None))
        else:
            detail=obj.get_status(now);phase=detail.get('state',detail.get('phase','running'))
            if backend=='filament':
                if obj.job!=active['job']:raise MenuError('Filament owner/job changed')
                if not obj.running:
                    self.active=None;self.last=detail;return self._info('Filament ended',obj.phase,result=detail)
                prompt=getattr(obj,'prompt',None)
                if prompt is not None:options.append(('Continue: '+str(prompt['kind']),('continue',dict(owner_token=prompt['token']))))
            elif backend in ('level','align'):
                if not getattr(obj,'running',False):
                    self.active=None;self.last=detail;return self._info('Calibration ended',phase,result=detail)
                if backend=='level':
                    if phase=='candidate':options.append(('Review / save depth',('commit_request',dict(deformed=bool(detail.get('deformed'))))))
                    else:options.append(('Adjusted / next sample' if phase=='adjust' else 'Measure next point',('continue',{})))
                    if detail.get('next_point') is not None:options.append(('Next pad: %s'%detail['next_point'],None))
                    if detail.get('candidate_depth_um') is not None:
                        options.append(('Depth %.3fmm'%(detail['candidate_depth_um']/1000.),None))
                    for instruction in detail.get('instructions',[]):
                        options.append((str(instruction.get('knob','Screw')).replace('_',' ').title(),None))
                        options.append(('%g turns %s'%(instruction['turns'],'CCW' if instruction['direction']=='counterclockwise' else 'CW'),None))
                elif phase in ('choose_Y','choose_X'):
                    axis=phase[-1]
                    choices=(7,) if axis=='X' and getattr(obj,'x_mode','stock_reference')=='stock_reference' else range(1,14)
                    options += [('Line %d%s'%(i,' (neutral)' if i==7 else ''),('line',dict(axis=axis,line=i))) for i in choices]
                elif phase=='candidate':options.append(('Review / save alignment',('commit_request',{})))
                else:options.append(('Continue calibration',('continue',{})))
                if backend=='align':
                    samples=detail.get('samples_steps',[[],[]])
                    options.append(('T0 %d/2  T1 %d/3'%(len(samples[0]),len(samples[1])),None))
            elif backend=='preflight':
                if obj.epoch!=active['epoch']:raise MenuError('Model inspection generation changed')
                phase=(detail.get('status') or {}).get('status','checking')
                if detail.get('token'):options.append(('Start checked model',('request',dict(action='print_model',owner_token=detail['token']))))
                elif not detail.get('busy'):
                    self.active=None;self.last=detail;return self._info('File check ended',phase,result=detail)
        key=(backend,phase,tuple((x[0],repr(x[1])) for x in options),self._binding())
        if key!=self.last_native_key:
            self.last_native_key=key
            self._set_view('running',str(phase).replace('_',' ').title(),options,data=dict(backend=backend,phase=phase))
        return self.view

    def _cancel_active(self):
        active=self.active
        if active is None:self.view=None;return
        obj=active['object'];backend=active['backend']
        owned=self._owns_active()
        self.active=None;self.view=None;self.last_native_key=None
        if not owned:return self._info('Owner changed','Previous procedure no longer owns these resources')
        if backend=='service':
            if self.board.load_test_context is obj:self._call(self.board,'cmd_load_test',ACTION='STOP')
        elif backend=='filament':
            if obj.job==active['job']:self._call(obj,'cmd_cancel')
        elif backend in ('level','align'):self._call(obj,'cmd_cancel')
        elif backend=='preflight':
            if obj.epoch==active['epoch']:self._call(obj,'cmd_cancel')
        self._info('Operation cancelled','Current owner stopped; no recovery movement requested')

    def _dispatch_choice(self,verb,args):
        if verb=='close':
            if self.active is not None:self.last_native_key=None;return self._running_view()
            if self.fan_session is not None:return self._fan_page(self.fan_session['channel'])
            self.view=None;return
        if verb=='browse':return self._browse()
        if verb=='preview':return self._preview(args['name'])
        if verb=='fan_page':return self._fan_page(args['channel'])
        if verb=='fan_end':return self._end_fan()
        if verb=='request':
            action=args['action'];params={k:v for k,v in args.items() if k!='action'}
            if self.active is not None and action=='print_model':return self._confirm(action,params)
            return self.open(action,**params)
        if verb=='cancel_request':return self._set_view('confirm','Cancel operation?',
            [('No / continue',('close',{})),('Yes / stop',('cancel',{}))])
        if verb=='commit_request':
            active=self.active
            if active is None:raise MenuError('Calibration owner ended')
            state=active['object'].get_status(self.reactor.monotonic())
            candidate=state.get('candidate_depth_um') if active['backend']=='level' else state.get('candidate')
            labels=[]
            if active['backend']=='level' and candidate is not None:labels=['Depth %.3fmm'%(candidate/1000.)]
            elif isinstance(candidate,dict):labels=['%s: %s'%(axis,candidate.get(axis)) for axis in ('ox','oy','oz')]
            return self._set_view('confirm','Accept deformed tray?' if args.get('deformed') else 'Save host calibration?',
                [('No / Back',('close',{})),('Yes / Save',('commit',args))]+[(label,None) for label in labels],
                data=dict(candidate=candidate,deformed=bool(args.get('deformed'))))
        if verb=='cancel':return self._cancel_active()
        if verb=='execute':
            return self._execute(args['action'],args['params'])
        active=self.active
        if active is None:raise MenuError('Procedure no longer active')
        obj=active['object'];backend=active['backend']
        if verb=='continue':
            if backend=='filament':self._call(obj,'cmd_continue',TOKEN=args['owner_token'])
            elif backend=='level':self._call(obj,'cmd_advance',ACK='ADJUSTED_AND_DOORS_CLOSED')
            elif backend=='align':self._call(obj,'cmd_advance')
            else:raise MenuError('This owner has no continuation')
        elif verb=='jog' and backend=='service':
            if self.board.load_test_context is not obj:raise MenuError('Heated service owner changed')
            self._call(self.board,'cmd_load_test',ACTION='JOG',STEPS=19200,
                DIR=obj['tool']^int(args['reverse']),RATE=2400)
        elif verb=='line' and backend=='align':self._call(obj,'cmd_line',AXIS=args['axis'],LINE=args['line'])
        elif verb=='commit':
            if backend=='level':
                params={'ACK':'SAVE_HOST_LEVELING'}
                if args.get('deformed'):params['DEFORMATION_ACK']='ACCEPT_DEFORMED_TRAY'
                self._call(obj,'cmd_commit',**params)
            elif backend=='align':self._call(obj,'cmd_commit',ACK='SAVE_HOST_ALIGNMENT')
            else:raise MenuError('This owner has no calibration commit')
        else:raise MenuError('Unknown procedure control')
        self.last_native_key=None;return self._running_view()

    def choose(self,token,index=None,confirm=False):
        if self.executing:raise MenuError('A menu command is already executing')
        view=self.view
        if view is None:raise MenuError('No current menu view')
        if confirm:
            if view.kind!='confirm':raise MenuError('Current view is not a confirmation')
            index=1
        index=view.selected if index is None else index
        if type(index) is not int or not 0<=index<len(view.options):raise MenuError('Menu choice is outside the current view')
        choice=view.options[index][1]
        if choice is None:return
        view.claim(token,self.reactor.monotonic(),self._binding())
        self.executing=True
        try:return self._dispatch_choice(*choice)
        except Exception as exc:
            self.last=dict(result='failed',reason=str(exc))
            self._info('Operation unavailable',str(exc));raise
        finally:self.executing=False

    def observe_buttons(self,mask,sequence):
        if self.last_sequence is not None:
            delta=(sequence-self.last_sequence)&0xffffffff
            if not 0<delta<0x80000000:return False
        self.last_sequence=sequence;self.last_buttons=mask
        if self.view is not None and not mask&4:self.view.released=True
        return True
    def legacy_buttons(self,mask):
        # P13's old level-triggered listener must not also consume menu OK.
        obj=self._obj('inventure_filament')
        return mask&~4 if obj is not None and getattr(obj,'running',False) and getattr(obj,'prompt',None) is not None else mask
    def panel_select_command(self,acknowledged):
        view=self.view
        if view is None:return None
        if not acknowledged or not view.released:raise MenuError('Wait for the displayed view and a new OK press')
        view.released=False
        return 'INVENTURE_ZORTRAX OP=CHOOSE TOKEN=%s INDEX=%d'%(view.token,view.selected)
    def panel_move(self,direction):
        if self.view is not None:
            self.view.move(direction);self.view.released=not bool(self.last_buttons&4);self._wake()
    def panel_back(self):
        if self.active is not None:return self._dispatch_choice('cancel_request',{})
        if self.fan_session is not None:return self._set_view('confirm','End fan test?',
            [('No / continue',('close',{})),('Yes / restore cooling',('fan_end',{}))])
        self.view=None;self._wake()
    def view_status(self):return None if self.view is None else self.view.render()

    def _tick(self,eventtime):
        if self.stopped:return self.reactor.NEVER
        try:
            filament=self._obj('inventure_filament')
            ctx=getattr(self.board,'load_test_context',None)
            if self.active is None and ctx is not None:
                action='heat_t%d'%ctx['tool'] if ctx.get('factory') is None and ctx.get('cassette_load') is None else 'external_service'
                self.active=dict(backend='service',object=ctx,action=action)
                self.view=None;self.last_native_key=None
            elif self.active is None and filament is not None and getattr(filament,'running',False) and getattr(filament,'prompt',None) is not None:
                self.active=dict(backend='filament',object=filament,job=filament.job,action='native_filament')
                self.view=None;self.last_native_key=None
            if self.active is not None and not self.executing:
                if self.view is None or self.view.kind=='running':self._running_view()
            elif self.view is not None and self.reactor.monotonic()>=self.view.deadline:
                self.view=None
            elif self.view is None:
                job=self._native_job()
                if job[2]=='paused' and job!=self.last_pause_view:
                    self.last_pause_view=job;self._job_view()
        except Exception as exc:
            reason=str(exc)
            try:self._cancel_active()
            except Exception as cancel_error:reason+='; cancellation: '+str(cancel_error)
            self._info('Procedure interrupted',reason)
        return self.reactor.monotonic()+.25
    def get_status(self,now):
        return dict(api=1,view=None if self.view is None else self.view.status(),
            active=None if self.active is None else dict(backend=self.active['backend'],action=self.active['action']),
            last=self.last,settings=self.settings.value,stopped=self.stopped,
            fan_session=self.fan_session,
            capabilities={a:self.capability(a) for a in sorted(ALL_ACTIONS) if a not in ('question_yes',)},
            checkpoint_survives_restart=False,physical_hardware_tested=False)
    def cmd(self,gcmd):
        op=str(gcmd.get('OP','STATUS')).upper()
        try:
            if op=='OPEN':
                from .inventure_factory_core import defaults
                allowed={k.upper() for k in defaults()}|{'T0_TEMP','T1_TEMP','MODE','X_MODE','T0_PROFILE'}
                query=getattr(gcmd,'get_command_parameters',None)
                values=query() if query is not None else getattr(gcmd,'values',{})
                params={k:v for k,v in values.items() if k not in ('OP','ACTION')}
                if not set(params)<=allowed:raise MenuError('Unknown procedure parameter: '+', '.join(sorted(set(params)-allowed)))
                self.open(gcmd.get('ACTION'),**params)
            elif op=='CHOOSE':self.choose(gcmd.get('TOKEN',''),gcmd.get_int('INDEX'))
            elif op=='CONFIRM':self.choose(gcmd.get('TOKEN',''),confirm=True)
            elif op=='BACK':self.panel_back()
            elif op=='CANCEL':
                if self.view is None:raise MenuError('No current cancellation token')
                self.view.claim(gcmd.get('TOKEN',''),self.reactor.monotonic(),self._binding())
                if self.fan_session is not None:self._end_fan()
                else:self._cancel_active()
            elif op not in ('STATUS','CATALOG'):raise MenuError('Use OP=OPEN, CHOOSE, CONFIRM, BACK, CANCEL, STATUS or CATALOG')
        except (ValueError,TypeError,OSError) as exc:raise gcmd.error(str(exc))
        status=self.get_status(self.reactor.monotonic())
        if op!='CATALOG':status.pop('capabilities',None)
        raw=json.dumps(status,sort_keys=True,default=str)
        if len(raw)<=1800:gcmd.respond_info('V9_ZORTRAX '+raw)
        else:
            for i in range(0,len(raw),1800):gcmd.respond_info('V9_ZORTRAX_PART %d/%d %s'%(i//1800+1,(len(raw)+1799)//1800,raw[i:i+1800]))
