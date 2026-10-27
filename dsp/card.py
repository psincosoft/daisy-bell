"""Draw the end card with given figures, by running the program's own card code in the BASIC 65
simulator: for pictures and videos that show what the MEGA65 measured.

The listing and its label map come from the build (bas65pp writes NAME.map); the card's line is the
label @card, the figures are the variables share, peak and gaps. The card waits for a key: Q ends it.
Usage: python3 card.py BUILD_DIR OUT.png SHARE PEAK GAPS
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_sings as R  # noqa: E402


def label_map(path):
    labels, names = {}, {}
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith('@'):
            labels[parts[1][1:]] = int(parts[0])
        elif len(parts) == 2:
            names[parts[1]] = parts[0]
    return labels, names


def main():
    build, out = Path(sys.argv[1]), sys.argv[2]
    share, peak, gaps = (float(v) for v in sys.argv[3:6])
    labels, names = label_map(build / 'daisy-bell.map')
    m = R.M2(build)
    m.missing = set()
    m.set_mode(40, 25)
    m.lowercase = True
    m.d020 = m.d021 = 0
    interp = R.I2(build / 'daisy-bell.bas', m)
    interp.vars.update({names['share']: share, names['peak']: peak, names['gaps']: gaps,
                        names['cram']: 0.0, names['ntsc']: 0.0, names['fps']: 50.0})
    m.events.append({'when': lambda mm: any('Q: quit' in mm.row_text(r) for r in range(mm.rows)),
                     'key': 'Q', 'delay': 0.2})
    shot = {}

    def watch(mm):
        if not shot and any('Q: quit' in mm.row_text(r) for r in range(mm.rows)):
            mm.render(out)
            shot['rows'] = [mm.row_text(r) for r in range(mm.rows)]
        return False
    m.shots.append({'when': watch, 'path': out})
    interp.run(max_us=5_000_000, start=labels['card'])
    for r in shot.get('rows', []):
        if r.strip():
            print(f"|{r}|")
    if not shot:
        sys.exit("the card was not drawn")


if __name__ == '__main__':
    main()
