"""Tables shared by the reference model and the engine (emitted into the engine as data)."""
import math

import numpy as np

import kl

FRAME = 50              # samples per control frame (5.0 ms)
VRATE = 7               # vibrato phase step per frame: 7/256 * 200 frames/s = 5.47 Hz


def ktab():
    return kl.ktab()


def gtab(peak=3000, oq=0.56, tp=0.40, harmonics=32):
    """One period of the glottal flow derivative (Rosenberg C pulse), band-limited, 256 entries."""
    n = 4096
    t = np.arange(n) / n
    tn = oq - tp
    u = np.where(t < tp, 0.5 * (1 - np.cos(np.pi * t / tp)),
                 np.where(t < tp + tn, np.cos(np.pi * (t - tp) / (2 * tn)), 0.0))
    du = np.gradient(u)
    spec = np.fft.rfft(du)
    spec[harmonics + 1:] = 0
    spec[0] = 0
    d = np.fft.irfft(spec, n)
    d = d[::n // 256]
    d *= peak / np.max(np.abs(d))
    out = [int(round(v)) for v in d]
    # zero mean over the period, so a held note adds no DC
    s = sum(out)
    out[int(np.argmax(np.abs(out)))] -= s
    return out


GT_HARMONICS = (32, 24, 16)     # three band-limited pulses, for low, middle and high notes


def gtabs():
    return [gtab(harmonics=h) for h in GT_HARMONICS]


def gt_select():
    """Which pulse each MIDI note uses: about 4.8 kHz of harmonics, never above 5 kHz."""
    out = []
    for n in range(128):
        f = 440.0 * 2 ** ((n - 69) / 12)
        out.append(0 if 32 * f <= 5000 else (1 if 24 * f <= 5000 else 2))
    return out


def note_inc():
    """24-bit phase increment per sample for MIDI note n (the table index is the phase's top byte)."""
    out = []
    for n in range(128):
        f = 440.0 * 2 ** ((n - 69) / 12)
        out.append(min(0xFFFFFF, int(round(f * 2 ** 24 / kl.FS))))
    return out


def frac():
    """65536 * (2^(f/3072) - 1): the fraction of a semitone, in 1/256 steps."""
    return [int(round(65536 * (2 ** (f / 3072) - 1))) for f in range(256)]


def sin8():
    """The vibrato wave, signed bytes -127..127."""
    return [int(round(127 * math.sin(2 * math.pi * i / 256))) for i in range(256)]
