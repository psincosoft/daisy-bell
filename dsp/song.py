"""The show: one timeline in samples for the voice, the SIDs and the screen.

Writes, into the build directory:
  DAISY-BELL.VOX   the voice stream (attic RAM $8000000)
  DAISY-BELL.EVT   the play stream: SID writes and screen events, fired as the samples play ($8010000)
  show.json   the timeline, for the preview and the tests

Play stream: groups of [delay lo, delay hi] (samples after the previous group) and commands, $00:
  $01 lo hi v        POKE a 16-bit address (palette, VIC, colour RAM)
  $02 r v            POKE $D400+r (any of the four SIDs, r < $80)
  $03 r n            SID frequency of MIDI note n into $D400+r, $D401+r (PAL or NTSC table)
  $04 lo hi n b...   n screen codes to the screen at $0800+offset
  $05 lo hi          text position: TEXTYPOS = its value at the start + offset (raster lines)
  $06 lo hi nl nh v  fill the screen from $0800+offset, n bytes of v
  $07 lo hi          the CPU meter on: two digits at $0800+offset, every second (this show sends
                     them to the mailbox at $1E30-$1E31: measured, not shown)
  $08                the CPU meter off
  $FF                the end of the show
"""
import json
import sys
import textwrap
from pathlib import Path

import kl
import score
import tables
import voice
import vstream

FS = kl.FS
BEAT = 100                         # frames per beat (0.50 s)
SPB = BEAT * tables.FRAME          # samples per beat: 5000
W, H = 40, 25
CURSOR = 160                       # a reversed space

# part 1: the SIDs play the whole chorus while the intro is typed; part 2: the voice sings it
T_INTRO = 2 * SPB                  # the tune starts
INTRO_BARS = 32                    # the whole chorus
T_FREEZE = T_INTRO + INTRO_BARS * 3 * SPB
T_DROP = T_FREEZE + 6 * SPB        # three seconds of stillness
T_SONG = T_DROP + 3 * SPB          # the text has fallen; the accompaniment starts
T_BAR1 = T_SONG + 6 * SPB          # two bars in, the voice
T_FINAL = T_BAR1 + 32 * 3 * SPB    # the last chord
T_END = T_FINAL + 6 * SPB
METER_MAILBOX = 0x1E30             # where the meter's two digits go: not on the screen

SID1, SID3 = 0x00, 0x40            # SID 1 ($D400, right pair) and SID 3 ($D440, left pair)
V1, V2, V3 = 0, 7, 14


def scode(ch):
    o = ord(ch)
    if 97 <= o <= 122:
        return o - 96
    if 65 <= o <= 90:
        return o
    if 32 <= o <= 63:
        return o
    raise ValueError(ch)


class Events:
    def __init__(self):
        self.ev = []                    # (time, order, bytes)
        self.n = 0

    def add(self, t, cmd):
        self.ev.append((int(round(t)), self.n, bytes(cmd)))
        self.n += 1

    def sid(self, t, r, v):
        self.add(t, [0x02, r, v])

    def freq(self, t, r, note):
        self.add(t, [0x03, r, note])

    def text(self, t, off, codes):
        codes = list(codes)
        while codes:
            part, codes = codes[:200], codes[200:]
            self.add(t, [0x04, off & 255, off >> 8, len(part)] + part)
            off += len(part)

    def encode(self):
        out = bytearray()
        last = 0
        groups = {}
        for t, n, cmd in sorted(self.ev):
            groups.setdefault(t, []).append(cmd)
        for t in sorted(groups):
            d = t - last
            assert d >= 0
            while d > 65535:
                out += bytes([0xFF, 0xFF, 0x00])
                d -= 65535
            out += bytes([d & 255, d >> 8])
            for cmd in groups[t]:
                out += cmd
            out += b'\x00'
            last = t
        return bytes(out)


def note_events(ev, t0, t1, regs, note, wave, gate_frac=0.85):
    """A note on the voices regs (SID register offsets of a voice) from t0 for (t1-t0)*gate_frac."""
    for r in regs:
        ev.freq(t0, r, note)
        ev.sid(t0, r + 4, wave | 1)
        ev.sid(t0 + (t1 - t0) * gate_frac, r + 4, wave)


def accompaniment(ev, t_bar, harmony, bass_alt=True):
    """Oom-pah-pah: the bass on beat 1 (root, the fifth when the chord stays), a dyad on 2 and 3."""
    dyads = {'G': (67, 71), 'C': (64, 72), 'D7': (66, 72), 'A7': (67, 73)}
    prev = None
    alt = False
    for b, ch in enumerate(harmony):
        root, pcs = score.CHORDS[ch]
        alt = (ch == prev) and not alt and bass_alt
        bass = 43 + (root - 7) % 12 if root != 7 else 43
        if alt:
            fifth = (root + 7) % 12
            bass = 43 + (fifth - 7) % 12
        t = t_bar + b * 3 * SPB
        note_events(ev, t, t + 1.5 * SPB, [SID1 + V3, SID3 + V3], bass, 0x10, 0.9)
        lo, hi = dyads[ch]
        for k in (1, 2):
            tb = t + k * SPB
            note_events(ev, tb, tb + 0.4 * SPB, [SID1 + V2], hi, 0x40, 1.0)
            note_events(ev, tb, tb + 0.4 * SPB, [SID3 + V2], lo, 0x40, 1.0)
        prev = ch


