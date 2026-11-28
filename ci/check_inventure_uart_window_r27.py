"""Execute actual linked F207 sampler/UART code under Cortex-M3 IRQ masks.

One useful instruction is charged one cycle; IT/hint instructions are charged
zero. This is a favourable lower-bound model, not a physical latency reading.
UART arrivals are 1600 cycles apart (750000 baud, 120 MHz, 10 bits/byte).
"""
import argparse,gc,json,struct,subprocess
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE,UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_LR,UC_ARM_REG_SP,UC_ARM_REG_PC,UC_ARM_REG_PRIMASK,UC_ARM_REG_BASEPRI

NOW=1000000000
UART=0x40011400
STOP=0x080ff000
RETURN=0x080ff100
IDLE=0x080ff200

class Image:
    def __init__(self,path,nm):
        self.data=Path(path).read_bytes();self.symbols={}
        for line in subprocess.check_output([nm,'-S',str(path)],text=True).splitlines():
            p=line.split()
            if len(p)==4 and not p[3].startswith('.'):
                self.symbols[p[3].split('.')[0]]=(int(p[0],16),int(p[1],16))
        offset=struct.unpack_from('<I',self.data,28)[0]
        size,count=struct.unpack_from('<HH',self.data,42)
        self.loads=[struct.unpack_from('<8I',self.data,offset+i*size) for i in range(count)]

    def cpu(self):
        uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        for base,size in ((0x08000000,0x100000),(0x20000000,0x20000),(0x40000000,0x30000),(0xe0000000,0x200000)):
            uc.mem_map(base,size)
        for typ,off,va,pa,fs,ms,fl,al in self.loads:
            if typ==1 and fs:uc.mem_write(va,self.data[off:off+fs])
        if 'inventure_sampler_uart_init' in self.symbols:
            uc.reg_write(UC_ARM_REG_SP,0x2001f800);uc.reg_write(UC_ARM_REG_LR,STOP|1)
            uc.emu_start(self.symbols['inventure_sampler_uart_init'][0]|1,STOP,count=1000)
        return uc

