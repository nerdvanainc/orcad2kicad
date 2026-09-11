"""나이틀리 OrCAD `.DSN` 임포터 출력의 결함 세 가지를 후처리로 고친다.

배경: docs/2026-09-11/[이슈]_[01]_DSN_임포트_결과_교차점_끊김표식_타이틀블록_중복_분석.md 1·2·7절.

1. **hop 갭 병합**(`merge_hop_gaps`) — 임포터는 지나가기만 하는 교차점마다 배선을
   ±0.254 mm 씩 끊고, 그 틈(0.508 mm)을 그래픽 `polyline` 으로 메워 눈에는 이어진 것처럼
   보이게 한다. KiCad 는 끊긴 배선 끝을 "끊긴 끝"으로 보고 작은 사각 표식을 그리고, ERC 는
   `unconnected_wire_endpoint` 를 잡는다. 전기적으로는 조각마다 얹힌 투명 `global_label` 이
   넷을 잇고 있어 문제가 없지만(같은 분석 문서 1절 참고), 표식·ERC 잡음은 남는다. 이 함수는
   같은 축에서 마주보는 두 `wire` 조각과 그 틈을 정확히 메우는 2점 `polyline` 을 찾아 하나의
   `wire` 로 합치고 `polyline` 을 지운다 — 문자열 단위 최소 수정(`kicad_sch_reader.iter_blocks`
   와 같은 방식)이라 그 밖의 바이트(라벨·심볼·uuid·서식)는 전혀 건드리지 않는다.
   투명 전역 라벨은 그대로 둔다(넷 이름의 출처이므로).

2. **빈 도면 양식 적용**(`blank_worksheet_text`/`apply_blank_worksheet`) — 임포터는 OrCAD
   페이지 테두리·타이틀블록을 그래픽(`polyline`+`text`)으로 그대로 옮기는데, `.kicad_pro`
   의 `schematic.page_layout_descr_file` 이 비어 있어 KiCad 가 기본 도면 양식을 같은 자리에
   또 그려 겹쳐 보인다(2절). 테두리·타이틀블록이 없는 최소 `.kicad_wks` 를 프로젝트 폴더에
   써 주고 그 값을 가리키게 하면(안 A) OrCAD 그래픽만 남아 겹침이 사라진다.

3. **라벨 끝점 정렬**(`snap_labels_to_wire_ends`) — 임포터는 지역 `label`(넷 별칭)을 배선
   중간의 아무 지점에나 얹어 둔다. 전기적으로는 배선 위 어디든 이어지지만, KiCad 는 배선의
   자유단(다른 배선·핀·정션·라벨 등이 없는 끝)을 "끊긴 끝"으로 보고 작은 사각 표식을 그리고
   ERC 가 `unconnected_wire_endpoint`를 잡는다(7절). 이 함수는 그런 자유단을 가진 수평/수직
   배선 위의 라벨을 찾아 그 자유단으로 옮기고(KiCad 사용자가 직접 그렸을 법한 모양), 배선을
   따라 글자가 거꾸로 흐르도록 회전(`at`의 각도)과 정렬(`justify`)을 맞춘다. 자유단 판정에는
   배선 끝점뿐 아니라 정션·no-connect·버스 진입점·다른 라벨류 앵커·계층 시트 핀·**심볼 핀
   위치**(내장 `lib_symbols`에서 계산)까지 전부 점유 좌표로 쓴다. `global_label`/
   `hierarchical_label`은 절대 건드리지 않는다(지역 `label`만 대상).

`cleanup_project` 는 위 세 가지를 프로젝트 하나에 적용하는 오케스트레이션이다.
`pipeline.run_kicad_project` 가 `kicad_stable.restructure_project` 뒤, ERC/[3]/PDF 전에
호출한다(`PipelineOptions.import_cleanup`, 기본 켜짐). 셋 다 파일 포맷 버전과
무관한 문자열 수준 수정이라(`kicad_stable`이 건드리는 `(version …)`/`generator_version`/
경로 치환과 겹치지 않는다) `--nightly-format`(재구성 끔) 경로에도 그대로 적용된다.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter

from .kicad_sch_reader import iter_blocks
from .kicad_writer import fnum

__all__ = ['merge_hop_gaps', 'blank_worksheet_text', 'apply_blank_worksheet', 'cleanup_project',
           'snap_labels_to_wire_ends']


_XY_RE = re.compile(r'\(xy\s+(-?[0-9]*\.?[0-9]+)\s+(-?[0-9]*\.?[0-9]+)\)')
_TOL = 1e-6
_MAX_GAP_MM = 0.6
_LABEL_TOL = 1e-3         # 라벨 스냅 비교 허용 오차(mm) — 좌표는 문자열 리터럴, 핀 위치는 계산값
_AT_XYR_RE = re.compile(r'\(at\s+(-?[0-9]*\.?[0-9]+)\s+(-?[0-9]*\.?[0-9]+)'
                        r'(?:\s+(-?[0-9]*\.?[0-9]+))?\s*\)')
_SIZE_XY_RE = re.compile(r'\(size\s+(-?[0-9]*\.?[0-9]+)\s+(-?[0-9]*\.?[0-9]+)\s*\)')
_MIRROR_RE = re.compile(r'\(mirror\s+([xy])\s*\)')
_UNIT_RE = re.compile(r'\(unit\s+(-?[0-9]+)\s*\)')
_LIBID_RE = re.compile(r'\(lib_id\s+"((?:[^"\\]|\\.)*)"')
_SYM_NAME_RE = re.compile(r'\(symbol\s+"((?:[^"\\]|\\.)*)"')


def _points(block_text):
    """블록 텍스트(wire/polyline) 안의 모든 `(xy x y)` 좌표 목록."""
    return [(float(a), float(b)) for a, b in _XY_RE.findall(block_text)]


def _axis_of(p1, p2):
    """두 점이 이루는 배선의 축: 세로('v', x 고정) | 가로('h', y 고정) | None(대각선)."""
    if abs(p1[0] - p2[0]) < _TOL:
        return 'v', p1[0]
    if abs(p1[1] - p2[1]) < _TOL:
        return 'h', p1[1]
    return None, None


def _line_span(text, start, end):
    """블록 (start,end)를 감싼 줄 전체 범위로 넓힌다(앞 들여쓰기 + 뒤 개행 포함해서 지우면
    빈 줄이 남지 않는다). 앞뒤가 공백이 아니면(다른 내용과 줄을 공유하면) 원래 범위만 돌려준다
    (안전한 쪽으로만 넓힌다 — 확신 없으면 최소 범위)."""
    nl_before = text.rfind('\n', 0, start)
    line_start = 0 if nl_before < 0 else nl_before + 1
    if text[line_start:start].strip(' \t') != '':
        line_start = start
    line_end = end
    if text[end:end + 2] == '\r\n':
        line_end = end + 2
    elif end < len(text) and text[end] == '\n':
        line_end = end + 1
    return line_start, line_end


def _replace_two_xy(block_text, new_points):
    """블록 안의 첫 두 `(xy ..)` 만 새 좌표로 바꾼다. 그 외 바이트(들여쓰기·stroke·uuid)는 그대로."""
    matches = list(_XY_RE.finditer(block_text))
    out = []
    pos = 0
    for m, (nx, ny) in zip(matches, new_points):
        out.append(block_text[pos:m.start()])
        out.append(f'(xy {fnum(nx)} {fnum(ny)})')
        pos = m.end()
    out.append(block_text[pos:])
    return ''.join(out)


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        """루트가 달랐으면 합치고 True, 이미 같은 그룹이면 아무 것도 안 하고 False."""
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.parent[ri] = rj
        return True


def merge_hop_gaps(sch_text: str) -> tuple[str, int]:
    """`sch_text`(.kicad_sch 전체 텍스트) 안의 hop 갭을 병합한다.

    반환값: (새 텍스트, 병합한 갭 개수). 최상위(`iter_blocks(..., depth=1)`) `wire`/`polyline`
    블록만 살펴보고 그 밖의 바이트는 전혀 건드리지 않는다. 여러 갭이 한 직선을 따라 이어져
    있어도(체인) union-find 로 한 번에 처리되므로 한 번의 호출로 전부 하나의 `wire` 로 합쳐진다
    (반복 호출이 필요 없다 — 재호출해도 더 합칠 것이 없으면 count=0 로 멱등이다)."""
    wires = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        block = sch_text[s:e]
        pts = _points(block)
        if len(pts) != 2:
            wires.append(None)
            continue
        axis, coord = _axis_of(pts[0], pts[1])
        wires.append({'start': s, 'end': e, 'p1': pts[0], 'p2': pts[1], 'axis': axis, 'coord': coord})

    polylines = []
    for _, s, e in iter_blocks(sch_text, 'polyline', depth=1):
        block = sch_text[s:e]
        pts = _points(block)
        if len(pts) != 2:
            continue
        axis, coord = _axis_of(pts[0], pts[1])
        if axis is None:
            continue
        gap = abs(pts[0][1] - pts[1][1]) if axis == 'v' else abs(pts[0][0] - pts[1][0])
        if gap > _MAX_GAP_MM + 1e-9:
            continue
        polylines.append({'start': s, 'end': e, 'p1': pts[0], 'p2': pts[1], 'axis': axis, 'coord': coord})

    def endpoint_match(w, pt):
        return ((abs(w['p1'][0] - pt[0]) < _TOL and abs(w['p1'][1] - pt[1]) < _TOL) or
                (abs(w['p2'][0] - pt[0]) < _TOL and abs(w['p2'][1] - pt[1]) < _TOL))

    def candidates(pt, axis, coord):
        return [i for i, w in enumerate(wires)
                if w is not None and w['axis'] == axis and abs(w['coord'] - coord) < _TOL
                and endpoint_match(w, pt)]

    uf = _UnionFind(len(wires))
    consumed = []       # 실제로 병합에 쓰인 polyline 인덱스
    for pidx, poly in enumerate(polylines):
        c1 = candidates(poly['p1'], poly['axis'], poly['coord'])
        c2 = candidates(poly['p2'], poly['axis'], poly['coord'])
        overlap = set(c1) & set(c2)
        c1 = [i for i in c1 if i not in overlap]
        c2 = [i for i in c2 if i not in overlap]
        if len(c1) == 1 and len(c2) == 1 and c1[0] != c2[0]:
            if uf.union(c1[0], c2[0]):
                consumed.append(pidx)

    groups = {}
    for i, w in enumerate(wires):
        if w is None:
            continue
        groups.setdefault(uf.find(i), []).append(i)

    edits = []           # (시작, 끝, 대체 텍스트) — 위치순으로 합쳐 적용
    gaps_merged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        axis = wires[members[0]]['axis']
        coord = wires[members[0]]['coord']
        vals = []
        for m in members:
            w = wires[m]
            vals.append(w['p1'][1] if axis == 'v' else w['p1'][0])
            vals.append(w['p2'][1] if axis == 'v' else w['p2'][0])
        lo, hi = min(vals), max(vals)
        rep = min(members, key=lambda m: wires[m]['start'])
        new_p1 = (coord, lo) if axis == 'v' else (lo, coord)
        new_p2 = (coord, hi) if axis == 'v' else (hi, coord)
        rep_w = wires[rep]
        new_block = _replace_two_xy(sch_text[rep_w['start']:rep_w['end']], [new_p1, new_p2])
        edits.append((rep_w['start'], rep_w['end'], new_block))
        for m in members:
            if m == rep:
                continue
            w = wires[m]
            ls, le = _line_span(sch_text, w['start'], w['end'])
            edits.append((ls, le, ''))
        gaps_merged += len(members) - 1

    for pidx in consumed:
        poly = polylines[pidx]
        ls, le = _line_span(sch_text, poly['start'], poly['end'])
        edits.append((ls, le, ''))

    if not edits:
        return sch_text, 0

    edits.sort(key=lambda t: t[0])
    out = []
    pos = 0
    for a, b, rep in edits:
        out.append(sch_text[pos:a])
        out.append(rep)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), gaps_merged


def blank_worksheet_text() -> str:
    """테두리·타이틀블록이 없는 최소 `.kicad_wks`(KiCad 가 그 자리에 기본 양식을 덧그리지
    않도록). 사용자는 KiCad Page Settings 에서 언제든 다른 양식으로 바꿀 수 있다."""
    return (
        '(kicad_wks (version 20231118) (generator "orcad2kicad")\n'
        '  (setup (textsize 1.5 1.5) (linewidth 0.15) (textlinewidth 0.15)\n'
        '    (left_margin 10) (right_margin 10) (top_margin 10) (bottom_margin 10))\n'
        ')\n'
    )


def apply_blank_worksheet(pro_path) -> str:
    """`<프로젝트 폴더>/blank.kicad_wks` 를 쓰고 `.kicad_pro` 의
    `schematic.page_layout_descr_file` 이 그 파일을 가리키게 한다(다른 키·서식은 그대로).

    반환값: 쓴 `.kicad_wks` 경로. `${KIPRJMOD}` 상대 경로는 kicad-cli 10.0.6 로 실측
    확인됨(sch export pdf/erc 모두 정상) — docs/2026-09-11/[이슈]_[01] 6절 참고."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    wks_path = os.path.join(folder, 'blank.kicad_wks')
    with open(wks_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(blank_worksheet_text())
    with open(pro_path, encoding='utf-8') as fh:
        pro = json.load(fh)
    pro.setdefault('schematic', {})['page_layout_descr_file'] = '${KIPRJMOD}/blank.kicad_wks'
    with open(pro_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(pro, fh, indent=2)
        fh.write('\n')
    return wks_path


# --------------------------------------------------------------------------------------
# 라벨 끝점 정렬(snap_labels_to_wire_ends) — 아래는 그 재료: 최소한의 규격 밖 S-식 파서.
# --------------------------------------------------------------------------------------

def _unq(s):
    return s.replace('\\"', '"').replace('\\\\', '\\')


def _child_span(text, name):
    """`text` 자신이 괄호를 포함한 블록 전체일 때, 그 직계 자식 중 첫 `name` 블록의
    (상대시작, 상대끝)을 돌려준다(없으면 None). `text[0]`가 그 블록 자신의 '(' 이어야 한다
    (`iter_blocks`가 돌려주는 부분 문자열은 전부 이 조건을 만족한다)."""
    for _, s, e in iter_blocks(text, name, depth=1):
        return s, e
    return None


def _at_xyr(text, span):
    """`span`(상대 (시작,끝))이 가리키는 `(at x y [r])` 조각에서 (x, y, r) — r 없으면 0.0."""
    if span is None:
        return None
    m = _AT_XYR_RE.match(text[span[0]:span[1]])
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)), float(m.group(3)) if m.group(3) else 0.0)


