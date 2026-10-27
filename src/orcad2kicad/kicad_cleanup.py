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

4. **배선 폭 기본값화**(`normalize_wire_widths`) — 임포터는 모든 `wire` 에 `(stroke (width
   0.254))`(10 mil)를 박아 두어, 사용자가 KiCad 에서 새로 그린 배선(`width 0` = 회로도 기본
   폭, 6 mil)보다 굵게 보인다(2026-10-27 사용자 보고). 최상위 `wire`/`bus` 의 stroke 폭만 0
   으로 바꿔 기본 폭을 따르게 한다. 그래픽 `polyline`·심볼 그림은 건드리지 않는다.

5. **오프페이지 커넥터 → 전역 라벨**(`offpage_to_global_labels`) — 임포터는 OrCAD 오프페이지
   커넥터를 빨간 갈매기(>>) `polyline` 4조각 + 빨간 `text` + 투명 `global_label` 로 옮겨,
   넷은 전역인데 보이는 것은 그림·글자뿐이다(2026-10-27 사용자 보고). 셋이 모두 맞을 때만
   라벨을 보이게 하고 그림·글자를 지운다.

`cleanup_project` 는 위 다섯 가지를 프로젝트 하나에 적용하는 오케스트레이션이다.
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

from .kicad_fonts import DEFAULT_KOREAN_FONT, apply_korean_font
from .kicad_sch_reader import iter_blocks
from .kicad_writer import fnum

__all__ = ['merge_hop_gaps', 'blank_worksheet_text', 'apply_blank_worksheet', 'cleanup_project',
           'snap_labels_to_wire_ends', 'normalize_wire_widths', 'offpage_to_global_labels',
           'remove_redundant_transparent_labels', 'dedupe_project_guarded', 'fix_cp949_mojibake',
           'localize_importer_globals', 'localize_project_guarded', 'shrink_transparent_labels']


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


_GLABEL_NAME_RE = re.compile(r'\(global_label\s+"((?:[^"\\]|\\.)*)"')
_TEXT_NAME_RE = re.compile(r'\(text\s+"((?:[^"\\]|\\.)*)"')
_TRANSPARENT_COLOR_RE = re.compile(r'\(color\s+[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+0\s*\)')
_CHEVRON_STEP = 1.27     # 갈매기 한 조각의 가로·세로 폭(mm) — 실측(나이틀리 임포터) 값
_TEXT_REACH_MM = 40.0    # 커넥터 글자를 찾는 범위(앵커에서 커넥터 방향으로)


