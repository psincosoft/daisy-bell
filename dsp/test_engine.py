"""Run the singing engine in a 6502 emulator (py65) against a model of the MEGA65 it uses.

Modelled: bank 0 below $2000 and bank 5 at $2000-$7FFF (BANK 5: SYS $2000), attic RAM with the two
streams, the hardware multiplier ($D770-$D77F, an unsigned 32x32 product as the core computes it),
enhanced DMA jobs (copy, megabyte options), audio DMA channel 0 (its read position advances by one
16-bit sample every 2^24/FREQ CPU cycles; every sample it fetches is recorded, so the test hears
what the DMA plays), the raster line and the frame counter (PAL50: 1,296 cycles a line, 625 lines),
TEXTYPOS, the hardware key queue, and the four SIDs (writes recorded).
Time is py65's NMOS cycle count taken as CPU cycles at 40.5 MHz; the 45GS02's own timing is not
modelled, so the CPU share this reports is an estimate for NMOS timings.

Checks: the samples the DMA plays equal the reference model's, sample for sample; no underrun;
every SID write of the play stream reaches the SID, in order, once its time has played; the
screen and the text position as the events say; the exit code and the results for BASIC; RAM
writes only inside the engine's areas, the screen and the mailbox (--strict); Q stops it.
Usage: python3 test_engine.py BUILD_DIR [seconds] [--strict] [--quit=SECONDS] [--ntsc]
"""
import json
import sys
from pathlib import Path

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kl  # noqa: E402
import preview  # noqa: E402
import synth  # noqa: E402
import tables  # noqa: E402

STUB = 0x17F0
CYC_LINE = 1296
LINES = 625
CYC_FRAME = CYC_LINE * LINES