def _rot90(x, y, steps):
    """(x, y)를 y-업 좌표계에서 반시계 90도씩 `steps`번 회전: (x,y) -> (-y,x)."""
    steps %= 4
    for _ in range(steps):
        x, y = -y, x
    return x, y


def _lib_pin_positions(sch_text):
    """`lib_symbols` 안의 핀 좌표(라이브러리 공간, 심볼 배치 전)를 모은다.

    반환값: {기본이름(접두사 없이): {유닛번호: [(px, py), ...]}}. 유닛 서브심볼 이름은
    `<기본이름>_<유닛>_<스타일>` 형태이고, 유닛 0은 모든 유닛에 공통인 핀(전원 핀 등)이다."""
    out = {}
    for _, ls, le in iter_blocks(sch_text, 'lib_symbols', depth=1):
        for _, s, e in iter_blocks(sch_text, 'symbol', depth=0, start=ls + 1, end=le):
            top = sch_text[s:e]
            m = _SYM_NAME_RE.match(top)
            if not m:
                continue
            full = _unq(m.group(1))
            base = full.split(':', 1)[1] if ':' in full else full
            units = out.setdefault(base, {})
            prefix = base + '_'
            for _, ss, se in iter_blocks(sch_text, 'symbol', depth=0, start=s + 1, end=e):
                sub = sch_text[ss:se]
                sm = _SYM_NAME_RE.match(sub)
                if not sm:
                    continue
                subname = _unq(sm.group(1))
                if not subname.startswith(prefix):
                    continue
                rest = subname[len(prefix):]
                parts = rest.rsplit('_', 1)
                if len(parts) != 2 or not parts[0].lstrip('-').isdigit():
                    continue
                unit = int(parts[0])
                pins = units.setdefault(unit, [])
                for _, ps, pe in iter_blocks(sch_text, 'pin', depth=0, start=ss + 1, end=se):
                    pin_block = sch_text[ps:pe]
                    xyr = _at_xyr(pin_block, _child_span(pin_block, 'at'))
                    if xyr:
                        pins.append((xyr[0], xyr[1]))
    return out