def offpage_to_global_labels(sch_text: str) -> tuple[str, int]:
    """임포터가 그림으로 옮긴 OrCAD 오프페이지 커넥터를 KiCad 전역 라벨로 바꾼다.

    임포터 출력: 배선 끝(앵커)에 **투명** `global_label`(글자색 알파 0) + 앵커에서 커넥터 방향으로
    겹친 갈매기(>>) 두 개를 이루는 대각선 `polyline` 4조각(각 1.27 mm) + 같은 이름의 `text`.
    전기적으로는 이미 전역 넷이지만 보이는 것은 그림과 글자뿐이라, 클릭해도 라벨이 아닌 글자가
    잡힌다. 이 세 가지가 **모두** 맞는 경우에만(확신 없으면 그대로 둔다) 라벨의 투명 색 지정을
    지워 보이게 하고 갈매기·글자를 지운다. 배선 중간에 얹힌 투명 라벨(hop 조각용)은 갈매기가
    없으므로 건드리지 않는다.

    방향: 라벨 각도 0 → +x, 180 → -x, 90 → -y(위), 270 → +y. 갈매기 조각은 **중점**이 앵커에서
    그 방향으로 (0, 2.54) 안, 옆으로 1.27 미만이어야 그 커넥터 것이다(2.54 mm 간격으로 붙은 이웃
    커넥터와 끝점을 공유해도 중점으로 갈린다). 반환값: (새 텍스트, 바꾼 커넥터 수). 멱등."""
    polys = []
    for _, s, e in iter_blocks(sch_text, 'polyline', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) != 2:
            continue
        (x1, y1), (x2, y2) = pts
        if abs(abs(x2 - x1) - _CHEVRON_STEP) > _LABEL_TOL or abs(abs(y2 - y1) - _CHEVRON_STEP) > _LABEL_TOL:
            continue
        polys.append({'mid': ((x1 + x2) / 2, (y1 + y2) / 2), 'start': s, 'end': e, 'used': False})
    texts = []
    for _, s, e in iter_blocks(sch_text, 'text', depth=1):
        m = _TEXT_NAME_RE.match(sch_text, s)
        if m:
            texts.append({'name': m.group(1), 'pos': None, 'start': s, 'end': e, 'used': False})
    def block_at(s, e):
        block = sch_text[s:e]
        return _at_xyr(block, _child_span(block, 'at'))

    for t in texts:
        t['pos'] = (block_at(t['start'], t['end']) or (None, None))[:2]
    texts = [t for t in texts if t['pos'][0] is not None]
    dirs = {0: (1, 0), 180: (-1, 0), 90: (0, -1), 270: (0, 1)}
    edits = []
    converted = 0
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        cm = _TRANSPARENT_COLOR_RE.search(block)
        nm = _GLABEL_NAME_RE.match(block)
        at = block_at(s, e)
        if not (cm and nm and at):
            continue
        d = dirs.get(int(round(at[2])) % 360)
        if d is None:
            continue
        ax, ay = at[0], at[1]

        def along_perp(p):
            vx, vy = p[0] - ax, p[1] - ay
            return vx * d[0] + vy * d[1], abs(vx * d[1] - vy * d[0])

        mine = [p for p in polys if not p['used']
                and 0 < along_perp(p['mid'])[0] < 2 * _CHEVRON_STEP
                and along_perp(p['mid'])[1] < _CHEVRON_STEP - _LABEL_TOL]
        if len(mine) != 4:
            continue
        cands = []
        for t in texts:
            if t['used'] or t['name'] != nm.group(1):
                continue
            a, p = along_perp(t['pos'])
            if 0 < a <= _TEXT_REACH_MM and p < 2 * _CHEVRON_STEP:
                cands.append((a, t))
        if not cands:
            continue
        text = min(cands, key=lambda c: c[0])[1]
        text['used'] = True
        for p in mine:
            p['used'] = True
            edits.append(_line_span(sch_text, p['start'], p['end']) + ('',))
        edits.append(_line_span(sch_text, text['start'], text['end']) + ('',))
        cs, ce = _line_span(sch_text, s + cm.start(), s + cm.end())
        edits.append((cs, ce, ''))
        converted += 1
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
    return ''.join(out), converted


_VALUE_PROP_RE = re.compile(r'\(property\s+"Value"\s+"((?:[^"\\]|\\.)*)"')


def _on_segment(p, a, b, tol=_LABEL_TOL):
    """점 p 가 선분 a-b 위(끝점 포함)에 있는가."""
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if not (min(x1, x2) - tol <= x <= max(x1, x2) + tol and min(y1, y2) - tol <= y <= max(y1, y2) + tol):
        return False
    length = abs(x2 - x1) + abs(y2 - y1)
    return abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) <= tol * max(1.0, length)