def syms(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        k, v = line.split(' = ')
        out[k] = int(v[1:], 16)
    return out


class Machine:
    def __init__(self, build, strict=False, keys=None, ntsc=False):
        b = Path(build)
        self.b = b
        self.sym = syms(b / 'engine.sym')
        self.cpu = [0] * 0x10000
        eng = (b / 'engine.bin').read_bytes()
        self.cpu[0x2000:0x2000 + len(eng)] = list(eng)
        self.attic = bytearray(0x20000)
        vox = (b / 'DAISY-BELL.VOX').read_bytes()[2:]
        evt = (b / 'DAISY-BELL.EVT').read_bytes()[2:]
        self.attic[0:len(vox)] = vox
        self.attic[0x10000:0x10000 + len(evt)] = evt
        self.vox, self.evt = vox, evt
        for i, v in enumerate([0x20, 0x00, 0x20, 0xEA]):
            self.cpu[STUB + i] = v
        # the mailbox as BASIC fills it
        mb = 0x1E00
        self.cpu[mb + 0] = 1 if ntsc else 0
        self.cpu[mb + 1] = 204                  # the voice's volume, as daisy-bell.b65 sets it
        lines = 525 if ntsc else 625
        self.cpu[mb + 2], self.cpu[mb + 3] = lines & 255, lines >> 8
        self.cpu[mb + 4] = 208 if ntsc else 210
        self.cpu[mb + 5] = 0x08
        # the screen: spaces
        for a in range(0x0800, 0x0800 + 1000):
            self.cpu[a] = 32
        self.strict = strict
        self.io = {}
        self.mula = [0] * 4
        self.mulb = [0] * 4
        self.dmareg = {}
        self.sid_writes = []          # (cycle, register offset, value)
        self.screen_writes = []       # (cycle, address, value)
        self.textypos = [(0, 0x68, 0x00)]
        self.bad = []
        self.io_bad = []
        self.keys = sorted(keys or [])
        self.keyq = []
        self.textyl, self.textyh = 0x68, 0x50   # the high nibble: sprite tile bits, to be kept
        # audio DMA channel 0
        self.ch = {'ctl': 0, 'base': 0, 'freq': 0, 'top': 0, 'vol': 0, 'cur': 0}
        self.auden = 0x08                       # PWM/PDM bit set, AUDEN off, as found
        self.panvol = 0x5A                      # a value to see restored
        self.play_start = None
        self.fetched = 0
        self.played = []
        self.fetch_cycles = []
        self.cyc = 0
        self.dma_bytes = 0
        self.curpc = STUB
        self.vol_at_start = None

    # ---------------- physical memory, as the DMA sees it ----------------
    def get(self, a):
        if a < 0x2000:
            return self.cpu[a]
        if 0x52000 <= a < 0x58000:
            return self.cpu[a - 0x50000]
        if 0x8000000 <= a < 0x8000000 + len(self.attic):
            return self.attic[a - 0x8000000]
        raise RuntimeError(f"DMA reads ${a:07X}")

    def put(self, a, v):
        if 0x52000 <= a < 0x58000:
            self.cpu[a - 0x50000] = v
            if self.strict and not (0x55000 <= a < 0x55200):
                self.bad.append(('dma', a))
            return
        self.bad.append(('dma', a))
        raise RuntimeError(f"DMA writes ${a:07X}")

    def dma(self, lo):
        r = self.dmareg
        a = (r.get(0xD704, 0) << 20) | (r.get(0xD702, 0) << 16) | (r.get(0xD701, 0) << 8) | lo
        smb = dmb = 0
        fmt = None
        while True:
            o = self.get(a)
            a += 1
            if o == 0:
                break
            if o == 0x0B:
                fmt = 'B'
            elif o == 0x80:
                smb = self.get(a)
                a += 1
            elif o == 0x81:
                dmb = self.get(a)
                a += 1
            else:
                raise RuntimeError(f"DMA option ${o:02X}")
        assert fmt == 'B'
        cmd = self.get(a)
        cnt = self.get(a + 1) | self.get(a + 2) << 8
        src = self.get(a + 3) | self.get(a + 4) << 8
        sb = self.get(a + 5)
        dst = self.get(a + 6) | self.get(a + 7) << 8
        db = self.get(a + 8)
        assert cmd == 0 and sb < 16 and db < 16, (cmd, sb, db)
        s = (smb << 20) | (sb << 16) | src
        d = (dmb << 20) | (db << 16) | dst
        for i in range(cnt):
            self.put(d + i, self.get(s + i))
        self.dma_bytes += cnt

    # ---------------- audio DMA ----------------
    def audio_on(self):
        return (self.auden & 0x80) and (self.ch['ctl'] & 0x80)

    def advance_audio(self):
        if self.play_start is None:
            return
        due = ((self.cyc - self.play_start) * self.ch['freq']) >> 24
        while self.fetched < due:
            k = self.fetched % 2048
            off = (self.ch['base'] & 0xFFFF) + 2 * k
            lo = self.cpu[off]
            hi = self.cpu[off + 1]
            v = lo | hi << 8
            self.played.append(v - 65536 if v & 0x8000 else v)
            self.fetch_cycles.append(self.cyc)
            self.fetched += 1

    def curaddr(self):
        self.advance_audio()
        k = self.fetched % 2048
        return self.ch['base'] + 2 * k

    # ---------------- I/O ----------------
    def io_write(self, a, v):
        self.cyc = self.mpu.processorCycles
        if 0xD770 <= a <= 0xD773:
            self.mula[a - 0xD770] = v
        elif 0xD774 <= a <= 0xD777:
            self.mulb[a - 0xD774] = v
        elif 0xD700 <= a <= 0xD70F:
            self.dmareg[a] = v
            if a == 0xD705:
                self.dma(v)
        elif 0xD400 <= a < 0xD480:
            self.sid_writes.append((self.cyc, a - 0xD400, v))
        elif a == 0xD711:
            was = self.audio_on()
            self.auden = v & 0xBF
            self.start_stop(was)
        elif a == 0xD71C:
            self.panvol = v
        elif 0xD720 <= a <= 0xD72F:
            r = a - 0xD720
            was = self.audio_on()
            if r == 0:
                self.ch['ctl'] = v
            elif 1 <= r <= 3:
                self.ch['base'] = (self.ch['base'] & ~(0xFF << (8 * (r - 1)))) | v << (8 * (r - 1))
            elif 4 <= r <= 6:
                self.ch['freq'] = (self.ch['freq'] & ~(0xFF << (8 * (r - 4)))) | v << (8 * (r - 4))
            elif 7 <= r <= 8:
                self.ch['top'] = (self.ch['top'] & ~(0xFF << (8 * (r - 7)))) | v << (8 * (r - 7))
            elif r == 9:
                self.ch['vol'] = v
            elif 0xA <= r <= 0xC:
                self.ch['cur'] = (self.ch['cur'] & ~(0xFF << (8 * (r - 0xA)))) | v << (8 * (r - 0xA))
            self.start_stop(was)
        elif a == 0xD04E:
            self.textyl = v
            self.textypos.append((self.cyc, self.textyl, self.textyh))
        elif a == 0xD04F:
            self.textyh = v
            self.textypos.append((self.cyc, self.textyl, self.textyh))
        elif a == 0xD610:
            if self.keyq:
                self.keyq.pop(0)
        else:
            self.io_bad.append(('w', a, v))

    def start_stop(self, was):
        on = self.audio_on()
        if on and not was:
            assert self.ch['cur'] == self.ch['base'], "playback starts at the ring's base"
            assert self.ch['ctl'] & 0x43 == 0x43 and not self.ch['ctl'] & 0x30, f"channel flags {self.ch['ctl']:02X}"
            assert (self.ch['top'] - self.ch['base']) & 0xFFFF == 0x1000, "a 4 KB ring"
            self.play_start = self.cyc
            self.fetched = 0
            self.vol_at_start = (self.ch['vol'], self.panvol)
        elif was and not on:
            self.advance_audio()
            self.play_start = None

    def io_read(self, a):
        self.cyc = self.mpu.processorCycles
        if 0xD778 <= a <= 0xD77F:
            pa = sum(b << (8 * i) for i, b in enumerate(self.mula))
            pb = sum(b << (8 * i) for i, b in enumerate(self.mulb))
            return ((pa * pb) >> (8 * (a - 0xD778))) & 255
        if 0xD770 <= a <= 0xD773:
            return self.mula[a - 0xD770]
        if 0xD774 <= a <= 0xD777:
            return self.mulb[a - 0xD774]
        if a == 0xD711:
            return self.auden
        if a == 0xD71C:
            return self.panvol
        if 0xD72A <= a <= 0xD72C:
            return (self.curaddr() >> (8 * (a - 0xD72A))) & 255
        if a == 0xD052:
            return (self.cyc // CYC_LINE % LINES) & 255
        if a == 0xD053:
            return ((self.cyc // CYC_LINE % LINES) >> 8) | 0x00
        if a == 0xD7FA:
            return (self.cyc // CYC_FRAME) & 255
        if a == 0xD04E:
            return self.textyl
        if a == 0xD04F:
            return self.textyh
        if a == 0xD610:
            while self.keys and self.keys[0][0] <= self.cyc:
                self.keyq.append(self.keys.pop(0)[1])
            return self.keyq[0] if self.keyq else 0
        self.io_bad.append(('r', a))
        return 0

    def ram_write(self, a, v):
        if 0x0800 <= a < 0x0800 + 1000:
            self.screen_writes.append((self.mpu.processorCycles, a, v))
            return
        if 0x0100 <= a < 0x0200 and self.cpu[self.curpc] in (0x20, 0x48, 0x08, 0x00):
            return                          # JSR, PHA, PHP, BRK: the stack itself
        if 0x1E00 <= a < 0x1E40:
            return
        if 0x5000 <= a < 0x5400 or 0x6000 <= a < 0x7000:
            return
        if 0x2000 <= a < 0x4000:            # self-modified operands and opcodes in the code
            return
        self.bad.append(('cpu', a, v))

    def run(self, max_samples=None, max_cycles=10_000_000_000):
        mem = ObservableMemory(subject=self.cpu)
        mem.subscribe_to_write(range(0xD000, 0xE000), self.io_write)
        mem.subscribe_to_read(range(0xD000, 0xE000), self.io_read)
        if self.strict:
            mem.subscribe_to_write(range(0x0000, 0xD000), self.ram_write)
            mem.subscribe_to_write(range(0xE000, 0x10000), self.ram_write)
        self.mpu = mpu = MPU(memory=mem)
        mpu.pc, mpu.sp = STUB, 0xFF
        n = 0
        idle = self.sym['IDLE']
        ffwd = '--exact' not in sys.argv
        hist = [0] * 256
        hp = 0
        while mpu.pc != STUB + 3:
            hist[hp & 255] = self.curpc = mpu.pc
            hp += 1
            if ffwd and mpu.pc == idle and self.play_start is not None and self.ch['freq']:
                # nothing changes in the idle loop until the DMA fetches its next sample
                nxt = self.play_start + (((self.fetched + 1) << 24) + self.ch['freq'] - 1) // self.ch['freq']
                if mpu.processorCycles < nxt - 60:
                    mpu.processorCycles = nxt - 60
            mpu.step()
            n += 1
            if n & 63 == 0:
                self.cyc = mpu.processorCycles
                self.advance_audio()
                if max_samples and self.fetched >= max_samples:
                    break
                if mpu.processorCycles > max_cycles:
                    raise RuntimeError("the engine did not return")
            if not (0x0000 <= mpu.pc < 0x8000):
                self.cyc = mpu.processorCycles
                self.advance_audio()
                last = [hist[(hp - 1 - i) & 255] for i in range(40)][::-1]
                inv = {v: k for k, v in self.sym.items()}
                trail = ' '.join(f"{a:04X}" + (f"({inv[a]})" if a in inv else '') for a in last)
                raise RuntimeError(f"PC ${mpu.pc:04X} after {self.fetched} samples played "
                                   f"({self.fetched / kl.FS:.2f} s), SP ${mpu.sp:02X}; last PCs: {trail}")
        self.cyc = mpu.processorCycles
        self.advance_audio()
        self.returned = mpu.pc == STUB + 3
        return n


def expected_sid_writes(evt):
    writes, t_end = preview.decode_events(evt)
    return writes, t_end


def main():
    build = sys.argv[1]
    secs = None
    for a in sys.argv[2:]:
        if not a.startswith('--'):
            secs = float(a)
    strict = '--strict' in sys.argv
    ntsc = '--ntsc' in sys.argv
    keys = []
    quit_at = [float(a.split('=')[1]) for a in sys.argv if a.startswith('--quit=')]
    if quit_at:
        keys = [(int(quit_at[0] * 40_500_000), ord('Q'))]
    mc = Machine(build, strict, keys, ntsc)
    max_samples = int(secs * kl.FS) if secs else None
    n = mc.run(max_samples)
    show = json.loads((Path(build) / 'show.json').read_text())
    print(f"instructions {n:,}, cycles {mc.cyc:,} ({mc.cyc / 40.5e6:.1f} s at 40.5 MHz); "
          f"samples played {mc.fetched:,} ({mc.fetched / kl.FS:.1f} s); returned {mc.returned}")
    fails = []
    # 1. the voice the DMA played, against the reference model
    nf = show['frames']
    ref, v = synth.render(mc.vox, nframes=nf, stop_at_end=False)
    played = mc.played
    m = min(len(played), len(ref))
    diff = [i for i in range(m) if played[i] != ref[i]]
    print(f"voice: {m:,} samples compared with the model, {len(diff)} differ" +
          (f" (first at {diff[0]}: engine {played[diff[0]]}, model {ref[diff[0]]})" if diff else ""))
    if diff:
        fails.append('voice differs from the model')
    if v.over:
        fails.append(f'the model overflowed {v.over} times')
    # 2. SID writes: all of the play stream's, in order, each after its time has played
    want, t_end = expected_sid_writes(mc.evt)
    got = mc.sid_writes
    fetch = mc.fetch_cycles
    import bisect

    def played_at(cyc):
        return bisect.bisect_right(fetch, cyc)
    t_lim = mc.fetched
    want_due = [w for w in want if w[0] <= t_lim - 200]
    got_ev = [g for g in got if played_at(g[0]) > 0 or True]
    # the engine's own writes at the end (silencing) are not in the stream
    exit_writes = 128 if mc.returned else 0
    stream_got = got[:len(got) - exit_writes] if exit_writes else got
    late = []
    k = 0
    for t, r, val in want_due:
        if k >= len(stream_got):
            fails.append(f'SID write {r:02X}={val:02X} at sample {t} never came')
            break
        c, r2, v2 = stream_got[k]
        if (r2, v2) != (r, val):
            fails.append(f'SID write {k}: expected {r:02X}={val:02X}, got {r2:02X}={v2:02X}')
            break
        p = played_at(c)
        if p < t:
            fails.append(f'SID write {k} early: at sample {p}, due {t}')
            break
        late.append(p - t)
        k += 1
    if late:
        late.sort()
        print(f"SID writes: {len(late)} checked, delay after their time median {late[len(late) // 2]} samples, "
              f"max {late[-1]} ({late[-1] / kl.FS * 1000:.1f} ms)")
    # 2b. the lyrics: each syllable's first letter on the screen once its sound has started, not before
    import song
    lyr_late = []
    for ly in show['lyrics']:
        if ly['sample'] > t_lim - 200:
            continue
        a = 0x0800 + ly['row'] * 40 + ly['col']
        code = song.scode(ly['syllable'].lstrip()[0]) if ly['syllable'].strip() else None
        hits = [c for c, aa, v in mc.screen_writes if aa == a and v == code and played_at(c) >= show['T_SONG']]
        if not hits:
            fails.append(f"lyric {ly['syllable']!r} never shown")
            break
        p = played_at(hits[0])
        if p < ly['sample']:
            fails.append(f"lyric {ly['syllable']!r} early: at sample {p}, due {ly['sample']}")
            break
        lyr_late.append(p - ly['sample'])
    if lyr_late:
        print(f"lyrics: {len(lyr_late)} syllables shown, delay after their sound median "
              f"{sorted(lyr_late)[len(lyr_late) // 2]} samples, max {max(lyr_late)}")
    # 3. results, the exit, the strict checks
    mb = mc.cpu[0x1E10:0x1E30]
    ex = mb[0]
    tplay = int.from_bytes(bytes(mb[1:5]), 'little')
    slines = int.from_bytes(bytes(mb[5:9]), 'little')
    under = mb[9] | mb[10] << 8
    minbuf = mb[11] | mb[12] << 8
    twrite = int.from_bytes(bytes(mb[13:17]), 'little')
    if mc.returned:
        share = slines * CYC_LINE / max(1, tplay / kl.FS * 40.5e6)
        print(f"exit code {ex:02X}; played {tplay:,}, written {twrite:,}; underruns {under}; "
              f"fewest waiting {minbuf} samples; voice CPU share {share * 100:.1f} % (NMOS timings); meter peak {mb[0x11]} %")
        if under:
            fails.append(f'{under} underruns')
        if quit_at:
            if ex != 0x51:
                fails.append(f'Q did not quit (exit {ex:02X})')
            after = (mc.cyc - keys[0][0]) / 40.5e6 if keys else 0
            print(f"Q pressed at {quit_at[0]} s; returned {after * 1000:.1f} ms later")
        elif ex != 0:
            fails.append(f'exit code {ex:02X}')
        if mc.audio_on():
            fails.append('audio DMA left on')
        if mc.auden != 0x08:
            fails.append(f'audio control not restored ({mc.auden:02X})')
        if mc.panvol != 0x5A:
            fails.append(f'channel 0 right volume not restored ({mc.panvol:02X})')
        if mc.textyl != 0x68 or (mc.textyh & 0x0F) != 0 or (mc.textyh & 0xF0) != 0x50:
            fails.append(f'text position not restored ({mc.textyl:02X} {mc.textyh:02X})')
        last = {}
        for c, r, val in got:
            last[r] = val
        if any(last.get(r, 0) for r in range(0x80)):
            fails.append('SIDs not silent at the end')
    want_vol = mc.cpu[0x1E01]
    if mc.vol_at_start != (want_vol, want_vol):
        fails.append(f'channel volumes at the start {mc.vol_at_start}, mailbox asks {want_vol}')
    if mc.bad:
        fails.append(f'stray writes {mc.bad[:5]}')
    if mc.io_bad:
        fails.append(f'stray I/O {mc.io_bad[:5]}')
    # 4. the screen at the end of the run, against the events
    print("screen at the end:")
    for row in range(25):
        line = ''.join(sc_char(mc.cpu[0x0800 + row * 40 + c]) for c in range(40))
        if line.strip():
            print(f"  {row:2d} |{line}|")
    ty = [t for t in mc.textypos if t[0] > 0]
    if ty:
        print(f"TEXTYPOS writes: {len(ty)}, largest offset {max(((l | (h & 15) << 8) - 0x68) for c, l, h in ty)} lines")
    print(f"DMA bytes {mc.dma_bytes:,}")
    dump = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--dump=')]
    if dump:
        # what the screen and the text position did, in samples played, for the preview video
        log = {'screen': [(played_at(c), a - 0x0800, v) for c, a, v in mc.screen_writes],
               'textypos': [(played_at(c), l | (h & 15) << 8) for c, l, h in mc.textypos],
               'played': mc.fetched}
        Path(dump[0]).write_text(json.dumps(log))
        print(f"wrote {dump[0]}: {len(log['screen'])} screen writes, {len(log['textypos'])} TEXTYPOS writes")
    print("FAIL:" if fails else "PASS", *fails, sep='\n  ')
    sys.exit(1 if fails else 0)


def sc_char(c):
    c &= 127
    if 1 <= c <= 26:
        return chr(c + 96)
    if 65 <= c <= 90:
        return chr(c)
    if 32 <= c <= 63:
        return chr(c)
    return '#'


if __name__ == '__main__':
    main()
