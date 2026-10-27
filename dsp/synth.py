"""The voice: the reference model the engine follows bit for bit.

Waves are volume-velocity waves (Rabiner & Schafer's sign convention): at the junction between
sections j-1 and j, with k = (A[j-1] - A[j]) / (A[j-1] + A[j]) and s = R[j-1] + L[j],
R[j] = R[j-1] - k*s and L[j-1] = L[j] + k*s. The glottis reflects +0.75, the lips -0.875, and the
output is the wave leaving through the lips. The multiplier works on |k|; for a negative k the
two signs swap (R[j] = R[j-1] + |k|*s, L[j-1] = L[j] - |k|*s), each product rounded down.

A control frame every FRAME samples reads the voice stream (ramps and holds of 15 byte-wide
parameters and a 16-bit pitch), works out the nine reflection coefficients from the log-areas
and the pitch increment with vibrato. Every sample runs the glottal source, the noise, the
Kelly-Lochbaum tube (even junctions, then odd ones), the frication and the nasal tap.

Voice stream records:
  $00-$06 RAMP over 2^H frames to the targets that follow ($00: set at once):
          LA0..LA9, AV, AH, AF, AN, VD (15 bytes), pitch lo, pitch hi, place (18 bytes)
  $80-$FE HOLD for (H & $7F) + 1 frames
  $FF     END
Parameters are 8.8 accumulators (the value is the high byte); the pitch is 16.8 in 1/256
semitone (MIDI note * 256). A ramp adds ((target << 8) - acc) >> H every frame and lands
exactly on the target in its last frame.
"""
import sys

import tables as T

NPAR = 15
OUT_SHIFT = 2          # the sample written for the DMA is the output wave times 4
P_AV, P_AH, P_AF, P_AN, P_VD = 10, 11, 12, 13, 14

KTAB = T.ktab()
GTABS = T.gtabs()
GTSEL = T.gt_select()
NOTEINC = T.note_inc()
FRAC = T.frac()
SIN = T.sin8()


def s16(v):
    return ((v + 32768) & 0xFFFF) - 32768


