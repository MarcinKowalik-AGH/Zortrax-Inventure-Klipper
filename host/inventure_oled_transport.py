# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, ACK-paced OLED transaction state machine; no printer dependencies."""
import math
import itertools

MAX_TOKEN = 0xffffffff
MAX_GENERATION = 0xffffffffffffffff

INIT = ((0xFD,b'\x12'),(0xAE,b''),(0xB3,b'\x91'),(0xCA,b'\x3f'),
        (0xA2,b'\x00'),(0xA1,b'\x00'),(0xA0,b'\x14\x11'),
        (0xB5,b'\x00'),(0xAB,b'\x01'),(0xB4,b'\xa0\xfd'),
        (0xC1,b'\x9f'),(0xC7,b'\x0f'),(0xB9,b''),(0xB1,b'\xe2'),
        (0xD1,b'\xa2\x20'),(0xBB,b'\x1f'),(0xB6,b'\x08'),
        (0xBE,b'\x07'),(0xA6,b''),(0xA9,b''))


def command(cmd, data=b'', delay=0.0):
    yield ('write', (0,bytes([cmd])), delay if not data else 0.0)
    if data:
        yield ('write',(1,bytes(data)),delay)


def frame_operations(wire, first_row=0, last_row=63, first_col=0, last_col=63):
    """Stream an SSD1322 rectangle in its 4-pixel-wide column address units.

    One SSD1322 column address is TWO packed wire bytes (4 pixels). Each
    row's data are concatenated in row order without changing nibble order.
    Full-screen defaults preserve the TEST01 init byte stream exactly.
    """
    if (len(wire)!=8192 or not 0<=first_row<=last_row<64 or
            not 0<=first_col<=last_col<64):
        raise ValueError('Invalid OLED frame/window')
    yield from command(0x15,bytes([0x1c+first_col,0x1c+last_col]))
    yield from command(0x75,bytes([first_row,last_row]))
    yield from command(0x5c)
    start=first_col*2
    stop=(last_col+1)*2
    # Keep the same <=32-byte transport contract as ABI2.
    packed=b''.join(wire[y*128+start:y*128+stop]
                    for y in range(first_row,last_row+1))
    for offset in range(0,len(packed),32):
        yield ('write',(1,packed[offset:offset+32]),0.0)


def diff_window(before,after):
    """Return minimal rectangular changed area, aligned to 4-pixel columns.

    None means no change. Input is SSD1322 row-major packed 4bpp, 8192B.
    The bounding rectangle may contain some unchanged pixels, which is safe;
    unlike the old implementation it does not resend the entire row width.
    """
    if len(before)!=8192 or len(after)!=8192:
        raise ValueError('Invalid OLED buffer for diff')
    lo_y=64;hi_y=-1;lo_col=64;hi_col=-1
    for y in range(64):
        start=y*128
        if before[start:start+128]==after[start:start+128]:
            continue
        lo_y=min(lo_y,y);hi_y=y
        for c in range(64):
            i=start+2*c
            if before[i:i+2]!=after[i:i+2]:
                lo_col=min(lo_col,c);hi_col=max(hi_col,c)
    if hi_y<0:
        return None
    return (lo_y,hi_y,lo_col,hi_col)