def _symbol_instances(sch_text):
    """최상위(시트 직계) `symbol` 인스턴스의 (기본 lib_id 이름, X, Y, R, mirror('x'/'y'/None), unit)."""
    out = []
    for _, s, e in iter_blocks(sch_text, 'symbol', depth=1):
        block = sch_text[s:e]
        libid_span = _child_span(block, 'lib_id')
        at_span = _child_span(block, 'at')
        if libid_span is None or at_span is None:
            continue
        m = _LIBID_RE.match(block[libid_span[0]:libid_span[1]])
        if not m:
            continue
        full = _unq(m.group(1))
        base = full.split(':', 1)[1] if ':' in full else full
        xyr = _at_xyr(block, at_span)
        if xyr is None:
            continue
        X, Y, R = xyr
        mdir = None
        mspan = _child_span(block, 'mirror')
        if mspan:
            mm = _MIRROR_RE.match(block[mspan[0]:mspan[1]])
            if mm:
                mdir = mm.group(1)
        unit = 1
        uspan = _child_span(block, 'unit')
        if uspan:
            um = _UNIT_RE.match(block[uspan[0]:uspan[1]])
            if um:
                unit = int(um.group(1))
        out.append((base, X, Y, R, mdir, unit))
    return out


def _symbol_pin_points(sch_text):
    """시트에 배치된 모든 심볼 인스턴스의 절대(시트 좌표) 핀 위치 목록.

    변환 규칙(실측으로 검증됨 — docs/2026-09-11/[이슈]_[01] 7절): 라이브러리 좌표(y-업)를
    반시계로 R도 회전한 뒤(`_rot90`) mirror(x: y부호반전, y: x부호반전)를 적용하고,
    시트 좌표(y-다운)로 변환한다: sheet_x = X + x', sheet_y = Y - y'."""
    lib_pins = _lib_pin_positions(sch_text)
    pts = []
    for base, X, Y, R, mdir, unit in _symbol_instances(sch_text):
        units = lib_pins.get(base)
        if not units:
            continue
        pins = list(units.get(0, ())) + list(units.get(unit, ()))
        steps = int(round(R / 90.0)) % 4
        for px, py in pins:
            x, y = _rot90(px, py, steps)
            if mdir == 'x':
                y = -y
            elif mdir == 'y':
                x = -x
            pts.append((X + x, Y - y))
    return pts


