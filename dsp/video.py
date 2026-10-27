"""A preview video of the show, rendered on a PC: the screen as the engine wrote it, with the
listening preview's sound.

The screen comes from the engine's own run in the 6502 emulator (test_engine.py --dump): every
write to the screen and to TEXTYPOS, at the sample that had played when it happened. The picture
is a 720 x 576 PAL frame with the 40 x 25 text area 640 x 400 in it; the characters are the
project's approximate character set (c64font.py), not the MEGA65's. The meter's digits are the
emulator's figures (NMOS 6502 timings), not a measurement on the MEGA65 (DAISY BELL does not show
the meter). The end card, if given, is the BASIC simulator's picture of it (card.py draws it with
the figures the MEGA65 measured), held for a few seconds after the show.
Usage: python3 video.py BUILD_DIR OUT.mp4 [--card PNG]
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from c64font import LOWER  # noqa: E402

import kl  # noqa: E402

W, H = 720, 576
TX, TY = 40, 88                  # the text area's top left in the frame
FPS = 25
BASE = 0x68                      # TEXTYPOS as the engine found it in the harness
WHITE, GREY = (255, 255, 255), (136, 136, 136)


def glyphs():
    g = np.zeros((256, 16, 16), dtype=bool)
    for c in range(256):
        for r in range(8):
            for b in range(8):
                if LOWER[c][r] & (0x80 >> b):
                    g[c, 2 * r:2 * r + 2, 2 * b:2 * b + 2] = True
    return g


def main():
    build = Path(sys.argv[1])
    out = sys.argv[2]
    card = Path(sys.argv[sys.argv.index('--card') + 1]) if '--card' in sys.argv else None
    log = json.loads((build / 'screen_log.json').read_text())
    show = json.loads((build / 'show.json').read_text())
    g = glyphs()
    writes = sorted(log['screen'], key=lambda w: w[0])
    typos = sorted(log['textypos'], key=lambda w: w[0])
    colour = np.zeros((25, 40, 3), dtype=np.uint8)
    colour[:, :] = WHITE
    colour[23, :] = GREY
    screen = np.full(1000, 32, dtype=np.int32)
    n_show = int(show['T_END'] / kl.FS * FPS) + 1
    hold = 2 * FPS
    card_frames = 5 * FPS if card else 0
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
           '-r', str(FPS), '-i', '-', '-i', str(build / 'preview.wav'), '-map', '0:v', '-map', '1:a',
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-vf', 'setsar=16/15', '-c:a', 'aac', '-b:a', '160k', out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    wi = ti = 0
    ty = BASE
    for k in range(n_show + hold):
        s = k / FPS * kl.FS
        while wi < len(writes) and writes[wi][0] <= s:
            _, off, v = writes[wi]
            if 0 <= off < 1000:
                screen[off] = v
            wi += 1
        while ti < len(typos) and typos[ti][0] <= s:
            ty = typos[ti][1]
            ti += 1
        text = g[screen.reshape(25, 40)]                      # 25 x 40 x 16 x 16
        text = text.transpose(0, 2, 1, 3).reshape(400, 640)
        rgb = np.where(text[..., None], np.repeat(np.repeat(colour, 16, 0), 16, 1), 0).astype(np.uint8)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        dy = ty - BASE
        top = TY + dy                                          # the text area moved down by dy lines
        y0, y1 = max(TY, top), min(TY + 400, top + 400)        # shown only between the borders
        if y1 > y0:
            frame[y0:y1, TX:TX + 640] = rgb[y0 - top:y1 - top]
        ff.stdin.write(frame.tobytes())
    if card:
        # the simulator's picture (bas65sim.render, scale 2: a 48-pixel border round 1280 x 800),
        # its text area brought to the 640 x 400 of the frames above
        from PIL import Image
        im = Image.open(card).convert('RGB')
        bw = (im.width - 1280) // 2
        im = im.crop((bw, bw, bw + 1280, bw + 800)).resize((640, 400), Image.NEAREST)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[TY:TY + 400, TX:TX + 640] = np.asarray(im)
        for _ in range(card_frames):
            ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    ff.wait()
    print(f"{out}: {(n_show + hold + card_frames) / FPS:.1f} s, {wi} screen writes, {ti} text positions")


if __name__ == '__main__':
    main()
