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
import math
import os
import re
import uuid
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


def _lib_pin_positions(sch_text, with_angle=False):
    """`lib_symbols` 안의 핀 좌표(라이브러리 공간, 심볼 배치 전)를 모은다.

    반환값: {기본이름(접두사 없이): {유닛번호: [(px, py), ...]}} (`with_angle` 이면 (px, py, 각도) —
    각도는 접속점에서 심볼 몸체 쪽으로 핀이 뻗는 방향, 라이브러리 y-업 기준). 유닛 서브심볼 이름은
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
                        pins.append(xyr if with_angle else (xyr[0], xyr[1]))
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


def _symbol_pin_outward(sch_text):
    """배치된 모든 심볼 핀의 [((시트 x, y), (바깥 방향 dx, dy))] — 바깥 = 접속점에서 심볼 몸체 **반대쪽**
    (시트 좌표, y-다운, 단위 벡터). 변환은 `_symbol_pin_points` 와 같다(벡터에는 이동 없음)."""
    lib_pins = _lib_pin_positions(sch_text, with_angle=True)
    out = []
    for base, X, Y, R, mdir, unit in _symbol_instances(sch_text):
        units = lib_pins.get(base)
        if not units:
            continue
        steps = int(round(R / 90.0)) % 4
        for px, py, ang in list(units.get(0, ())) + list(units.get(unit, ())):
            x, y = _rot90(px, py, steps)
            vx, vy = _rot90(round(math.cos(math.radians(ang))), round(math.sin(math.radians(ang))), steps)
            if mdir == 'x':
                y, vy = -y, -vy
            elif mdir == 'y':
                x, vx = -x, -vx
            out.append(((X + x, Y - y), (-vx, vy)))     # 몸체 쪽(vx, -vy)의 반대
    return out


def face_pin_end_labels_outward(sch_text: str) -> tuple[str, int]:
    """배선 없이 **부품 핀 끝에 바로** 붙은 라벨(지역·전역)의 글자가 심볼 몸체 쪽으로 겹치지 않게, 접속점은
    그대로 두고 회전·justify 만 핀 바깥 방향으로 맞춘다.

    임포터는 이런 라벨에 justify 를 주지 않아 KiCad 가 글자를 접속점 **가운데**에 놓는다 — 절반이 핀·핀 번호
    위로 겹친다(코퍼스 실측). 한 점에 핀이 하나일 때만(방향이 하나로 정해질 때). 접속점이 안 움직이므로 연결은
    그대로다. 반환값: (새 텍스트, 손댄 라벨 수). 멱등."""
    group_of = _wire_groups(sch_text)
    pins = {}
    for pt, d in _symbol_pin_outward(sch_text):
        pins.setdefault((round(pt[0], 3), round(pt[1], 3)), []).append(d)
    edits, n = [], 0
    for kind in ('label', 'global_label'):
        for _, s, e in iter_blocks(sch_text, kind, depth=1):
            block = sch_text[s:e]
            at_span = _child_span(block, 'at')
            xyr = _at_xyr(block, at_span)
            if xyr is None or group_of(xyr[:2]) is not None:
                continue
            ds = pins.get((round(xyr[0], 3), round(xyr[1], 3)))
            if not ds or len(ds) != 1:
                continue
            rot, just = {(1, 0): (0, 'left'), (-1, 0): (180, 'right'),
                         (0, -1): (90, 'left'), (0, 1): (270, 'right')}.get(ds[0], (None, None))
            if rot is None:
                continue
            if kind == 'label':
                just += ' bottom'               # 지역 라벨 글자는 핀 선 위에 얹힌다
            eff = _child_span(block, 'effects')
            cur_j = _child_span(block[eff[0]:eff[1]], 'justify') if eff else None
            cur = block[eff[0] + cur_j[0]:eff[0] + cur_j[1]] if cur_j else ''
            if abs(xyr[2] - rot) < 0.5 and cur == f'(justify {just})':
                continue
            edits.append((s + at_span[0], s + at_span[1], f'(at {fnum(xyr[0])} {fnum(xyr[1])} {rot})'))
            je = _justify_edit(block, eff, just)
            if je is not None:
                edits.append((s + je[0], s + je[1], je[2]))
            n += 1
    if not edits:
        return sch_text, 0
    return _apply_rel_edits(sch_text, edits), n


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
    # 버스 끝점(버스 중간은 `_bus_segments` + `_free_point` 로 따로 막는다). 넷 라벨이 버스에 닿으면 버스
    # 라벨이 되어 멤버 넷이 합쳐진다(코퍼스 실측 회귀, 2026-10-28).
    for a, b in _bus_segments(sch_text):
        add(*a)
        add(*b)
    return counts


def _bus_segments(sch_text):
    """시트의 버스 선분 [(p1, p2)]."""
    segs = []
    for _, s, e in iter_blocks(sch_text, 'bus', depth=1):
        pts = _points(sch_text[s:e])
        segs.extend(zip(pts, pts[1:]))
    return segs


def _free_point(counts, buses, pt):
    """배선 끝점 `pt` 가 비어 있는가: 그 배선 끝 말고는 아무것도 없고(`counts` ≤ 1) 버스 위도 아니다."""
    if counts[(round(pt[0], 3), round(pt[1], 3))] > 1:
        return False
    return not any(_on_segment(pt, a, b) for a, b in buses)


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
    라벨이 두 개면 먼저(문서 순서) 처리되는 것만 옮겨진다). 라벨이 핀이 있는 배선 끝에 놓이고 배선이 그 너머
    빈 끝까지 이어진 경우도 빈 끝으로 옮긴다(그 점의 배선 끝이 하나일 때만). 버스 끝점·버스 위의 점은 빈 끝이
    아니다(넷 라벨이 버스에 닿으면 멤버 넷이 합쳐진다)."""
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
    buses = _bus_segments(sch_text)

    def key(pt):
        return (round(pt[0], 3), round(pt[1], 3))

    def is_free(pt):
        return _free_point(counts, buses, pt)

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

    wire_ends = Counter(key(p) for w in candidate_wires for p in (w['p1'], w['p2']))
    edits = []
    moved = 0
    for lab in labels:
        match_w = None
        for w in candidate_wires:
            if on_segment(lab['anchor'], w):
                match_w = w
                break
        if match_w is None:
            # 라벨이 핀 쪽 끝(배선 끝 + 라벨 + 핀 등)에 있고 배선이 라벨 너머 빈 끝까지 더 나간 경우(OrCAD 그리기
            # 습관, 2026-10-28 코퍼스 ~73건)도 빈 끝으로 — 그 점에 배선 끝이 하나뿐일 때만(갈림길이면 그대로).
            ak = key(lab['anchor'])
            if wire_ends[ak] != 1 or counts[ak] < 3:
                continue
            for w in candidate_wires:
                if ak in (key(w['p1']), key(w['p2'])):
                    match_w = w
                    break
            if match_w is None or not is_free(match_w['p2'] if key(match_w['p1']) == ak else match_w['p1']):
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


_NEAR_END_MM = 0.3       # 보이는 전역 라벨을 배선 끝으로 붙이는 최대 거리(임포터 오차 0.254 mm 실측)


def snap_global_labels_to_near_ends(sch_text: str, reach: float = _NEAR_END_MM) -> tuple[str, int]:
    """보이는 `global_label` 앵커가 배선 위 **빈 끝에서 `reach` 이내**(끝은 아님)면 그 끝으로 옮긴다.

    실측(2026-10-28 코퍼스): 오프페이지 커넥터를 변환한 전역 라벨 일부가 배선 끝에서 0.254 mm 모자라 배선 끝이
    빈 것으로 잡혔다(ERC unconnected_wire_endpoint). 회전·justify 는 그대로(이미 배선 밖을 향함). 반환값:
    (새 텍스트, 옮긴 수)."""
    is_free = _free_end_checker(sch_text)
    wires = [p for p in (_points(sch_text[s:e]) for _, s, e in iter_blocks(sch_text, 'wire', depth=1))
             if len(p) == 2]
    edits = []
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        if _TRANSPARENT_COLOR_RE.search(block):
            continue
        span = _child_span(block, 'at')
        at = _at_xyr(block, span)
        if at is None:
            continue
        for p1, p2 in wires:
            if not _on_segment(at[:2], p1, p2):
                continue
            near = [q for q in (p1, p2) if _LABEL_TOL < abs(q[0] - at[0]) + abs(q[1] - at[1]) <= reach]
            if len(near) == 1 and is_free(near[0]):
                q = near[0]
                edits.append((s + span[0], s + span[1], f'(at {fnum(q[0])} {fnum(q[1])} {fnum(at[2])})'))
            break
    if not edits:
        return sch_text, 0
    out, pos = [], 0
    for a, b, rep in sorted(edits):
        out.append(sch_text[pos:a])
        out.append(rep)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(edits)