def _collect_occupied(sch_text, wire_points):
    """점유 좌표 멀티셋(반올림 좌표 -> 등장 횟수). `wire_points`는 미리 모아 둔 모든 `wire`
    블록의 모든 점(축 정렬 여부 무관 — 대각선 배선의 끝점도 다른 배선 판정에는 점유로 쓴다)."""
    counts = Counter()

    def add(x, y):
        counts[(round(x, 3), round(y, 3))] += 1

    for x, y in wire_points:
        add(x, y)
    for name in ('junction', 'no_connect'):
        for _, s, e in iter_blocks(sch_text, name, depth=1):
            block = sch_text[s:e]
            xyr = _at_xyr(block, _child_span(block, 'at'))
            if xyr:
                add(xyr[0], xyr[1])
    for _, s, e in iter_blocks(sch_text, 'bus_entry', depth=1):
        block = sch_text[s:e]
        at_span = _child_span(block, 'at')
        size_span = _child_span(block, 'size')
        xyr = _at_xyr(block, at_span)
        if xyr is None or size_span is None:
            continue
        sm = _SIZE_XY_RE.match(block[size_span[0]:size_span[1]])
        if not sm:
            continue
        dx, dy = float(sm.group(1)), float(sm.group(2))
        add(xyr[0], xyr[1])
        add(xyr[0] + dx, xyr[1] + dy)
    for name in ('label', 'global_label', 'hierarchical_label'):
        for _, s, e in iter_blocks(sch_text, name, depth=1):
            block = sch_text[s:e]
            xyr = _at_xyr(block, _child_span(block, 'at'))
            if xyr:
                add(xyr[0], xyr[1])
    for _, s, e in iter_blocks(sch_text, 'sheet', depth=1):
        block = sch_text[s:e]
        for _, ps, pe in iter_blocks(block, 'pin', depth=1):
            pin_block = block[ps:pe]
            xyr = _at_xyr(pin_block, _child_span(pin_block, 'at'))
            if xyr:
                add(xyr[0], xyr[1])
    for x, y in _symbol_pin_points(sch_text):
        add(x, y)
    return counts