def _power_pin_points(sch_text):
    """전원 심볼(`lib_symbols` 정의에 `(power …)` 표식) 인스턴스의 [(Value, 핀 시트 좌표)]."""
    power_bases = set()
    for _, ls, le in iter_blocks(sch_text, 'lib_symbols', depth=1):
        for _, s, e in iter_blocks(sch_text, 'symbol', depth=0, start=ls + 1, end=le):
            top = sch_text[s:e]
            m = _SYM_NAME_RE.match(top)
            # 표식은 정의의 직계 자식이어야 한다(하위 유닛 심볼 안의 글자와 헷갈리지 않게)
            if m and any(True for _ in iter_blocks(top, 'power', depth=1)):
                full = _unq(m.group(1))
                power_bases.add(full.split(':', 1)[1] if ':' in full else full)
    if not power_bases:
        return []
    lib_pins = _lib_pin_positions(sch_text)
    instances = []
    for _, s, e in iter_blocks(sch_text, 'symbol', depth=1):
        vm = _VALUE_PROP_RE.search(sch_text, s, e)
        instances.append(_unq(vm.group(1)) if vm else None)
    out = []
    for (base, X, Y, R, mdir, unit), value in zip(_symbol_instances_all(sch_text), instances):
        if base is None or base not in power_bases or value is None:
            continue
        units = lib_pins.get(base, {})
        steps = int(round(R / 90.0)) % 4
        for px, py in list(units.get(0, ())) + list(units.get(unit, ())):
            x, y = _rot90(px, py, steps)
            if mdir == 'x':
                y = -y
            elif mdir == 'y':
                x = -x
            out.append((value, (X + x, Y - y)))
    return out


def _symbol_instances_all(sch_text):
    """`_symbol_instances` 와 같지만 해석 못 한 인스턴스도 자리(None 튜플)를 지켜 순서를 맞춘다."""
    out = []
    for _, s, e in iter_blocks(sch_text, 'symbol', depth=1):
        one = _symbol_instances('(kicad_sch\n' + sch_text[s:e] + '\n)')
        out.append(one[0] if one else (None, 0, 0, 0, None, 1))
    return out


