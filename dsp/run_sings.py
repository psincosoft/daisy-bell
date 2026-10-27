"""Run the BASIC program of DAISY BELL in the BASIC 65 simulator, with the real engine.

bas65sim interprets the listing; BLOAD loads the real files from the build directory into a memory
model (bank 0, bank 5, attic RAM, colour RAM); every SYS runs the real engine binary from that
memory in the 6502 emulator of test_engine.py (its model of the MEGA65: multiplier, DMA, audio DMA,
raster, key queue, SIDs, TEXTYPOS), and the engine's writes come back into the memory model.
Sessions (each a fresh machine, scripted keys):
  quick     the card skipped by RETURN, the files loaded, the show stopped by Q after 2 s
  cardq     Q on the opening card: nothing loaded, back to BASIC
  ntsc      an NTSC machine: the mailbox for NTSC; Q after 1 s
  noattic   no attic RAM: the program says so and stops
  screen    the screen elsewhere than $0800: the program says so and stops
  nofile    DAISY-BELL.EVT missing: the TRAP handler cleans up and names the error
  stop      RUN/STOP while the files load: the TRAP handler cleans up
  still     the audio DMA stands still: the engine gives up after 50 frames, the program says so
  full      the whole show (about 12 minutes here), the end card, R, a second show stopped by Q
Checks: the mailbox BASIC writes; the engine's exit; the end card's text and the measured share
against the engine's own figures; that everything is put back (screen mode, case, border and
background, the card's palette entries, the SIDs silent, TEXTYPOS, the audio DMA off).
It checks the BASIC logic and its use of the engine; not the ROM, not the real timing.
Usage: python3 run_sings.py BUILD_DIR [session ...] [--png DIR]
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'tools'))
import bas65sim as S  # noqa: E402
import kl  # noqa: E402
import synth  # noqa: E402
import test_engine as TE  # noqa: E402

S.ERRORS[4] = "FILE NOT FOUND"
CPU_HZ = 40.5e6


class M2(S.Machine):
    def __init__(self, build, ntsc=False, attic=True, sa=0x0800, co=0x0000):
        super().__init__(width=80, height=25, pal_ntsc=ntsc)
        self.build = Path(build)
        self.bank0 = bytearray(0x10000)
        self.bank5 = bytearray(0x10000)
        self.attic = bytearray(0x800000) if attic else None
        self.cram = bytearray(0x8000)
        self.sa, self.co = sa, co
        self.textypos = (0x68, 0x50)
        self.sys_calls = []           # per call: mailbox, exit, results, engine machine
        self.bloads = []
        self.fast = 0
        self.edma = []
        self.engine_keys = []         # (seconds into the call, key code) for the next SYS
        self.dma_still = False
        self.out_lines = []

    # the screen in memory: 40 x 25 or 80 x 25 at self.sa in bank 0
    def screen_index(self, a):
        i = a - self.sa
        if 0 <= i < self.cols * self.rows:
            return i // self.cols, i % self.cols
        return None

    def peek(self, a):
        a = int(a)
        if 0xFFD3060 <= a <= 0xFFD3065:
            sa, co = self.sa, self.co
            return [sa & 255, (sa >> 8) & 255, (sa >> 16) & 255, sa >> 24, co & 255, co >> 8][a - 0xFFD3060]
        if 0xFF80000 <= a < 0xFF88000:
            i = a - 0xFF80000 - self.co
            if 0 <= i < self.cols * self.rows:
                return self.colr[i // self.cols][i % self.cols]
            return self.cram[a - 0xFF80000]
        if a < 0x10000:
            rc = self.screen_index(a)
            if rc:
                return self.text[rc[0]][rc[1]]
            return self.bank0[a]
        if 0x50000 <= a < 0x60000:
            return self.bank5[a - 0x50000]
        if 0x8000000 <= a < 0x8800000:
            return self.attic[a - 0x8000000] if self.attic is not None else 0xFF
        return super().peek(a)

    def poke(self, a, v):
        a, v = int(a), int(v)
        if not 0 <= v <= 255:
            raise S.BasicError(14, f"POKE {a},{v}")
        if 0xFF80000 <= a < 0xFF88000:
            i = a - 0xFF80000 - self.co
            if 0 <= i < self.cols * self.rows:
                self.colr[i // self.cols][i % self.cols] = v
            self.cram[a - 0xFF80000] = v
            return
        if a < 0x10000:
            rc = self.screen_index(a)
            if rc:
                self.text[rc[0]][rc[1]] = v
                return
            if not (0x1E00 <= a < 0x1F00):
                raise S.BasicError(99, f"POKE ${a:04X} outside the mailbox and the screen")
            self.bank0[a] = v
            return
        if 0x50000 <= a < 0x60000:
            self.bank5[a - 0x50000] = v
            return
        if 0x8000000 <= a < 0x8800000:
            if self.attic is not None:
                self.attic[a - 0x8000000] = v
            return
        super().poke(a, v)

    def row_text(self, y):
        # as the screen shows it: in the lower-case set, codes 65-90 are the capitals
        out = []
        for code in self.text[y]:
            c = code & 127
            if 1 <= c <= 26:
                out.append(chr(c + (96 if self.lowercase else 64)))
            elif self.lowercase and 65 <= c <= 90:
                out.append(chr(c))
            elif 32 <= c <= 63:
                out.append(chr(c))
            elif c == 0:
                out.append('@')
            else:
                out.append('#')
        return ''.join(out)

    def put(self, ch):
        if not self.esc and ch == 13:
            self.out_lines.append(self.row_text(self.cy).rstrip())
        super().put(ch)


class I2(S.Interpreter):
    def s_BLOAD(self):
        if self.peek_is('op', '('):
            self.take()
            name = self.expr()
            self.expect('op', ')')
        else:
            name = self.expect('str')[1]
        addr = bank = None
        while self.peek_is('op', ','):
            self.take()
            t = self.take()
            if t == ('id', 'P'):
                (addr,) = self.args()
            elif t[0] == 'id' and t[1].startswith('B') and t[1][1:].isdigit():
                bank = int(t[1][1:])
            else:
                raise S.BasicError(99, f"BLOAD option {t}")
        if addr <= 0xFFFF:
            assert bank is not None, "BLOAD with a 16-bit address and no bank"
            addr = bank * 0x10000 + int(addr)
        path = self.m.build / name
        if name in self.m.missing or not path.exists():
            raise S.BasicError(4, name)
        data = path.read_bytes()[2:]
        a = int(addr)
        for i, b in enumerate(data):
            self.m.poke(a + i, b)
        self.m.time += len(data) * 20            # about 50 KB a second from the SD card
        self.m.bloads.append((name, a, len(data)))
        self.m.log.append((self.m.time, f"BLOAD {name} {a:X} {len(data)}"))

    def s_EDMA(self):
        cmd = int(self.num(self.expr()))
        vals = []
        for _ in range(3):
            self.expect('op', ',')
            vals.append(int(self.num(self.expr())))
        n, src, dst = vals
        self.m.edma.append((cmd, n, src, dst))
        if cmd == 0:
            data = [self.m.peek(src + i) for i in range(n)]
            for i, b in enumerate(data):
                self.m.poke(dst + i, b)
        elif cmd == 3:
            for i in range(n):
                self.m.poke(dst + i, src & 255)
        else:
            raise S.BasicError(99, f"EDMA {cmd}")

    def s_FAST(self):
        self.m.fast += 1

    def step(self):
        # BASIC checks RUN/STOP between statements: scripted events arrive there too
        self.m.poll_events()
        super().step()


def run_engine(m, interp):
    """The SYS $2000 in BANK 5: the engine from the memory model, in the 6502 emulator."""
    assert interp.bank == 5, f"SYS $2000 in BANK {interp.bank}"
    keys = [(int(s * CPU_HZ), k) for s, k in m.engine_keys]
    mc = TE.Machine(m.build, strict=True, keys=keys)
    # the engine's memory as BASIC left it: bank 0 below $2000, bank 5 $2000-$7FFF, attic RAM
    mc.cpu[0:0x2000] = list(m.bank0[0:0x2000])
    mc.cpu[0x2000:0x8000] = list(m.bank5[0x2000:0x8000])
    for i, v in enumerate([0x20, 0x00, 0x20, 0xEA]):    # the harness's caller: JSR $2000, NOP
        mc.cpu[TE.STUB + i] = v
    assert m.cols == 40 and m.rows == 25 and m.sa == 0x0800, "the engine needs the 40 x 25 screen at $0800"
    for i in range(1000):
        mc.cpu[0x0800 + i] = m.text[i // 40][i % 40]
    mc.attic = m.attic if m.attic is not None else bytearray(0x20000)
    mc.textyl, mc.textyh = m.textypos
    if m.dma_still:
        mc.advance_audio = lambda: None
    mailbox = bytes(mc.cpu[0x1E00:0x1E06])
    sid_before = list(m.sid)
    mc.run()
    # back into the memory model
    for i in range(0x2000):
        m.bank0[i] = mc.cpu[i]
    for i in range(0x2000, 0x8000):
        m.bank5[i] = mc.cpu[i]
    for i in range(1000):
        m.text[i // 40][i % 40] = mc.cpu[0x0800 + i]
    for c, r, v in mc.sid_writes:
        m.sid[r] = v
    m.textypos = (mc.textyl, mc.textyh)
    m.time += int(mc.cyc / CPU_HZ * 1e6)
    m.sys_calls.append({'mailbox': mailbox, 'mc': mc, 'sid_before': sid_before,
                        'results': bytes(mc.cpu[0x1E10:0x1E22])})
    m.engine_keys = []
    m.log.append((m.time, f"SYS: exit {mc.cpu[0x1E10]:02X}, {mc.fetched} samples played"))


def session(build, name, png=None):
    ntsc = name == 'ntsc'
    m = M2(build, ntsc=ntsc, attic=(name != 'noattic'), sa=(0x1000 if name == 'screen' else 0x0800))
    m.missing = {'DAISY-BELL.EVT'} if name == 'nofile' else set()
    m.dma_still = name == 'still'
    listing = Path(build) / 'daisy-bell.bas'
    interp = I2(listing, m)
    m.sys_hook = lambda mm, a: run_engine(mm, interp)
    assert m.cols == 80
    pal0 = [[list(ch) for ch in bank] for bank in m.pal]
    d020, d021 = m.d020, m.d021
    fr = lambda n: (lambda mm, n=n: mm.frame() >= n)
    if name == 'cardq':
        m.events.append({'when': fr(60), 'key': 'Q', 'delay': 0})
    elif name == 'stop':
        m.events.append({'when': lambda mm: len(mm.bloads) >= 1, 'key': 'BREAK', 'delay': 0})
    elif name != 'full':
        m.events.append({'when': fr(30), 'key': '\r', 'delay': 0})     # RETURN ends the card early
    if name in ('quick', 'ntsc', 'still'):
        m.engine_keys = [(2.0 if name == 'quick' else 1.0, ord('Q'))]
        if name == 'still':
            m.engine_keys = []
    shots = {}
    checks = []
    t_end = m.time + 60_000_000
    full_state = {'card_seen': False}
    while not interp.ended and m.time < t_end + (2_000_000_000 if name == 'full' else 0):
        if name == 'full' and not full_state['card_seen'] and len(m.sys_calls) == 1 and \
                any('Q: quit' in m.row_text(r) for r in range(m.rows)):      # the card's last words
            full_state['card_seen'] = True
            full_state['card'] = [m.row_text(r) for r in range(m.rows)]
            full_state['card_colr'] = [list(r) for r in m.colr]
            if png:
                m.render(Path(png) / 'sim_endcard.png')
            m.engine_keys = [(1.0, ord('Q'))]                  # the second show: Q after a second
            m.events.append({'when': lambda mm: True, 'key': 'R', 'delay': 0.5})
        interp.run(max_us=20_000)
        if png and name == 'quick' and 'loading' not in shots and m.bloads:
            shots['loading'] = True
            m.render(Path(png) / 'sim_loading.png')
        if png and name == 'full' and 'card' not in shots and m.frame() >= 100:
            shots['card'] = True
            m.render(Path(png) / 'sim_card.png')
    out = m.out_lines + [m.row_text(m.cy).rstrip()]
    said = ' '.join(l for l in out if l.strip())
    calls = m.sys_calls
    # ---- the checks common to every session
    checks.append(('the program ended', interp.ended))
    checks.append(('no error outside the program\'s own TRAP', all(t[1] in (30, 4) for t in interp.trace)))
    checks.append(('screen mode back to 80 x 25', (m.cols, m.rows) == (80, 25)))
    checks.append(('upper case back', not m.lowercase))
    checks.append(('border and background back', (m.d020, m.d021) == (d020, d021)))
    checks.append(('every palette entry as found', m.pal == pal0))
    checks.append(('the SIDs silent', not any(m.sid)))
    checks.append(('TEXTYPOS as found', m.textypos == (0x68, 0x50)))
    for c in calls:
        mc = c['mc']
        checks.append(('the audio DMA off after the call', not mc.audio_on()))
        nt = 1 if ntsc else 0
        want = bytes([nt, 204, (525 if ntsc else 625) & 255, 2, 208 if ntsc else 210, 0x08])
        checks.append((f'the mailbox for {"NTSC" if ntsc else "PAL"}: {c["mailbox"].hex()}', c['mailbox'] == want))
        checks.append(('no stray writes or I/O in the engine', not mc.bad and not mc.io_bad))
        # the voice as far as it played, against the reference model
        n = min(len(mc.played), 40000)
        ref, _ = synth.render(mc.vox, nframes=(n + 49) // 50, stop_at_end=False)
        checks.append((f'the voice\'s first {n} samples as the model\'s', mc.played[:n] == ref[:n]))
    # ---- per session
    if name == 'quick':
        checks.append(('the card ended by RETURN, the program went on', len(calls) == 1))
        checks.append(('three files loaded where the engine reads them',
                       [(b[0], b[1]) for b in m.bloads] == [('DAISY-BELL.ENG', 0x52000), ('DAISY-BELL.VOX', 0x8000000),
                                                           ('DAISY-BELL.EVT', 0x8010000)]))
        checks.append(('FAST before the call', m.fast >= 1))
        checks.append(('Q in the show: exit $51', calls and calls[0]['results'][0] == 0x51))
        checks.append(('the SIDs silenced before the show', calls and not any(calls[0]['sid_before'])))
        checks.append(('it says it ended', 'DAISY BELL ENDED.' in said))
    if name == 'cardq':
        checks.append(('Q on the card: nothing loaded, no call', not m.bloads and not calls))
        checks.append(('it says it ended', 'DAISY BELL ENDED.' in said))
    if name == 'ntsc':
        checks.append(('one call, ended by Q', len(calls) == 1 and calls[0]['results'][0] == 0x51))
    if name == 'noattic':
        checks.append(('no attic RAM: no file loaded, no call', not m.bloads and not calls))
        checks.append(('it names the attic RAM', 'DAISY BELL NEEDS ITS ATTIC RAM (8 MB AT $8000000).' in said))
    if name == 'screen':
        checks.append(('screen elsewhere: no call', not calls))
        checks.append(('it names the screen', 'DAISY BELL NEEDS ITS 40 X 25 SCREEN AT $0800.' in said))
    if name == 'nofile':
        checks.append(('a missing file: no call', not calls))
        checks.append(('the TRAP handler names the error', 'DAISY BELL STOPPED: FILE NOT FOUND IN LINE' in said))
    if name == 'stop':
        checks.append(('RUN/STOP: no call', not calls))
        checks.append(('the TRAP handler says it stopped', 'DAISY BELL STOPPED.' in said))
    if name == 'still':
        checks.append(('the engine gave up: exit $FE', calls and calls[0]['results'][0] == 0xFE))
        checks.append(('it says the audio DMA did not play', 'THE AUDIO DMA DID NOT PLAY' in said))
    if name == 'full':
        checks.append(('two calls: the whole show, then a second show stopped by Q',
                       len(calls) == 2 and calls[0]['results'][0] == 0 and calls[1]['results'][0] == 0x51))
        card = full_state.get('card', [])
        r = calls[0]['results'] if calls else bytes(18)
        tplay = int.from_bytes(r[1:5], 'little')
        slines = int.from_bytes(r[5:9], 'little')
        under = r[9] | r[10] << 8
        share = round(100 * slines / 31250 / (tplay / kl.FS))
        checks.append((f'the whole show played: {tplay} samples', tplay >= 835000))
        checks.append((f'the card shows the share the engine measured ({share} %)',
                       any(f'The voice took {share}% of the CPU.' in row for row in card)))
        checks.append((f'the card shows the busiest second ({r[17]} %)',
                       any(f'({r[17]}% in its busiest second).' in row for row in card)))
        checks.append(('the card shows the gaps', any(('none.' if under == 0 else f'{under}.') in row for row in card)))
        checks.append(('the card: title, credits, keys',
                       any('A Machine that Sings' in row for row in card)
                       and any('By Dawid Blachowski, with Claude' in row for row in card)
                       and any("(Anthropic's AI). A psinco. production." in row for row in card)
                       and any('R: sing again' in row and 'Q: quit' in row for row in card)))
        checks.append(('no text past the card\'s edges', all(len(row.rstrip()) <= 40 for row in card)))
        checks.append(('it says it ended', 'DAISY BELL ENDED.' in said))
        full_state['results'] = (tplay, slines, under, r[17], share)
    print(f"--- session {name}: {interp.statements} statements, {m.frame()} frames, calls {len(calls)}, "
          f"loads {[b[0] for b in m.bloads]}")
    print(f"    said: {said[:160]!r}")
    if name == 'full':
        for row in full_state.get('card', []):
            print(f"    |{row}|")
    bad = 0
    for what, ok in checks:
        print(f"    {'PASS' if ok else 'FAIL'}  {what}")
        bad += not ok
    return bad, len(checks)


def main():
    build = sys.argv[1]
    png = None
    if '--png' in sys.argv:
        png = sys.argv[sys.argv.index('--png') + 1]
    names = [a for a in sys.argv[2:] if not a.startswith('--') and a != png] or \
        ['quick', 'cardq', 'ntsc', 'noattic', 'screen', 'nofile', 'stop', 'still']
    bad = total = 0
    for n in names:
        b, t = session(build, n, png)
        bad += b
        total += t
    print(f"checks: {total - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