def _justify_edit(block, eff_span, justify_value):
    """`block`(라벨 블록 전체) 안의 `effects` 블록 span 을 받아 `justify` 를
    `justify_value`("right bottom" 등)로 바꾸는 (상대시작, 상대끝, 새 텍스트)를 돌려준다
    (없으면 새로 삽입). `eff_span` 이 None 이면(effects 가 없는 비정상 라벨) None."""
    if eff_span is None:
        return None
    es, ee = eff_span
    eff_text = block[es:ee]
    j = _child_span(eff_text, 'justify')
    new_just = f'(justify {justify_value})'
    if j is not None:
        js, je = j
        return es + js, es + je, new_just
    # justify 가 없으면 새로 넣는다: effects 의 닫는 ')' 바로 앞, 형제와 같은 들여쓰기 + 한 단.
    nl = '\r\n' if '\r\n' in eff_text else '\n'
    close_idx = len(eff_text) - 1               # eff_text[-1] == ')'
    line_start = eff_text.rfind('\n', 0, close_idx)
    indent = ''
    if line_start >= 0:
        indent = re.match(r'[ \t]*', eff_text[line_start + 1:]).group(0)
    insert_text = f'{nl}{indent}\t{new_just}'
    return es + close_idx, es + close_idx, insert_text


