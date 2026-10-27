"""The Kelly-Lochbaum tube: constants, area coding, reflection table, formants, fitting.

The tract is N cylinders of equal length l from the glottis (section 0) to the lips (section N-1).
With the sample period T equal to the round trip through one section (l = c*T/2), every
junction is updated once per sample in two half-passes (even junctions, then odd ones), which is
the Kelly-Lochbaum lattice with its half-sample delays.

Areas are coded as LA = 24*log2(A / 0.01 cm^2), 0..255 (0.01 to 15.97 cm^2, 1/24 octave a step).
The reflection coefficient of the junction between sections j-1 and j depends only on the
difference d = LA[j-1] - LA[j]:  k = (A1 - A2)/(A1 + A2) = tanh(d*ln2/48).
"""
import math

import numpy as np

CPU_HZ = 40_500_000
DMA_FREQ = 4142                       # audio DMA time base: fs = DMA_FREQ * CPU_HZ / 2^24
FS = DMA_FREQ * CPU_HZ / 2 ** 24      # 9998.9 Hz
N = 10                                # sections
C_CM = 35000.0                        # speed of sound, cm/s (warm, moist air)
SEC_CM = C_CM / (2 * FS)              # 1.750 cm per section; 17.5 cm in all
A_MIN = 0.01


def area(la):
    return A_MIN * 2.0 ** (np.asarray(la, dtype=float) / 24.0)


def la_of(a):
    return 24.0 * np.log2(np.asarray(a, dtype=float) / A_MIN)


def ktab():
    """|k| * 65536 for d = 0..255 (k for a positive d: the area gets smaller towards the lips)."""
    return [min(65535, int(round(65536 * math.tanh(d * math.log(2) / 48)))) for d in range(256)]


def chain_m22(areas, f):
    """M22 of the lossless chain matrix glottis -> lips at frequency f (array)."""
    th = 2 * math.pi * np.asarray(f) * SEC_CM / C_CM
    c, s = np.cos(th), np.sin(th)
    # matrices with Z = 1/A (rho*c dropped); entries: [[c, j s/A], [j A s, c]]
    m11 = np.ones_like(th)
    m12 = np.zeros_like(th)          # imaginary part
    m21 = np.zeros_like(th)          # imaginary part
    m22 = np.ones_like(th)
    for a in areas:
        z = 1.0 / a
        # [m11, j m12; j m21, m22] @ [c, j s z; j s / z, c]
        n11 = m11 * c - m12 * s / z
        n12 = m11 * s * z + m12 * c
        n21 = m21 * c + m22 * s / z
        n22 = -m21 * s * z + m22 * c
        m11, m12, m21, m22 = n11, n12, n21, n22
    return m22


_GRID = {}


def formants(la, fmax=None, step=2.0, exact=True):
    """Resonances (Hz) of the lossless tube, closed at the glottis, open at the lips."""
    fmax = fmax or FS / 2 - 1
    key = (fmax, step)
    if key not in _GRID:
        _GRID[key] = np.arange(step, fmax, step)
    f = _GRID[key]
    ar = area(la)
    m = chain_m22(ar, f)
    idx = np.nonzero(np.sign(m[:-1]) != np.sign(m[1:]))[0]
    # linear interpolation of each zero crossing, then (optionally) a few secant steps
    a, b = f[idx], f[idx + 1]
    ma, mb = m[idx], m[idx + 1]
    x = a - ma * (b - a) / (mb - ma)
    if exact and len(x):
        for _ in range(4):
            mx = chain_m22(ar, x)
            left = np.sign(mx) == np.sign(ma)
            a = np.where(left, x, a)
            ma = np.where(left, mx, ma)
            b = np.where(left, b, x)
            mb = np.where(left, mb, mx)
            x = a - ma * (b - a) / (mb - ma)
    return list(x)


def fit(targets, start, weights=(1.0, 1.0, 0.7, 0.15), smooth=0.002, lo=None, hi=None, f4=3500.0,
        fixed=None):
    """Log-areas whose first formants match the targets (Hz), smooth and within bounds."""
    from scipy.optimize import minimize
    lo = la_of(0.25) if lo is None else lo
    hi = la_of(11.0) if hi is None else hi
    tg = list(targets) + [f4][:max(0, 4 - len(targets))]
    fixed = fixed or {}

    def unpack(x):
        la = np.array(x, dtype=float)
        for j, v in fixed.items():
            la[j] = v
        return la

    def cost(x):
        la = unpack(x)
        fs = formants(la, step=10.0, exact=False)
        c = 0.0
        for i, t in enumerate(tg):
            if i < len(fs):
                c += weights[i] * math.log(fs[i] / t) ** 2
            else:
                c += 10.0
        c += smooth * float(np.sum(np.diff(la / 24.0) ** 2))
        c += 0.5 * float(np.sum(np.clip(lo - la, 0, None) ** 2 + np.clip(la - hi, 0, None) ** 2)) / 24.0
        return c

    best = None
    for s0 in (start if isinstance(start[0], (list, tuple, np.ndarray)) else [start]):
        r = minimize(cost, np.array(s0, dtype=float), method='Powell',
                     options={'maxiter': 20000, 'xtol': 0.05, 'ftol': 1e-7})
        if best is None or r.fun < best.fun:
            best = r
    la = unpack(best.x)
    return la, formants(la), best.fun