class Voice:
    def __init__(self, stream):
        self.s = bytes(stream)
        self.pos = 0
        self.acc = [0] * NPAR
        self.inc = [0] * NPAR
        self.tgt = [0] * NPAR
        self.pacc = 0
        self.pinc = 0
        self.ptgt = 0
        self.place = 9
        self.mode = 'HOLD'
        self.cnt = 0
        self.done = False
        self.vph = 0
        # derived
        self.km = [0] * 10
        self.neg = [False] * 10
        self.av = self.ah = self.af = self.an = 0
        self.incp = 0
        self.gt = GTABS[0]
        # sample state
        self.R = [0] * 10
        self.L = [0] * 10
        self.nl = 0
        self.phase = 0
        self.x = 1
        self.over = 0           # values that left the 16-bit range (must stay 0)
        self.peak = 0           # largest |wave| seen in the tube
        self.frames = 0

    # ---------------- control ----------------
    def read(self):
        h = self.s[self.pos]
        self.pos += 1
        if h == 0xFF:
            self.mode, self.done = 'END', True
            self.pos -= 1                    # stays on END
            return
        if h & 0x80:
            self.mode, self.cnt = 'HOLD', (h & 0x7F) + 1
            return
        assert h <= 6, h
        t = self.s[self.pos:self.pos + 18]
        self.pos += 18
        self.tgt = list(t[:NPAR])
        self.ptgt = t[15] | t[16] << 8
        self.place = t[17]
        for i in range(NPAR):
            self.inc[i] = ((self.tgt[i] << 8) - self.acc[i]) >> h if h else 0
        self.pinc = ((self.ptgt << 8) - self.pacc) >> h if h else 0
        self.mode, self.cnt = 'RAMP', 1 << h

    def frame(self):
        if self.cnt == 0 and self.mode != 'END':
            self.read()
        if self.mode == 'RAMP':
            self.cnt -= 1
            if self.cnt == 0:
                self.acc = [t << 8 for t in self.tgt]
                self.pacc = self.ptgt << 8
            else:
                for i in range(NPAR):
                    self.acc[i] += self.inc[i]
                    assert 0 <= self.acc[i] <= 0xFFFF, (i, self.acc[i])
                self.pacc += self.pinc
                assert 0 <= self.pacc <= 0xFFFFFF
        elif self.mode == 'HOLD':
            self.cnt -= 1
        self.derive()
        self.frames += 1

    def derive(self):
        la = [a >> 8 for a in self.acc[:10]]
        for j in range(1, 10):
            d = la[j - 1] - la[j]
            self.neg[j] = d < 0
            self.km[j] = KTAB[-d if d < 0 else d]
        self.av = self.acc[P_AV] >> 8
        self.ah = self.acc[P_AH] >> 8
        self.af = self.acc[P_AF] >> 8
        self.an = self.acc[P_AN] >> 8
        vd = self.acc[P_VD] >> 8
        vib = (SIN[self.vph] * vd) >> 8
        pe = (self.pacc >> 8) + vib
        assert 0 <= pe <= 0x7FFF, pe
        n, f = pe >> 8, pe & 255
        self.gt = GTABS[GTSEL[n]]
        ni = NOTEINC[n]
        self.incp = (ni + ((ni * FRAC[f]) >> 16)) & 0xFFFFFF
        self.vph = (self.vph + T.VRATE) & 255

    # ---------------- samples ----------------
    def block(self, n):
        """n samples with the current frame's values; returns them as ints (int16)."""
        R, L, km, neg = self.R, self.L, self.km, self.neg
        av, ah, af, an, place = self.av, self.ah, self.af, self.an, self.place
        incp, phase, x, nl, gt = self.incp, self.phase, self.x, self.nl, self.gt
        over, peak = self.over, self.peak
        out = []
        for _ in range(n):
            phase = (phase + incp) & 0xFFFFFF
            src = (gt[phase >> 16] * av) >> 8
            x ^= (x << 7) & 0xFFFF
            x ^= x >> 9
            x ^= (x << 8) & 0xFFFF
            ns = x - 65536 if x & 0x8000 else x
            if ah:
                v = src + ((ns * ah) >> 11)
                if not -32768 <= v <= 32767:
                    over += 1
                src = s16(v)
            # even pass: the glottis, junctions 2 4 6 8, the lips
            l0 = L[0]
            v = l0 - (l0 >> 2) + src
            if not -32768 <= v <= 32767:
                over += 1
            R[0] = s16(v)
            for j in (2, 4, 6, 8):
                a, b = R[j - 1], L[j]
                d = a + b
                if not -32768 <= d <= 32767:
                    over += 1
                    d = s16(d)
                dl = (d * km[j]) >> 16
                if neg[j]:
                    r, l = a + dl, b - dl
                else:
                    r, l = a - dl, b + dl
                if not (-32768 <= r <= 32767 and -32768 <= l <= 32767):
                    over += 1
                R[j], L[j - 1] = s16(r), s16(l)
            r9 = R[9]
            L[9] = s16((r9 >> 3) - r9)
            if af and not place & 1:
                v = R[place] + ((ns * af) >> 11)
                if not -32768 <= v <= 32767:
                    over += 1
                R[place] = s16(v)
            # odd pass: junctions 1 3 5 7 9
            for j in (1, 3, 5, 7, 9):
                a, b = R[j - 1], L[j]
                d = a + b
                if not -32768 <= d <= 32767:
                    over += 1
                    d = s16(d)
                dl = (d * km[j]) >> 16
                if neg[j]:
                    r, l = a + dl, b - dl
                else:
                    r, l = a - dl, b + dl
                if not (-32768 <= r <= 32767 and -32768 <= l <= 32767):
                    over += 1
                R[j], L[j - 1] = s16(r), s16(l)
            if af and place & 1:
                v = R[place] + ((ns * af) >> 11)
                if not -32768 <= v <= 32767:
                    over += 1
                R[place] = s16(v)
            y = R[9]
            if an:
                pv = R[4] + L[4]
                if not -32768 <= pv <= 32767:
                    over += 1
                pv = s16(pv)
                nl = s16(nl + (pv >> 2) - (nl >> 2))
                v = y + ((nl * an) >> 8)
                if not -32768 <= v <= 32767:
                    over += 1
                y = s16(v)
            m = max(max(R), -min(R), max(L), -min(L))
            if m > peak:
                peak = m
            v = y << OUT_SHIFT
            if not -32768 <= v <= 32767:
                over += 1
            out.append(s16(v))
        self.phase, self.x, self.nl, self.over, self.peak = phase, x, nl, over, peak
        return out


def render(stream, nframes=None, stop_at_end=True):
    """The voice as the engine writes it: a list of int16 samples, FRAME per control frame."""
    v = Voice(stream)
    out = []
    f = 0
    while nframes is None or f < nframes:
        v.frame()
        if v.done and stop_at_end:
            break
        out += v.block(T.FRAME)
        f += 1
    return out, v


if __name__ == '__main__':
    import numpy as np
    data = open(sys.argv[1], 'rb').read()
    out, v = render(data)
    print(f"{len(out)} samples, {v.frames} frames, overflows {v.over}, tube peak {v.peak}, out peak {max(map(abs, out))}")
    if len(sys.argv) > 2:
        import wave
        w = wave.open(sys.argv[2], 'wb')
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(__import__('kl').FS)))
        w.writeframes(np.array(out, dtype='<i2').tobytes())
        w.close()