def inspect(image,hot,tach,phase,initial_base=0,mode=1,fixture=None,instruction_cycles=1):
    uc=image.cpu();symbols=image.symbols
    state=dict(cycles=0,arrivals=0,overruns=0,received=[],pending=None,interrupt=False,
        event=None,context=None,skip=False,open_cycles=0,open_without_ceiling=0,aborted=False,
        blocked_run=0,max_blocked_run=0,observed_overruns=0)
    def word(a,v):uc.mem_write(a,struct.pack('<I',v&0xffffffff))
    def put(name,val):
        a,n=symbols[name];uc.mem_write(a,int(val).to_bytes(n,'little'))
    table=bytes(uc.mem_read(*symbols['iv9_lut']))
    adc=next(raw for raw,temp in struct.iter_unpack('<Hh',table) if temp==29)
    for name,value in dict(control_mode=mode,initialized=1,sample_started=1,cool_native_claimed=1,
            cool_native_seen=255,heartbeat_live=1,heartbeat_deadline=NOW+600000000,guard_div=199).items():put(name,value)
    uc.mem_write(symbols['adc_time'][0],struct.pack('<8I',*[NOW-1000]*8))
    uc.mem_write(symbols['latest_adc'][0],struct.pack('<8H',330 if hot else 185,185,*[adc]*6))
    if tach:word(0x40021010,(1<<9)|(1<<11)|(1<<13)|(1<<14))
    arg=0x2001e000;word(arg+8,NOW)
    callback = 'sample_event'
    if fixture is not None:
        callback = fixture(uc, symbols, put, word)
    uc.reg_write(UC_ARM_REG_SP,0x2001f800);uc.reg_write(UC_ARM_REG_LR,STOP|1)
    uc.reg_write(UC_ARM_REG_R0,arg);uc.reg_write(UC_ARM_REG_PRIMASK,1);uc.reg_write(UC_ARM_REG_BASEPRI,initial_base)
    uc.mem_write(IDLE,b'\x00\xbf\xfd\xe7') # NOP / branch back, outside the sampler
    root_done=False
    def code(u,address,size,_):
        if address==RETURN:
            state['event']='return';u.emu_stop();return
        if address in {symbols[name][0] for name in ('sched_try_shutdown','sched_shutdown') if name in symbols}:
            state['aborted']=True;u.emu_stop();return
        cycles=state['cycles']
        while state['arrivals']<2 and cycles>=phase+1600*state['arrivals']:
            byte=0x41+state['arrivals'];state['arrivals']+=1
            if state['pending'] is not None:
                # F207 retains the unread DR byte and loses the new one.
                state['overruns']+=1;word(UART,40) # RXNE | ORE
            else:
                state['pending']=byte;word(UART,32);word(UART+4,byte)
        # Priority 0 is never masked by BASEPRI, but is masked by PRIMASK.
        if state['pending'] is not None and not state['interrupt'] and not u.reg_read(UC_ARM_REG_PRIMASK):
            state['context']=u.context_save();state['event']='interrupt';u.emu_stop();return
        if not root_done and not state['interrupt'] and not u.reg_read(UC_ARM_REG_PRIMASK):
            state['open_cycles']+=1
            if u.reg_read(UC_ARM_REG_BASEPRI)!=16:state['open_without_ceiling']+=1
        instruction=struct.unpack('<H',u.mem_read(address,2))[0]
        if not root_done and not state['interrupt']:
            if u.reg_read(UC_ARM_REG_PRIMASK):
                state['blocked_run']+=int(instruction&0xff00!=0xbf00)
                state['max_blocked_run']=max(state['max_blocked_run'],state['blocked_run'])
            else:
                state['blocked_run']=0
        state['cycles']+=instruction_cycles*int(instruction&0xff00!=0xbf00)
        word(0xe0001004,NOW+state['cycles'])
        if root_done and state['cycles']>=5000 and state['pending'] is None:
            u.emu_stop()
    def read(u,access,address,size,value,_):
        if state['interrupt'] and address==UART:
            status=struct.unpack('<I',uc.mem_read(UART,4))[0]
            state['observed_overruns']+=int(bool(status&8))
        if state['interrupt'] and address==UART+4 and state['pending'] is not None:
            state['received'].append(state['pending']);state['pending']=None;word(UART,0)
    hooks=[uc.hook_add(UC_HOOK_CODE,code),uc.hook_add(UC_HOOK_MEM_READ,read,begin=UART,end=UART+7)]
    pc=symbols[callback][0]|1
    guard_cycles=None
    for _ in range(20):
        state['event']=None
        uc.emu_start(pc,STOP,count=200000)
        if state['aborted']:raise AssertionError('Sampler invoked a guard shutdown in the healthy fixture')
        if state['event']=='interrupt':
            state['interrupt']=True;uc.reg_write(UC_ARM_REG_LR,RETURN|1);pc=symbols['USARTx_IRQHandler'][0]|1
        elif state['event']=='return':
            uc.context_restore(state['context']);state['context']=None;state['interrupt']=False
            pc=uc.reg_read(UC_ARM_REG_PC)|1
        elif not root_done:
            guard_cycles=state['cycles']
            assert uc.reg_read(UC_ARM_REG_PRIMASK)==1,'Sampler did not restore caller PRIMASK'
            assert uc.reg_read(UC_ARM_REG_BASEPRI)==initial_base,'Sampler did not restore caller BASEPRI'
            assert uc.reg_read(UC_ARM_REG_R0)==1,'Sampler rescheduling result changed'
            root_done=True;uc.reg_write(UC_ARM_REG_PRIMASK,0);pc=IDLE|1
        else:break
    else:raise AssertionError('UART model did not terminate')
    logic={name:bytes(uc.mem_read(*symbols[name])).hex() for name in
        ('control_mode','control_latched','native_hot_mask','guard_div','heartbeat_live')}
    logic['next_sampler_clock']=struct.unpack('<I',uc.mem_read(arg+8,4))[0]
    if fixture is not None:
        logic['zh'] = bytes(uc.mem_read(*symbols['zh'])).hex()
    rx_size=int.from_bytes(uc.mem_read(*symbols['receive_pos']),'little')
    actual_bytes=list(uc.mem_read(symbols['receive_buf'][0],rx_size))
    actual_overruns=int.from_bytes(uc.mem_read(*symbols['uart_overrun']),'little')
    assert actual_bytes==state['received'],(actual_bytes,state['received'])
    assert actual_overruns==state['observed_overruns'],(actual_overruns,state['observed_overruns'])
    result=dict(hot=hot,tach=tach,phase=phase,mode=mode,initial_base=initial_base,guard_cycles=guard_cycles,
        arrivals=state['arrivals'],overruns=state['overruns'],received=state['received'],
        firmware_received=actual_bytes,firmware_overrun_counter=actual_overruns,
        max_uart_blocked_instructions=state['max_blocked_run'],
        preemptible_instructions=state['open_cycles'],unprotected_timer_instructions=state['open_without_ceiling'],logic=logic)
    for handle in hooks:uc.hook_del(handle)
    del uc;gc.collect()
    return result