def sid_init(ev, t):
    for s in (SID1, SID3):
        for r in range(0x19):
            ev.sid(t, s + r, 0)
        ev.sid(t, s + V1 + 5, 0x28)       # melody: triangle, attack 16 ms, decay 300 ms
        ev.sid(t, s + V1 + 6, 0xA9)       # sustain 10, release 750 ms
        ev.sid(t, s + V2 + 2, 0x00)       # dyads: pulse 37.5 %
        ev.sid(t, s + V2 + 3, 0x06)
        ev.sid(t, s + V2 + 5, 0x07)       # attack 2 ms, decay 240 ms
        ev.sid(t, s + V2 + 6, 0x00)
        ev.sid(t, s + V3 + 5, 0x0A)       # bass: triangle, decay 1.5 s
        ev.sid(t, s + V3 + 6, 0x00)
        ev.sid(t, s + 0x18, 0x0A)         # volume 10, no filter


def melody_events(ev, t_bar, bars, register_up=0):
    t = t_bar
    end = t_bar + bars * 3 * SPB
    for syl, midi, beats in score.MELODY:
        d = beats * SPB
        if t >= end:
            break
        if syl is not None:
            notes = midi if isinstance(midi, list) else [midi]
            step = d / len(notes)
            for k, m in enumerate(notes):
                note_events(ev, t + k * step, t + (k + 1) * step, [SID1 + V1, SID3 + V1], m + register_up, 0x10, 0.9)
        t += d


INTRO_WIDTH = 26                   # the intro's lines, as in the proof of concept


