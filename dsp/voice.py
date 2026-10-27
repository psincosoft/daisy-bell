"""Syllables, notes and phonemes to keyframes for the tube (speech synthesis by rule, ours).

Every phoneme is a state that holds its values for a while; between states the values glide
linearly. Consonants take the shape of the neighbouring vowel with a constriction at their
place, as a tongue or the lips would; the vowel starts on the beat, the consonants before it
come out of the previous note, the closing consonants end the note.
Frames are 5 ms (tables.FRAME samples). Sections: 0 at the glottis .. 9 at the lips.
"""
import json
from pathlib import Path

import numpy as np

import kl

SHAPES = {k: np.array(v, dtype=float) for k, v in json.load(open(Path(__file__).with_name('vowels.json'))).items()}
DIPH = {'EY': ('E', 'IY'), 'AY': ('AA', 'IH'), 'OW': ('O', 'UH')}

LA = lambda a: float(kl.la_of(a))
CLOSED = LA(0.012)

# place: the section of the constriction; noise: the section whose forward wave takes the noise
CONS = {
    # stops: place, voiced
    'B': ('stop', 9, True), 'P': ('stop', 9, False), 'D': ('stop', 8, True), 'T': ('stop', 8, False),
    'G': ('stop', 5, True), 'K': ('stop', 5, False),
    'M': ('nasal', 9), 'N': ('nasal', 8),
    # fricatives: place, area cm^2, noise section, level, voiced, frames
    'F': ('fric', 9, 0.10, 9, 40, False, 13), 'V': ('fric', 9, 0.14, 9, 26, True, 11),
    'S': ('fric', 8, 0.07, 9, 52, False, 17), 'Z': ('fric', 8, 0.09, 9, 34, True, 13),
    'SH': ('fric', 7, 0.12, 8, 52, False, 17), 'DH': ('fric', 9, 0.20, 9, 22, True, 8),
    'HH': ('asp',), 'JH': ('affr', 7),
    'L': ('approx', 'L', 11), 'R': ('approx', 'R', 11), 'W': ('approx', 'W', 9), 'Y': ('approx', 'Y', 9),
}


def constrict(shape, place, area, spread=1):
    la = shape.copy()
    c = LA(area)
    la[place] = min(la[place], c)
    for n in (place - 1, place + 1):
        if 0 <= n < len(la) and spread:
            la[n] = min(la[n], c + 30.0)
    return la


class State:
    def __init__(self, shape, dur, av=0.0, ah=0.0, af=0.0, an=0.0, noise=9, trans=4, name=''):
        self.shape, self.dur, self.av, self.ah, self.af, self.an = shape, dur, av, ah, af, an
        self.noise, self.trans, self.name = noise, trans, name


def onset_states(cons, vowel):
    """States for the consonants before a vowel (a list, in time order)."""
    out = []
    v = SHAPES[vowel]
    for i, c in enumerate(cons):
        spec = CONS[c]
        nxt = cons[i + 1] if i + 1 < len(cons) else None
        if spec[0] == 'stop':
            _, place, voiced = spec
            after_s = i > 0 and cons[i - 1] == 'S'
            out.append(State(constrict(v, place, 0.012), 10 if voiced else (8 if after_s else 12),
                             av=0.28 if voiced else 0.0, trans=3, name=c + ' closure'))
            out.append(State(constrict(v, place, 0.35, 0), 1, av=0.3 if voiced else 0.0,
                             af=50 if voiced else 70, noise=min(9, place + 1), trans=1, name=c + ' burst'))
            if not voiced and not after_s:
                tgt = SHAPES[CONS[nxt][1]] if nxt and CONS[nxt][0] == 'approx' else v
                out.append(State(tgt, 9, ah=46, trans=2, name=c + ' aspiration'))
        elif spec[0] == 'nasal':
            out.append(State(constrict(v, spec[1], 0.012), 12, av=0.85, an=190, trans=4, name=c))
        elif spec[0] == 'fric':
            _, place, area, noise, level, voiced, dur = spec
            out.append(State(constrict(v, place, area), dur, av=0.45 if voiced else 0.0, af=level,
                             noise=noise, trans=4, name=c))
        elif spec[0] == 'asp':
            out.append(State(v.copy(), 12, ah=52, trans=3, name='HH'))
        elif spec[0] == 'approx':
            shp = 0.85 * SHAPES[spec[1]] + 0.15 * v
            out.append(State(shp, spec[2], av=0.85, trans=6, name=c))
        else:
            raise ValueError(c)
    return out