def remove_alias_labels_duplicating_globals(sch_text: str) -> tuple[str, int]:
    """같은 배선(보수적 연결 그룹)에 **같은 이름의 보이는 전역 라벨**이 있는 보이는 지역 `label` 을 지운다
    (`--duplicate-alias-labels remove`, 기본은 그대로 둠).

    OrCAD 에서는 넷 별칭과 같은 이름 오프페이지 커넥터를 한 배선에 나란히 그리는 일이 흔하다. KiCad 에서는
    전역 라벨이 이미 넷 이름을 주므로 지역 라벨은 중복이고 ERC `same_local_global_label` 경고가 난다(코퍼스
    ~126건, 공개 설계 54건). 지우면 경고가 사라지지만 도면에서 글자 하나가 빠진다. 반환값: (새 텍스트, 지운 수)."""
    group_of = _wire_groups(sch_text)
    globals_in = set()
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        nm = _GLABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if nm and at and not _TRANSPARENT_COLOR_RE.search(block):
            g = group_of(at[:2])
            if g is not None:
                globals_in.add((_unq(nm.group(1)), g))
    by_name = {}              # 지역 이름 -> [(그룹, span)] — 투명 지역 라벨도 시트 안 연결을 만드므로 포함
    for _, s, e in iter_blocks(sch_text, 'label', depth=1):
        block = sch_text[s:e]
        nm = _LABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if nm and at:
            by_name.setdefault(_unq(nm.group(1)), []).append(
                (group_of(at[:2]), _line_span(sch_text, s, e), bool(_TRANSPARENT_COLOR_RE.search(block))))
    spans = []
    for name, items in by_name.items():
        # 같은 이름 지역 라벨은 시트 안에서 서로 잇는다 — 전부가 같은 이름 전역 라벨이 있는 배선에 있을 때만
        # 지워도 연결이 그대로다(전역 라벨끼리 잇는다).
        if all(g is not None and (name, g) in globals_in for g, _sp, _t in items):
            spans.extend(sp for _g, sp, transparent in items if not transparent)
    if not spans:
        return sch_text, 0
    out, pos = [], 0
    for a, b in sorted(spans):
        out.append(sch_text[pos:a])
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(spans)


_GLABEL_NAME_RE = re.compile(r'\(global_label\s+"((?:[^"\\]|\\.)*)"')
_TEXT_NAME_RE = re.compile(r'\(text\s+"((?:[^"\\]|\\.)*)"')
_TRANSPARENT_COLOR_RE = re.compile(r'\(color\s+[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+0\s*\)')
_CHEVRON_STEP = 1.27     # 갈매기 한 조각의 가로·세로 폭(mm) — 실측(나이틀리 임포터) 값
_TEXT_REACH_MM = 40.0    # 커넥터 글자를 찾는 범위(앵커에서 커넥터 방향으로)


def offpage_to_global_labels(sch_text: str, report: list | None = None) -> tuple[str, int]:
    """임포터가 그림으로 옮긴 OrCAD 오프페이지 커넥터를 KiCad 전역 라벨로 바꾼다.

    임포터 출력: 배선 끝(앵커)에 **투명** `global_label`(글자색 알파 0) + 커넥터 그림 + 같은 이름의 `text`.
    커넥터 그림은 두 가지 모양이 실측됐다(2026-10-28 코퍼스):
    - 겹 갈매기(>>): 앵커에서 커넥터 방향으로 대각선 `polyline` 4조각(각 1.27 mm).
    - 한 겹 꺾쇠(—<): 앵커에서 커넥터 방향으로 2.54 mm 직선 `polyline` + 그 끝에서 벌어지는 대각선 2조각
      (+ 앵커의 길이 0 `polyline`).
    전기적으로는 이미 전역 넷이지만 보이는 것은 그림과 글자뿐이라, 클릭해도 라벨이 아닌 글자가 잡힌다. 라벨·
    그림·같은 이름 글자가 **모두** 맞는 경우에만(확신 없으면 그대로 둔다) 라벨의 투명 색 지정을 지워 보이게
    하고 그림·글자를 지운다. 배선 중간에 얹힌 투명 라벨(hop 조각용)은 그림이 없으므로 건드리지 않는다.

    방향: 라벨 각도 0 → +x, 180 → -x, 90 → -y(위), 270 → +y. 갈매기 조각은 **중점**이 앵커에서 그 방향으로
    정해진 거리 안, 옆으로 1.27 미만이어야 그 커넥터 것이다(2.54 mm 간격으로 붙은 이웃 커넥터와 끝점을 공유해도
    중점으로 갈린다). 임포터가 라벨 각도를 그림과 반대로 주는 일이 있어(실측: 떠 있는 커넥터, 꺾쇠 모양 —
    라벨이 배선 쪽을 향함, 세로 커넥터에도 각도 0) 라벨 각도 쪽에 그림·글자가 없으면 반대쪽, 수직 두 쪽도
    찾고, 찾으면 라벨을 그림 쪽으로 돌린다.
    커넥터 글자가 숨은 라벨 이름과 **다르면**(임포터가 커넥터 이름 대신 넷 이름을 붙인 경우 — OrCAD 에서 한 넷에
    커넥터 이름이 둘) 바꾸지 않는다: 라벨을 커넥터 이름으로 바꾸면 그 이름의 다른 커넥터와 이어지지 않아 넷이 갈라진다
    (2026-10-28 코퍼스 시험: 8개 설계 중 6개에서 핀 연결이 달라짐). `report` 리스트를 주면 이런 커넥터를
    `{'text': 보이는 글자, 'net': 숨은 라벨(실제 넷) 이름, 'at': (x, y)}` 로 넣는다 — 화면 이름과 넷 이름이 다르다고
    사용자에게 알리기 위해.
    반환값: (새 텍스트, 바꾼 커넥터 수). 멱등."""
    polys = []                # 대각선 1.27 x 1.27 조각
    straights = []            # 그 밖의 2점 polyline(꺾쇠 줄기·길이 0 점)
    for _, s, e in iter_blocks(sch_text, 'polyline', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) != 2:
            continue
        (x1, y1), (x2, y2) = pts
        if abs(abs(x2 - x1) - _CHEVRON_STEP) <= _LABEL_TOL and abs(abs(y2 - y1) - _CHEVRON_STEP) <= _LABEL_TOL:
            polys.append({'mid': ((x1 + x2) / 2, (y1 + y2) / 2), 'start': s, 'end': e, 'used': False})
        else:
            straights.append({'pts': pts, 'start': s, 'end': e, 'used': False})
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
        # 가로 글자의 가운데 x 추정(세로 커넥터 판정용): 획 글꼴 글자당 0.78 × 높이, justify 기준점 반영
        tb = sch_text[t['start']:t['end']]
        sz = _SIZE_IN_FONT_RE.search(tb)
        w = len(_unq(t['name'])) * (float(sz.group(2)) if sz else 1.27) * 0.78
        jm = re.search(r'\(justify\s+([a-z ]+)\)', tb)
        j = jm.group(1).split() if jm else []
        half = w / 2 if 'left' in j else (-w / 2 if 'right' in j else 0.0)
        t['cx'] = t['pos'][0] + half if t['pos'][0] is not None else None
    texts = [t for t in texts if t['pos'][0] is not None]
    dirs = {0: (1, 0), 180: (-1, 0), 90: (0, -1), 270: (0, 1)}
    rot_of = {(1, 0): (0, 'left'), (-1, 0): (180, 'right'), (0, -1): (90, 'left'), (0, 1): (270, 'right')}

    def same(p, q):
        return abs(p[0] - q[0]) <= _LABEL_TOL and abs(p[1] - q[1]) <= _LABEL_TOL

    def find_shape(ax, ay, d):
        """방향 d 의 커넥터 그림 조각 목록(겹 갈매기 4 또는 꺾쇠 줄기+2(+점)) 또는 None."""
        def along_perp(p):
            vx, vy = p[0] - ax, p[1] - ay
            return vx * d[0] + vy * d[1], abs(vx * d[1] - vy * d[0])
        free = [p for p in polys if not p['used']]
        double = [p for p in free if 0 < along_perp(p['mid'])[0] < 2 * _CHEVRON_STEP
                  and along_perp(p['mid'])[1] < _CHEVRON_STEP - _LABEL_TOL]
        if len(double) == 4:
            return double
        tip = (ax + 2 * _CHEVRON_STEP * d[0], ay + 2 * _CHEVRON_STEP * d[1])
        stem = [q for q in straights if not q['used']
                and ((same(q['pts'][0], (ax, ay)) and same(q['pts'][1], tip))
                     or (same(q['pts'][1], (ax, ay)) and same(q['pts'][0], tip)))]
        fork = [p for p in free if 2 * _CHEVRON_STEP < along_perp(p['mid'])[0] < 3 * _CHEVRON_STEP
                and along_perp(p['mid'])[1] < _CHEVRON_STEP - _LABEL_TOL]
        if len(stem) == 1 and len(fork) == 2:
            dots = [q for q in straights if not q['used'] and q is not stem[0]
                    and same(q['pts'][0], (ax, ay)) and same(q['pts'][1], (ax, ay))]
            return stem + fork + dots
        return None

    def find_text(ax, ay, d, name, any_name=False):
        cands = []
        for t in texts:
            if t['used'] or (t['name'] != name and not any_name):
                continue
            vx, vy = t['pos'][0] - ax, t['pos'][1] - ay
            a, p = vx * d[0] + vy * d[1], abs(vx * d[1] - vy * d[0])
            if d[0] == 0:
                # 세로 커넥터의 글자는 가로로 쓰여(실측) 시작점이 아니라 글자 가운데가 커넥터 축 근처
                p = abs(t['cx'] - ax)
            if 0 < a <= _TEXT_REACH_MM and p <= 2 * _CHEVRON_STEP + _LABEL_TOL:
                cands.append((a, t))
        return min(cands, key=lambda c: c[0])[1] if cands else None

    edits = []
    converted = 0
    cands = []
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        cm = _TRANSPARENT_COLOR_RE.search(block)
        nm = _GLABEL_NAME_RE.match(block)
        at = block_at(s, e)
        if not (cm and nm and at):
            continue
        d = dirs.get(int(round(at[2])) % 360)
        if d is not None:
            cands.append((s, block, cm, nm.group(1), at, d))
    # 1단계: 모든 라벨이 라벨 각도 쪽에서만. 2단계: 남은 라벨만 반대쪽, 그다음 수직 두 쪽(임포터가 세로 커넥터에도
    # 각도 0 을 준다) — 각도가 맞는 이웃 커넥터(같은 이름)의 그림을 먼저 가져가지 않게 한다.
    done = set()
    for stage in ((0, 1, 2) if report is not None else (0, 1)):
        for s, block, cm, name, at, d in cands:
            if s in done:
                continue
            ax, ay = at[0], at[1]
            tries = [d] if stage == 0 else [(-d[0], -d[1]), (d[1], d[0]), (-d[1], -d[0])]
            if stage == 2:
                tries = [d] + tries          # 3단계(report): 이름이 다른 글자 — 바꾸지 않고 알리기만
            found = None
            for dd in tries:
                pieces = find_shape(ax, ay, dd)
                text = find_text(ax, ay, dd, name, any_name=(stage == 2)) if pieces else None
                if pieces and text:
                    found = (dd, pieces, text)
                    break
            if found is None:
                continue
            done.add(s)
            dd, pieces, text = found
            if stage == 2:
                report.append({'text': _unq(text['name']), 'net': _unq(name), 'at': (ax, ay)})
                continue
            text['used'] = True
            for p in pieces:
                p['used'] = True
                edits.append(_line_span(sch_text, p['start'], p['end']) + ('',))
            edits.append(_line_span(sch_text, text['start'], text['end']) + ('',))
            cs, ce = _line_span(sch_text, s + cm.start(), s + cm.end())
            edits.append((cs, ce, ''))
            if dd != d:
                rot, just = rot_of[dd]
                at_span = _child_span(block, 'at')
                edits.append((s + at_span[0], s + at_span[1], f'(at {fnum(ax)} {fnum(ay)} {rot})'))
                je = _justify_edit(block, _child_span(block, 'effects'), just)
                if je is not None:
                    edits.append((s + je[0], s + je[1], je[2]))
            converted += 1
    if not edits:
        return sch_text, 0
    return _apply_rel_edits(sch_text, edits), converted


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


