"""나이틀리 OrCAD `.DSN` 임포터 출력의 결함 두 가지를 후처리로 고친다.

배경: docs/2026-09-11/[이슈]_[01]_DSN_임포트_결과_교차점_끊김표식_타이틀블록_중복_분석.md 1·2절.

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

`cleanup_project` 는 위 두 가지를 프로젝트 하나에 적용하는 오케스트레이션이다.
`pipeline.run_kicad_project` 가 `kicad_stable.restructure_project` 뒤, ERC/[3]/PDF 전에
호출한다(`PipelineOptions.import_cleanup`, 기본 켜짐). 두 가지 모두 파일 포맷 버전과
무관한 문자열 수준 수정이라(`kicad_stable`이 건드리는 `(version …)`/`generator_version`/
경로 치환과 겹치지 않는다) `--nightly-format`(재구성 끔) 경로에도 그대로 적용된다.
"""
from __future__ import annotations

import glob
import json
import os
import re

from .kicad_sch_reader import iter_blocks
from .kicad_writer import fnum

__all__ = ['merge_hop_gaps', 'blank_worksheet_text', 'apply_blank_worksheet', 'cleanup_project']


_XY_RE = re.compile(r'\(xy\s+(-?[0-9]*\.?[0-9]+)\s+(-?[0-9]*\.?[0-9]+)\)')
_TOL = 1e-6
_MAX_GAP_MM = 0.6


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


def cleanup_project(pro_path, merge_hops=True, blank_worksheet=True) -> dict:
    """`.kicad_pro` 프로젝트 폴더의 모든 `.kicad_sch`(루트 + 하위 시트)에 `merge_hop_gaps` 를
    적용해 제자리에 다시 쓰고, (선택) 빈 도면 양식을 적용한다.

    반환값: `{'sheets': 찾은 시트 파일 수, 'gaps_merged': 병합한 갭 총합, 'worksheet': 쓴
    .kicad_wks 경로 또는 None}`."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    sheet_paths = sorted(glob.glob(os.path.join(folder, '*.kicad_sch')))
    gaps_merged = 0
    if merge_hops:
        for path in sheet_paths:
            with open(path, encoding='utf-8', newline='') as fh:
                text = fh.read()
            new_text, n = merge_hop_gaps(text)
            if n:
                gaps_merged += n
                with open(path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(new_text)
    wks_path = apply_blank_worksheet(pro_path) if blank_worksheet else None
    return {'sheets': len(sheet_paths), 'gaps_merged': gaps_merged, 'worksheet': wks_path}