def remove_redundant_transparent_labels(sch_text: str, keep_names=frozenset()) -> tuple[str, int]:
    """배선 중간의 **중복** 투명 `global_label` 을 지운다.

    배경: 임포터는 hop 조각마다 투명 전역 라벨(글자색 알파 0)을 얹어 넷 이름을 잇는다. KiCad
    화면에서는 안 보이지만 SVG/PDF 플롯은 알파를 무시해 검은 라벨로 찍는다. hop 병합 뒤에는 대부분
    중복이다. 시트 안 배선을 연결 그룹으로 묶고(**보수적으로**: 끝점끼리 맞닿음 + 정션 점만 연결로
    본다 — 정션 없는 T자 접속은 다른 그룹으로 취급해 지우지 않는 쪽으로 틀린다), 투명 라벨 X 가 놓인
    그룹에 (a) 보이는 global_label X, (b) Value 가 X 인 전원 심볼 핀, (c) 이미 남기기로 한 다른 투명
    라벨 X 중 하나가 있으면 지운다. 같은 이름을 대신 붙여 주는 것이 그룹에 남으므로 넷 이름·연결이
    바뀌지 않는다. 지역 `label` 만 있는 경우는 범위(시트 안/전역)가 달라 지우지 않는다. 라벨이 배선
    위가 아니거나 여러 그룹에 걸치면 그대로 둔다. `keep_names` 에 든 이름은 지우지 않는다
    (`dedupe_project_guarded` 가 넷리스트가 달라진 넷의 이름을 넣는다). 반환값: (새 텍스트, 지운
    라벨 수). 멱등.

    주의: 한 넷이 여러 시트에 걸쳐 이름 후보(전원 핀 이름·다른 이름의 전역 라벨)가 섞여 있으면
    시트 안 정보만으로는 KiCad 가 고를 넷 이름을 보장할 수 없다 — 이름까지 같음을 보장하려면
    `dedupe_project_guarded` 로 넷리스트 대조와 함께 쓴다."""
    segs = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) >= 2:
            segs.append((pts[0], pts[1]))
    if not segs:
        return sch_text, 0
    uf = _UnionFind(len(segs))
    by_end = {}
    for i, (a, b) in enumerate(segs):
        for p in (a, b):
            by_end.setdefault((round(p[0], 3), round(p[1], 3)), []).append(i)
    for idx in by_end.values():
        for j in idx[1:]:
            uf.union(idx[0], j)
    for _, s, e in iter_blocks(sch_text, 'junction', depth=1):
        block = sch_text[s:e]
        at = _at_xyr(block, _child_span(block, 'at'))
        if at is None:
            continue
        idx = [i for i, (a, b) in enumerate(segs) if _on_segment(at[:2], a, b)]
        for j in idx[1:]:
            uf.union(idx[0], j)

    def group_of(p):
        roots = {uf.find(i) for i, (a, b) in enumerate(segs) if _on_segment(p, a, b)}
        return roots.pop() if len(roots) == 1 else None     # 배선 밖이거나 애매하면 None

    named = set()             # (이름, 그룹) — 이름을 대신 붙여 주는 것이 있는 그룹
    transparent = []
    global_names = {}         # 그룹 -> 그 그룹의 전역 라벨 이름 집합(투명 포함)
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        nm = _GLABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at):
            continue
        g = group_of(at[:2])
        if g is None:
            continue
        name = _unq(nm.group(1))
        global_names.setdefault(g, set()).add(name)
        if _TRANSPARENT_COLOR_RE.search(block):
            transparent.append((name, g, s, e))
        else:
            named.add((name, g))
    # 전원 심볼은 같은 그룹에 **다른 이름의** 전역 라벨이 없을 때만 대신 이름을 붙여 준다고 본다.
    # KiCad 10 은 한 넷에 전원 핀 이름과 전역 라벨 이름이 섞이면 전역 라벨 쪽을 넷 이름으로 고르는
    # 것으로 실측됐다(픽스처: 투명 'VCC' 를 지우자 넷 이름이 'VCC' -> 'VCC_3.3V'). 섞인 그룹에서는
    # 같은 이름 투명 라벨을 하나 남겨 이름 후보 구성을 바꾸지 않는다.
    for value, p in _power_pin_points(sch_text):
        g = group_of(p)
        if g is not None and global_names.get(g, set()) <= {value}:
            named.add((value, g))
    edits = []
    for name, g, s, e in transparent:
        if name in keep_names:
            continue
        if (name, g) in named:
            edits.append(_line_span(sch_text, s, e))
        else:
            named.add((name, g))          # 이 그룹에서 이 이름은 이 라벨 하나만 남긴다
    if not edits:
        return sch_text, 0
    edits.sort()
    out = []
    pos = 0
    for a, b in edits:
        out.append(sch_text[pos:a])
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(edits)


