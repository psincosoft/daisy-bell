"""A still of the screen at a given moment of the show, as the engine wrote it in the emulator:
for the publication's picture of the full introduction text.

From the screen log of test_engine.py --dump; the cursor (a reversed space) left out; the
project's approximate character set (c64font.py), not the MEGA65's. Square pixels: the 40 x 25 text
area is drawn 1280 x 800 on a black 1536 x 1152 picture (a 4:3 frame).
Usage: python3 slide.py BUILD_DIR SECONDS OUT.png
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from c64font import LOWER  # noqa: E402

import kl  # noqa: E402

WHITE = (255, 255, 255)


def main():
    build, secs, out = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    log = json.loads((build / 'screen_log.json').read_text())
    s = secs * kl.FS
    screen = [32] * 1000
    for t, off, v in sorted(log['screen'], key=lambda w: w[0]):
        if t > s:
            break
        if 0 <= off < 1000:
            screen[off] = v
    screen = [32 if c == 160 else c for c in screen]      # no cursor
    img = np.zeros((1152, 1536, 3), dtype=np.uint8)
    x0, y0 = (1536 - 1280) // 2, (1152 - 800) // 2
    for i, code in enumerate(screen):
        row, col = divmod(i, 40)
        for r in range(8):
            bits = LOWER[code][r]
            for b in range(8):
                if bits & (0x80 >> b):
                    y, x = y0 + row * 32 + r * 4, x0 + col * 32 + b * 4
                    img[y:y + 4, x:x + 4] = WHITE
    Image.fromarray(img).save(out)
    print(f"{out}: the screen at {secs} s")


if __name__ == '__main__':
    main()