def diff_regions(before, after, max_regions=16):
    """Choose a small set of safe, non-overlapping SSD1322 rectangles.

    The SSD1322 window addresses groups of four pixels (two bytes); the
    conservative alternative is a single exact bounding rectangle. An 8x8
    grid of 32x8-pixel tiles is used only when it decreases the number of
    ACK-paced operations. No previous frame is committed until EVERY window
    has completed, including the final ACK. This is GDDRAM single-page mode:
    there is no unverified A1 page flip on real hardware.
    """
    if type(max_regions) is not int or not 1<=max_regions<=64:
        raise ValueError('Invalid maximum OLED dirty region count')
    bounding=diff_window(before, after)
    if bounding is None:
        return ()
    def cost(w):
        y0,y1,c0,c1=w
        size=(y1-y0+1)*(c1-c0+1)*2
        # Each rectangle needs five ACK-paced command/data operations.
        return 5 + (size+31)//32
    if bounding==(0,63,0,63):
        pass
    # 8 column-address groups (32 visible px) by 8 row tiles; 64 tiles.
    tile_dirty=[[False]*8 for _ in range(8)]
    for ty in range(8):
        for tc in range(8):
            for y in range(ty*8,ty*8+8):
                off=y*128+tc*16
                if before[off:off+16]!=after[off:off+16]:
                    tile_dirty[ty][tc]=True
                    break
    # Coalesce horizontal runs on each tile row, then vertically merge runs
    # sharing precisely the same column span. No windows overlap.
    rects=[]
    active={}
    for ty in range(8):
        runs=[];c=0
        while c<8:
            if not tile_dirty[ty][c]:
                c+=1;continue
            start=c
            while c<8 and tile_dirty[ty][c]:c+=1
            runs.append((start,c-1))
        following={}
        for c0,c1 in runs:
            key=(c0,c1)
            if key in active:
                y0,_,_,_=active[key]
                following[key]=(y0,ty*8+7,c0*8,c1*8+7)
            else:
                following[key]=(ty*8,ty*8+7,c0*8,c1*8+7)
        rects.extend(w for key,w in active.items() if key not in following)
        active=following
    rects.extend(active.values())
    rects.sort(key=lambda w:(w[0],w[2]))
    if len(rects)>max_regions or sum(map(cost,rects))>=cost(bounding):
        return (bounding,)
    return tuple(rects)

def init_operations(wire):
    # Keep RESET asserted while the panel supply settles; never expose old RAM.
    yield ('begin',(),0.500)
    yield ('control',(0,1),0.010)
    yield ('control',(1,1),0.020)
    for cmd, data in INIT:
        yield from command(cmd,data)
    yield from frame_operations(wire)
    yield from command(0xaf,delay=0.200)


