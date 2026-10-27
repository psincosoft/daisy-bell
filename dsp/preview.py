"""A listening preview on a PC: the voice from the reference model, the SIDs from reSIDfp.

The voice is the engine's exact sample stream (bit for bit what it writes for the DMA). The SIDs
are emulated by reSIDfp (pyresidfp), not the MEGA65's own SID cores, at the PAL50 SID clock of
the MEGA65 (984,375 Hz); filters and levels differ from the machine's. The mix follows the
hypervisor's default stereo mixer (each pair 0.74 on its side, 0.25 on the other); the voice is
centred. The balance between voice and SIDs is therefore approximate.
Usage: python3 preview.py BUILD_DIR OUT.wav [seconds]
"""
import sys
import wave
from datetime import timedelta
from pathlib import Path

import numpy as np
from pyresidfp import SoundInterfaceDevice
from scipy.signal import resample_poly

import kl
import synth
import tables

SID_CLK = 984375.0
OUT_FS = 48000
VOICE_VOLUME = 204                  # the DMA channel's volume, as daisy-bell.b65 sets it (of 256)


def sid_freq(note, clk=SID_CLK):
    f = 440.0 * 2 ** ((note - 69) / 12)
    return min(0xFFFF, int(round(f * 2 ** 24 / clk)))


def decode_events(evt):
    """(sample time, register offset from $D400, value) for every SID write; other commands skipped."""
    out = []
    i, t = 0, 0
    while i < len(evt):
        d = evt[i] | evt[i + 1] << 8
        i += 2
        t += d
        while True:
            c = evt[i]
            i += 1
            if c == 0:
                break
            if c == 1:
                i += 3
            elif c == 2:
                out.append((t, evt[i], evt[i + 1]))
                i += 2
            elif c == 3:
                fr = sid_freq(evt[i + 1])
                out.append((t, evt[i], fr & 255))
                out.append((t, evt[i] + 1, fr >> 8))
                i += 2
            elif c == 4:
                i += 3 + evt[i + 2]
            elif c == 5:
                i += 2
            elif c == 6:
                i += 5
            elif c == 7:
                i += 2
            elif c == 8:
                pass
            elif c == 0xFF:
                return out, t
            else:
                raise ValueError(f"command {c:02X} at {i - 1}")
    return out, t


def render_sids(writes, t_end):
    sids = {0x00: SoundInterfaceDevice(clock_frequency=SID_CLK, sampling_frequency=OUT_FS),
            0x40: SoundInterfaceDevice(clock_frequency=SID_CLK, sampling_frequency=OUT_FS)}
    bufs = {k: [] for k in sids}
    now = 0.0
    writes = sorted(writes, key=lambda w: w[0])
    j = 0
    end_s = t_end / kl.FS
    while now < end_s:
        nxt = writes[j][0] / kl.FS if j < len(writes) else end_s
        nxt = min(nxt, end_s)
        if nxt > now:
            dt = timedelta(seconds=nxt - now)
            for k, s in sids.items():
                bufs[k] += s.clock(dt)
            now = nxt
        while j < len(writes) and writes[j][0] / kl.FS <= now + 1e-9:
            t, r, v = writes[j]
            base = r & 0x60
            if base in sids:
                sids[base].write_register(r & 0x1F, v)
            j += 1
        if j >= len(writes) and now >= end_s:
            break
    n = min(len(b) for b in bufs.values())
    return {k: np.array(b[:n], dtype=float) for k, b in bufs.items()}


def main():
    build = Path(sys.argv[1])
    out_wav = sys.argv[2]
    vox = (build / 'DAISY-BELL.VOX').read_bytes()[2:]
    evt = (build / 'DAISY-BELL.EVT').read_bytes()[2:]
    writes, t_end = decode_events(evt)
    if len(sys.argv) > 3:
        t_end = min(t_end, int(float(sys.argv[3]) * kl.FS))
    voice, v = synth.render(vox, nframes=t_end // tables.FRAME, stop_at_end=False)
    print(f"voice: {len(voice)} samples, overflows {v.over}, peak {max(map(abs, voice))}")
    sids = render_sids(writes, t_end)
    y = np.array(voice, dtype=float) * VOICE_VOLUME / 256
    vy = resample_poly(y, 24, 5)                 # 10 kHz -> 48 kHz (the DMA plays 9,998.7 Hz)
    n = min(len(vy), len(sids[0x00]))
    vy = vy[:n]
    s1, s3 = sids[0x00][:n] * 0.5, sids[0x40][:n] * 0.5
    left = 0.74 * (s3 + vy) + 0.25 * (s1 + vy)
    right = 0.74 * (s1 + vy) + 0.25 * (s3 + vy)
    peak = max(np.max(np.abs(left)), np.max(np.abs(right)))
    g = min(1.0, 30000 / peak)
    st = np.stack([left, right], axis=1) * g
    w = wave.open(out_wav, 'wb')
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(OUT_FS)
    w.writeframes(np.clip(st, -32767, 32767).astype('<i2').tobytes())
    w.close()
    print(f"{out_wav}: {n / OUT_FS:.1f} s, mix gain {g:.2f}")


if __name__ == '__main__':
    main()
