"""Fit 10-section area functions to the male vowel formants of Peterson & Barney (1952)."""
import json, sys
import numpy as np
import kl

TARGETS = {
    'IY': (270, 2290, 3010), 'IH': (390, 1990, 2550), 'EH': (530, 1840, 2480),
    'AE': (660, 1720, 2410), 'AH': (640, 1190, 2390), 'AA': (730, 1090, 2440),
    'AO': (570, 840, 2410), 'UH': (440, 1020, 2240), 'UW': (300, 870, 2240),
    'ER': (490, 1350, 1690), 'AX': (500, 1500, 2500), 'E': (480, 2000, 2600), 'O': (500, 900, 2400),
    'W': (290, 610, 2150), 'Y': (260, 2070, 3020), 'R': (310, 1060, 1380), 'L': (360, 1150, 2650),
}
STARTS = {
    'front': [1.5, 3, 6, 8, 8, 6, 2, 0.5, 0.4, 1.5],
    'open':  [1.0, 0.6, 0.6, 1.0, 2.5, 5, 7, 7, 6, 4],
    'back':  [1.5, 3, 5, 5, 3, 1, 0.6, 2, 3, 0.4],
    'mid':   [2, 2.5, 3, 3, 3, 3, 3, 3, 3, 3],
    'r':     [1.5, 1.0, 1.5, 3, 4, 3, 0.6, 2, 2, 1.2],
}
def main():
    out = {}
    for v, tg in TARGETS.items():
        starts = [kl.la_of(s) for s in STARTS.values()]
        lo = np.full(10, kl.la_of(0.15 if v in ('W', 'Y', 'R', 'L') else 0.25))
        hi = np.full(10, kl.la_of(11.0))
        lo[0], hi[0] = kl.la_of(0.8), kl.la_of(4.0)      # the larynx tube
        la, fs, c = kl.fit(tg, starts, lo=lo, hi=hi)
        out[v] = [round(float(x), 2) for x in la]
        print(f"{v:3s} target {tg}  got {[round(f) for f in fs[:4]]}  cost {c:.4f}")
        print("     areas", [round(float(a), 2) for a in kl.area(la)])
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else 'vowels.json', 'w'), indent=1)
main()
