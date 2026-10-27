"""Keyframes to the voice stream: linear ramps cut into power-of-two runs, and holds.

A keyframe is (frame, params, place): params holds the 15 byte parameters (LA0..LA9, AV, AH,
AF, AN, VD) and the pitch (1/256 semitone) as its 16th entry. Between keyframes the values glide
linearly; a run whose values do not change becomes a hold. The place (where frication enters
the tube) is the place of the keyframe that starts the run.

Frame f's values are the ones the engine uses for the samples of frame f. The first keyframe
must be at frame 0; it is set at once (a ramp of one frame). A run from keyframe a to keyframe b
covers the frames a+1 .. b and lands on b's values in frame b.
"""
NP = 16


def clamp8(v):
    return max(0, min(255, int(round(v))))


def quant(vals):
    return [clamp8(v) for v in vals[:15]] + [max(0, min(0x7FFF, int(round(vals[15]))))]


def record_ramp(h, vals, place):
    q = quant(vals)
    return bytes([h] + q[:15] + [q[15] & 255, q[15] >> 8, place])


def record_hold(n):
    out = b''
    while n > 0:
        k = min(n, 127)
        out += bytes([0x80 | (k - 1)])
        n -= k
    return out


def build(keys):
    """keys: list of (frame, [16 values], place), frames strictly increasing, the first at 0."""
    assert keys[0][0] == 0
    out = bytearray(record_ramp(0, keys[0][1], keys[0][2]))
    cur = quant(keys[0][1])
    frame = 0
    for (fa, va, pa), (fb, vb, pb) in zip(keys, keys[1:]):
        assert fb > fa, (fa, fb)
        n = fb - frame
        tgt = quant(vb)
        if tgt == cur:
            out += record_hold(n)
            frame = fb
            continue
        done = 0
        k = 6
        while done < n:
            while (1 << k) > n - done:
                k -= 1
            done += 1 << k
            frac = done / n
            vals = [cur[i] + (tgt[i] - cur[i]) * frac for i in range(NP)]
            out += record_ramp(k, vals, pa)
        frame = fb
        cur = tgt
    out += bytes([0xFF])
    return bytes(out)
