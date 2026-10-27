"""Check a D81 image: the directory, every file's sector chain, the BAM, and the files' contents.

Checks: links inside the disk (tracks 1-80, sectors 0-39), no loop, no sector in two files or in
a file and the directory, every used sector marked used in the BAM and every free count equal to
its bitmap, directory block counts equal to the chains' lengths, and each file equal to its
source (NAME=PATH pairs). Prints the directory in order.
Usage: python3 check_d81.py DISK.d81 [NAME=PATH ...]
"""
import sys
from pathlib import Path

TRACKS, SECTORS = 80, 40


def off(t, s):
    assert 1 <= t <= TRACKS and 0 <= s < SECTORS, (t, s)
    return ((t - 1) * SECTORS + s) * 256


def chain(img, t, s, owner, used, errors):
    data, n, seen = bytearray(), 0, set()
    while True:
        if not (1 <= t <= TRACKS and 0 <= s < SECTORS):
            errors.append(f"{owner}: link out of range {t}/{s}")
            break
        if (t, s) in seen:
            errors.append(f"{owner}: loop at {t}/{s}")
            break
        seen.add((t, s))
        if (t, s) in used:
            errors.append(f"{owner}: sector {t}/{s} also used by {used[(t, s)]}")
        used[(t, s)] = owner
        blk = img[off(t, s):off(t, s) + 256]
        n += 1
        nt, ns = blk[0], blk[1]
        if nt == 0:
            if ns < 1:
                errors.append(f"{owner}: last block {t}/{s} ends at {ns}")
            data += blk[2:ns + 1]
            break
        data += blk[2:]
        t, s = nt, ns
    return bytes(data), n


def main():
    img = Path(sys.argv[1]).read_bytes()
    assert len(img) == TRACKS * SECTORS * 256, len(img)
    sources = dict(a.split('=', 1) for a in sys.argv[2:])
    errors, used = [], {}
    for s in (0, 1, 2):
        used[(40, s)] = 'header/BAM'
    hdr = img[off(40, 0):off(40, 0) + 256]
    name = bytes(hdr[4:20]).rstrip(b'\xa0').decode('latin-1')
    print(f"disk name {name!r}, id {bytes(hdr[22:24]).decode('latin-1')!r}, dos {chr(hdr[25])}{chr(hdr[26])}")
    # the directory
    files = []
    t, s = hdr[0], hdr[1]
    dseen = set()
    while t:
        if (t, s) in dseen or not (1 <= t <= TRACKS and 0 <= s < SECTORS):
            errors.append(f"directory: bad link {t}/{s}")
            break
        dseen.add((t, s))
        if (t, s) in used and used[(t, s)] != 'header/BAM':
            errors.append(f"directory: sector {t}/{s} also used by {used[(t, s)]}")
        used[(t, s)] = 'directory'
        blk = img[off(t, s):off(t, s) + 256]
        for e in range(8):
            ent = blk[e * 32:(e + 1) * 32]
            ftype = ent[2]
            if ftype == 0:
                continue
            fname = bytes(ent[5:21]).rstrip(b'\xa0').decode('latin-1')
            files.append((fname, ftype, ent[3], ent[4], ent[30] | ent[31] << 8))
        t, s = blk[0], blk[1]
    kinds = {0x81: 'SEQ', 0x82: 'PRG', 0x83: 'USR', 0x84: 'REL'}
    for fname, ftype, ft, fs, blocks in files:
        data, n = chain(img, ft, fs, fname, used, errors)
        if n != blocks:
            errors.append(f"{fname}: directory says {blocks} blocks, chain has {n}")
        src = sources.get(fname)
        same = ''
        if src is not None:
            want = Path(src).read_bytes()
            same = 'equal to its source' if data == want else 'DIFFERENT FROM ITS SOURCE'
            if data != want:
                errors.append(f"{fname}: content differs from {src}")
        print(f"  {fname:16s} {kinds.get(ftype & 0x8F, hex(ftype))} {blocks:4d} blocks {len(data):6d} bytes  {same}")
    for fname in sources:
        if fname not in [f[0] for f in files]:
            errors.append(f"{fname}: not on the disk")
    # the BAM: sectors 40/1 (tracks 1-40) and 40/2 (tracks 41-80)
    free_total = 0
    for b, first in ((1, 1), (2, 41)):
        bam = img[off(40, b):off(40, b) + 256]
        for i in range(40):
            tr = first + i
            e = bam[16 + i * 6:16 + i * 6 + 6]
            bits = [(e[1 + j // 8] >> (j % 8)) & 1 for j in range(SECTORS)]
            if e[0] != sum(bits):
                errors.append(f"BAM: track {tr} free count {e[0]}, bitmap {sum(bits)}")
            for sec in range(SECTORS):
                if bits[sec] and (tr, sec) in used:
                    errors.append(f"BAM: {tr}/{sec} marked free but used by {used[(tr, sec)]}")
            if tr != 40:
                free_total += sum(bits)
    print(f"files {len(files)}, sectors used {len(used)}, blocks free (track 40 excluded) {free_total}")
    print("errors:", len(errors))
    for e in errors[:20]:
        print("  ", e)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