def snap_labels_to_wire_ends(sch_text: str) -> tuple[str, int]:
    """`sch_text` 안의 지역 `label`(넷 별칭) 중, 수평/수직 배선 중간에 앵커가 있고 그 배선의
    한쪽 끝만 비어 있는(다른 배선·핀·정션·라벨 등이 없는) 것을 그 자유단으로 옮긴다.

    반환값: (새 텍스트, 옮긴 라벨 개수). `global_label`/`hierarchical_label`은 절대 건드리지
    않는다(`iter_blocks`의 이름 매칭은 전체 토큰 일치라 `global_label`이 `label`에 걸리지
    않는다). 라벨을 옮기면 그 새 위치는 이후 라벨 판정에서 즉시 점유로 취급한다(같은 배선에
    라벨이 두 개면 먼저(문서 순서) 처리되는 것만 옮겨진다)."""
    all_wire_points = []
    candidate_wires = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        all_wire_points.extend(pts)
        if len(pts) == 2:
            axis, coord = _axis_of(pts[0], pts[1])
            if axis is not None:
                candidate_wires.append({'p1': pts[0], 'p2': pts[1], 'axis': axis, 'coord': coord})

    counts = _collect_occupied(sch_text, all_wire_points)

    def key(pt):
        return (round(pt[0], 3), round(pt[1], 3))

    def is_free(pt):
        return counts[key(pt)] <= 1

    def on_segment(anchor, w):
        ax, ay = anchor
        if w['axis'] == 'h':
            if abs(ay - w['coord']) > _LABEL_TOL:
                return False
            lo, hi = sorted((w['p1'][0], w['p2'][0]))
            return lo + _LABEL_TOL < ax < hi - _LABEL_TOL
        if abs(ax - w['coord']) > _LABEL_TOL:
            return False
        lo, hi = sorted((w['p1'][1], w['p2'][1]))
        return lo + _LABEL_TOL < ay < hi - _LABEL_TOL

    labels = []
    for _, s, e in iter_blocks(sch_text, 'label', depth=1):
        block = sch_text[s:e]
        at_span = _child_span(block, 'at')
        xyr = _at_xyr(block, at_span)
        if xyr is None:
            continue
        labels.append({'start': s, 'block': block, 'at_span': at_span, 'anchor': (xyr[0], xyr[1])})

    edits = []
    moved = 0
    for lab in labels:
        match_w = None
        for w in candidate_wires:
            if on_segment(lab['anchor'], w):
                match_w = w
                break
        if match_w is None:
            continue
        free1, free2 = is_free(match_w['p1']), is_free(match_w['p2'])
        if free1 == free2:            # 둘 다 자유롭거나 둘 다 막혀 있으면 손대지 않는다
            continue
        target = match_w['p1'] if free1 else match_w['p2']
        other = match_w['p2'] if free1 else match_w['p1']
        if match_w['axis'] == 'h':
            rot, justify = (180, 'right bottom') if target[0] > other[0] else (0, 'left bottom')
        else:
            rot, justify = (90, 'left bottom') if target[1] < other[1] else (270, 'right bottom')

        block, ats = lab['block'], lab['at_span']
        new_at = f'(at {fnum(target[0])} {fnum(target[1])} {rot})'
        edits.append((lab['start'] + ats[0], lab['start'] + ats[1], new_at))

        je = _justify_edit(block, _child_span(block, 'effects'), justify)
        if je is not None:
            edits.append((lab['start'] + je[0], lab['start'] + je[1], je[2]))

        counts[key(target)] += 1
        moved += 1

    if not edits:
        return sch_text, 0

    edits.sort(key=lambda t: t[0])
    out = []
    pos = 0
    for a, b, rep in edits:
        out.append(sch_text[pos:a])
        out.append(rep)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), moved