def dedupe_project_guarded(pro_path, netlist_fn, max_rounds=3) -> dict:
    """프로젝트의 모든 시트에 `remove_redundant_transparent_labels` 를 적용하되, 넷리스트가 **넷 이름과
    핀 집합까지** 그대로일 때만 결과를 남긴다.

    `netlist_fn(root_sch_path)` 는 {넷 이름: 핀 집합} 을 돌려주는 함수(파이프라인이 kicad-cli 로
    만들어 넘긴다 — 이 모듈은 kicad-cli 를 모른다). 적용 전 넷리스트를 기준으로, 적용 후가 다르면
    달라진 넷의 이름(전·후 양쪽)을 `keep_names` 에 더해 원본에서 다시 적용한다(최대 `max_rounds`).
    끝까지 다르면 원본으로 되돌린다.

    반환값: `{'removed': 지운 라벨 수, 'kept_names': 보호한 이름 정렬 목록, 'verified': 대조 통과 여부,
    'reverted': 되돌렸는가, 'rounds': 시도 횟수}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    root = os.path.splitext(pro_path)[0] + '.kicad_sch'
    sheet_paths = sorted(glob.glob(os.path.join(folder, '*.kicad_sch')))
    originals = {}
    for p in sheet_paths:
        with open(p, encoding='utf-8', newline='') as fh:
            originals[p] = fh.read()

    def write_all(texts):
        for p, t in texts.items():
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(t)

    base = netlist_fn(root)
    keep = set()
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        new = {}
        removed = 0
        for p, t in originals.items():
            new[p], n = remove_redundant_transparent_labels(t, frozenset(keep))
            removed += n
        if not removed:
            write_all(originals)
            return {'removed': 0, 'kept_names': sorted(keep), 'verified': True, 'reverted': False,
                    'rounds': rounds}
        write_all(new)
        after = netlist_fn(root)
        if after == base:
            return {'removed': removed, 'kept_names': sorted(keep), 'verified': True, 'reverted': False,
                    'rounds': rounds}
        changed = {k for k in set(base) | set(after) if base.get(k) != after.get(k)}
        if changed <= keep:
            break
        keep |= changed
    write_all(originals)
    return {'removed': 0, 'kept_names': sorted(keep), 'verified': False, 'reverted': True, 'rounds': rounds}


def localize_importer_globals(sheet_texts: dict, keep_names=frozenset()) -> tuple[dict, int]:
    """`--net-names kicad`(.DSN/KiCad 프로젝트): 임포터가 덧붙인 투명 전역 라벨 중 **진짜 전역 이름이
    아닌 것**을 지운다 — OrCAD 페이지 별칭(`RTS#`)은 보이는 지역 `label` 만 남아 지역 넷이 되고, 자동
    이름 넷(`N16780834`)은 라벨 없이 KiCad 자동 이름이 된다.

    진짜 전역 이름 = 프로젝트 어느 시트든 (a) 보이는 `global_label`(오프페이지 커넥터를 변환한 것)이
    있거나 (b) 전원 심볼의 Value 인 이름. 그런 이름의 투명 라벨은 남긴다(전역 연결을 실어 나름).
    판정은 모든 시트를 함께 본다(`sheet_texts`: {경로: 텍스트}). `keep_names` 는 지우지 않는다
    (`localize_project_guarded` 가 핀 분할이 바뀐 넷 이름을 넣는다). 반환값: ({경로: 새 텍스트}, 지운 수)."""
    global_names = set()
    for text in sheet_texts.values():
        for _, s, e in iter_blocks(text, 'global_label', depth=1):
            block = text[s:e]
            nm = _GLABEL_NAME_RE.match(block)
            if nm and not _TRANSPARENT_COLOR_RE.search(block):
                global_names.add(_unq(nm.group(1)))
        for value, _p in _power_pin_points(text):
            global_names.add(value)
    out = {}
    removed = 0
    for path, text in sheet_texts.items():
        spans = []
        for _, s, e in iter_blocks(text, 'global_label', depth=1):
            block = text[s:e]
            nm = _GLABEL_NAME_RE.match(block)
            if not nm or not _TRANSPARENT_COLOR_RE.search(block):
                continue
            name = _unq(nm.group(1))
            if name in global_names or name in keep_names:
                continue
            spans.append(_line_span(text, s, e))
        if spans:
            parts, pos = [], 0
            for a, b in sorted(spans):
                parts.append(text[pos:a])
                pos = b
            parts.append(text[pos:])
            text = ''.join(parts)
            removed += len(spans)
        out[path] = text
    return out, removed


def _partition(sig):
    """{넷 이름: 핀 집합} -> 핀 집합들의 정렬 목록(이름 무시한 연결 분할)."""
    return sorted(sorted(p) for p in sig.values())


def localize_project_guarded(pro_path, netlist_fn, max_rounds=4) -> dict:
    """`localize_importer_globals` 를 프로젝트에 적용하되, 넷의 **핀 집합 분할이 그대로**일 때만 남긴다.

    이름은 바뀌는 것이 목적이다(`RTS#` → `/05-USB/RTS#`, `N123` → `Net-(R1-Pad1)`). 적용 후 분할이 다르거나
    이름이 있던 넷이 `unconnected-…` 로 이름을 잃으면, 그런 넷의 원래 이름을 보호 목록에 넣고 원본에서
    다시 적용한다.
    끝까지 다르면 원복. 반환값: `{'removed', 'kept_names', 'renamed': {전 이름: 후 이름}, 'verified',
    'reverted', 'rounds'}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    root = os.path.splitext(pro_path)[0] + '.kicad_sch'
    originals = {}
    for p in sorted(glob.glob(os.path.join(folder, '*.kicad_sch'))):
        with open(p, encoding='utf-8', newline='') as fh:
            originals[p] = fh.read()

    def write_all(texts):
        for p, t in texts.items():
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(t)

    base = netlist_fn(root)
    keep = set()
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        new, removed = localize_importer_globals(originals, frozenset(keep))
        if not removed:
            write_all(originals)
            return {'removed': 0, 'kept_names': sorted(keep), 'renamed': {}, 'verified': True,
                    'reverted': False, 'rounds': rounds}
        write_all(new)
        after = netlist_fn(root)
        by_pins = {v: k for k, v in after.items()}
        # 이름을 잃은 넷: 핀 분할은 같아도 라벨이 핀 없는 배선 조각에만 남아 넷이 'unconnected-…' 가
        # 된 경우(hop 갭이 병합되지 않은 상태 등). 연결 의미가 바뀐 것으로 보고 그 이름을 보호한다.
        lost = {k for k, v in base.items()
                if not k.startswith('unconnected-') and by_pins.get(v, '').startswith('unconnected-')}
        if _partition(after) == _partition(base) and not lost:
            renamed = {k: by_pins[v] for k, v in base.items() if by_pins.get(v, k) != k}
            return {'removed': removed, 'kept_names': sorted(keep), 'renamed': renamed,
                    'verified': True, 'reverted': False, 'rounds': rounds}
        after_sets = set(after.values())
        changed = {k for k, v in base.items() if v not in after_sets} | lost
        if changed <= keep:
            break
        keep |= changed
    write_all(originals)
    return {'removed': 0, 'kept_names': sorted(keep), 'renamed': {}, 'verified': False,
            'reverted': True, 'rounds': rounds}


_SIZE_IN_FONT_RE = re.compile(r'(\(size\s+)([0-9]*\.?[0-9]+)(\s+)([0-9]*\.?[0-9]+)(\s*\))')
_TINY = '0.01'


def shrink_transparent_labels(sch_text: str) -> tuple[str, int]:
    """남은 투명 라벨(전역·지역·계층)의 글자 크기를 0.01 mm 로 줄인다(`--hidden-labels shrink`).

    KiCad 화면은 투명(알파 0)을 지켜 안 그리지만 PDF/SVG 플로터는 알파를 무시해 R=1(거의 검정)로
    찍는다. 라벨 모양은 글자 크기를 따르므로 0.01 mm 면 점 수준이 된다. 연결·이름은 그대로(위치·
    텍스트 불변). 라벨 블록 안 첫 `(size w h)`(= effects/font 의 크기)만 바꾼다. 멱등."""
    edits = []
    for kind in ('global_label', 'label', 'hierarchical_label'):
        for _, s, e in iter_blocks(sch_text, kind, depth=1):
            block = sch_text[s:e]
            if not _TRANSPARENT_COLOR_RE.search(block):
                continue
            m = _SIZE_IN_FONT_RE.search(sch_text, s, e)
            if m is None or (m.group(2) == _TINY and m.group(4) == _TINY):
                continue
            edits.append((m.start(2), m.end(4), f'{_TINY}{m.group(3)}{_TINY}'))
    if not edits:
        return sch_text, 0
    edits.sort()
    out, pos = [], 0
    for a, b, rep in edits:
        out.append(sch_text[pos:a])
        out.append(rep)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(edits)


_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_KSX1001_PAIR_RE = re.compile(rb'[\xa1-\xfe][\xa1-\xfe]')


def _undo_cp949_as_latin1(s):
    """Latin-1 로 잘못 읽힌 CP949 문자열이면 바로잡은 문자열, 아니면 None.

    조건(셋 다): 모든 문자가 U+00FF 이하이고 그중 고상위(≥0x80)가 있다 / 고상위 바이트가 전부
    둘씩 짝을 이룬 KS X 1001 영역(0xA1–0xFE 두 바이트)이다 — 'µF'(0xB5 'F'), '±5'(0xB1 '5'),
    '°C' 같은 정상 Latin-1 은 짝이 안 맞아 걸러진다 / CP949 로 엄격하게 풀리고 결과에 한글 음절이
    하나 이상 있다."""
    if not any('\x80' <= c for c in s) or any(c > '\xff' for c in s):
        return None
    raw = s.encode('latin-1')
    if any(b >= 0x80 for b in _KSX1001_PAIR_RE.sub(b'', raw)):
        return None
    try:
        fixed = raw.decode('cp949')
    except UnicodeDecodeError:
        return None
    return fixed if any('가' <= c <= '힣' for c in fixed) else None


def fix_cp949_mojibake(text: str) -> tuple[str, int]:
    """KiCad 파일 텍스트의 따옴표 문자열 중 CP949→Latin-1 로 깨진 한글을 되돌린다.

    나이틀리 OrCAD 임포터는 `.DSN` 의 CP949(한국어 OrCAD) 문자열을 Latin-1 로 읽는다
    ('TVS Diode는 커넥터…' → 'TVS Diode´Â Ä¿³ØÅÍ…', 2026-10-27 사용자 보고). EDIF 경로는 리더가
    `%NNN%` 바이트를 CP949 로 풀어 문제가 없다. 판정은 `_undo_cp949_as_latin1` — 확신 없으면 그대로.
    따옴표 안의 이스케이프(`\\"`, `\\\\`, `\\n`)는 ASCII 라 그대로 보존된다. 반환값: (새 텍스트,
    바꾼 문자열 수). 멱등."""
    count = 0

    def repl(m):
        nonlocal count
        fixed = _undo_cp949_as_latin1(m.group(1))
        if fixed is None:
            return m.group(0)
        count += 1
        return '"' + fixed + '"'
    out = _QUOTED_RE.sub(repl, text)
    return out, count


_STROKE_WIDTH_RE = re.compile(r'(\(stroke\s+\(width\s+)([0-9]*\.?[0-9]+)(\s*\))')


def normalize_wire_widths(sch_text: str) -> tuple[str, int]:
    """최상위 `wire`/`bus` 블록의 stroke 폭이 0 이 아니면 0(KiCad 기본 폭)으로 바꾼다.

    반환값: (새 텍스트, 바꾼 블록 수). 블록 안의 폭 숫자만 바꾸고 그 밖의 바이트는 그대로 둔다.
    이미 0 이면 건드리지 않으므로 멱등이다."""
    edits = []
    for kind in ('wire', 'bus'):
        for _, s, e in iter_blocks(sch_text, kind, depth=1):
            m = _STROKE_WIDTH_RE.search(sch_text, s, e)
            if m is not None and float(m.group(2)) != 0.0:
                edits.append((m.start(2), m.end(2)))
    if not edits:
        return sch_text, 0
    out = []
    pos = 0
    for a, b in sorted(edits):
        out.append(sch_text[pos:a])
        out.append('0')
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(edits)


def cleanup_project(pro_path, merge_hops=True, blank_worksheet=True, default_wire_width=True,
                    offpage_labels=True, dedupe_labels=True, fix_korean=True,
                    korean_font=DEFAULT_KOREAN_FONT) -> dict:
    """`.kicad_pro` 프로젝트 폴더의 모든 `.kicad_sch`(루트 + 하위 시트)에 (선택) 깨진 한글 복원
    (`fix_cp949_mojibake`, 폴더의 `.kicad_sym` 에도), (선택) 오프페이지
    커넥터 → 전역 라벨(`offpage_to_global_labels`), `merge_hop_gaps`(배선이 합쳐진 뒤에
    `snap_labels_to_wire_ends` 를 이어서), (선택) 중복 투명 라벨 제거
    (`remove_redundant_transparent_labels`), (선택) 배선 폭 기본값화(`normalize_wire_widths`)를
    적용해 제자리에 다시 쓰고, (선택) 빈 도면 양식을 적용한다.

    반환값: `{'sheets': 찾은 시트 파일 수, 'offpage_converted': 전역 라벨로 바꾼 오프페이지 커넥터
    수, 'gaps_merged': 병합한 갭 총합, 'labels_moved': 옮긴 라벨 총합, 'transparent_removed': 지운
    중복 투명 라벨 수, 'wires_normalized': 폭을 기본값으로 바꾼 wire/bus 수, 'korean_fixed': 되돌린
    깨진 한글 문자열 수(시트 + 심볼 라이브러리), 'worksheet': 쓴 .kicad_wks 경로 또는 None}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    sheet_paths = sorted(glob.glob(os.path.join(folder, '*.kicad_sch')))
    korean_fixed = 0
    korean_font_set = 0
    if fix_korean:
        for path in sorted(glob.glob(os.path.join(folder, '*.kicad_sym'))):
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            text, k = fix_cp949_mojibake(text)
            text, f = apply_korean_font(text, korean_font)
            if k or f:
                korean_fixed += k
                korean_font_set += f
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(text)
    offpage_converted = 0
    gaps_merged = 0
    labels_moved = 0
    transparent_removed = 0
    wires_normalized = 0
    if merge_hops or default_wire_width or offpage_labels or dedupe_labels or fix_korean:
        for path in sheet_paths:
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            n = n2 = n3 = n0 = n4 = k = f = 0
            # 한글 먼저: 라벨·글자 이름 비교(오프페이지 판정 등)가 복원된 이름으로 이뤄지게.
            # 복원한 뒤 한글 글꼴 지정 — 임포터가 옮긴 'Arial' 등은 한글을 네모로 그린다.
            if fix_korean:
                text, k = fix_cp949_mojibake(text)
                text, f = apply_korean_font(text, korean_font)
                korean_fixed += k
                korean_font_set += f
            # 오프페이지 먼저: 갈매기는 대각선이라 hop 갭(수평/수직 2점 polyline)과 겹치지 않지만,
            # 라벨 끝점 정렬이 보는 점유 좌표가 정리된 상태가 되도록 앞에 둔다.
            if offpage_labels:
                text, n0 = offpage_to_global_labels(text)
                offpage_converted += n0
            if merge_hops:
                text, n = merge_hop_gaps(text)
                text, n2 = snap_labels_to_wire_ends(text)
            # hop 병합 뒤라야 조각이 합쳐져 중복이 드러난다(조각마다 따로 있던 투명 라벨).
            if dedupe_labels:
                text, n4 = remove_redundant_transparent_labels(text)
            if default_wire_width:
                text, n3 = normalize_wire_widths(text)
            gaps_merged += n
            labels_moved += n2
            transparent_removed += n4
            wires_normalized += n3
            if n0 or n or n2 or n3 or n4 or k or f:
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(text)
    wks_path = apply_blank_worksheet(pro_path) if blank_worksheet else None
    return {'sheets': len(sheet_paths), 'offpage_converted': offpage_converted,
            'gaps_merged': gaps_merged, 'labels_moved': labels_moved,
            'transparent_removed': transparent_removed,
            'wires_normalized': wires_normalized, 'korean_fixed': korean_fixed,
            'korean_font_set': korean_font_set, 'worksheet': wks_path}
