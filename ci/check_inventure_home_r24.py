#!/usr/bin/env python3
"""Exercise installed R24 macros/OLED, original factory interpreter and Z engine.

Only physical interfaces are simulated. No UART, flash or running host is used.
"""
import argparse
from contextlib import redirect_stdout
from hashlib import sha256
import importlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import zlib
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'ci'),str(ROOT/'tests')]

def save_oled_preview(screens,destination):
    """Actual 256x64 OLED wires, five requests and four M300 animation frames."""
    width,height=256*4,64*5
    pixels=[]
    for axis in ('X','Y','XY','Z','XYZ'):
        for y in range(64):
            row=bytearray()
            for frame in screens[axis]['frames']:
                for packed in frame[y*128:(y+1)*128]:
                    row.extend(((packed>>4)*17,(packed&15)*17))
            pixels.append(b'\0'+row)
    def chunk(name,data):
        return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,0,0,0,0))
         +chunk(b'IDAT',zlib.compress(b''.join(pixels),9))+chunk(b'IEND',b''))
    destination.write_bytes(png)


def inspect(klipper,config,preview=None):
    sys.path.insert(0,str(klipper/'klippy'))
    import klippy,reactor
    from check_inventure_full_cycles import InstalledRig,require
    from check_inventure_home_xy import attach_panel
    from test_orange_pi_oled_fast import Panel
    from test_orange_pi_native_z import HomingMove as ContactMove,Z_STEPS_PER_MM
    from test_orange_pi_load_test import Wire
    loop=reactor.Reactor()
    printer=klippy.Printer(loop,None,dict(config_file=str(config),debuginput=True,
                           start_reason='startup',software_version='offline-r24-operator-home'))
    rows=[];refusals=[];screens={};shared_menu=None
    try:
        printer._read_config()
        printer.lookup_object('inventure_native_z')._install_g28()
        gcode=printer.lookup_object('gcode')
        gcode.gcode_handlers=gcode.ready_gcode_handlers;gcode.is_printer_ready=True
        package=type(printer.lookup_object('inventure_native_z')).__module__.rsplit('.',1)[0]
        actual_kin=printer.lookup_object('toolhead').get_kinematics()
        maximum_z_speed=actual_kin.max_z_velocity
        motion=importlib.import_module(package+'.homing')
        sequences=(('X','Y','Z'),('Y','X','Z'),('XY','Z'),('X','Z'),('Y','Z'),('Z',),
                   ('ALL',),('ALL','Z'),('X','Y','Z','Z'),('X','Y','DRIVE_RELEASE','Z'),
                   ('X','Y','X','Z'),('X','Y','Y','Z'),('X','Y','ALL'),
                   ('X','ALL'),('Y','ALL'),('ALL','ALL'))
        cases=[(sequence,None) for sequence in sequences]+[
            (('X',),'seek_x'),(('X',),'release_x'),(('Y',),'release_y'),
            (('X',),'early_release_x'),(('Z',),'held_receipt'),
            (('X',),'backoff_x'),(('Y',),'backoff_y'),(('X',),'overrun_x'),
            (('Z',),'tray_missing'),(('Z',),'bad_ack')]
        cases=[(sequence,fault,machine) for machine in ('A','B') for sequence,fault in cases]
        for origin in ('fluidd','oled','gcode','fluidd_open','fluidd_menu'):
            for hot in (False,True):
                for sequence,fault,machine in cases:
                    if fault=='bad_ack' and origin!='fluidd':continue
                    rig=InstalledRig(printer,klipper,hot=hot)
                    native,model,board=rig.native,rig.model,rig.board
                    model.max_z_velocity=maximum_z_speed
                    native.xy_references={};native.xy_frame_release_mm={};native.xy_clearance_position=None
                    native.requested_home_axes=None
                    model.axes='';model.position[2]=0.;model.physical_z=80.
                    if fault=='tray_missing':model.tray=1
                    configured_pad=[native.pad_x,native.pad_y]
                    # Emulate CoreXY's native position/range interfaces. The
                    # real installed adapter and all T0/Z engines remain in use.
                    model.limits=[(1.,-1.)]*3
                    model.axes_min=gcode.Coord([r.get_range()[0] for r in native.kin.rails])
                    model.axes_max=gcode.Coord([r.get_range()[1] for r in native.kin.rails])
                    set_position,clear_homing=model.set_position,model.clear_homing_state
                    def set_native_position(position,homing_axes=''):
                        model.calls.append(('frame_set',tuple(position),homing_axes))
                        set_position(position,homing_axes)
                        for index,axis in enumerate('xyz'):
                            if axis in homing_axes:model.limits[index]=model.axis_rails[index].get_range()
                    def clear_native_homing(axes):
                        clear_homing(axes)
                        for index,axis in enumerate('xyz'):
                            if axis in axes:model.limits[index]=(1.,-1.)
                    model.set_position=set_native_position
                    model.clear_homing_state=clear_native_homing
                    rounds={'X':0,'Y':0};pending_release=[]
                    overrun_steps=1 if machine=='A' else 7
                    pins={axis:types.SimpleNamespace(active=False) for axis in 'XY'}
                    for i,axis in enumerate('XY'):
                        pin=pins[axis]
                        pin.query_endstop=lambda _,pin=pin:pin.active
                        rail=native.kin.rails[i]
                        rail.homing_retract_dist=1.
                        rail.get_endstops=lambda pin=pin:[(pin,'limit')]
                    # Deliver actual invalidation events; the older fixture
                    # intentionally ignored them for its separate policy checks.
                    def event(name,*args):
                        if name=='inventure:native_motion_lost':native._native_motion_lost()
                        return []
                    native.printer.send_event=event
                    def held_entry(args):
                        owner,=args
                        require(model.shared_enable,'Held entry lost PE10 before the MCU call')
                        require(board.control_reply['mode']==1,'Held entry was not NATIVE')
                        model.calls.append(('held_service',owner))
                        board.control_reply.update(mode=2,armed=0)
                        # A periodic query can observe SERVICE before the
                        # held-entry reply reaches the homing coroutine.
                        board._control_query()
                        require(set(native.xy_references)=={'X','Y'},'Status polling discarded held XY')
                        return dict(owner=owner+(fault=='held_receipt'),mode=2,enabled=1,reject=0)
                    native.held_service=Wire(held_entry)
                    manual_move=model.manual_move
                    def checked_move(position,speed):
                        for index,axis in enumerate('XY'):
                            if (fault=='backoff_'+axis.lower() and position[index] is not None
                                    and pending_release==[axis]):
                                raise ValueError('Selected axis post-home backoff failed')
                            if position[index] is not None:
                                require(model.limits[index][0]<=position[index]<=model.limits[index][1],
                                        'Native motion exceeded the translated physical envelope')
                                if axis in native.xy_frame_release_mm:
                                    require(abs([native.pad_x,native.pad_y][index]
                                                +native.xy_frame_release_mm[axis]-configured_pad[index])<1e-9,
                                            'The physical Z pad shifted during the post-release park')
                        return manual_move(position,speed)
                    model.manual_move=checked_move
                    class Approach:
                        def __init__(self,p):self.axes=[]
                        def set_axes(self,axes):self.axes=axes
                        def home_rails(self,rails,force,target):
                            i,=self.axes;axis='XYZ'[i];rail,=rails
                            require(force[i]-target[i]==160.,'OEM approach budget changed')
                            require(rail.homing_retract_dist==0.,'A second contact replaced release')
                            native._home_rails_begin(self,rails)
                            model.calls.append(('xy_seek',axis,rail.homing_speed))
                            if fault=='seek_'+axis.lower():raise ValueError('No selected endstop trigger')
                            model.position[i]=target[i];model.set_position(model.position,axis.lower())
                            pins[axis].active=True;model.shared_enable=True
                    class PhysicalMove(ContactMove):
                        def homing_move(self,position,speed,probe_pos,triggered,check_triggered):
                            if self.pin in pins.values():
                                axis=next(a for a,p in pins.items() if p is self.pin)
                                i='XY'.index(axis)
                                require(not triggered and check_triggered and speed==1.,'Invalid OEM reverse release')
                                require(abs(position[i]-model.position[i]-5000./640.)<1e-9,'Release bound changed')
                                strokes={'A':{'X':(80,112),'Y':(120,152)},
                                         'B':{'X':(233,169),'Y':(41,89)}}
                                value=strokes[machine][axis][rounds[axis]%2]/640.
                                rounds[axis]+=1
                                if fault=='release_'+axis.lower():raise ValueError('Selected endstop did not release')
                                if fault=='early_release_'+axis.lower():value=0.
                                pending_release[:]=[axis]
                                model.calls.append(('xy_release',axis,round(value*640)))
                                measured=model.get_position();measured[i]=value
                                overrun=6. if fault=='overrun_'+axis.lower() else overrun_steps/640.
                                model.position[i]=value+overrun;self.pin.active=False
                                return measured
                            if self.pin is model.bottom:
                                if triggered:
                                    require(not check_triggered,'Pre-XY clearance became a full bottom home')
                                    delta=position[2]-model.position[2]
                                    require(0<delta<=10.,'Initial clearance exceeds the accepted bound')
                                    model.calls.append(('initial_clearance',delta))
                                    delta=min(delta,native.bottom_position-model.physical_z)
                                    model.position[2]+=delta;model.physical_z+=delta;model.shared_enable=True
                                    return model.get_position()
                                model.calls.append(('bottom_release',))
                                release=native.bottom_position-1./Z_STEPS_PER_MM
                                model.z+=release-model.physical_z;model.physical_z=release
                                return model.get_position()
                            return super().homing_move(position,speed,probe_pos,triggered,check_triggered)
                        def check_no_movement(self):return None
                    panel=attach_panel(rig)
                    rig.menu._attach_home_ui()
                    ui=rig.menu.home_ui
                    original_phase=type(ui)._phase.__get__(ui)
                    rendered=[];render_errors=[]
                    graphics=importlib.import_module(package+'.inventure_oled_graphics')
                    lcd=Panel(panel.stream.previous)
                    display_clock=[panel.reactor.monotonic()]
                    delivered=[]
                    for kind in ('begin','control','burst'):
                        if kind not in panel.commands:continue
                        original_send=panel.commands[kind].send
                        def send_pixels(args,*,kind=kind,original_send=original_send,**kwargs):
                            require(kwargs.get('reqclock')==0x7fffffff00000000,
                                    'Home pixels lost background UART priority')
                            original_send(args,**kwargs)
                            generation=(args[0]<<32)|args[1];token=args[2]
                            lcd.apply((kind,token,args[3:]))
                            require(panel.stream.ack(token,display_clock[0],generation),'MCU rejected Home OLED packet')
                            panel._fast_tx.pop(token,None)
                            delivered.append((kind,token))
                        panel.commands[kind]=types.SimpleNamespace(send=send_pixels)
                    def record_phase(message):
                        original_phase(message)
                        if not ui.busy:return
                        try:
                            view=rig.menu.view_status()
                            frames=[graphics.menu_wire(view,index=i) for i in range(4)]
                            require(len(set(frames))==4,'Home has no four-frame axis animation')
                            # Exercise the actual OLED timer during the thermal
                            # hold, including its original bounded transport.
                            # Only the MCU wire/SSD1322 RAM and ACKs are modelled.
                            held_before=panel.thermal_test_hold
                            panel.thermal_test_hold=True
                            try:
                                display_clock[0]=max(display_clock[0],panel.reactor.monotonic())
                                for _ in range(2000):
                                    now=display_clock[0]
                                    due=panel._tick(now)
                                    require(panel.thermal_test_hold,'Home cleared the thermal display-hold flag')
                                    if (panel.stream.previous==panel.stream.latest
                                            and panel.stream.previous==panel._render_wire
                                            and bytes(lcd.ram)==panel._render_wire):break
                                    display_clock[0]=max(now+.0001,min(now+.01,due))
                                else:raise AssertionError('Home OLED pixels stayed frozen during thermal hold')
                            finally:
                                panel.thermal_test_hold=held_before
                            require(panel._render_wire in frames,'OLED rendered the wrong Home phase')
                            require(panel.stream.active_work,'Home bypassed the existing work UART budget')
                            rendered.append(dict(axes=ui.axes,message=message,icon=view['icon']))
                            if message.startswith('Homing ') or message=='Refining Z reference':
                                screens.setdefault(ui.axes,dict(message=message,frames=frames))
                                if message=='Refining Z reference':
                                    screens[ui.axes]=dict(message=message,frames=frames)
                            # The running view consumes no encoder action or stale confirmation.
                            page,selected=panel.oem_menu.page,panel.oem_menu.selected
                            panel._select(now);panel._navigate(1,now)
                            require((panel.oem_menu.page,panel.oem_menu.selected)==(page,selected),
                                    'Encoder acted on the borrowed Home display')
                        except Exception as exc:
                            render_errors.append(str(exc))
                    ui._phase=record_phase
                    with patch.object(motion,'Homing',Approach),patch.object(motion,'HomingMove',PhysicalMove):
                        for name in sequence:
                            if name=='DRIVE_RELEASE':
                                native._xy_enable_changed(model.get_last_move_time(),False)
                                model.shared_enable=False
                                require(not native.xy_references and not model.axes,'Drive release left a reusable reference')
                                require(not native.xy_frame_release_mm,'Drive release retained an origin translation')
                                continue
                            if rig.menu.view is not None:
                                if origin=='fluidd_menu' and rig.menu.view.data.get('operator_page'):
                                    require(rig.menu.view.kind=='choice','Home did not restore the Fluidd menu')
                                else:
                                    require(rig.menu.view.kind=='info','Unexpected modal between completed Homes')
                                    rig.menu.view.selected,rig.menu.view.released=0,True
                                    rig.run(rig.menu.panel_select_command(acknowledged=True))
                            start=len(model.calls);action_start=len(rig.actions)
                            ui_start=len(rendered)
                            rig.lines.clear()
                            before=set(native.xy_references)
                            before_position=model.get_position()
                            targets={n:h.get_temp(0.)[1] for n,h in model.heaters.items()}
                            try:
                                if origin=='fluidd':rig.run('INVENTURE_HOME_'+name+(' ACK=INVALID' if fault=='bad_ack' else ''))
                                elif origin=='gcode':rig.run('G28'+('' if name=='ALL' else ' '+' '.join(name)))
                                elif origin=='fluidd_open':
                                    action='home_xyz' if name=='ALL' else 'home_'+name.lower()
                                    rig.run('INVENTURE_ZORTRAX OP=OPEN ACTION='+action+' PROMPT=1')
                                elif origin=='fluidd_menu':
                                    rig.run('INVENTURE_MAINTENANCE')
                                    view=rig.menu.view
                                    label='Home XYZ' if name=='ALL' else 'Home '+name
                                    index=next(i for i,(text,_) in enumerate(view.options) if text==label)
                                    rig.lines.clear()
                                    rig.run('INVENTURE_ZORTRAX OP=CHOOSE TOKEN=%s INDEX=%d'%(view.token,index))
                                else:
                                    panel.oem_menu.open_page('maintenance');panel.oem_display_mode='menu'
                                    label='Home XYZ' if name=='ALL' else 'Home '+name
                                    panel.oem_menu.selected=next(i for i,n in enumerate(panel.oem_menu.items()) if n.label==label)
                                    panel._select(panel.reactor.monotonic())
                                initial_review=rig.menu.view is not None and rig.menu.view.kind=='confirm'
                                require(not initial_review,'Home still requires an initial confirmation')
                                require(not any('Pad and clearance OK?' in line or 'Yes / Start|' in line
                                                or 'Confirm Home ' in line for line in rig.lines),
                                        'Home emitted the removed confirmation')
                                if origin!='fluidd_menu':
                                    require(not any('action:prompt_begin' in line for line in rig.lines),
                                            'Home phase opened an unexpected Fluidd dialog')
                            except Exception as exc:
                                if fault is None:raise
                                require(not native.xy_references,'Rejected Home retained an XY reference')
                                require(not native.xy_frame_release_mm,'Rejected Home retained an origin translation')
                                require(not native.last_result or not native.last_result.get('reference_established'),
                                        'Rejected Home certified Z')
                                require(board.load_test_context is None,'Rejected Home retained a factory owner')
                                require(not rig.actions,'Rejected XY/entry started T0 motion')
                                refusals.append(dict(origin=origin,machine=machine,fault=fault,reason=str(exc),reference_invalid=True))
                                continue
                            if fault is not None:raise AssertionError('Expected refusal: '+fault)
                            require(not render_errors,'Home OLED render failed: '+repr(render_errors))
                            require(len(rendered)>ui_start,'The installed Home did not present OLED progress')
                            require(not ui.busy,'Home retained a running display after completion')
                            expected_axes='XYZ' if name=='ALL' else name
                            require(all(r['icon']=='home:home_'+expected_axes.lower() for r in rendered[ui_start:]),
                                    'OLED showed an animation for a different Home request')
                            if origin in ('oled','fluidd_menu'):
                                require(panel.oem_menu.page=='maintenance' and panel.oem_menu.items()[panel.oem_menu.selected].label
                                        ==('Home XYZ' if name=='ALL' else 'Home '+name),
                                        'Home did not return to its selected Zortrax menu row')
                            calls=model.calls[start:]
                            seeks=[c[1] for c in calls if c[0]=='xy_seek']
                            expected=([a for a in 'YX' if a not in before] if name in ('Z','ALL')
                                      else [a for a in 'YX' if a in name])
                            require(seeks==expected,'Wrong requested/retained XY: '+repr((sequence,name,seeks,expected)))
                            require([c[1] for c in calls if c[0]=='xy_release']==seeks,'Missing release after approach')
                            backoffs=[]
                            for offset,call in enumerate(calls):
                                if call[0]!='xy_release':continue
                                axis=call[1];index='XY'.index(axis)
                                following=calls[offset+1:]
                                moves=[(i,c) for i,c in enumerate(following) if c[0]=='move']
                                require(bool(moves),'Missing 5 mm move after '+axis+' release')
                                move_index,backoff=moves[0]
                                require(not any(c[0] in ('xy_seek','held_service') for c in following[:move_index]),
                                        'Next procedure started before the '+axis+' backoff')
                                target=[None,None,None,None]
                                target[index]=5.
                                require(backoff[1]==tuple(target) and backoff[2]==1.,
                                        'Backoff moved another axis, changed speed or was not 5 mm: '+repr(backoff))
                                ref=native.xy_references[axis]
                                # Later explicit rehomes may supersede this
                                # axis's earlier measurement in the same request.
                                require(ref['release_steps']==call[2] and ref['post_home_backoff_mm']==5.,
                                        'Backoff changed the measured datum')
                                rebases=[c for c in following[:move_index] if c[0]=='frame_set']
                                require(len(rebases)==1 and rebases[0][2]==axis.lower(),
                                        'Release did not rebase just the measured axis')
                                require(abs(rebases[0][1][index]-overrun_steps/640.)<1e-9,
                                        'Stop overrun was mistaken for the measured release edge')
                                require(abs(ref['parked_native_mm']-target[index])<1e-9,'Wrong retained park')
                                if name in ('X','Y','XY'):
                                    require(model.position[index]==target[index],'Standalone XY did not stay parked')
                                backoffs.append(axis)
                            for index,axis in enumerate('XY'):
                                if axis not in native.xy_references:continue
                                ref=native.xy_references[axis]
                                require(ref['reference_native_mm']==0.,'Configured reference changed')
                                release=ref['release_mm']
                                require(abs(native.kin.limits[index][0]+release)<1e-9
                                        and abs(native.kin.limits[index][1]+release-138.)<1e-9,
                                        'Rebasing changed the original physical travel envelope')
                                require(abs([native.pad_x,native.pad_y][index]+release-configured_pad[index])<1e-9,
                                        'Rebasing moved the configured physical Z pad')
                                # Execute the actual pinned CoreXY bounds
                                # checker, using this run's translated limits.
                                saved_limits=actual_kin.limits
                                actual_kin.limits=list(model.limits)
                                try:
                                    point=model.get_position();point[index]=model.limits[index][1]
                                    axes_d=[0.,0.,0.,0.];axes_d[index]=1.
                                    move=types.SimpleNamespace(end_pos=point,axes_d=axes_d,
                                        move_error=lambda: ValueError('outside physical travel'))
                                    actual_kin.check_move(move)
                                    point[index]+=1./640.
                                    try:actual_kin.check_move(move)
                                    except ValueError:pass
                                    else:raise AssertionError('CoreXY accepted movement past the original physical limit')
                                finally:
                                    actual_kin.limits=saved_limits
                            require(not any(c[0]=='motor_off' for c in calls),'A successful Home released motors')
                            if name in ('X','Y'):
                                other=1 if name=='X' else 0
                                require(model.position[other]==before_position[other],
                                        'Single-axis Home displaced the other axis')
                            require(model.shared_enable,'Successful Home did not hold PE10')
                            require(board._control_query()['mode']=='NATIVE','Home did not return NATIVE')
                            require(targets=={n:h.get_temp(0.)[1] for n,h in model.heaters.items()},'Targets changed')
                            actions=rig.actions[action_start:]
                            require(not any(a['kind'] in ('home','home_release','z_contact_move') for a in actions),
                                    'Retained T0 preparation repeated SERVICE homing/clearance')
                            if name in ('Z','ALL'):
                                require(native.last_result and native.last_result.get('coordinate_saved'),'Z not certified')
                                require(model.position[2]==native.park_z,'Z did not park at its existing setting')
                                require(model.physical_tool==0,'T0 was not physically selected')
                                require(len([c for c in calls if c[0]=='held_service'])==1,'Missing held entry')
                                require([c[1] for c in calls if c[0]=='G28']==['Z'],'Full PE12 reference repeated')
                                require(board.load_test_context is None,'Factory owner survived completion')
                                progress=[json.loads(line.split('V9_NATIVE_Z_PROGRESS ',1)[1]) for line in rig.lines
                                          if 'V9_NATIVE_Z_PROGRESS ' in line]
                                prepared=[row for row in progress if row.get('stage')=='T0 selection and cleaning']
                                require(len(prepared)==1 and prepared[0]['xy_homed_axes']==seeks,
                                        'Home progress does not report the actual missing axes')
                                require(prepared[0]['xy_reused_axes']==[axis for axis in 'YX' if axis in before],
                                        'Home progress lost a reused, held reference')
                                pad_moves=[a for a in actions if a['kind']=='move' and a.get('leg')=='z_contact_pad']
                                require(bool(pad_moves),'Missing original service pad leg')
                                for pad_move in pad_moves:
                                    for index,axis in enumerate('XY'):
                                        release=native.xy_references[axis]['release_mm']
                                        require(abs(pad_move[axis.lower()]+release-configured_pad[index])<1e-9,
                                                'T0/Z pad leg changed physical position or counted release twice')
                            require(set(native.xy_references).issuperset(before|set(seeks)),'Held XY reference was discarded')
                            rows.append(dict(origin=origin,machine=machine,initial='NATIVE_HOT' if hot else 'OFF',sequence=list(sequence),
                                initial_review=initial_review,
                                home_ui_release=rig.menu.get_status(0.)['home_ui_release'],
                                oled_progress_rendered=True,m300_axis_animation=True,oled_delivered_during_thermal_hold=True,
                                request=name,xy_seek=seeks,xy_release=seeks,xy_backoff=backoffs,
                                measured_release_steps={axis:r['release_steps'] for axis,r in native.xy_references.items()},
                                release_origin_dynamic=True,physical_z_pad_unchanged=True,
                                actual_corexy_limits_checked=True,
                                xy_backoff_mm=5.,enable_held=True,targets_unchanged=True,
                                z_certified=name in ('Z','ALL'),motion_interfaces='simulated'))
                            if (shared_menu is None and origin=='fluidd' and not hot and machine=='A'
                                    and sequence==('XY','Z') and name=='Z'):
                                from check_inventure_shared_menu_r24 import inspect_shared_menu
                                def render_running():
                                    view=rig.menu.view_status()
                                    symbol=graphics._symbol(view['icon'])
                                    count=len(symbol['frames']) if symbol else 1
                                    frames=[graphics.menu_wire(view,index=i) for i in range(count)]
                                    held_before=panel.thermal_test_hold
                                    panel.thermal_test_hold=True
                                    try:
                                        display_clock[0]=max(display_clock[0],panel.reactor.monotonic())
                                        for _ in range(2000):
                                            now=display_clock[0];due=panel._tick(now)
                                            require(panel.thermal_test_hold,'Menu cleared the thermal hold flag')
                                            if (panel.stream.previous==panel.stream.latest==panel._render_wire
                                                    and bytes(lcd.ram)==panel._render_wire):break
                                            display_clock[0]=max(now+.0001,min(now+.01,due))
                                        else:raise AssertionError('Menu OLED froze during thermal hold')
                                        require(panel._render_wire in frames,'OLED did not render its running owner')
                                        require(panel.stream.active_work,'Menu bypassed the work UART budget')
                                    finally:panel.thermal_test_hold=held_before
                                ui._phase=original_phase
                                try:shared_menu=inspect_shared_menu(rig,panel,package,render_running)
                                finally:ui._phase=record_phase
        require(set(screens)=={'X','Y','XY','Z','XYZ'},'A Home icon has no installed render evidence')
        if preview is not None:save_oled_preview(screens,preview)
        return dict(status='PASS_INSTALLED_R24_HOME_CYCLES',case_count=len(rows),cases=rows,
                    shared_menu=shared_menu,
                    oled_screens={axis:dict(message=screen['message'],
                                           frame_sha256=[sha256(frame).hexdigest() for frame in screen['frames']])
                                  for axis,screen in screens.items()},
                    refusal_count=len(refusals),refusals=refusals,
                    serial_opened=False,firmware_flashed=False,hardware_tested=False)
    finally:
        executor=printer.lookup_object('aio_executor',None)
        if executor is not None:executor._handle_disconnect()
        loop.finalize()