def check(baseline,candidate,nm):
    old,new=Image(baseline,nm),Image(candidate,nm)
    rows=[]
    for mode in (0,1,2):
        for hot in (False,True):
            for tach in (False,True):
                for phase in range(0,1600,128):
                    for ceiling in (0,32):
                        before=inspect(old,hot,tach,phase,ceiling,mode);after=inspect(new,hot,tach,phase,ceiling,mode)
                        assert after['arrivals']==2 and after['received']==[65,66] and not after['overruns'],after
                        assert after['preemptible_instructions']>0 and not after['unprotected_timer_instructions'],after
                        assert before['logic']==after['logic'],(before,after)
                        rows.append(dict(before=before,after=after))
    assert any(row['before']['overruns'] for row in rows),'The preceding firmware did not reproduce the observed receive overrun'
    # A longjmp from a guard must not leave a priority ceiling after shutdown.
    uc=new.cpu();uc.reg_write(UC_ARM_REG_SP,0x2001f800);uc.reg_write(UC_ARM_REG_LR,STOP|1)
    uc.reg_write(UC_ARM_REG_PRIMASK,1);uc.reg_write(UC_ARM_REG_BASEPRI,16)
    uc.mem_write(new.symbols['uart_sample_active'][0],b'\x01')
    uc.emu_start(new.symbols['inventure_sampler_uart_shutdown'][0]|1,STOP,count=1000)
    assert uc.reg_read(UC_ARM_REG_BASEPRI)==0 and uc.reg_read(UC_ARM_REG_PRIMASK)==1
    assert bytes(uc.mem_read(new.symbols['uart_sample_active'][0],1))==b'\x00'
    del uc;gc.collect()
    # Normalize all possible inherited AIRCR groupings without changing the
    # caller's PRIMASK. Otherwise a bootloader can make UART/SysTick share
    # preemption priority despite their distinct NVIC priority numbers.
    for grouping in range(8):
        for primask in (0,1):
            uc=new.cpu();uc.mem_write(0xe000ed0c,struct.pack('<I',grouping<<8))
            uc.reg_write(UC_ARM_REG_SP,0x2001f800);uc.reg_write(UC_ARM_REG_LR,STOP|1)
            uc.reg_write(UC_ARM_REG_PRIMASK,primask)
            uc.emu_start(new.symbols['inventure_sampler_uart_init'][0]|1,STOP,count=1000)
            assert struct.unpack('<I',uc.mem_read(0xe000ed0c,4))[0]&(7<<8)==0
            assert uc.reg_read(UC_ARM_REG_PRIMASK)==primask
            del uc;gc.collect()
    return dict(status='PASS_REAL_F207_SAMPLER_UART_PRIORITY_WINDOW',cases=len(rows),
        baseline_overrun_cases=sum(bool(row['before']['overruns']) for row in rows),
        candidate_overruns=0,uart_byte_cycles=1600,clock_hz=120000000,
        timing_model='one useful instruction/cycle; IT/hints zero; not a physical measurement',
        lower_bound_native_cycles=min(row['before']['guard_cycles'] for row in rows if row['before']['mode']==1),
        inherited_priority_groups_checked=8,shutdown_priority_restoration=True,hardware_tested=False,rows=rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('baseline',type=Path);p.add_argument('candidate',type=Path)
    p.add_argument('--nm',default='arm-none-eabi-nm');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report=check(a.baseline,a.candidate,a.nm)
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key!='rows'},indent=2))
