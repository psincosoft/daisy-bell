"""Two-pass 6502 assembler driver around py65 (NMOS opcodes only), with expressions.

Extends the project's asm6502.py dialect:
  label:            NAME = expr          * = expr
  .byte e, e, ...   .word e, ...         .fill count, value
  expressions: numbers ($hex, %bin, decimal), symbols, + - * / & | ^ << >> ( ),
  and a leading < (low byte) or > (high byte) on an operand or a data item.
Absolute addresses below $0100 would become zero page; the engine never uses them.
Usage: python3 asm65.py SOURCE.asm OUT.bin [LISTING.txt] [SYMBOLS.txt]
"""
import re
import sys

from py65.assembler import Assembler
from py65.devices.mpu6502 import MPU

BRANCHES = {'BCC', 'BCS', 'BEQ', 'BMI', 'BNE', 'BPL', 'BVC', 'BVS'}
IMPLIED_A = {'ASL', 'LSR', 'ROL', 'ROR'}
TOKEN = re.compile(r'\$[0-9A-Fa-f]+|%[01]+|\d+|[A-Za-z_][A-Za-z0-9_]*|<<|>>|[-+*/&|^()~]|\s+')


class AsmError(Exception):
    pass


def evaluate(expr, syms, pc, strict):
    expr = expr.strip()
    part = 0
    if expr.startswith('<'):
        part, expr = 1, expr[1:]
    elif expr.startswith('>') and not expr.startswith('>>'):
        part, expr = 2, expr[1:]
    out, pos = [], 0
    while pos < len(expr):
        m = TOKEN.match(expr, pos)
        if not m:
            raise AsmError(f"bad expression '{expr}'")
        t = m.group(0)
        pos = m.end()
        if t.isspace():
            continue
        if t.startswith('$'):
            out.append(str(int(t[1:], 16)))
        elif t.startswith('%'):
            out.append(str(int(t[1:], 2)))
        elif t[0].isdigit():
            out.append(str(int(t)))
        elif t[0].isalpha() or t[0] == '_':
            if t == 'PC':
                out.append(str(pc))
            elif t in syms:
                out.append(str(syms[t]))
            elif strict:
                raise AsmError(f"unknown symbol {t}")
            else:
                out.append('6144')       # pass 1: any absolute address keeps the size right
        elif t == '/':
            out.append('//')
        else:
            out.append(t)
    v = eval(''.join(out), {'__builtins__': {}}, {})
    if part == 1:
        v &= 255
    elif part == 2:
        v = (v >> 8) & 255
    return v


def split_data(s):
    items, depth, cur = [], 0, ''
    for ch in s:
        if ch == ',' and depth == 0:
            items.append(cur)
            cur = ''
            continue
        depth += ch == '('
        depth -= ch == ')'
        cur += ch
    if cur.strip():
        items.append(cur)
    return items