def cleanup_project(pro_path, merge_hops=True, blank_worksheet=True) -> dict:
    """`.kicad_pro` 프로젝트 폴더의 모든 `.kicad_sch`(루트 + 하위 시트)에 `merge_hop_gaps` 를
    적용해 제자리에 다시 쓰고(배선이 합쳐진 뒤에 `snap_labels_to_wire_ends` 를 이어서 적용),
    (선택) 빈 도면 양식을 적용한다.

    반환값: `{'sheets': 찾은 시트 파일 수, 'gaps_merged': 병합한 갭 총합, 'labels_moved': 옮긴
    라벨 총합, 'worksheet': 쓴 .kicad_wks 경로 또는 None}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    sheet_paths = sorted(glob.glob(os.path.join(folder, '*.kicad_sch')))
    gaps_merged = 0
    labels_moved = 0
    if merge_hops:
        for path in sheet_paths:
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            text, n = merge_hop_gaps(text)
            gaps_merged += n
            text, n2 = snap_labels_to_wire_ends(text)
            labels_moved += n2
            if n or n2:
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(text)
    wks_path = apply_blank_worksheet(pro_path) if blank_worksheet else None
    return {'sheets': len(sheet_paths), 'gaps_merged': gaps_merged, 'labels_moved': labels_moved,
            'worksheet': wks_path}
