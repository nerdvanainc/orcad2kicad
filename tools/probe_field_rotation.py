"""KiCad 심볼 필드(Reference/Value)의 "저장값 -> 그려지는 모습" 실측 도구.

KiCad는 심볼 필드의 각도·정렬을 **심볼 배치(회전/미러)에 상대적으로** 해석해 그린다.
그래서 OrCAD(EDIF)의 절대 회전·정렬을 그대로 저장하면 90/270도 회전된 부품의 텍스트가
같이 세워져 버린다. 올바른 저장값을 계산하려면 (배치, 저장값) -> (그려진 모습) 표가 필요한데,
이 표는 추측하지 않고 실제 KiCad 렌더러(kicad-cli sch export svg)로 측정한다.

동작:
  1. ORIENT_KICAD 의 8가지 배치 x 저장 각도 {0,90} x 저장 hjust {left,right}
     x 저장 vjust {top,bottom} = 64 조합을 격자로 배치한 임시 .kicad_sch 를 만든다.
     각 조합의 식별자는 Reference 텍스트("F00".."F63")로 쓰고, 필드 앵커는 심볼 원점과
     같은 좌표에 둔다(앵커 자체가 배치 변환을 받는지와 무관하게 측정하기 위함).
  2. kicad-cli 로 SVG 를 뽑는다.
  3. SVG 의 <g transform="rotate(A cx cy)"><text x= y= textLength=>ID</text> 를 파싱해
     그려진 각도와 앵커 대비 텍스트 중심의 위치에서 정렬을 역산한다.
  4. 마크다운 표를 표준출력으로 낸다(docs 의 실측표에 붙여 넣는다).

사용:
  python tools/probe_field_rotation.py [--out DIR] [--kicad-cli PATH] [--keep]
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from orcad2kicad.kicad_netlist import find_kicad_cli   # noqa: E402
from orcad2kicad.kicad_writer import FORMAT_VERSION, ORIENT_KICAD   # noqa: E402

FONT = 1.27                 # KiCad 기본 글자 크기(mm)
STEP = 35.0                 # 격자 간격(mm)
ORIGIN = (25.0, 25.0)
ROOT_UUID = '00000000-0000-4000-8000-000000000000'

STORED_ANGLES = (0, 90)
HJUST = ('left', 'right')
VJUST = ('top', 'bottom')


def placements():
    """ORIENT_KICAD 의 (회전, 미러) 8종 — EDIF orientation 이름을 붙여 돌려준다."""
    out = []
    seen = set()
    for edif, rm in ORIENT_KICAD.items():
        if rm in seen:
            continue
        seen.add(rm)
        out.append((edif, rm[0], rm[1]))
    return out


def combos():
    """(식별자, 배치, 저장값) 목록. 식별자는 SVG 에서 되찾을 유일 문자열."""
    out = []
    i = 0
    for edif, rot, mirror in placements():
        for ang in STORED_ANGLES:
            for h in HJUST:
                for v in VJUST:
                    out.append((f'F{i:02d}', edif, rot, mirror, ang, h, v))
                    i += 1
    return out


# ---------- .kicad_sch 생성 ----------

def _sch_text(items):
    lib = [
        '  (lib_symbols',
        '    (symbol "probe:MARK" (pin_numbers hide) (pin_names (offset 0.254) hide)'
        ' (exclude_from_sim no) (in_bom yes) (on_board yes)',
        '      (property "Reference" "F" (at 0 0 0) (effects (font (size 1.27 1.27))))',
        '      (property "Value" "MARK" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
        '      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
        '      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
        '      (symbol "MARK_1_1"',
        '        (rectangle (start -1 -1) (end 1 1) (stroke (width 0) (type default)) (fill (type none)))',
        '        (pin passive line (at -3 0 0) (length 2) (name "A" (effects (font (size 1.27 1.27))))'
        ' (number "1" (effects (font (size 1.27 1.27)))))',
        '      )',
        '    )',
        '  )',
    ]
    body = []
    for n, (ident, edif, rot, mirror, ang, h, v) in enumerate(items):
        x = ORIGIN[0] + (n % 8) * STEP
        y = ORIGIN[1] + (n // 8) * STEP
        m = f' (mirror {mirror})' if mirror else ''
        uid = f'00000000-0000-4000-8000-{n:012d}'
        body += [
            f'  (symbol (lib_id "probe:MARK") (at {x} {y} {rot}){m} (unit 1)'
            ' (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
            f'    (uuid "{uid}")',
            f'    (property "Reference" "{ident}" (at {x} {y} {ang})'
            f' (effects (font (size {FONT} {FONT})) (justify {h} {v})))',
            f'    (property "Value" "MARK" (at {x} {y} 0) (effects (font (size {FONT} {FONT})) hide))',
            f'    (property "Footprint" "" (at {x} {y} 0) (effects (font (size {FONT} {FONT})) hide))',
            f'    (property "Datasheet" "" (at {x} {y} 0) (effects (font (size {FONT} {FONT})) hide))',
            f'    (pin "1" (uuid "10000000-0000-4000-8000-{n:012d}"))',
            f'    (instances (project "probe" (path "/{ROOT_UUID}" (reference "{ident}") (unit 1))))',
            '  )',
        ]
    head = [f'(kicad_sch (version {FORMAT_VERSION}) (generator "orcad2kicad-probe")',
            f'  (uuid "{ROOT_UUID}")',
            '  (paper "A2")',
            '  (title_block (title "field rotation probe"))']
    return '\n'.join(head + lib + body + ['  (sheet_instances (path "/" (page "1")))', ')']) + '\n'


# ---------- SVG 파싱 ----------

_TOK = re.compile(r'<g\b[^>]*>|</g>|<text\b[^>]*>.*?</text>', re.S)
_ROT = re.compile(r'rotate\(\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*\)')
_ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
_BODY = re.compile(r'>([^<]*)</text>', re.S)


def parse_svg_texts(svg):
    """SVG -> {텍스트 내용: (svg 회전각 A, x, y, textLength, font_size)}.

    <g transform="rotate(A cx cy)"> 중첩을 스택으로 추적한다. 같은 내용이 여러 번
    나오면(스트로크 폰트로 다시 그린 사본 등) 처음 것만 쓴다."""
    out = {}
    stack = []
    for m in _TOK.finditer(svg):
        s = m.group(0)
        if s.startswith('</g'):
            if stack:
                stack.pop()
        elif s.startswith('<g'):
            r = _ROT.search(s)
            stack.append(float(r.group(1)) if r else 0.0)
        else:
            body = _BODY.search(s)
            if body is None:
                continue
            label = body.group(1).strip()
            if not label or label in out:
                continue
            a = dict(_ATTR.findall(s))
            if 'x' not in a or 'textLength' not in a:
                continue
            out[label] = (sum(stack), float(a['x']), float(a['y']),
                          float(a['textLength']), float(a.get('font-size', FONT * 4 / 3)))
    return out


def drawn_placement(rec, anchor):
    """(SVG 텍스트 기록, 앵커 좌표) -> (그려진 각도, hjust, vjust).

    SVG 의 <text> 는 text-anchor=middle 이라 x 가 글자열의 가로 중심, y 가 베이스라인이다.
    베이스라인은 글자 세로 중심보다 font-size*0.375 (= KiCad 크기/2) 아래에 있다.
    회전 전 좌표에서 중심을 구한 뒤 회전을 적용해 화면 좌표의 중심을 얻고, 앵커에서 중심으로
    가는 벡터를 텍스트의 진행 방향 u 와 아래 방향 v 로 분해해 정렬을 역산한다."""
    a_deg, x, y, length, fsize = rec
    half_h = fsize * 0.375
    cx, cy = x, y - half_h                      # 회전 전 텍스트 중심(화면 y 아래)
    rx, ry = _ROT_CENTER[0], _ROT_CENTER[1]
    rad = math.radians(a_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    dx, dy = cx - rx, cy - ry
    sx, sy = rx + dx * ca - dy * sa, ry + dx * sa + dy * ca
    u = (ca, sa)                                # 진행 방향(화면)
    v = (-sa, ca)                               # 텍스트의 아래쪽 방향(화면)
    du = (sx - anchor[0]) * u[0] + (sy - anchor[1]) * u[1]
    dv = (sx - anchor[0]) * v[0] + (sy - anchor[1]) * v[1]
    drawn_angle = int(round(-a_deg)) % 360
    h = _classify(du, length / 2, 'left', 'right')
    vj = _classify(dv, half_h, 'top', 'bottom')
    return drawn_angle, h, vj


_ROT_CENTER = (0.0, 0.0)   # drawn_placement 호출 전에 세팅 (회전 중심)


def _classify(d, half, pos, neg):
    """중심이 앵커보다 +방향이면 앵커가 시작(pos), -방향이면 끝(neg), 0이면 center."""
    if abs(d) < half * 0.35:
        return 'center'
    return pos if d > 0 else neg


def measure(svg, items):
    texts = parse_svg_texts(svg)
    rows = []
    global _ROT_CENTER
    for n, (ident, edif, rot, mirror, ang, h, v) in enumerate(items):
        rec = texts.get(ident)
        anchor = (ORIGIN[0] + (n % 8) * STEP, ORIGIN[1] + (n // 8) * STEP)
        if rec is None:
            rows.append((ident, edif, rot, mirror, ang, h, v, None, None, None))
            continue
        # 회전 중심은 SVG transform 에 들어 있다. parse_svg_texts 는 각도만 모으므로
        # 회전 중심은 텍스트 중심과 같다는 KiCad 의 출력 규약을 쓴다(회전 중심 = 글자열 중심).
        a_deg, x, y, length, fsize = rec
        _ROT_CENTER = (x, y - fsize * 0.375)
        rows.append((ident, edif, rot, mirror, ang, h, v) + drawn_placement(rec, anchor))
    return rows


def markdown(rows):
    out = ['| id | EDIF orientation | kicad rot | mirror | stored angle | stored hjust | stored vjust '
           '| drawn angle | drawn hjust | drawn vjust |',
           '|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        ident, edif, rot, mirror, ang, h, v, da, dh, dv = r
        out.append(f'| {ident} | {edif} | {rot} | {mirror or "-"} | {ang} | {h} | {v} '
                   f'| {da if da is not None else "?"} | {dh or "?"} | {dv or "?"} |')
    return '\n'.join(out)


def python_table(rows):
    """kicad_writer 에 넣을 dict 리터럴."""
    out = ['MEASURED_FIELD_DRAW = {']
    for ident, edif, rot, mirror, ang, h, v, da, dh, dv in rows:
        out.append(f'    ({rot}, {mirror!r}, {ang}, {h!r}, {v!r}): ({da}, {dh!r}, {dv!r}),')
    out.append('}')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='작업 디렉터리 (기본: 임시 디렉터리)')
    ap.add_argument('--kicad-cli')
    ap.add_argument('--python-table', action='store_true', help='마크다운 대신 python dict 로 출력')
    args = ap.parse_args()

    cli = find_kicad_cli(args.kicad_cli)
    if not cli:
        print('ERROR: kicad-cli not found', file=sys.stderr)
        return 2
    work = args.out or tempfile.mkdtemp(prefix='probe_field_')
    os.makedirs(work, exist_ok=True)
    items = combos()
    sch = os.path.join(work, 'probe.kicad_sch')
    with open(sch, 'w', encoding='utf-8') as f:
        f.write(_sch_text(items))
    svg_dir = os.path.join(work, 'svg')
    os.makedirs(svg_dir, exist_ok=True)
    r = subprocess.run([cli, 'sch', 'export', 'svg', '--no-background-color', '-o', svg_dir, sch],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f'ERROR: kicad-cli sch export svg failed: {r.stdout}\n{r.stderr}', file=sys.stderr)
        return 2
    svgs = [os.path.join(svg_dir, n) for n in sorted(os.listdir(svg_dir)) if n.endswith('.svg')]
    if not svgs:
        print('ERROR: no svg produced', file=sys.stderr)
        return 2
    with open(svgs[0], encoding='utf-8') as f:
        svg = f.read()
    rows = measure(svg, items)
    missing = [r_[0] for r_ in rows if r_[7] is None]
    if missing:
        print(f'WARNING: not found in svg: {",".join(missing)}', file=sys.stderr)
    print(python_table(rows) if args.python_table else markdown(rows))
    print(f'# work dir: {work}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