class OledStream:
    """One in-flight operation, generation-fenced ACKs, complete-frame commit.

    ready is CURRENT transport readiness. ever_ready/completed_frames are history.
    start() always sends init and a full frame; partial failed writes are discarded.
    ACK proves MCU transaction completion, not optical/readback verification.
    """
    def __init__(self, timeout=1.0, refresh_interval=0.5, generation_seed=0):
        if not 0.1<=timeout<=10 or not 0.2<=refresh_interval<=10:
            raise ValueError('Invalid OLED timeout/refresh interval')
        if type(generation_seed) is not int or not 0<=generation_seed<MAX_GENERATION:
            raise ValueError('Invalid OLED generation seed')
        self.timeout=timeout;self.refresh_interval=refresh_interval
        self.enabled=False;self.failure=None;self.ever_ready=False
        self.latest=bytes(8192);self.previous=None;self.target=None
        self.operations=None;self.pending=None;self.token=0
        self.generation=generation_seed;self.generation_frames=0
        self.ready_after=0.;self.next_frame=0.;self.sent=0;self.acked=0
        self.completed_frames=0;self.ignored_acks=0;self.last_complete_at=None
        self.phase='stopped';self.last_frame_bytes=None
        self.urgent_refresh=False;self.window=(0,63,0,63)
        self.windows=((0,63,0,63),);self.last_windows=None
        self.partial_frames=0;self.full_frames=0;self.committed_wire_bytes=0
        self.last_window=None
        self.retry_enabled=False;self.retry_after=0.45;self.retry_limit=1
        self.retry_at=None;self.retry_count=0;self.retransmits=0
        self.pending_op=None

    @property
    def ready(self):
        return self.enabled and self.failure is None and self.generation_frames>0

    def request(self,wire,urgent=False):
        wire=bytes(wire)
        if len(wire)!=8192:raise ValueError('OLED wire buffer must contain 8192 bytes')
        if urgent and wire!=self.previous:
            self.urgent_refresh=True
        self.latest=wire

    def start(self,now):
        if not math.isfinite(now):raise ValueError('Invalid OLED time')
        if self.generation>=MAX_GENERATION or self.token>=MAX_TOKEN:
            self.stop('OLED sequence space exhausted');raise ValueError(self.failure)
        self.generation+=1;self.generation_frames=0
        self.enabled=True;self.failure=None;self.phase='initializing'
        self.target=self.latest;self.previous=None
        self.window=(0,63,0,63)
        self.windows=((0,63,0,63),);self.last_windows=None
        self.urgent_refresh=False
        self.operations=iter(init_operations(self.target))
        self.pending=None;self.pending_op=None;self.retry_at=None;self.retry_count=0
        self.ready_after=now;self.next_frame=now

    def stop(self,reason=None):
        self.enabled=False;self.generation_frames=0;self.phase='fault' if reason else 'stopped'
        self.operations=None;self.pending=None;self.pending_op=None;self.retry_at=None
        self.previous=None;self.target=None
        self.urgent_refresh=False
        if reason:self.failure=str(reason)

    def ack(self,token,now,generation=None,result=0,processed_at=None):
        if (not self.enabled or self.pending is None or type(token) is not int
                or generation!=self.generation or self.pending[0]!=token):
            self.ignored_acks+=1;return False
        processed=now if processed_at is None else processed_at
        if (not math.isfinite(now) or not math.isfinite(processed)
                or now<self.pending[3] or now>processed+.001
                or now>=self.pending[1] or processed>=self.pending[1]):
            self.ignored_acks+=1;self.stop('OLED stale/invalid acknowledgement');return False
        if result!=0:
            self.stop('OLED MCU rejected operation (%d)'%result);return False
        self.ready_after=now+self.pending[2]
        self.pending=None;self.pending_op=None;self.retry_at=None
        self.acked+=1;return True

    def step(self,now):
        if not self.enabled:return None
        if not math.isfinite(now):self.stop('OLED invalid clock');return None
        if self.pending:
            if now>=self.pending[1]:
                self.stop('OLED acknowledgement timeout');raise TimeoutError(self.failure)
            if (self.retry_enabled and self.pending_op is not None
                    and self.retry_count<self.retry_limit
                    and now>=self.retry_at):
                self.retry_count+=1;self.retransmits+=1
                self.retry_at=self.pending[1]+1.0 # Never retry twice unless enabled above.
                return self.pending_op
            return None
        if now<self.ready_after:return None
        if self.operations is None:
            if self.previous==self.latest:
                self.urgent_refresh=False
                return None
            if now<self.next_frame and not self.urgent_refresh:
                return None
            self.target=self.latest
            if self.previous is None:
                window=(0,63,0,63)
            else:
                window=diff_window(self.previous,self.target)
            if window is None:
                self.urgent_refresh=False
                return None
            self.window=window
            self.windows=diff_regions(self.previous,self.target) if self.previous is not None else (window,)
            self.operations=itertools.chain.from_iterable(
                frame_operations(self.target,*region) for region in self.windows)
            self.phase='updating'
        try:kind,args,delay=next(self.operations)
        except StopIteration:
            self.operations=None;self.previous=self.target;self.phase='ready'
            self.generation_frames+=1;self.ever_ready=True;self.completed_frames+=1
            self.last_complete_at=now;self.last_frame_bytes=self.target
            self.last_window=self.window
            self.last_windows=self.windows
            self.committed_wire_bytes+=sum((y1-y0+1)*(c1-c0+1)*2
                for y0,y1,c0,c1 in self.windows)
            if self.windows==((0,63,0,63),):self.full_frames+=1
            else:self.partial_frames+=1
            # A new button may have been pressed during this transfer; only
            # consume priority when the latest image has actually been shown.
            if self.previous==self.latest:self.urgent_refresh=False
            self.next_frame=now+self.refresh_interval;return None
        if self.token>=MAX_TOKEN:
            self.stop('OLED token space exhausted');return None
        self.token+=1;self.pending=(self.token,now+self.timeout,delay,now)
        self.pending_op=(kind,self.token,args)
        self.retry_count=0;self.retry_at=now+min(self.retry_after,self.timeout*.6)
        self.sent+=1;return self.pending_op