def coda_states(cons, vowel, final):
    """States for the consonants after a vowel; final: a rest follows, so stops are released."""
    out = []
    v = SHAPES[DIPH[vowel][1]] if vowel in DIPH else SHAPES[vowel]
    for i, c in enumerate(cons):
        spec = CONS[c]
        last = i == len(cons) - 1
        if spec[0] == 'stop':
            _, place, voiced = spec
            out.append(State(constrict(v, place, 0.012), 9, av=0.25 if voiced else 0.0, trans=3, name=c + ' closure'))
            if last:
                out.append(State(constrict(v, place, 0.35, 0), 1, af=64 if not voiced else 44,
                                 noise=min(9, place + 1), trans=1, name=c + ' release'))
                out.append(State(v * 0.6 + SHAPES['AX'] * 0.4, 6, ah=30 if not voiced else 8, trans=2, name=c + ' breath'))
        elif spec[0] == 'nasal':
            out.append(State(constrict(v, spec[1], 0.012), 14, av=0.85, an=190, trans=4, name=c))
        elif spec[0] == 'fric':
            _, place, area, noise, level, voiced, dur = spec
            out.append(State(constrict(v, place, area), dur, av=0.45 if voiced else 0.0, af=level,
                             noise=noise, trans=4, name=c))
        elif spec[0] == 'affr':
            out.append(State(constrict(v, 7, 0.012), 6, av=0.3, trans=3, name='JH closure'))
            out.append(State(constrict(v, 7, 0.13), 11, av=0.4, af=36, noise=8, trans=1, name='JH fric'))
        elif spec[0] == 'approx':
            shp = 0.85 * SHAPES[spec[1]] + 0.15 * v
            out.append(State(shp, spec[2] + 2, av=0.85, trans=6, name=c))
        else:
            raise ValueError(c)
    return out


def span(states):
    return sum(s.dur + s.trans for s in states)


class Track:
    """Breakpoints (frame, value) with linear interpolation; values beyond the ends hold."""
    def __init__(self, v0):
        self.pts = [(0, v0)]

    def add(self, f, v):
        f = int(round(f))
        if f < self.pts[-1][0]:
            f = self.pts[-1][0]
        if f == self.pts[-1][0]:
            self.pts[-1] = (f, v)
        else:
            self.pts.append((f, v))

    def at(self, f):
        p = self.pts
        if f <= p[0][0]:
            return p[0][1]
        for (fa, va), (fb, vb) in zip(p, p[1:]):
            if fa <= f <= fb:
                if fb == fa:
                    return vb
                t = (f - fa) / (fb - fa)
                return va + (vb - va) * t
        return p[-1][1]