def wrap_intro():
    """The intro's two paragraphs: (row, col, line, paragraph), each line centred, a blank row
    between the paragraphs, the block centred on the screen."""
    paras = [textwrap.wrap(score.INTRO, INTRO_WIDTH), textwrap.wrap(score.INTRO2, INTRO_WIDTH)]
    rows = len(paras[0]) + 1 + len(paras[1])
    top = (H - rows) // 2 - 1
    out, row = [], top
    for p, lines in enumerate(paras):
        for l in lines:
            out.append((row, (W - len(l)) // 2, l, p))
            row += 1
        row += 1
    return out


def build(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ev = Events()
    sid_init(ev, 0)
    # ---------------- part 1: the whole chorus on the SIDs, the intro typed with it ----------------
    melody_events(ev, T_INTRO, INTRO_BARS)
    accompaniment(ev, T_INTRO, score.HARMONY[:INTRO_BARS])
    # the last bar: the tonic chord rings out into the stillness
    t_last = T_INTRO + (INTRO_BARS - 1) * 3 * SPB
    for s in (SID1, SID3):
        ev.sid(t_last, s + V2 + 5, 0x0B)  # the last dyad decays slowly (2.4 s)
        ev.sid(t_last, s + V3 + 5, 0x0B)
    # the first paragraph is typed during the first half, as in the proof of concept; the second from
    # the start of the second half, at the same pace; then it stands until the stillness
    lines = wrap_intro()
    chars = []
    for row, col, l, p in lines:
        for i, ch in enumerate(l):
            chars.append((row, col + i, ch, p))
    weights = []                           # pauses, in units of one character
    for k, (row, col, ch, p) in enumerate(chars):
        w = 1.0
        if k and chars[k - 1][3] == p:
            if chars[k - 1][2] == ',':
                w = 2.5
            if chars[k - 1][2] == '.':
                w = 4.0
        weights.append(w)
    half = T_INTRO + INTRO_BARS // 2 * 3 * SPB
    t_type0, t_type1 = T_INTRO + SPB, half - SPB
    first = [k for k in range(len(chars)) if chars[k][3] == 0]
    scale = (t_type1 - t_type0) / sum(weights[k] for k in first)
    times = []
    t = t_type0
    for k, (row, col, ch, p) in enumerate(chars):
        if p == 1 and chars[k - 1][3] == 0:
            t = half + SPB                 # the second paragraph starts on the second half's second beat
        t += weights[k] * scale
        times.append(t)
    assert times[-1] < T_FREEZE - 4 * SPB, 'the second paragraph must end two seconds before the stillness'
    for k, (row, col, ch, p) in enumerate(chars):
        t = times[k]
        off = row * W + col
        ev.text(t, off, [scode(ch)])
        if k + 1 < len(chars):
            nr, nc, _, _ = chars[k + 1]
            nxt = nr * W + nc
        else:
            nxt = off + 1
        ev.text(t, nxt, [CURSOR])
    last_cursor = nxt
    # ---------------- the stillness: the cursor blinks ----------------
    for i in range(6):
        ev.text(T_FREEZE + i * SPB, last_cursor, [32 if i % 2 == 0 else CURSOR])
    # ---------------- the drop: the whole text falls off the screen ----------------
    ev.text(T_DROP, last_cursor, [32])
    g = 900.0                              # raster lines per second squared
    k = 0
    while True:
        tt = k * 0.02
        y = 0.5 * g * tt * tt
        ev.add(T_DROP + k * 200, [0x05, int(y) & 255, int(y) >> 8])
        if y > 680:
            break
        k += 1
    t_fallen = T_DROP + k * 200 + 200
    ev.add(t_fallen, [0x06, 0, 0, 1000 & 255, 1000 >> 8, 32])
    ev.add(t_fallen + 200, [0x05, 0, 0])
    assert t_fallen + 200 < T_SONG
    # ---------------- the song ----------------
    sid_init(ev, T_SONG - 100)
    accompaniment(ev, T_SONG, ['G', 'G'])
    accompaniment(ev, T_BAR1, score.HARMONY)
    # the last chord, ringing out, and the fade
    for s in (SID1, SID3):
        ev.sid(T_FINAL, s + V3 + 5, 0x0C)
        ev.sid(T_FINAL, s + V2 + 5, 0x0C)
        ev.sid(T_FINAL, s + V1 + 5, 0x0B)
        ev.sid(T_FINAL, s + V1 + 6, 0x00)
    note_events(ev, T_FINAL, T_FINAL + 3 * SPB, [SID1 + V3, SID3 + V3], 43, 0x10, 1.0)
    note_events(ev, T_FINAL, T_FINAL + 3 * SPB, [SID1 + V2], 71, 0x40, 1.0)
    note_events(ev, T_FINAL, T_FINAL + 3 * SPB, [SID3 + V2], 67, 0x40, 1.0)
    note_events(ev, T_FINAL, T_FINAL + 3 * SPB, [SID1 + V1, SID3 + V1], 79, 0x10, 1.0)
    for i in range(11):
        tv = T_FINAL + 3 * SPB + i * SPB * 0.25
        for s in (SID1, SID3):
            ev.sid(tv, s + 0x18, 10 - i)
    # the lyrics, a syllable as its sound starts; the meter under them
    nframes = T_END // tables.FRAME
    keys, onsets = voice.build(score.MELODY, BEAT, T_BAR1 // tables.FRAME, nframes)
    sylpos = []
    for row, line in zip(score.LYRIC_ROWS, score.LYRICS):
        parts = line.split('|')
        text = ''.join(parts)
        col = (W - len(text.rstrip())) // 2
        c = col
        for p in parts:
            sylpos.append((row, c, p))
            c += len(p)
    assert len(sylpos) == len(onsets), (len(sylpos), len(onsets))
    lyric_times = []
    for (row, col, p), (syl, f, t0) in zip(sylpos, onsets):
        codes = [scode(ch) for ch in p.rstrip(' ')]
        ev.text(f * tables.FRAME, row * W + col, codes)
        lyric_times.append({'syllable': p, 'row': row, 'col': col, 'sample': f * tables.FRAME, 'beat': t0 * tables.FRAME})
    # the meter measures the voice's share of every second of the song (its highest goes to the end
    # card) and writes its two digits into the mailbox, not onto the screen: part 2 shows the lyrics only
    moff = METER_MAILBOX - 0x0800
    ev.add(T_BAR1 - 2 * SPB, [0x07, moff & 255, moff >> 8])
    ev.add(T_END - SPB, [0x08])
    ev.add(T_END, [0xFF])
    vox = vstream.build(keys)
    evt = ev.encode()
    (out / 'DAISY-BELL.VOX').write_bytes(bytes([0, 0]) + vox)
    (out / 'DAISY-BELL.EVT').write_bytes(bytes([0, 0]) + evt)
    show = {
        'fs': FS, 'frame': tables.FRAME, 'samples_per_beat': SPB, 'frames': nframes,
        'T_INTRO': T_INTRO, 'T_FREEZE': T_FREEZE, 'T_DROP': T_DROP, 'T_SONG': T_SONG,
        'T_BAR1': T_BAR1, 'T_FINAL': T_FINAL, 'T_END': T_END,
        'intro_lines': [list(x) for x in wrap_intro()], 'lyrics': lyric_times, 'meter_mailbox': METER_MAILBOX,
        'vox_bytes': len(vox), 'evt_bytes': len(evt),
    }
    (out / 'show.json').write_text(json.dumps(show, indent=1))
    print(f"voice stream {len(vox)} bytes, {len(keys)} keyframes; play stream {len(evt)} bytes, {len(ev.ev)} commands; "
          f"show {T_END / FS:.1f} s")
    return show


if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else '../build')