def assemble(path):
    asm = Assembler(MPU())
    syms, labels = {}, {}
    lines = []
    for n, raw in enumerate(open(path), 1):
        line = raw.split(';', 1)[0].rstrip()
        if line.strip():
            lines.append((n, line.strip()))
    range_errors = []
    for npass in (1, 2):
        strict = npass == 2
        pc, origin, out, listing = None, None, bytearray(), []
        for n, s in lines:
            try:
                m = re.match(r'^\*\s*=\s*(.+)$', s)
                if m:
                    pc = evaluate(m.group(1), syms, pc, True)
                    if origin is None:
                        origin = pc
                    elif pc < origin + len(out):
                        raise AsmError("origin moves backwards")
                    else:
                        out += bytes(pc - origin - len(out))
                    continue
                m = re.match(r'^([A-Za-z_]\w*)\s*=\s*(.+)$', s)
                if m:
                    v = evaluate(m.group(2), syms, pc, strict)
                    syms[m.group(1)] = v
                    continue
                m = re.match(r'^([A-Za-z_]\w*):\s*(.*)$', s)
                if m:
                    name = m.group(1)
                    if npass == 1:
                        if name in labels:
                            raise AsmError(f"duplicate label {name}")
                        labels[name] = pc
                        syms[name] = pc
                    elif labels[name] != pc:
                        raise AsmError(f"label {name} moved between passes ({labels[name]:04X} -> {pc:04X})")
                    s = m.group(2).strip()
                    if not s:
                        continue
                low = s.lower()
                if low.startswith('.byte'):
                    data = bytes(evaluate(e, syms, pc, strict) & 255 for e in split_data(s[5:]))
                elif low.startswith('.word'):
                    data = b''.join((evaluate(e, syms, pc, strict) & 0xFFFF).to_bytes(2, 'little')
                                    for e in split_data(s[5:]))
                elif low.startswith('.fill'):
                    cnt, val = split_data(s[5:])
                    data = bytes([evaluate(val, syms, pc, True) & 255]) * evaluate(cnt, syms, pc, True)
                else:
                    data = None
                if data is not None:
                    listing.append((pc, data[:8], s))
                    out += data
                    pc += len(data)
                    continue
                parts = s.split(None, 1)
                mnem = parts[0].upper()
                operand = parts[1].strip() if len(parts) > 1 else ''
                if operand.upper() == 'A' and mnem in IMPLIED_A:
                    stmt = f"{mnem} A"
                elif not operand:
                    stmt = mnem
                else:
                    stmt = mnem + ' ' + rebuild(operand, mnem, syms, pc, strict)
                code = asm.assemble(stmt, pc)
                if not code:
                    raise AsmError(f"cannot assemble '{stmt}'")
                if mnem in BRANCHES and strict:
                    target = evaluate(operand, syms, pc, True)
                    if not -128 <= target - (pc + 2) <= 127:
                        range_errors.append(f"{path}:{n}: branch to {operand} out of range ({target - (pc + 2)})")
                listing.append((pc, bytes(code), s))
                out += bytes(code)
                pc += len(code)
            except AsmError as e:
                sys.exit(f"{path}:{n}: {e}")
    if range_errors:
        sys.exit("\n".join(range_errors))
    return origin, bytes(out), listing, labels, syms


def rebuild(op, mnem, syms, pc, strict):
    if op.startswith('#'):
        v = evaluate(op[1:], syms, pc, strict)
        if not 0 <= v <= 255:
            if strict:
                raise AsmError(f"immediate {v} out of range")
            v &= 255
        return f"#${v:02X}"
    m = re.match(r'^\((.+)\),\s*[Yy]$', op)
    if m:
        return f"(${evaluate(m.group(1), syms, pc, strict):02X}),Y"
    m = re.match(r'^\((.+),\s*[Xx]\)$', op)
    if m:
        return f"(${evaluate(m.group(1), syms, pc, strict):02X},X)"
    m = re.match(r'^\((.+)\)$', op)
    if m:
        return f"(${evaluate(m.group(1), syms, pc, strict):04X})"
    m = re.match(r'^(.+),\s*([XxYy])$', op)
    if m:
        v = evaluate(m.group(1), syms, pc, strict)
        return f"${v:04X},{m.group(2).upper()}"
    v = evaluate(op, syms, pc, strict)
    if mnem in BRANCHES:
        if not strict:
            v = pc
        return f"${v:04X}"
    return f"${v:04X}"


def main():
    src, binpath = sys.argv[1], sys.argv[2]
    origin, code, listing, labels, syms = assemble(src)
    open(binpath, 'wb').write(code)
    if len(sys.argv) > 3:
        with open(sys.argv[3], 'w') as f:
            for pc, data, text in listing:
                f.write(f"{pc:04X}  {' '.join(f'{b:02X}' for b in data):<24}  {text}\n")
    if len(sys.argv) > 4:
        with open(sys.argv[4], 'w') as f:
            for k, v in sorted(syms.items(), key=lambda t: t[1]):
                f.write(f"{k} = ${v:04X}\n")
    print(f"{binpath}: ${origin:04X}-${origin + len(code) - 1:04X}, {len(code)} bytes")


if __name__ == '__main__':
    main()