def build(notes, beat_frames, start_frame, total_frames, av_mf=200, av_f=228, vib=150):
    """notes: list of (syllable key or None, midi or [midis], beats) from score.MELODY, sung an
    octave down from start_frame. Returns keyframes for vstream.build and, for every syllable,
    (key, frame its sound starts, frame of its beat)."""
    import score
    neutral = SHAPES['AX']
    shape = Track(neutral.copy())
    av, ah, af, an, noise = Track(0.0), Track(0.0), Track(0.0), Track(0.0), Track(9.0)
    vd, pitch = Track(0.0), Track(float((notes[0][1] - 12) * 256))
    t = start_frame
    events = []
    for syl, midi, beats in notes:
        events.append((syl, midi, t, int(round(beats * beat_frames))))
        t += int(round(beats * beat_frames))

    def pre(syl):
        on, nuc, _ = score.LEXICON[syl]
        st = onset_states(on, DIPH.get(nuc, (nuc,))[0])
        return st, span(st) + (6 if st else 8)

    onsets = []
    forte = False
    silent = True
    for i, (syl, midi, t0, dur) in enumerate(events):
        if syl is None:
            continue
        if syl == score.FORTE_FROM:
            forte = True
        level = (av_f if forte else av_mf) + (12 if syl in score.ACCENT else 0)
        on, nuc, coda = score.LEXICON[syl]
        v1, v2 = DIPH.get(nuc, (nuc, nuc))
        nxt = events[i + 1] if i + 1 < len(events) else None
        rest_after = nxt is None or nxt[0] is None
        on_st, p = pre(syl)
        cd_st = coda_states(coda, nuc, rest_after)
        t_end = t0 + dur if rest_after else nxt[2] - pre(nxt[0])[1]
        t_cd = t_end - span(cd_st)
        assert t_cd > t0 + 4, (syl, t0, t_cd)
        f = t0 - p
        onsets.append((syl, f, t0))
        if silent:
            for tr in (av, ah, af, an):
                tr.add(f, 0.0)
        for s in on_st:
            f += s.trans
            put(shape, av, ah, af, an, noise, f, s, level)
            f += s.dur
            put(shape, av, ah, af, an, noise, f, s, level)
        # the vowel, on the beat
        shape.add(t0, SHAPES[v1])
        av.add(t0, level)
        ah.add(t0, 3.0)
        af.add(t0, 0.0)
        an.add(t0, 0.0)
        noise.add(t0, 9.0)
        if v2 != v1:
            glide = min(14, max(6, (t_cd - t0) // 4))
            hold2 = min(16, max(4, (t_cd - t0) // 6))
            shape.add(t_cd - hold2 - glide, SHAPES[v1])
            shape.add(t_cd - hold2, SHAPES[v2])
        shape.add(t_cd, SHAPES[v2])
        av.add(t_cd, level)
        ah.add(t_cd, 3.0)
        af.add(t_cd, 0.0)
        an.add(t_cd, 0.0)
        noise.add(t_cd, 9.0)
        f = t_cd
        for s in cd_st:
            f += s.trans
            put(shape, av, ah, af, an, noise, f, s, level)
            f += s.dur
            put(shape, av, ah, af, an, noise, f, s, level)
        silent = rest_after
        if rest_after:
            fade = 10 if not cd_st else 3
            for tr in (av, ah, af, an):
                tr.add(f + fade, 0.0)
            shape.add(f + fade + 6, neutral * 0.5 + SHAPES[v2] * 0.5)
        # pitch: a glide into the note just before its beat; a triplet is three steps
        steps = midi if isinstance(midi, list) else [midi]
        step = dur // len(steps)
        for k, m in enumerate(steps):
            a = t0 + k * step
            g = 6 if k == 0 else 4
            pitch.add(a - g, pitch.at(a - g))
            pitch.add(a, (m - 12) * 256.0)
            pitch.add(a + step - (4 if k + 1 < len(steps) else 1), (m - 12) * 256.0)
        # vibrato on long notes, after a fifth of a second
        if dur >= int(1.5 * beat_frames):
            vd.add(t0 + 40, 0.0)
            vd.add(t0 + 110, float(vib))
            vd.add(t_cd - 8, float(vib))
            vd.add(t_cd, 0.0)
    av.add(total_frames - 1, 0.0)
    keys = merge([shape, av, ah, af, an, vd, pitch], noise, total_frames)
    return keys, onsets


def prev_silent(av, f):
    return av.at(f) < 1.0


def put(shape, av, ah, af, an, noise, f, s, level):
    shape.add(f, s.shape)
    av.add(f, s.av * level)
    ah.add(f, s.ah)
    af.add(f, s.af)
    an.add(f, s.an)
    noise.add(f, float(s.noise))


def merge(tracks, noise, total):
    frames = sorted(set(f for tr in tracks + [noise] for f, _ in tr.pts if 0 <= f < total) | {0, total - 1})
    keys = []
    nz = noise.pts
    for f in frames:
        vals = list(tracks[0].at(f)) + [tracks[i].at(f) for i in range(1, 6)] + [tracks[6].at(f)]
        # the noise place of the latest breakpoint at or before f
        pl = 9
        for ff, vv in nz:
            if ff <= f:
                pl = int(vv)
        keys.append((f, vals, pl))
    # a run that ramps frication in takes the place it ramps into
    out = []
    for i, (f, vals, pl) in enumerate(keys):
        if i + 1 < len(keys) and keys[i + 1][1][12] > 0.5 and vals[12] < 0.5:
            pl = keys[i + 1][2]
        out.append((f, vals, pl))
    return out