def check(destination):
    import inventure_bootstrap as bootstrap
    from inventure_bootstrap_config import stage_config,_set_scalar
    destination.mkdir(parents=True,exist_ok=False)
    source=destination/'source'
    shutil.copytree(ROOT/'tests/fixtures/field_config_20261125',source)
    gcodes=destination/'gcodes';gcodes.mkdir()
    with bootstrap.procedure_profile('operator-home-r24'):
        host=bootstrap.prepare_host(destination/'runtime')
        import importlib.util
        spec=importlib.util.spec_from_file_location('r24_installer',ROOT/'install-new-host.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        config_module=installer.updater.klipper_config_module(host)
        _set_scalar(config_module,source/'printer.cfg','virtual_sdcard','path',str(gcodes))
        config=Path(stage_config(host,destination/'config',source/'printer.cfg',serial='/dev/ttyS5',
                                 baud=750000,gcodes_path=gcodes)['config_path'])
        installer.require_identical_configuration(host,source/'printer.cfg',config)
        before={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}
        result=subprocess.run([sys.executable,__file__,'--klipper',str(host),'--config',str(config),
                               '--oled-preview',str(destination/'R24_HOME_OLED.png')],
                              text=True,capture_output=True,timeout=120)
        (destination/'cycles.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(result.stdout+'\n'+result.stderr)
        report=json.loads(result.stdout)
        if before!={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}:
            raise RuntimeError('Cycle test changed printer configuration')
        bootstrap.verify_host(host)
    report['configuration_unchanged']=True
    (destination/'R24_HOME_CYCLES.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--klipper',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--oled-preview',type=Path)
    args=parser.parse_args()
    if args.klipper and args.config:report=inspect(args.klipper.absolute(),args.config.absolute(),args.oled_preview)
    elif args.destination:
        with redirect_stdout(io.StringIO()):report=check(args.destination.absolute())
    else:
        with tempfile.TemporaryDirectory(prefix='inventure-r24-home-') as temp:
            with redirect_stdout(io.StringIO()):report=check(Path(temp)/'verified')
    print(json.dumps(report,indent=2))