def _wire_groups(sch_text):
    """시트 배선을 **보수적** 연결 그룹으로 묶어 `group_of(점) -> 그룹 id | None` 을 돌려준다.

    끝점끼리 맞닿음 + 정션 점만 연결로 본다(정션 없는 T자는 다른 그룹). 점이 배선 밖이거나 여러
    그룹에 걸치면 None."""
    segs = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) >= 2:
            segs.append((pts[0], pts[1]))
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
        return roots.pop() if len(roots) == 1 else None
    return group_of


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
    `dedupe_project_guarded` 로 넷리스트 대조와 함께 쓴다.

    배선 밖 투명 라벨: 같은 이름 전원 심볼 핀 위에 **정확히** 놓여 있고 그 점에 다른 이름 전역 라벨이
    없으면 지운다(실측 WSL: 배선 없이 핀에 바로 붙은 투명 GND/VCC — 전원 핀이 같은 이름을 붙인다)."""
    group_of = _wire_groups(sch_text)
    named = set()             # (이름, 그룹) — 이름을 대신 붙여 주는 것이 있는 그룹
    transparent = []
    global_names = {}         # 그룹 -> 그 그룹의 전역 라벨 이름 집합(투명 포함)
    off_wire = []             # 배선 밖 투명 라벨 (이름, 점, s, e)
    names_at = {}             # 배선 밖 점 -> 그 점의 전역 라벨 이름 집합(투명 포함)
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        nm = _GLABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at):
            continue
        name = _unq(nm.group(1))
        g = group_of(at[:2])
        if g is None:
            key = (round(at[0], 3), round(at[1], 3))
            names_at.setdefault(key, set()).add(name)
            if _TRANSPARENT_COLOR_RE.search(block):
                off_wire.append((name, key, s, e))
            continue
        global_names.setdefault(g, set()).add(name)
        if _TRANSPARENT_COLOR_RE.search(block):
            transparent.append((name, g, s, e))
        else:
            named.add((name, g))
    edits = []
    if off_wire:
        power_at = {}
        for value, p in _power_pin_points(sch_text):
            power_at.setdefault((round(p[0], 3), round(p[1], 3)), set()).add(value)
        for name, key, s, e in off_wire:
            if name not in keep_names and name in power_at.get(key, ()) and names_at[key] == {name}:
                edits.append(_line_span(sch_text, s, e))
    # 전원 심볼은 같은 그룹에 **다른 이름의** 전역 라벨이 없을 때만 대신 이름을 붙여 준다고 본다.
    # KiCad 10 은 한 넷에 전원 핀 이름과 전역 라벨 이름이 섞이면 전역 라벨 쪽을 넷 이름으로 고르는
    # 것으로 실측됐다(픽스처: 투명 'VCC' 를 지우자 넷 이름이 'VCC' -> 'VCC_3.3V'). 섞인 그룹에서는
    # 같은 이름 투명 라벨을 하나 남겨 이름 후보 구성을 바꾸지 않는다.
    for value, p in _power_pin_points(sch_text):
        g = group_of(p)
        if g is not None and global_names.get(g, set()) <= {value}:
            named.add((value, g))
    for name, g, s, e in transparent:
        if name in keep_names:
            continue
        if (name, g) in named:
            edits.append(_line_span(sch_text, s, e))
        else:
            named.add((name, g))          # 이 그룹에서 이 이름은 이 라벨 하나만 남긴다
    # 투명 지역 label 도 같은 규칙(실측 공개 설계: 보이는 지역 라벨과 같은 배선에 투명 지역 라벨 5개).
    # 지역 이름은 시트 안 같은 이름끼리 잇는다 — 같은 그룹에 같은 이름 지역 라벨이 남아 있어야만 지운다.
    local_named = set()
    local_transparent = []
    for _, s, e in iter_blocks(sch_text, 'label', depth=1):
        block = sch_text[s:e]
        nm = _LABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at):
            continue
        g = group_of(at[:2])
        if g is None:
            continue
        name = _unq(nm.group(1))
        if _TRANSPARENT_COLOR_RE.search(block):
            local_transparent.append((name, g, s, e))
        else:
            local_named.add((name, g))
    for name, g, s, e in local_transparent:
        if name in keep_names:
            continue
        if (name, g) in local_named:
            edits.append(_line_span(sch_text, s, e))
        else:
            local_named.add((name, g))
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


def remove_bus_wire_junctions(sch_text: str) -> tuple[str, int]:
    """배선 끝이 버스에 **버스 엔트리 없이** 직접 닿는 점에 임포터가 찍은 `junction` 을 지운다.

    OrCAD 에서 버스에 닿은 배선은 그림일 뿐이고 연결은 넷 별칭 이름(버스 멤버 이름)으로 이뤄진다. 그런데
    KiCad 는 버스 위 junction 을 실제 접속으로 보아, 그 점에 닿은 배선들을 버스를 통해 한 넷으로 묶는다
    (코퍼스 실측 2026-10-29: 2개 설계에서 데이터선 7~16개가 한 넷으로 합쳐짐 — PADS 정답은 각각 따로).
    지우면 두 설계 모두 PADS 정답과 100% 일치했다.

    버스 분기점(그 점에서 버스 팔이 3개 이상 — 버스 중간 통과는 팔 2, 끝점은 팔 1)의 junction 은 버스끼리의
    접속이라 남긴다. 배선 끝이 닿지 않는 버스 위 junction 도 건드리지 않는다. 반환값: (새 텍스트, 지운 수)."""
    buses = _bus_segments(sch_text)
    if not buses:
        return sch_text, 0
    wire_ends = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if pts:
            wire_ends.extend((pts[0], pts[-1]))

    def same(p, q):
        return abs(p[0] - q[0]) <= _LABEL_TOL and abs(p[1] - q[1]) <= _LABEL_TOL
    edits = []
    for _, s, e in iter_blocks(sch_text, 'junction', depth=1):
        block = sch_text[s:e]
        at = _at_xyr(block, _child_span(block, 'at'))
        if not at:
            continue
        p = at[:2]
        on = [(a, b) for a, b in buses if _on_segment(p, a, b)]
        if not on or not any(same(p, w) for w in wire_ends):
            continue
        arms = sum(1 if same(p, a) or same(p, b) else 2 for a, b in on)
        if arms >= 3:
            continue
        edits.append(_line_span(sch_text, s, e))
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


BUS_ENTRY_SIZE = 2.54     # KiCad 기본 버스 엔트리(45도, 가로·세로 2.54 mm)


def draw_bus_entries(sch_text: str) -> tuple[str, int]:
    """배선 끝이 버스 엔트리 없이 버스에 바로 닿은 곳을 KiCad 식으로 다시 그린다: 배선을 버스 쪽 끝에서
    `BUS_ENTRY_SIZE` 만큼 줄이고, 줄인 끝에서 버스까지 45도 `bus_entry` 를 넣는다(연결은 그대로 배선의 넷
    이름으로 — 엔트리는 KiCad 에서 이름을 잇지 않는다. ERC `unconnected_wire_endpoint` 가 그 끝마다 없어진다).

    고치는 조건(하나라도 어긋나면 그 끝은 그대로): 2점 가로/세로 배선이 버스 선분과 수직이고, 배선 길이가
    엔트리보다 길고, 버스 위 그 점에 이 배선 말고 다른 배선 끝·핀·접속점·라벨이 없고, 줄인 끝 자리에 다른
    배선·핀이 없고(라벨은 그 배선 것이라 괜찮다), 엔트리의 버스 쪽 끝이 그 버스 선분 위이고 남는 배선 끝·핀·
    접속점과 겹치지 않을 것. 엔트리 방향은 버스를 따라 좌표가 커지는 쪽을 먼저(빗살 모양이 한 방향으로 정렬),
    안 되면 반대쪽. 반환값: (새 텍스트, 넣은 엔트리 수). 멱등(이미 엔트리가 있는 끝은 버스에 닿지 않는다)."""
    buses = _bus_segments(sch_text)
    if not buses:
        return sch_text, 0
    tol = _LABEL_TOL

    def key(p):
        return (round(p[0], 3), round(p[1], 3))
    wires = []                       # (블록 시작, 끝, p1, p2)
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) == 2:
            wires.append((s, e, pts[0], pts[1]))
    ends = Counter()
    for _, _, a, b in wires:
        ends[key(a)] += 1
        ends[key(b)] += 1
    pins = Counter(key(p) for p in _symbol_pin_points(sch_text))
    juncs = set()
    for _, s, e in iter_blocks(sch_text, 'junction', depth=1):
        at = _at_xyr(sch_text[s:e], _child_span(sch_text[s:e], 'at'))
        if at:
            juncs.add(key(at[:2]))
    label_pts = set()
    for kind in ('label', 'global_label', 'hierarchical_label'):
        for _, s, e in iter_blocks(sch_text, kind, depth=1):
            at = _at_xyr(sch_text[s:e], _child_span(sch_text[s:e], 'at'))
            if at:
                label_pts.add(key(at[:2]))
    plans = []                       # (wire 시작, 끝, 버스 점 E, 줄인 끝 N, 버스 선분)
    for s, e, a, b in wires:
        axis, _ = _axis_of(a, b)
        if axis is None:
            continue
        for E, F in ((a, b), (b, a)):
            on = [(p, q) for p, q in buses if _on_segment(E, p, q)]
            if len(on) != 1:
                continue
            bp, bq = on[0]
            baxis, _ = _axis_of(bp, bq)
            if baxis is None or baxis == axis:
                continue
            length = abs(F[0] - E[0]) + abs(F[1] - E[1])
            if length <= BUS_ENTRY_SIZE + tol:
                continue
            k = key(E)
            if ends[k] != 1 or pins[k] or k in juncs or k in label_pts:
                continue
            d = ((F[0] - E[0]) / length, (F[1] - E[1]) / length)
            N = (E[0] + BUS_ENTRY_SIZE * d[0], E[1] + BUS_ENTRY_SIZE * d[1])
            if pins[key(N)] or ends[key(N)] or any(
                    _on_segment(N, w[2], w[3]) for w in wires if w[0] != s):
                continue
            plans.append((s, e, E, N, (bp, bq)))
            break
    freed = Counter(key(p[2]) for p in plans)
    edits = []
    used_q = set()
    for s, e, E, N, (bp, bq) in plans:
        baxis, _ = _axis_of(bp, bq)
        along = (1.0, 0.0) if baxis == 'h' else (0.0, 1.0)
        Q = None
        for sign in (1, -1):
            cand = (E[0] + sign * BUS_ENTRY_SIZE * along[0], E[1] + sign * BUS_ENTRY_SIZE * along[1])
            ck = key(cand)
            if not _on_segment(cand, bp, bq) or ck in used_q or ck in juncs or pins[ck] \
                    or ends[ck] - freed[ck] > 0:
                continue
            Q = cand
            break
        if Q is None:
            continue
        used_q.add(key(Q))
        block = sch_text[s:e]
        # 버스 쪽 끝(E)의 (xy …) 만 N 으로 바꾼다
        new_block = None
        for m in _XY_RE.finditer(block):
            if abs(float(m.group(1)) - E[0]) < tol and abs(float(m.group(2)) - E[1]) < tol:
                new_block = block[:m.start()] + f'(xy {fnum(N[0])} {fnum(N[1])})' + block[m.end():]
                break
        if new_block is None:
            continue
        wid = re.search(r'\(uuid "([^"]*)"\)', block)
        eid = uuid.uuid5(uuid.NAMESPACE_URL, f'orcad2kicad-bus-entry:{wid.group(1) if wid else ""}:{key(E)}')
        entry = ('\n\t(bus_entry\n'
                 f'\t\t(at {fnum(N[0])} {fnum(N[1])})\n'
                 f'\t\t(size {fnum(Q[0] - N[0])} {fnum(Q[1] - N[1])})\n'
                 '\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n'
                 f'\t\t(uuid "{eid}")\n'
                 '\t)')
        edits.append((s, e, new_block + entry))
    if not edits:
        return sch_text, 0
    out = []
    pos = 0
    for a, b, new in sorted(edits):
        out.append(sch_text[pos:a])
        out.append(new)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), len(edits)


def split_bus_wire_junctions_guarded(pro_path, netlist_fn) -> dict:
    """프로젝트의 모든 시트에 `remove_bus_wire_junctions` 를 적용하되, 넷리스트가 **나뉘기만** 했을 때만
    남긴다 — 적용 후의 모든 넷(핀 집합)이 적용 전 어느 한 넷의 부분집합이어야 한다(합쳐지거나 핀이 다른 넷으로
    옮겨가면 원래대로 되돌린다). 이 단계는 연결을 **일부러** 바꾸므로(임포터가 잘못 합친 넷을 나눔)
    `cleanup_project_guarded` 의 "넷리스트 동일" 가드 밖에서, 그보다 먼저 실행한다.

    이어서 버스에 엔트리 없이 닿은 배선 끝을 `draw_bus_entries` 로 KiCad 식(줄인 배선 + 45도 엔트리)으로
    다시 그린다 — 이건 연결을 바꾸지 않아야 하므로 넷리스트(이름+핀)가 그대로일 때만 남긴다(다르면 엔트리만 원복).

    `netlist_fn(root_sch_path)` 는 {넷 이름: 핀 집합}. 반환값: `{'removed': 지운 junction 수, 'splits':
    [{'net': 적용 전 넷 이름, 'into': [적용 후 넷 이름 정렬]}], 'verified': 대조 통과 여부,
    'reverted': 되돌렸는가, 'entries_added': 넣은 버스 엔트리 수, 'entries_reverted': 엔트리를 되돌렸는가}`."""
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
    res = {'removed': 0, 'splits': [], 'verified': True, 'reverted': False,
           'entries_added': 0, 'entries_reverted': False}
    current = dict(originals)
    # 1단계: 버스 위 junction 삭제 — 넷이 "나뉘기만" 했을 때만
    new = {}
    for p, t in originals.items():
        t2, n = remove_bus_wire_junctions(t)
        if n:
            new[p] = t2
            res['removed'] += n
    if not new and not any(draw_bus_entries(t)[1] for t in originals.values()):
        return res                      # 버스에 닿은 배선이 없음 — kicad-cli 를 부르지 않는다
    base = netlist_fn(root)
    if new:
        write_all(new)
        after = netlist_fn(root)
        owner = {}                      # 핀 -> 적용 전 넷 이름
        for name, pins in base.items():
            for pin in pins:
                owner[pin] = name
        pieces = {}                     # 적용 전 넷 이름 -> 적용 후 넷 이름들
        ok = True
        for name, pins in after.items():
            before = {owner.get(pin) for pin in pins}
            if len(before) != 1 or None in before:
                ok = False
                break
            pieces.setdefault(before.pop(), set()).add(name)
        if ok:
            current.update(new)
            base = after
            res['splits'] = [{'net': k, 'into': sorted(v)} for k, v in sorted(pieces.items()) if len(v) > 1]
        else:
            write_all({p: originals[p] for p in new})
            res.update(removed=0, verified=False, reverted=True)
    # 2단계: 버스에 바로 닿은 배선 끝을 버스 엔트리로 다시 그림 — 넷리스트(이름+핀)가 그대로일 때만
    drawn = {}
    for p, t in current.items():
        t2, n = draw_bus_entries(t)
        if n:
            drawn[p] = t2
            res['entries_added'] += n
    if drawn:
        write_all(drawn)
        if netlist_fn(root) != base:
            write_all({p: current[p] for p in drawn})
            res.update(entries_added=0, entries_reverted=True)
    return res


def localize_importer_globals(sheet_texts: dict, keep_names=frozenset(), kinds=('global',)) -> tuple[dict, int]:
    """`--net-names kicad`(.DSN/KiCad 프로젝트): 임포터가 덧붙인 투명 전역 라벨 중 **진짜 전역 이름이
    아닌 것**을 지운다 — OrCAD 페이지 별칭(`RTS#`)은 보이는 지역 `label` 만 남아 지역 넷이 되고, 자동
    이름 넷(`N16780834`)은 라벨 없이 KiCad 자동 이름이 된다.

    진짜 전역 이름 = 프로젝트 어느 시트든 (a) 보이는 `global_label`(오프페이지 커넥터를 변환한 것)이
    있거나 (b) 전원 심볼의 Value 인 이름. 그런 이름의 투명 라벨은 남긴다(전역 연결을 실어 나름).
    판정은 모든 시트를 함께 본다(`sheet_texts`: {경로: 텍스트}). `keep_names` 는 지우지 않는다
    (`localize_project_guarded` 가 핀 분할이 바뀐 넷 이름을 넣는다). `kinds` 에 'local' 이 있으면 투명 **지역**
    라벨도 지운다('global' 이 없으면 지역만). 반환값: ({경로: 새 텍스트}, 지운 수)."""
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
        for _, s, e in iter_blocks(text, 'global_label', depth=1) if 'global' in kinds else ():
            block = text[s:e]
            nm = _GLABEL_NAME_RE.match(block)
            if not nm or not _TRANSPARENT_COLOR_RE.search(block):
                continue
            name = _unq(nm.group(1))
            if name in global_names or name in keep_names:
                continue
            spans.append(_line_span(text, s, e))
        # 투명 **지역** 라벨(임포터가 OrCAD 자동 이름 `N167787351` 등에 붙임)도 지운다 — 지역 이름이 시트 안에서
        # 실어 나르던 연결이 있으면 호출자(`localize_project_guarded`)의 핀 분할 대조가 그 이름을 보호한다.
        for _, s, e in iter_blocks(text, 'label', depth=1) if 'local' in kinds else ():
            block = text[s:e]
            nm = _LABEL_NAME_RE.match(block)
            if nm and _TRANSPARENT_COLOR_RE.search(block) and _unq(nm.group(1)) not in keep_names:
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


def _transparent_names(sheet_texts, kinds):
    """투명 라벨 이름 집합(`kinds`: 'global' → global_label, 'local' → label)."""
    wanted = ([('global_label', _GLABEL_NAME_RE)] if 'global' in kinds else []) + \
             ([('label', _LABEL_NAME_RE)] if 'local' in kinds else [])
    out = set()
    for text in sheet_texts.values():
        for kind, rx in wanted:
            for _, s, e in iter_blocks(text, kind, depth=1):
                block = text[s:e]
                nm = rx.match(block)
                if nm and _TRANSPARENT_COLOR_RE.search(block):
                    out.add(_unq(nm.group(1)))
    return out


def localize_project_guarded(pro_path, netlist_fn, max_rounds=6) -> dict:
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

    def check(texts):
        """`texts` 를 쓰고 넷리스트 대조 → (연결 동일?, 달라진 원래 넷 이름 집합)."""
        write_all(texts)
        after = netlist_fn(root)
        by_pins = {v: k for k, v in after.items()}
        # 이름을 잃은 넷: 핀 분할은 같아도 라벨이 핀 없는 배선 조각에만 남아 넷이 'unconnected-…' 가
        # 된 경우(hop 갭이 병합되지 않은 상태 등). 연결 의미가 바뀐 것으로 보고 그 이름을 보호한다.
        lost = {k for k, v in base.items()
                if not k.startswith('unconnected-') and by_pins.get(v, '').startswith('unconnected-')}
        after_sets = set(after.values())
        changed = {k for k, v in base.items() if v not in after_sets} | lost
        return _partition(after) == _partition(base) and not lost, changed

    def culprits(start, kinds, keep, cands):
        """넷 이름 보호로 안 풀릴 때(한 넷을 **다른 이름** 숨은 라벨들이 묶고 있음 — 코퍼스 실측: 버스로 합쳐진
        `DATA1` 넷을 `DATA2..7` 라벨이 묶음) 지울 후보 이름을 반씩 나눠 연결을 깨는 이름을 찾는다."""
        names = set(cands)

        def breaks(sub):
            new, n = localize_importer_globals(start, frozenset(keep | (names - set(sub))), kinds)
            return bool(n) and not check(new)[0]

        def rec(sub):
            if not breaks(sub):
                return set()
            if len(sub) == 1:
                return set(sub)
            half = len(sub) // 2
            found = rec(sub[:half]) | rec(sub[half:])
            return found or set(sub)        # 따로는 괜찮고 함께일 때만 깨지면 통째로 보호
        return rec(sorted(cands))

    def stage(start, kinds):
        """`start` 텍스트에서 `kinds` 를 지역화 — 성공하면 (결과 dict, 새 텍스트), 실패하면 (None, keep 이름)"""
        keep = set()
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            new, removed = localize_importer_globals(start, frozenset(keep), kinds)
            if not removed:
                return {'removed': 0, 'kept_names': sorted(keep), 'rounds': rounds}, start
            ok, changed = check(new)
            if ok:
                return {'removed': removed, 'kept_names': sorted(keep), 'rounds': rounds}, new
            if changed - keep:
                keep |= changed
                continue
            bad = culprits(start, kinds, keep, _transparent_names(start, kinds) - keep)
            if not bad:
                break
            keep |= bad
        write_all(start)
        return None, sorted(keep)

    # 1단계 전역(별칭·자동 이름 넷), 2단계 투명 지역 라벨 — 2단계가 실패해도 1단계 결과는 남긴다(코퍼스 실측:
    # 한 번에 하면 지역 라벨 쪽 문제로 설계 전체가 keep 으로 돌아간 경우가 있었다).
    g, texts = stage(originals, ('global',))
    if g is None:
        return {'removed': 0, 'kept_names': texts, 'renamed': {}, 'verified': False,
                'reverted': True, 'rounds': max_rounds, 'locals': None}
    loc, texts2 = stage(texts, ('local',))
    if loc is not None:
        texts = texts2
    write_all(texts)
    after = netlist_fn(root)
    by_pins = {v: k for k, v in after.items()}
    renamed = {k: by_pins[v] for k, v in base.items() if by_pins.get(v, k) != k}
    return {'removed': g['removed'] + (loc['removed'] if loc else 0),
            'kept_names': sorted(set(g['kept_names']) | set(loc['kept_names'] if loc else ())),
            'renamed': renamed, 'verified': True, 'reverted': False, 'rounds': g['rounds'],
            'locals': 'ok' if loc is not None else 'reverted'}


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


_LABEL_NAME_RE = re.compile(r'\(label\s+"((?:[^"\\]|\\.)*)"')


def _transparent_label_survey(sch_text):
    """투명 전역 라벨마다 (이름, (x, y), 블록 span, 종류, 같은 배선의 보이는 지역 라벨 정보) 를 모은다.

    종류: 'alias'(같은 이름 보이는 지역 라벨이 같은 배선에 — OrCAD 별칭이 같은 이름 전역 넷에 붙은 것),
    'renamed'(다른 이름 보이는 지역 라벨만 — OrCAD 표시 이름 바꿈, 예: 넷 ETH_NRST 의 nRST),
    'wire'(보이는 라벨 없는 배선 위), 'free'(배선 밖), 'local'(투명 **지역** 라벨 — 그 배선에 시트 안 이름을
    붙이는 유일한 라벨)."""
    group_of = _wire_groups(sch_text)
    locals_by_group = {}
    for _, s, e in iter_blocks(sch_text, 'label', depth=1):
        block = sch_text[s:e]
        nm = _LABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at) or _TRANSPARENT_COLOR_RE.search(block):
            continue
        g = group_of(at[:2])
        if g is not None:
            locals_by_group.setdefault(g, []).append((_unq(nm.group(1)), s, e))
    out = []
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        nm = _GLABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at) or not _TRANSPARENT_COLOR_RE.search(block):
            continue
        name = _unq(nm.group(1))
        g = group_of(at[:2])
        locs = locals_by_group.get(g, []) if g is not None else []
        same = [x for x in locs if x[0] == name]
        if same:
            kind, local = 'alias', same[0]
        elif locs:
            kind, local = 'renamed', locs[0]
        else:
            kind, local = ('wire' if g is not None else 'free'), None
        out.append((name, (at[0], at[1]), (s, e), kind, local))
    # 투명 지역 label(중복 제거 뒤 남은 것 = 그 배선에 이름을 붙이는 유일한 라벨)
    for _, s, e in iter_blocks(sch_text, 'label', depth=1):
        block = sch_text[s:e]
        nm = _LABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if nm and at and _TRANSPARENT_COLOR_RE.search(block):
            out.append((_unq(nm.group(1)), (at[0], at[1]), (s, e), 'local', None))
    out.sort(key=lambda r: r[2][0])
    return out


def classify_transparent_labels(sch_text: str) -> list:
    """남은 투명 전역 라벨 설명용 목록: `[{'name', 'at': (x, y), 'kind', 'alias'}]`.

    `kind` 는 `_transparent_label_survey` 참조, `alias` 는 같은 배선의 보이는 지역 라벨 이름(없으면 None).
    중복 제거(`remove_redundant_transparent_labels`) 뒤에 남은 것은 연결을 실어 나르므로 지우면 넷이
    끊기거나 이름이 바뀐다 — 그래서 사용자가 `--hidden-labels` 로 처리 방법을 고른다."""
    return [{'name': n, 'at': at, 'kind': k, 'alias': loc[0] if loc else None}
            for n, at, _span, k, loc in _transparent_label_survey(sch_text)]


SUBTLE_LABEL_COLOR = '(color 150 150 150 1)'   # `--hidden-label-style subtle`: 변환기가 드러낸 연결 표시색(RGB 0~255, 알파 0~1)
SUBTLE_LABEL_SIZE = '0.9'


def show_transparent_labels(sch_text: str, style: str = 'normal') -> tuple[str, int]:
    """남은 투명 전역 라벨을 **보이게** 한다(`--hidden-labels show`). 연결·넷 이름은 그대로.

    - 'alias': 같은 배선의 같은 이름 지역 라벨을 그 자리의 보이는 전역 라벨로 바꾸고 투명 라벨은 지운다
      (같은 이름 라벨이 두 번 그려지지 않게, ERC `same_local_global_label` 도 사라짐). 모양(shape)은 투명
      라벨 것을 쓴다.
    - 그 밖: 글자색 줄만 지워 기본색으로 보이게 한다. 배선 중간에 있으면 보이는 라벨이 배선 위에 겹쳐
      그려지므로, 그 배선의 한쪽 끝만 비어 있으면 라벨을 그 끝으로 옮긴다(배선 밖을 향하게).
    `style='subtle'`: 'alias' 가 아닌 것(원래 OrCAD 페이지에 보이는 글자가 없던 연결)은 글자색 줄을 회색
    (`SUBTLE_LABEL_COLOR`)으로, 크기를 `SUBTLE_LABEL_SIZE` mm 로 — 변환기가 드러낸 연결임이 구별되고 덜 거슬린다.
    반환값: (새 텍스트, 처리한 투명 라벨 수). 멱등."""
    edits = []
    shown = 0
    done_locals = set()
    is_free = _free_end_checker(sch_text)
    for name, at, (s, e), kind, local in _transparent_label_survey(sch_text):
        block = sch_text[s:e]
        if kind == 'alias' and local[1] not in done_locals:
            done_locals.add(local[1])
            _n, ls, le = local
            lblock = sch_text[ls:le]
            sm = re.search(r'\(shape\s+([a-z_]+)\s*\)', block)
            shape = sm.group(1) if sm else 'bidirectional'
            nl = '\r\n' if '\r\n' in lblock else '\n'
            head = re.match(r'\(label(\s+"(?:[^"\\]|\\.)*")', lblock)
            indent = re.search(r'\n([ \t]*)\(at\s', lblock)
            ind = indent.group(1) if indent else '\t\t'
            new = f'(global_label{head.group(1)}{nl}{ind}(shape {shape})' + lblock[head.end():]
            # 지역 라벨 justify 의 세로 성분(bottom)은 전역 라벨에 없다
            new = re.sub(r'\(justify\s+(left|right)\s+(?:top|bottom)\s*\)', r'(justify \1)', new)
            # 지역 라벨은 글자가 배선 위에 얹히지만 전역 라벨은 상자가 배선 방향으로 펼쳐진다 — 배선 밖을 향하게
            # 돌리고(배선 중간이면 비어 있는 끝으로 옮기고), 큰 글씨는 이웃 라벨(2.54 mm 간격)과 겹치지 않게 줄인다.
            lat = _at_xyr(lblock, _child_span(lblock, 'at'))
            place = None
            if lat is not None:
                ro = _outward_at_wire_end(sch_text, lat[:2])
                if ro is not None:
                    place = (lat[:2], ro[0], ro[1])
                else:
                    place = _free_end_of_wire_under(sch_text, lat[:2], is_free)
            new = _apply_rel_edits(new, _place_and_cap_edits(new, place))
            edits.append((ls, le, new))
            a, b = _line_span(sch_text, s, e)
            edits.append((a, b, ''))
            shown += 1
        else:
            m = re.search(r'\n[ \t]*\(color\s+[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+0\s*\)', block)
            if m is None:
                continue
            if style == 'subtle':
                edits.append((s + m.start(), s + m.end(),
                              re.sub(r'\(color[^)]*\)', SUBTLE_LABEL_COLOR, m.group(0))))
                sz = _SIZE_IN_FONT_RE.search(block)
                if sz:
                    edits.append((s + sz.start(2), s + sz.end(4),
                                  f'{SUBTLE_LABEL_SIZE}{sz.group(3)}{SUBTLE_LABEL_SIZE}'))
            else:
                edits.append((s + m.start(), s + m.end(), ''))
                if kind != 'local':
                    edits.extend((s + a, s + b, r) for a, b, r in _place_and_cap_edits(block, None))
            shown += 1
            target = _free_end_of_wire_under(sch_text, at, is_free) if kind in ('wire', 'local') else None
            if target is not None:
                (tx, ty), rot, justify = target
                if kind == 'local':
                    justify += ' bottom'          # 지역 라벨은 글자가 배선 위에 놓인다
                at_span = _child_span(block, 'at')
                edits.append((s + at_span[0], s + at_span[1], f'(at {fnum(tx)} {fnum(ty)} {rot})'))
                je = _justify_edit(block, _child_span(block, 'effects'), justify)
                if je is not None:
                    edits.append((s + je[0], s + je[1], je[2]))
    if not edits:
        return sch_text, 0
    edits.sort(key=lambda t: t[0])
    out, pos = [], 0
    for a, b, rep in edits:
        out.append(sch_text[pos:a])
        out.append(rep)
        pos = b
    out.append(sch_text[pos:])
    return ''.join(out), shown


SHOWN_LABEL_MAX_SIZE = 1.27   # show 로 드러낸 전역 라벨 글자 상한(KiCad 기본) — 상자 높이가 2.54 mm 피치를 넘지 않게


def _apply_rel_edits(text, edits):
    """(시작, 끝, 새 텍스트) 목록을 `text` 에 적용(겹치지 않는다고 가정)."""
    out, pos = [], 0
    for a, b, rep in sorted(edits, key=lambda t: t[0]):
        out.append(text[pos:a])
        out.append(rep)
        pos = b
    out.append(text[pos:])
    return ''.join(out)


def _place_and_cap_edits(block, place):
    """전역 라벨 블록 상대 편집 목록 — `place=(점, 회전, justify)` 면 위치·방향을 바꾸고, 글자 크기가
    `SHOWN_LABEL_MAX_SIZE` 보다 크면 그 크기로 줄인다(가로세로 비율 유지)."""
    edits = []
    if place is not None:
        (tx, ty), rot, justify = place
        at_span = _child_span(block, 'at')
        if at_span is not None:
            edits.append((at_span[0], at_span[1], f'(at {fnum(tx)} {fnum(ty)} {rot})'))
        je = _justify_edit(block, _child_span(block, 'effects'), justify)
        if je is not None:
            edits.append(je)
    sz = _SIZE_IN_FONT_RE.search(block)
    if sz:
        h, w = float(sz.group(2)), float(sz.group(4))
        if h > SHOWN_LABEL_MAX_SIZE + 1e-6:
            k = SHOWN_LABEL_MAX_SIZE / h
            edits.append((sz.start(2), sz.end(4), f'{fnum(h * k)}{sz.group(3)}{fnum(round(w * k, 3))}'))
    return edits


def _outward_at_wire_end(sch_text, at):
    """점 `at` 이 수평/수직 배선 **하나**의 끝이면 그 배선 밖을 향하는 (회전, justify), 아니면 None."""
    found = None
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) != 2:
            continue
        for end, other in ((pts[0], pts[1]), (pts[1], pts[0])):
            if abs(at[0] - end[0]) <= _LABEL_TOL and abs(at[1] - end[1]) <= _LABEL_TOL:
                if found is not None:
                    return None               # 여러 배선이 만나는 점 — 방향을 정할 수 없다
                found = (end, other)
    if found is None:
        return None
    end, other = found
    axis, _c = _axis_of(end, other)
    if axis == 'h':
        return (0, 'left') if end[0] > other[0] else (180, 'right')
    if axis == 'v':
        return (270, 'right') if end[1] > other[1] else (90, 'left')
    return None


def orient_hidden_labels_outward(sch_text: str) -> tuple[str, int]:
    """숨은(투명) 전역 라벨이 배선 쪽으로 펼쳐져 배선과 겹치지 않게 **배선 밖을 향하도록** 돌린다.

    숨은 라벨은 KiCad 화면에 안 보여도 PDF/SVG 에는 검게 찍힌다. 나이틀리 임포터는 이를 배선 중간에 회전 0
    으로 놓아 상자가 배선 위에 겹쳐 그려진다(라즈베리파이 CMIO `CD0_SCL` 등). 라벨이 수평/수직 배선 하나의
    끝에 있으면 그 자리에서 방향만, 배선 중간이면 다른 배선·핀·정션이 없는 끝(라벨은 있어도 됨 — 같은 배선이라
    같은 넷)으로 옮겨 방향을 맞춘다(둘 다 비면 가까운 쪽, 한 끝엔 하나만). 같은 배선 위 이동이라 연결은 그대로
    — 호출 측(`cleanup_project_guarded`)이 넷리스트로 다시 확인한다. 반환값: (새 텍스트, 손댄 라벨 수). 멱등."""
    wire_points, wires = [], []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        wire_points.extend(pts)
        if len(pts) == 2 and _axis_of(pts[0], pts[1])[0] is not None:
            wires.append(pts)
    counts = _collect_occupied(sch_text, wire_points)
    for name in ('label', 'global_label', 'hierarchical_label'):
        for _, s, e in iter_blocks(sch_text, name, depth=1):
            block = sch_text[s:e]
            xyr = _at_xyr(block, _child_span(block, 'at'))
            if xyr:
                counts[(round(xyr[0], 3), round(xyr[1], 3))] -= 1
    buses = _bus_segments(sch_text)

    def bare_end(pt):                   # 그 배선 끝 말고는 연결 대상이 없다
        return _free_point(counts, buses, pt)

    def same(p, q):
        return abs(p[0] - q[0]) <= _LABEL_TOL and abs(p[1] - q[1]) <= _LABEL_TOL

    def outward(end, other):
        if _axis_of(end, other)[0] == 'h':
            return (0, 'left') if end[0] > other[0] else (180, 'right')
        return (270, 'right') if end[1] > other[1] else (90, 'left')

    edits, used, n = [], set(), 0
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        if not _TRANSPARENT_COLOR_RE.search(block):
            continue
        at_span = _child_span(block, 'at')
        xyr = _at_xyr(block, at_span)
        if xyr is None:
            continue
        p = xyr[:2]
        at_end = [(a, b) for a, b in wires for end, other in ((a, b), (b, a)) if same(p, end)]
        if at_end:
            if len(at_end) != 1:
                continue                # 여러 배선이 만나는 점
            a, b = at_end[0]
            end, other = (a, b) if same(p, a) else (b, a)
            target = end
        else:
            under = [(a, b) for a, b in wires if _on_segment(p, a, b)]
            if len(under) != 1:
                continue
            a, b = under[0]
            cands = [(q, o) for q, o in ((a, b), (b, a))
                     if bare_end(q) and (round(q[0], 3), round(q[1], 3)) not in used]
            if not cands:
                continue                # 양 끝이 모두 다른 것과 이어짐 — 겹침을 피할 자리가 없다
            target, other = min(cands, key=lambda c: abs(c[0][0] - p[0]) + abs(c[0][1] - p[1]))
            end = target
        rot, just = outward(end, other)
        key = (round(target[0], 3), round(target[1], 3))
        if same(p, target) and abs(xyr[2] - rot) < 0.5:
            used.add(key)
            continue                    # 이미 바른 자리·방향
        used.add(key)
        edits.append((s + at_span[0], s + at_span[1], f'(at {fnum(target[0])} {fnum(target[1])} {rot})'))
        je = _justify_edit(block, _child_span(block, 'effects'), just)
        if je is not None:
            edits.append((s + je[0], s + je[1], je[2]))
        n += 1
    if not edits:
        return sch_text, 0
    return _apply_rel_edits(sch_text, edits), n


def _free_end_checker(sch_text):
    """`is_free(점)` — 배선 끝점이 비어 있는가(다른 배선·핀·정션·라벨 등이 없음). 판정은
    `snap_labels_to_wire_ends` 와 같다(`_collect_occupied`)."""
    wire_points = []
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        wire_points.extend(_points(sch_text[s:e]))
    counts = _collect_occupied(sch_text, wire_points)
    buses = _bus_segments(sch_text)
    return lambda pt: _free_point(counts, buses, pt)


def _free_end_of_wire_under(sch_text, at, is_free):
    """점 `at` 이 중간에 놓인 수평/수직 배선의 끝 중 **한쪽만** 비어 있으면 (그 끝, 회전, justify) —
    전역 라벨이 배선 밖을 향하게. 이미 끝에 있거나 해당 없으면 None."""
    for _, s, e in iter_blocks(sch_text, 'wire', depth=1):
        pts = _points(sch_text[s:e])
        if len(pts) != 2 or not _on_segment(at, pts[0], pts[1]):
            continue
        p1, p2 = pts
        if any(abs(at[0] - q[0]) <= _LABEL_TOL and abs(at[1] - q[1]) <= _LABEL_TOL for q in (p1, p2)):
            return None
        axis, _coord = _axis_of(p1, p2)
        f1, f2 = is_free(p1), is_free(p2)
        if axis is None or f1 == f2:
            return None
        tgt, other = (p1, p2) if f1 else (p2, p1)
        if axis == 'h':
            rot, just = (0, 'left') if tgt[0] > other[0] else (180, 'right')
        else:
            rot, just = (270, 'right') if tgt[1] > other[1] else (90, 'left')
        return tgt, rot, just
    return None


def find_hidden_label_conflicts(sch_text: str) -> list:
    """숨은(투명) 전역 라벨 이름 ≠ 같은 배선(또는 배선 밖이면 같은 점)의 전원 심볼 Value·보이는 전역 라벨 이름인
    곳 목록: `[{'name': 숨은 라벨 이름, 'others': [다른 이름…], 'at': (x, y), 'with': 'power'|'label'|'both'}]`.

    두 전역 이름이 한 배선에 있으면 KiCad 는 두 넷을 **하나로 합친다**(ERC 는 경고 `multiple_net_names` 뿐).
    2026-10-28 현장 사고: 전원 심볼을 `AVCC_3.3V` 짜리로 바꾸자 같은 배선의 숨은 `AVCC_5V` 7개 때문에 LDO
    입·출력 레일이 한 넷이 됐다. 원본 OrCAD 에서 이미 한 넷에 이름이 둘인 경우도 같은 신호로 잡힌다."""
    group_of = _wire_groups(sch_text)
    power_g, power_pt = {}, {}
    for value, p in _power_pin_points(sch_text):
        g = group_of(p)
        if g is not None:
            power_g.setdefault(g, set()).add(value)
        power_pt.setdefault((round(p[0], 3), round(p[1], 3)), set()).add(value)
    vis_g, vis_pt, hidden = {}, {}, []
    for _, s, e in iter_blocks(sch_text, 'global_label', depth=1):
        block = sch_text[s:e]
        nm = _GLABEL_NAME_RE.match(block)
        at = _at_xyr(block, _child_span(block, 'at'))
        if not (nm and at):
            continue
        name = _unq(nm.group(1))
        g = group_of(at[:2])
        if _TRANSPARENT_COLOR_RE.search(block):
            hidden.append((name, (at[0], at[1]), g))
        elif g is not None:
            vis_g.setdefault(g, set()).add(name)
        else:
            vis_pt.setdefault((round(at[0], 3), round(at[1], 3)), set()).add(name)
    out, seen = [], set()
    for name, at, g in hidden:
        key = (round(at[0], 3), round(at[1], 3))
        pw = (power_g.get(g, set()) if g is not None else power_pt.get(key, set())) - {name}
        lb = (vis_g.get(g, set()) if g is not None else vis_pt.get(key, set())) - {name}
        if not (pw or lb):
            continue
        sig = (name, frozenset(pw | lb), g if g is not None else key)
        if sig in seen:                    # 같은 배선의 같은 충돌은 한 번만
            continue
        seen.add(sig)
        out.append({'name': name, 'others': sorted(pw | lb), 'at': at,
                    'with': 'both' if pw and lb else ('power' if pw else 'label')})
    return out


_PWR_REF_RE = re.compile(r'"#PWR(\d+)"')
_AT_NUM_RE = re.compile(r'\(at\s+(-?[0-9]*\.?[0-9]+)\s+(-?[0-9]*\.?[0-9]+)')
_UUID_RE = re.compile(r'\(uuid\s+"[^"]*"\)')
_PATH_RE = re.compile(r'\(path\s+"([^"]*)"')


def _instance_pin_point(sheet_text, block):
    """심볼 인스턴스 블록(`sheet_text` 의 lib_symbols 정의 기준)의 첫 핀 시트 좌표, 못 구하면 None."""
    inst = _symbol_instances('(kicad_sch\n' + block + '\n)')
    if not inst:
        return None
    base, X, Y, R, mdir, unit = inst[0]
    units = _lib_pin_positions(sheet_text).get(base, {})
    pins = list(units.get(0, ())) + list(units.get(unit, ()))
    if not pins:
        return None
    x, y = _rot90(pins[0][0], pins[0][1], int(round(R / 90.0)) % 4)
    if mdir == 'x':
        y = -y
    elif mdir == 'y':
        x = -x
    return X + x, Y - y


def _lib_def_block(sheet_text, lib_id):
    """`lib_symbols` 안의 `lib_id` 정의 블록 텍스트(없으면 None)."""
    for _, ls, le in iter_blocks(sheet_text, 'lib_symbols', depth=1):
        for _, s, e in iter_blocks(sheet_text, 'symbol', depth=0, start=ls + 1, end=le):
            m = _SYM_NAME_RE.match(sheet_text[s:e])
            if m and _unq(m.group(1)) == lib_id:
                return sheet_text[s:e]
    return None


def place_power_symbols_for_hidden_labels(sheet_texts: dict) -> tuple[dict, list]:
    """배선 없이 **부품 핀에 바로** 얹힌 숨은 전원 넷 라벨(예: 투명 `GND` 가 U9.1 위)을 같은 이름 전원 심볼로
    바꾼다 — 2026-10-28 현장 사고 대책 H1. 보이지 않는 전원 연결이 남지 않게 한다.

    대상: 투명 `global_label` 중 배선 위가 아니고, 그 점이 (전원 심볼이 아닌) 심볼 핀이고, 그 점에 전원 심볼
    핀이 없고, 다른 이름 전역 라벨도 없으며, 이름이 프로젝트 어딘가의 전원 심볼 Value 인 것. 그 전원 심볼
    인스턴스 하나를 본으로 복사해(같은 시트 것을 먼저) 핀이 그 점에 오도록 옮기고, uuid·`#PWR` 번호를 새로
    매기고, 다른 시트 본이면 이 시트의 인스턴스 경로와 라이브러리 정의를 맞춘다. 반환값: ({경로: 새 텍스트},
    [(시트 경로, 이름, (x, y), 새 레퍼런스)]). 연결 보증은 호출자가 넷리스트로 한다.

    배선 위의 숨은 전원 라벨은 바꾸지 않는다 — 2026-10-28 시험(`convert`, 폐기): 연결은 맞지만 이미 있는 같은
    전원 심볼 옆에 하나 더 붙어 도면이 지저분했다."""
    templates = {}            # Value -> [(시트 경로, 인스턴스 블록)]
    max_ref = 0
    for path, text in sheet_texts.items():
        for m in _PWR_REF_RE.finditer(text):
            max_ref = max(max_ref, int(m.group(1)))
        values = {v for v, _p in _power_pin_points(text)}
        if not values:
            continue
        for _, s, e in iter_blocks(text, 'symbol', depth=1):
            block = text[s:e]
            vm = _VALUE_PROP_RE.search(block)
            if vm and _unq(vm.group(1)) in values and '#PWR' in block:
                templates.setdefault(_unq(vm.group(1)), []).append((path, block))
    out = dict(sheet_texts)
    placed = []
    for path, text in sheet_texts.items():
        group_of = _wire_groups(text)
        power_pts = {(round(p[0], 3), round(p[1], 3)) for _v, p in _power_pin_points(text)}
        pin_pts = {(round(x, 3), round(y, 3)) for x, y in _symbol_pin_points(text)}
        names_at = {}
        for _, s, e in iter_blocks(text, 'global_label', depth=1):
            block = text[s:e]
            nm = _GLABEL_NAME_RE.match(block)
            at = _at_xyr(block, _child_span(block, 'at'))
            if nm and at:
                names_at.setdefault((round(at[0], 3), round(at[1], 3)), set()).add(_unq(nm.group(1)))
        sheet_path = None
        for m in _PATH_RE.finditer(text):
            sheet_path = m.group(1)
            break
        edits = []
        lib_inserts = []
        for _, s, e in iter_blocks(text, 'global_label', depth=1):
            block = text[s:e]
            nm = _GLABEL_NAME_RE.match(block)
            at = _at_xyr(block, _child_span(block, 'at'))
            if not (nm and at) or not _TRANSPARENT_COLOR_RE.search(block):
                continue
            name = _unq(nm.group(1))
            key = (round(at[0], 3), round(at[1], 3))
            if name not in templates:
                continue
            if group_of(at[:2]) is not None:
                continue
            if key not in pin_pts or key in power_pts or names_at.get(key) != {name}:
                continue
            same = [x for x in templates[name] if x[0] == path]
            tpath, tblock = (same or templates[name])[0]
            pin = _instance_pin_point(sheet_texts[tpath], tblock)
            if pin is None:
                continue
            dx, dy = at[0] - pin[0], at[1] - pin[1]
            new = _AT_NUM_RE.sub(lambda m: f'(at {fnum(float(m.group(1)) + dx)} {fnum(float(m.group(2)) + dy)}',
                                 tblock)
            new = _UUID_RE.sub(lambda _m: f'(uuid "{uuid.uuid4()}")', new)
            max_ref += 1
            ref = f'#PWR{max_ref:04d}'
            new = _PWR_REF_RE.sub(f'"{ref}"', new)
            if tpath != path:
                if sheet_path is None:
                    continue
                new = _PATH_RE.sub(f'(path "{sheet_path}"', new)
                lm = _LIBID_RE.search(new)
                lib_id = _unq(lm.group(1)) if lm else None
                if lib_id and _lib_def_block(text, lib_id) is None and lib_id not in [x[0] for x in lib_inserts]:
                    ldef = _lib_def_block(sheet_texts[tpath], lib_id)
                    if ldef is None:
                        continue
                    lib_inserts.append((lib_id, ldef))
            a, b = _line_span(text, s, e)
            nl = '\r\n' if '\r\n' in text else '\n'
            edits.append((a, b, '\t' + new + nl))
            placed.append((path, name, (at[0], at[1]), ref))
        if lib_inserts:
            for _, ls, le in iter_blocks(text, 'lib_symbols', depth=1):
                nl = '\r\n' if '\r\n' in text else '\n'
                close_line = text.rfind('\n', ls, le - 1) + 1     # lib_symbols 를 닫는 ')' 줄의 시작
                edits.append((close_line, close_line, ''.join('\t\t' + d + nl for _lid, d in lib_inserts)))
                break
        if edits:
            edits.sort(key=lambda t: t[0])
            parts, pos = [], 0
            for a, b, rep in edits:
                parts.append(text[pos:a])
                parts.append(rep)
                pos = b
            parts.append(text[pos:])
            out[path] = ''.join(parts)
    return out, placed


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


PIN_NUMBER_FILL = 0.7        # 핀 번호 글자 폭을 핀 길이의 이 비율 안으로(양 끝에 틈)
PIN_NUMBER_MIN_SIZE = 0.6    # 그 이하로는 줄이지 않는다(mm)
_PIN_NUMBER_RE = re.compile(
    r'\(pin [a-z_]+ [a-z_]+\s*\(at [^)]*\)\s*\(length ([0-9.]+)\)(?:(?!\(pin ).)*?'
    r'\(number "((?:[^"\\]|\\.)*)"\s*\(effects\s*\(font\s*\(size ([0-9.]+) ([0-9.]+)\)', re.S)


def fit_pin_numbers_to_pins(text: str) -> tuple[str, int]:
    """라이브러리 심볼 핀 번호 글자가 핀 길이보다 길면 핀 길이의 `PIN_NUMBER_FILL` 안에 들어가게 줄인다.

    임포터는 OrCAD 핀 번호를 1.7 mm 등으로 옮겨 "CN11_16" 같은 긴 번호가 5 mm 핀보다 길다 — KiCad 는 번호를
    핀 가운데에 그리므로 양쪽으로 삐져나와 몸체 안 핀 이름과 핀 끝 라벨을 덮는다(코퍼스 실측). 핀 번호 위치는
    핀마다 옮길 수 없고(핀 길이를 바꾸면 접속점이 움직임) 크기만 줄인다(최소 `PIN_NUMBER_MIN_SIZE`). 글자 폭은
    획 글꼴 근사(글자당 0.78 × 높이). `.kicad_sch` 의 내장 `lib_symbols` 와 `.kicad_sym` 양쪽에 같은 규칙을 써야
    ERC `lib_symbol_mismatch` 가 생기지 않는다. 연결과 무관. 반환값: (새 텍스트, 줄인 핀 수). 멱등."""
    edits = []
    for m in _PIN_NUMBER_RE.finditer(text):
        length, num, h = float(m.group(1)), m.group(2), float(m.group(3))
        if length <= 0 or not num:
            continue
        fit = length * PIN_NUMBER_FILL / (len(num) * 0.78)
        if fit >= h - 1e-6:
            continue
        k = max(PIN_NUMBER_MIN_SIZE, fit)
        if k >= h - 1e-6:
            continue
        k = round(k, 2)
        w = float(m.group(4)) * k / h
        edits.append((m.start(3), m.end(4), f'{fnum(k)} {fnum(round(w, 2))}'))
    if not edits:
        return text, 0
    return _apply_rel_edits(text, edits), len(edits)


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
                    korean_font=DEFAULT_KOREAN_FONT, snap_labels=True, fit_pin_numbers=True) -> dict:
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
    pin_numbers_fitted = 0
    if fix_korean or fit_pin_numbers:
        for path in sorted(glob.glob(os.path.join(folder, '*.kicad_sym'))):
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            k = f = p = 0
            if fix_korean:
                text, k = fix_cp949_mojibake(text)
                text, f = apply_korean_font(text, korean_font)
            if fit_pin_numbers:
                text, p = fit_pin_numbers_to_pins(text)
            if k or f or p:
                korean_fixed += k
                korean_font_set += f
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(text)
    offpage_converted = 0
    gaps_merged = 0
    labels_moved = 0
    transparent_removed = 0
    wires_normalized = 0
    globals_snapped = 0
    hidden_oriented = 0
    pin_labels_faced = 0
    offpage_unconverted = []   # 커넥터 글자 ≠ 실제 넷 이름이라 그림으로 둔 것(사용자에게 알림)
    if merge_hops or default_wire_width or offpage_labels or dedupe_labels or fix_korean or fit_pin_numbers:
        for path in sheet_paths:
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            n = n2 = n3 = n0 = n4 = n5 = n6 = n7 = n8 = k = f = 0
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
                rep_ = []
                text, n0 = offpage_to_global_labels(text, report=rep_)
                offpage_converted += n0
                sheet = os.path.splitext(os.path.basename(path))[0]
                offpage_unconverted.extend(dict(r, sheet=sheet) for r in rep_)
                text, n5 = snap_global_labels_to_near_ends(text)
                globals_snapped += n5
            if merge_hops:
                text, n = merge_hop_gaps(text)
                if snap_labels:
                    text, n2 = snap_labels_to_wire_ends(text)
            # hop 병합 뒤라야 조각이 합쳐져 중복이 드러난다(조각마다 따로 있던 투명 라벨).
            if dedupe_labels:
                text, n4 = remove_redundant_transparent_labels(text)
            if snap_labels:
                text, n6 = orient_hidden_labels_outward(text)
                hidden_oriented += n6
                text, n7 = face_pin_end_labels_outward(text)
                pin_labels_faced += n7
            if default_wire_width:
                text, n3 = normalize_wire_widths(text)
            if fit_pin_numbers:
                text, n8 = fit_pin_numbers_to_pins(text)
                pin_numbers_fitted += n8
            gaps_merged += n
            labels_moved += n2
            transparent_removed += n4
            wires_normalized += n3
            if n0 or n or n2 or n3 or n4 or n5 or n6 or n7 or n8 or k or f:
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(text)
    wks_path = apply_blank_worksheet(pro_path) if blank_worksheet else None
    return {'sheets': len(sheet_paths), 'offpage_converted': offpage_converted,
            'gaps_merged': gaps_merged, 'labels_moved': labels_moved,
            'transparent_removed': transparent_removed,
            'wires_normalized': wires_normalized, 'korean_fixed': korean_fixed,
            'korean_font_set': korean_font_set, 'worksheet': wks_path,
            'globals_snapped': globals_snapped, 'hidden_oriented': hidden_oriented,
            'pin_labels_faced': pin_labels_faced, 'pin_numbers_fitted': pin_numbers_fitted,
            'offpage_unconverted': offpage_unconverted}


# 넷리스트가 달라지면 이 순서로 위험한 단계를 빼며 다시 한다(앞일수록 많이 적용).
_CLEANUP_FALLBACKS = (
    ('full', {}),
    ('no label snapping', {'snap_labels': False}),
    ('safe steps only', {'snap_labels': False, 'merge_hops': False, 'offpage_labels': False}),
)


def cleanup_project_guarded(pro_path, netlist_fn, **kwargs) -> dict:
    """`cleanup_project` 를 적용하되, 시트 넷리스트(`netlist_fn(root) -> {넷 이름: 핀 집합}`)가 **넷 이름과
    핀까지** 그대로일 때만 남긴다. 다르면 시트·심볼 라이브러리를 원래대로 되돌리고 `_CLEANUP_FALLBACKS`
    순서로 위험한 단계(라벨 끝점 이동 → hop 병합·오프페이지 변환)를 빼며 다시 한다. 마지막 단계(한글·배선 폭·
    도면 양식 = 연결과 무관)도 다르면 전부 되돌린다.

    배경(2026-10-28 코퍼스 실측): 라벨 끝점 이동이 버스 끝점을 빈 배선 끝으로 오인해 넷 라벨을 버스 위로
    옮겨 버스 멤버 넷이 합쳐졌다. 원인은 고쳤지만(`_free_point`) 연결을 건드리는 문자열 수정은 실행할 때마다
    넷리스트로 확인한다. 반환값: `cleanup_project` 결과 + `'guard'`: `{'level': 적용한 단계 이름 | None(되돌림),
    'verified': bool, 'tried': [시도한 단계 이름]}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    root = os.path.splitext(pro_path)[0] + '.kicad_sch'
    paths = sorted(glob.glob(os.path.join(folder, '*.kicad_sch'))
                   + glob.glob(os.path.join(folder, '*.kicad_sym')))
    originals = {}
    for p in paths:
        with open(p, encoding='utf-8', newline='') as fh:
            originals[p] = fh.read()

    def restore():
        for p, t in originals.items():
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(t)
    base = netlist_fn(root)
    tried = []
    res = None
    for level, override in _CLEANUP_FALLBACKS:
        tried.append(level)
        res = cleanup_project(pro_path, **dict(kwargs, **override))
        if netlist_fn(root) == base:
            res['guard'] = {'level': level, 'verified': True, 'tried': tried}
            return res
        restore()
    # 연결을 바꾸는 단계는 모두 되돌렸다(도면 양식은 연결과 무관해 그대로 둔다)
    res = {k: (0 if isinstance(v, int) and k != 'sheets' else v) for k, v in res.items()}
    res['guard'] = {'level': None, 'verified': False, 'tried': tried}
    return res
