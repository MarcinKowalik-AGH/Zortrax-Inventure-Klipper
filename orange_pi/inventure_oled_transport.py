# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure OLED: generation-fenced DMA, bounded frames and M300 window planning.

Wire ABI stays Inventure: 32 encoded / 256 decoded bytes and 64-bit generation.
The compression/window planner is ported from M300 6c359c8b; clocks, GPIO and
bootloader selection remain owned by the Inventure MCU.
"""
import math
import itertools
from collections import deque

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
    return _dirty_regions(before,after,bounding,max_regions)


def _dirty_regions(before,after,bounding,max_regions):
    def cost(w):
        y0,y1,c0,c1=w
        size=(y1-y0+1)*(c1-c0+1)*2
        # Each rectangle needs five ACK-paced command/data operations.
        return 5 + (size+31)//32
    y0,y1,c0,c1=bounding
    if y0//8==y1//8 and c0//8==c1//8:
        return (bounding,)
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
    def __init__(self, timeout=1.0, refresh_interval=0.2, generation_seed=0):
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
        self.display_mode='unknown';self.requested_mode='awake';self._mode_target=None

    def set_display_mode(self,mode):
        if mode not in ('awake','dim','off'):
            raise ValueError('Invalid OLED display mode')
        self.requested_mode=mode

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
        self.display_mode='unknown';self._mode_target=None
        self.operations=iter(init_operations(self.target))
        self.pending=None;self.pending_op=None;self.retry_at=None;self.retry_count=0
        self.ready_after=now;self.next_frame=now

    def stop(self,reason=None):
        self.enabled=False;self.generation_frames=0;self.phase='fault' if reason else 'stopped'
        self.operations=None;self.pending=None;self.pending_op=None;self.retry_at=None
        self.previous=None;self.target=None
        self.display_mode='unknown';self._mode_target=None
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
            if self.display_mode!=self.requested_mode:
                self._mode_target=self.requested_mode
                self.operations=iter(mode_operations(self.display_mode,self._mode_target))
            elif self.requested_mode=='off':return None
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
            self.windows=_dirty_regions(self.previous,self.target,window,16) if self.previous is not None else (window,)
            self.operations=itertools.chain.from_iterable(
                frame_operations(self.target,*region) for region in self.windows)
            self.phase='updating'
        try:kind,args,delay=next(self.operations)
        except StopIteration:
            if self._mode_target is not None:
                self.display_mode=self._mode_target;self._mode_target=None
                self.operations=None;return None
            self.display_mode='awake' if self.generation_frames==0 else self.display_mode
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


def mode_operations(previous,requested,fast=False):
    emit=fast_command if fast else command
    if requested=='off':
        yield from emit(0xae)
    else:
        yield from emit(0xc7,bytes([1 if requested=='dim' else 15]))
        if previous=='off':yield from emit(0xaf)


def fast_blocks(data, dc=1):
    """<=32 encoded bytes, <=256 decoded bytes; entropy falls back to raw.

    One bounded pass fills each encoded packet, avoiding repeated compression
    of ever-shorter prefixes on the Orange Pi. High entropy uses 32 raw bytes.
    """
    data = bytes(data)
    offset = 0
    while offset < len(data):
        end = min(offset+256, len(data))
        i = offset
        encoded = bytearray()
        while i < end and len(encoded) <= 30:
            run = 1
            while i+run < end and run < 128 and data[i+run] == data[i]:
                run += 1
            if run >= 3:
                encoded.extend((128 | (run-1), data[i])); i += run
            else:
                start = i
                cap = min(end, i+31-len(encoded))
                while i < cap:
                    if i+2 < end and data[i] == data[i+1] == data[i+2]:
                        break
                    i += 1
                encoded.append(i-start-1)
                encoded.extend(data[start:i])
        count = i-offset
        encoded = bytes(encoded)
        if len(encoded) <= 32 and len(encoded)+2 < count:
            yield ('burst', (dc, 1, count, encoded), 0.)
            offset += count
        else:
            raw = data[offset:offset+32]
            yield ('burst', (dc, 0, len(raw), raw), 0.)
            offset += len(raw)


def fast_command(cmd, data=b'', delay=0.):
    yield ('burst', (0, 0, 1, bytes([cmd])), delay if not data else 0.)
    if data:
        yield ('burst', (1, 0, len(data), bytes(data)), delay)


def fast_frame_operations(wire, first_row=0, last_row=63, first_col=0, last_col=63):
    if (len(wire) != 8192 or not 0 <= first_row <= last_row < 64
            or not 0 <= first_col <= last_col < 64):
        raise ValueError('Invalid OLED frame/window')
    yield from fast_command(0x15, bytes([0x1c+first_col, 0x1c+last_col]))
    yield from fast_command(0x75, bytes([first_row, last_row]))
    yield from fast_command(0x5c)
    start, stop = first_col*2, (last_col+1)*2
    packed = b''.join(wire[y*128+start:y*128+stop]
                      for y in range(first_row, last_row+1))
    yield from fast_blocks(packed)


def _gap_plans(before, after, window):
    """Try only the widest untouched band in each axis."""
    y0, y1, c0, c1 = window
    points = []
    for row in range(y0, y1 + 1):
        offset = row * 128
        if before[offset:offset + 128] == after[offset:offset + 128]:
            continue
        for col in range(c0, c1 + 1):
            i = offset + col * 2
            if before[i:i + 2] != after[i:i + 2]:
                points.append((row, col))

    def bounds(group):
        ys, cols = zip(*group)
        return min(ys), max(ys), min(cols), max(cols)

    for axis in (0, 1):
        occupied = sorted({p[axis] for p in points})
        gaps = ((right - left, left) for left, right in zip(occupied, occupied[1:]))
        width, cut = max(gaps, default=(0, 0))
        if width > 1:
            yield (bounds(p for p in points if p[axis] <= cut),
                   bounds(p for p in points if p[axis] > cut))


def optimized_frame_operations(before, after, regions):
    """Choose up to four encoded plans with the existing OLED ABI."""
    if not regions:
        return ()
    bounding = diff_window(before, after)
    plans = dict.fromkeys(itertools.chain((regions, (bounding,)),
                                         _gap_plans(before, after, bounding)))
    encoded = {}

    def operations(plan):
        result = []
        for window in plan:
            if window not in encoded:
                encoded[window] = tuple(fast_frame_operations(after, *window))
            result.extend(encoded[window])
        return tuple(result)

    def cost(ops):
        # Approximate one burst and its ACK using native serial envelopes.
        return sum(len(args[3]) + 52 + (args[2] >= 96) for _, args, _ in ops)

    best = operations(regions)
    best_cost = cost(best)
    baseline_count = len(best)
    baseline_wire = sum(args[2] for _, args, _ in best)
    for plan in plans:
        if plan == regions:
            continue
        # SSD1322 uses two bytes per addressable column and seven setup
        # bytes per window. Reject additional SPI work before encoding.
        if sum(7 + 2 * (y1 - y0 + 1) * (c1 - c0 + 1)
               for y0, y1, c0, c1 in plan) > baseline_wire:
            continue
        candidate = operations(plan)
        if len(candidate) > baseline_count:
            continue
        candidate_cost = cost(candidate)
        if candidate_cost < best_cost:
            best, best_cost = candidate, candidate_cost
    return best


def fast_init_operations(wire):
    # Inventure begins/revokes the generation before changing RESET. Keep its
    # audited supply delays and SSD1322 setup, including the first full image.
    yield ('begin',(),.500)
    yield ('control',(0,1),.010)
    yield ('control',(1,1),.020)
    for cmd,data in INIT:
        yield from fast_command(cmd,data)
    yield from fast_frame_operations(wire)
    yield from fast_command(0xaf,delay=.200)


class FastOledStream(OledStream):
    """Four FIFO DMA credits; one target and one replaceable latest image.

    All pending operations belong to one generation. A later successful ACK
    in the MCU FIFO can prove predecessors, but no burst is retransmitted:
    repeating an SSD1322 RAM write would advance the pixel address twice.
    A timeout discards the display session; the adapter starts a new full one.
    """
    def __init__(self,timeout=1.,refresh_interval=.2,generation_seed=0,
                 work_budget=15000.,interactive_interval=.020,
                 work_interval=.100,optimize_windows=True):
        super().__init__(timeout,refresh_interval,generation_seed)
        if not math.isfinite(work_budget) or not 1.<=work_budget<=1000000.:
            raise ValueError('Invalid OLED work UART budget')
        if not .020<=interactive_interval<=.200 or not .050<=work_interval<=1.:
            raise ValueError('Invalid OLED interactive/work interval')
        self.window=4;self.work_budget=work_budget
        self.interactive_interval=interactive_interval;self.work_interval=work_interval
        self.optimize_windows=bool(optimize_windows);self.optimized_frames=0
        self.active_work=False;self.inflight=deque();self.peak_pending=0
        self.coalesced_requests=0;self.recovered_acks=0
        self.payload_bytes=0;self.wire_bytes=0
        self.last_payload_bytes=0;self.last_frame_ms=None
        self.frame_samples=deque(maxlen=128)
        # A packet's conservative envelope includes generation, token, args,
        # ACK and native framing. At most one four-packet burst earns credit.
        self.work_credit=336.;self.credit_at=0.;self.budget_at=0.
        self.next_interactive=0.;self.exhausted=False;self.barrier=False
        self._held=None;self._initializing=True
        self._frame_payload=0;self.frame_started_at=0.
        self.timeout_context=None

    def request(self,wire,urgent=False):
        if self.operations is not None and wire!=self.latest and self.latest!=self.target:
            self.coalesced_requests+=1
        super().request(wire,urgent)

    def start(self,now):
        super().start(now)
        self.window=4;self.window_rect=(0,63,0,63)
        self.inflight.clear();self.pending=None
        self.operations=iter(fast_init_operations(self.target))
        self.exhausted=False;self.barrier=False;self._held=None
        self._initializing=True;self.frame_started_at=now
        self._frame_payload=self.payload_bytes
        self.next_interactive=now;self.credit_at=now;self.budget_at=now
        self.work_credit=336.

    def stop(self,reason=None):
        super().stop(reason)
        self.inflight.clear();self._held=None
        self.exhausted=False;self.barrier=False

    def ack(self,token,now,generation=None,result=0,processed_at=None):
        tokens=[p[0] for p in self.inflight]
        if (not self.enabled or type(token) is not int or
                generation!=self.generation or token not in tokens):
            self.ignored_acks+=1;return False
        processed=now if processed_at is None else processed_at
        count=tokens.index(token)+1
        confirmed=tuple(itertools.islice(self.inflight,count))
        if (not math.isfinite(now) or not math.isfinite(processed)
                or now>processed+.001 or any(now<p[3] or now>=p[1]
                    or processed>=p[1] for p in confirmed)):
            self.ignored_acks+=1
            self.stop('OLED stale/invalid acknowledgement');return False
        if result!=0:
            self.stop('OLED MCU rejected operation (%d)'%result);return False
        for _ in range(count):
            _,_,delay,_=self.inflight.popleft()
        if delay:self.ready_after=max(now,processed)+delay
        self.pending=self.inflight[0] if self.inflight else None
        self.acked+=count;self.recovered_acks+=count-1
        return True

    def _commit(self,now):
        self.operations=None;self.exhausted=False
        if self._mode_target is not None:
            self.display_mode=self._mode_target;self._mode_target=None
            return
        self.previous=self.target;self.phase='ready'
        self.generation_frames+=1;self.ever_ready=True;self.completed_frames+=1
        self.last_complete_at=now;self.last_frame_bytes=self.target
        self.last_window=self.window_rect;self.last_windows=self.windows
        self.committed_wire_bytes+=sum((y1-y0+1)*(c1-c0+1)*2
            for y0,y1,c0,c1 in self.windows)
        if self.windows==((0,63,0,63),):self.full_frames+=1
        else:self.partial_frames+=1
        self.last_frame_ms=1000.*(now-self.frame_started_at)
        self.last_payload_bytes=self.payload_bytes-self._frame_payload
        if self._initializing:self.display_mode='awake'
        else:self.frame_samples.append(self.last_frame_ms)
        if self.previous==self.latest:self.urgent_refresh=False
        self.next_frame=now+self.refresh_interval

    def step(self,now):
        if not self.enabled:return None
        if not math.isfinite(now):self.stop('OLED invalid clock');return None
        if self.inflight and now>=self.inflight[0][1]:
            self.timeout_context={'time':now,'generation':self.generation,
                'pending':[{'token':p[0],'deadline':p[1]} for p in self.inflight],
                'sent':self.sent,'acked':self.acked}
            self.stop('OLED acknowledgement timeout')
            raise TimeoutError(self.failure)
        if now<self.ready_after:return None
        if self.barrier:
            if self.inflight:return None
            self.barrier=False
        if self.exhausted:
            if not self.inflight:self._commit(now)
            return None
        if self.operations is None:
            if self.display_mode!=self.requested_mode:
                self._mode_target=self.requested_mode
                self.operations=iter(mode_operations(self.display_mode,self._mode_target,True))
            elif self.requested_mode=='off':return None
            elif self.previous==self.latest:
                self.urgent_refresh=False;return None
            else:
                due=self.next_interactive if self.urgent_refresh else self.next_frame
                if now<due:return None
                self.target=self.latest;self._initializing=False
                self.frame_started_at=now;self._frame_payload=self.payload_bytes
                self.next_interactive=now+(self.work_interval if self.active_work
                                            else self.interactive_interval)
                self.window_rect=diff_window(self.previous,self.target)
                self.windows=diff_regions(self.previous,self.target)
                if self.optimize_windows and self.urgent_refresh and not self.active_work:
                    self.operations=iter(optimized_frame_operations(self.previous,self.target,self.windows))
                    self.optimized_frames+=1
                else:
                    self.operations=itertools.chain.from_iterable(
                        fast_frame_operations(self.target,*w) for w in self.windows)
                self.phase='updating'
        if len(self.inflight)>=4:return None
        if self._held is None:
            try:self._held=next(self.operations)
            except StopIteration:
                self.exhausted=True
                if not self.inflight:self._commit(now)
                return None
        kind,args,delay=self._held
        if (kind in ('begin','control') or delay) and self.inflight:return None
        if self.active_work:
            cost=(len(args[3]) if kind=='burst' else 0)+52
            self.work_credit=min(336.,self.work_credit+max(0.,now-self.credit_at)*self.work_budget)
            self.credit_at=now
            if cost>self.work_credit+1.e-9:
                self.budget_at=now+(cost-self.work_credit)/self.work_budget
                return None
            self.work_credit-=cost;self.budget_at=now
        if self.token>=MAX_TOKEN:
            self.stop('OLED token space exhausted');return None
        self._held=None;self.barrier=kind in ('begin','control') or bool(delay)
        self.token+=1;self.inflight.append((self.token,now+self.timeout,delay,now))
        self.pending=self.inflight[0];self.peak_pending=max(self.peak_pending,len(self.inflight))
        self.sent+=1
        if kind=='burst':self.payload_bytes+=len(args[3]);self.wire_bytes+=args[2]
        return kind,self.token,args

    def next_wakeup(self,now):
        if not self.enabled:return now+1.
        if self.inflight:
            deadline=self.inflight[0][1]
            return min(deadline,self.budget_at) if self._held is not None and self.budget_at>now else deadline
        if self.ready_after>now:return self.ready_after
        if self.operations is not None or self.display_mode!=self.requested_mode:
            return max(now+.0001,self.budget_at) if self.active_work else now+.0001
        if self.requested_mode!='off' and self.previous!=self.latest:
            return max(now+.0001,self.next_interactive if self.urgent_refresh else self.next_frame)
        return now+1.


