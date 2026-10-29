"""PADS 보드 연동: kicad-cli 임포트, .kicad_pcb 읽기, 회로도와 대조, 풋프린트 라이브러리 추출, 보드 재작성.

근거: docs/2026-09-09/[참고]_[01], [검증]_[01], [설계]_[01] "확인된 사실".
"""
from __future__ import annotations
import copy
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from .sexp import parse, child, children, head, Sym, to_text
from .verify import compare_netlists, format_report, alias_nets
from .kicad_writer import NAMESPACE, fp_base as _fp_base


def import_pads_board(kicad_cli, asc_path, out_pcb, report_json=None):
    """kicad-cli pcb import --format pads. 리포트 JSON(dict) 반환. 실패 시 RuntimeError."""
    report_json = report_json or (os.path.splitext(out_pcb)[0] + '_import.json')
    args = [kicad_cli, 'pcb', 'import', '--format', 'pads', '--report-format', 'json',
            '--report-file', report_json, '-o', out_pcb, asc_path]
    # 출력 디코딩은 UTF-8 고정(한국어 로캘의 cp949 디코딩 실패 방지).
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not os.path.isfile(out_pcb):
        raise RuntimeError(f'kicad-cli pcb import failed ({r.returncode}): {r.stdout}\n{r.stderr}')
    with open(report_json, encoding='utf-8') as f:
        return json.load(f)


_CAM_DOC_RE = re.compile(r'CAM_DOC_NAME[^\n]*\n\s*\{(.*?)\n\s*\}', re.S)
_PADS_LAYER_RE = re.compile(r'^LAYER\s+(\d+)\s*\n\s*\{(.*?)\n\s*\}', re.S | re.M)


def _pads_unit_mm(asc_text):
    """PADS ASCII 헤더의 단위 -> mm 배율(BASIC: 1 mil = 38100, MILS, METRIC). 모르면 None."""
    head_line = asc_text[:80].upper()
    if 'BASIC' in head_line:
        return 0.0254 / 38100
    if 'MILS' in head_line:
        return 0.0254
    if 'METRIC' in head_line or 'MM' in head_line:
        return 1.0
    return None


def pads_mask_cam(asc_text):
    """PADS ASCII 보드의 CAM 문서 중 솔더마스크 문서(`DOC_PLOT_TYPE Solder_Mask`)를 읽어 면별 출력 대상을 돌려준다.

    PADS 는 패드스택에 마스크 레이어를 적지 않아도 CAM 의 솔더마스크 문서가 패드(`DOC_PADS`)·비아(`DOC_VIAS`)를
    그리면 그 자리에 개구가 생긴다(오버사이즈 `DOC_PAD_OVERSIZE`). KiCad 의 PADS 임포터는 패드스택에 적힌 마스크만
    옮기고 CAM 은 읽지 않아, 스루홀 패드의 마스크 개구가 빠지거나 한쪽만 남는다(2026-10-29 실측: 샘플 보드 스루홀
    159개 중 101개 개구 없음·58개 뒷면 없음, 코퍼스 PADS 보드 31개 전부 해당 패드스택 있음).

    면 판정: 문서의 `DOC_FABRICATION_LAYER_NUMBER` 레이어 정의 이름에 Top/Bottom 이 있으면 그것으로, 없으면 PADS 기본
    번호(21 = Solder Mask Top, 28 = Solder Mask Bottom). 반환값: `{'top': {'pads': bool, 'vias': bool,
    'oversize_mm': float | None}, 'bottom': {...}}` — 솔더마스크 문서가 없는 면은 키가 없다(판단 근거 없음)."""
    names = {}
    for m in _PADS_LAYER_RE.finditer(asc_text):
        nm = re.search(r'LAYER_NAME\s+([^\n]*)', m.group(2))
        if nm:
            names[int(m.group(1))] = nm.group(1).strip().lower()
    unit = _pads_unit_mm(asc_text)
    out = {}
    for m in _CAM_DOC_RE.finditer(asc_text):
        body = m.group(1)
        if not re.search(r'DOC_PLOT_TYPE\s+Solder_Mask', body):
            continue
        fab = re.search(r'DOC_FABRICATION_LAYER_NUMBER\s+(\d+)', body)
        num = int(fab.group(1)) if fab else None
        name = names.get(num, '')
        side = 'top' if 'top' in name else 'bottom' if 'bottom' in name else \
            {21: 'top', 28: 'bottom'}.get(num)
        if side is None:
            continue

        # DOC_PADS/DOC_VIAS 는 DOC_LAYER_NUMBERS 와 같은 순서의 레이어별 색 목록이다. 구리 레이어 칸만 본다 —
        # 마스크 레이어 자체(21/28) 칸은 패드스택에 직접 적힌 마스크 항목(임포터가 이미 옮김)을 그리는 것이라
        # 판단에 넣으면 안 된다(실측: 밑면 문서 `DOC_VIAS None Black` = 구리 비아는 안 그림, PADS 거버에도 비아 없음).
        nums = re.search(r'DOC_LAYER_NUMBERS\s+([^\n]*)', body)
        nums = [int(x) for x in nums.group(1).split() if x.lstrip('-').isdigit()] if nums else []
        copper_idx = next((k for k, n in enumerate(nums) if n not in (0, num)), None)

        def on(key):
            v = re.search(key + r'\s+([^\n]*)', body)
            if not v or copper_idx is None:
                return False
            cols = v.group(1).split()
            return copper_idx < len(cols) and cols[copper_idx] != 'None'
        over = re.search(r'DOC_PAD_OVERSIZE\s+(-?\d+)', body)
        cur = out.setdefault(side, {'pads': False, 'vias': False, 'oversize_mm': None})
        cur['pads'] |= on('DOC_PADS')
        cur['vias'] |= on('DOC_VIAS')
        if over and unit is not None and cur['oversize_mm'] is None:
            cur['oversize_mm'] = round(int(over.group(1)) * unit, 4)
    return out


_PAD_HEAD_RE = re.compile(r'^\s*\(pad\s+"(?:[^"\\]|\\.)*"\s+(thru_hole|smd|np_thru_hole|connect)\b')
_LAYERS_LINE_RE = re.compile(r'^(\s*\(layers)((?:\s+"[^"]*")*)(\s*\).*)$')


def restore_pad_mask_openings(pcb_text, cam):
    """`pads_mask_cam` 결과대로 `.kicad_pcb` 의 패드·비아 마스크 개구를 복원한다(문자열 수정, 다른 내용 불변).

    - 패드: CAM 이 그 면 솔더마스크에 패드를 그리면, 그 면에 구리가 있는 도금 패드(스루홀 `*.Cu`/`F.Cu`/`B.Cu`,
      SMD 의 자기 면)의 `(layers …)` 에 빠진 `F.Mask`/`B.Mask` 를 더한다. 이미 `*.Mask`·해당 마스크가 있으면 그대로.
      무도금 홀(`np_thru_hole`)·`connect` 패드는 건드리지 않고 수만 센다.
    - 비아: CAM 이 그 면 솔더마스크에 비아를 그리면 그 면 `(tenting (front|back yes))` 를 `no` 로(개구).
    - 패드 크기·구멍·배선·넷·마스크 여유값(`solder_mask_margin`, `pad_to_mask_clearance`)은 바꾸지 않는다.

    반환값: (새 텍스트, {'pads_front': 더한 F.Mask 수, 'pads_back': 더한 B.Mask 수, 'vias_front': 연 비아 앞면 수,
    'vias_back': 연 비아 뒷면 수, 'npth': 건드리지 않은 무도금 홀 수}). 멱등."""
    stats = {'pads_front': 0, 'pads_back': 0, 'vias_front': 0, 'vias_back': 0, 'npth': 0}
    want_pad = {s: bool(cam.get(s, {}).get('pads')) for s in ('top', 'bottom')}
    want_via = {s: bool(cam.get(s, {}).get('vias')) for s in ('top', 'bottom')}
    lines = pcb_text.split('\n')
    pad_kind = None              # 지금 보고 있는 pad 의 종류(그 pad 의 layers 줄을 만나기 전까지)
    in_via = in_tenting = False
    via_indent = ''
    for i, line in enumerate(lines):
        m = _PAD_HEAD_RE.match(line)
        if m:
            pad_kind = m.group(1)
            continue
        if pad_kind is not None:
            lm = _LAYERS_LINE_RE.match(line)
            if lm:
                kind, pad_kind = pad_kind, None
                toks = re.findall(r'"([^"]*)"', lm.group(2))
                if kind == 'np_thru_hole':
                    stats['npth'] += 1
                    continue
                if kind == 'connect':
                    continue
                front = '*.Cu' in toks or 'F.Cu' in toks
                back = '*.Cu' in toks or 'B.Cu' in toks
                add = []
                if front and want_pad['top'] and not ({'F.Mask', '*.Mask'} & set(toks)):
                    add.append('F.Mask')
                    stats['pads_front'] += 1
                if back and want_pad['bottom'] and not ({'B.Mask', '*.Mask'} & set(toks)):
                    add.append('B.Mask')
                    stats['pads_back'] += 1
                if add:
                    lines[i] = lm.group(1) + lm.group(2) + ''.join(f' "{a}"' for a in add) + lm.group(3)
            continue
        stripped = line.lstrip('\t ')
        if stripped.startswith('(via') and (stripped == '(via' or stripped.startswith('(via ')):
            in_via, in_tenting = True, False
            via_indent = line[:len(line) - len(stripped)]
            continue
        if in_via:
            if line == via_indent + ')':
                in_via = in_tenting = False
            elif stripped.startswith('(tenting'):
                in_tenting = True
            elif in_tenting:
                for side, key in (('top', 'front'), ('bottom', 'back')):
                    if want_via[side] and stripped == f'({key} yes)':
                        lines[i] = line.replace(f'({key} yes)', f'({key} no)')
                        stats['vias_' + key] += 1
                if stripped == ')':
                    in_tenting = False
    return '\n'.join(lines), stats


_CLEARANCE_RE = re.compile(r'\(pad_to_mask_clearance\s+(-?[0-9.]+(?:[eE]-?\d+)?)\)')


def mask_clearance_from_cam(cam):
    """PADS CAM 솔더마스크 오버사이즈 -> 그에 해당하는 KiCad 보드 여유값(쪽당, mm) — **적용하지 않고 고지용**.
    PADS 오버사이즈는 **전체 폭**에 더해진다 — PADS 마스크 거버 실측(BASIC 보드, 오버사이즈 0.254): 개구 = 패드 + 0.254
    (스루홀 0.762 -> 1.016, SMD 1.4×1.0 -> 1.654×1.254)이므로 쪽당 절반. 앞·뒤 값이 다르면 작은 쪽. 값이 없으면 0."""
    vals = [v['oversize_mm'] for v in (cam or {}).values() if v.get('pads') and v.get('oversize_mm') is not None]
    return round(min(vals) / 2, 4) if vals else 0.0


def fix_importer_mask_clearance(pcb_text, asc_text, cam=None):
    """KiCad PADS 임포터가 넣은 보드 마스크 여유값(`pad_to_mask_clearance`)이 임포터 고정값이면 0 으로 바꾼다.

    실측(2026-10-29, 코퍼스 PADS 보드 31개 전부): 임포터는 PADS 의 어떤 값도 읽지 않고 숫자 3 을 **파일 단위
    그대로** 넣는다 — METRIC 파일은 3 mm(마스크가 패드보다 6 mm 크게 열림), BASIC 파일은 3 × (1 mil/38100) =
    0.000002 mm. PADS 파일의 ARPTOM/SLMINMASK 를 바꿔 다시 임포트해도 값이 그대로여서 고정값임을 확인했다.

    새 값은 0(KiCad 새 보드 기본값, 제조사가 공정에 맞춰 확장) — CAM 오버사이즈의 절반(`mask_clearance_from_cam`)을
    넣으면 PADS 거버 크기와 같아지지만, 10 mil 오버사이즈면 0.5~0.65 mm 피치 IC 의 핀 사이 마스크(웹)가 사라진다
    (샘플 보드 151쌍). 실제 제조 거버는 출력 때 오버사이즈를 줄인 사례가 있어(0.5 mm QFP 개구가 패드 + 0.1), 사용자
    결정(2026-10-29)으로 0 + CAM 값 고지로 한다. `cam` 은 받지만 값 결정에는 쓰지 않는다(호출 호환).

    값이 `3 × 파일 단위`(허용 오차 1 %)와 같을 때만 바꾼다(사용자가 KiCad 에서 직접 정한 값은 건드리지 않음).
    반환값: (새 텍스트, 바꾸기 전 값 mm | None, 새 값 mm | None)."""
    unit = _pads_unit_mm(asc_text)
    m = _CLEARANCE_RE.search(pcb_text)
    if unit is None or m is None:
        return pcb_text, None, None
    old = float(m.group(1))
    bogus = 3 * unit
    if old == 0 or abs(old - bogus) > bogus * 0.01:
        return pcb_text, None, None
    return pcb_text[:m.start(1)] + '0' + pcb_text[m.end(1):], old, 0.0


# KiCad PADS 임포터는 레이어 종류(LAYER_TYPE)가 정해져 있으면 그대로 따르지만, UNASSIGNED 면 번호별 기본값을 쓴다.
# 그 기본값 중 마스크로 가는 것(실측 2026-10-29, 레이어 5~30 을 하나씩 UNASSIGNED 로 바꿔 임포트): 25 -> F.Mask,
# 28 -> B.Mask. PADS 에서 이 레이어는 CAM 마스크 문서가 그리지 않는 한 마스크와 무관하다(코퍼스 31개 전부 25 = UNASSIGNED).
_IMPORTER_UNASSIGNED_TO_MASK = {25: 'F.Mask', 28: 'B.Mask'}
_DECAL_HEAD_RE = re.compile(r'^(\S+)\s+[IMU]\s+-?[\d.]+\s+-?[\d.]+(?:\s+\d+){5}\s*$')
_PIECE_RE = re.compile(r'^(OPEN|CLOSED|CIRCLE|COPOPN|COPCLS|COPCIR|KPTCLS|KPTCIR|KPTOPN)\s+(\d+)\s+(\S+)\s+\S+\s+(-?\d+)')
_PADS_SECTION_RE = re.compile(r'\n\*(?!REMARK)[A-Z]+\*')


def _pads_section(asc_text, name):
    i = asc_text.find('\n*' + name + '*')
    if i < 0:
        return ''
    m = _PADS_SECTION_RE.search(asc_text, i + 3)
    return asc_text[i:m.start() if m else len(asc_text)]


def pads_spurious_mask_layers(asc_text):
    """임포터가 마스크로 보내지만 PADS 에서는 마스크가 아닌 레이어: {PADS 레이어 번호: KiCad 마스크 레이어}.
    조건: 레이어 종류가 UNASSIGNED(정의가 없으면 판단 안 함)이고, 어떤 CAM 솔더마스크 문서도 그 레이어를 그리지 않음."""
    types = {}
    for m in _PADS_LAYER_RE.finditer(asc_text):
        tm = re.search(r'LAYER_TYPE\s+(\w+)', m.group(2))
        if tm:
            types.setdefault(int(m.group(1)), tm.group(1).upper())
    plotted = set()
    for m in _CAM_DOC_RE.finditer(asc_text):
        if re.search(r'DOC_PLOT_TYPE\s+Solder_Mask', m.group(1)):
            nums = re.search(r'DOC_LAYER_NUMBERS\s+([^\n]*)', m.group(1))
            if nums:
                plotted |= {int(x) for x in nums.group(1).split() if x.isdigit()}
    return {n: lay for n, lay in _IMPORTER_UNASSIGNED_TO_MASK.items()
            if types.get(n) == 'UNASSIGNED' and n not in plotted}


def _decal_pieces(asc_text, layers, unit):
    """`*PARTDECAL*` 에서 `layers` 레이어 위 조각: {디칼 이름: [(폭 mm, [(x, y) mm …]), …]} (PADS 좌표, y 위가 양수)."""
    out = {}
    lines = _pads_section(asc_text, 'PARTDECAL').split('\n')
    cur, i = None, 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        pm = _PIECE_RE.match(line)
        if pm:
            n = int(pm.group(2))
            coords = lines[i:i + n]
            i += n
            if cur is None or int(pm.group(4)) not in layers:
                continue
            try:
                pts = [(float(c.split()[0]) * unit, float(c.split()[1]) * unit) for c in coords]
                width = float(pm.group(3)) * unit
            except (IndexError, ValueError):
                continue
            if pm.group(1) in ('CIRCLE', 'COPCIR', 'KPTCIR') and len(pts) == 2:
                (x1, y1), (x2, y2) = pts
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                r = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 / 2
                pts += [(cx, cy), (cx + r, cy), (cx - r, cy), (cx, cy + r), (cx, cy - r)]
            out.setdefault(cur, []).append((width, pts))
            continue
        hm = _DECAL_HEAD_RE.match(line)
        if hm:
            cur = hm.group(1)
    return out


_PAREN_TOKEN_RE = re.compile(r'\\.|"|\(|\)')


def _sexp_end(text, start):
    """`text[start] == '('` 인 S-식의 끝(닫는 괄호 다음) 위치. 따옴표 안의 괄호는 무시."""
    depth, quoted = 0, False
    for m in _PAREN_TOKEN_RE.finditer(text, start):
        tok = m.group()
        if tok == '"':
            quoted = not quoted
        elif quoted or len(tok) == 2:
            continue
        elif tok == '(':
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.end()
    return len(text)


_FP_GRAPHIC_RE = re.compile(r'\(fp_(?:line|rect|poly|circle|arc)\b')
_GRAPHIC_PT_RE = re.compile(r'\((?:start|end|center|mid|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)\)')


def move_spurious_mask_graphics(pcb_text, asc_text):
    """임포터가 PADS 의 마스크 아닌 레이어(`pads_spurious_mask_layers`) 그림을 F.Mask/B.Mask 에 넣은 것을 `Cmts.User` 로
    옮긴다(지우지 않음 — 임포터가 다른 미지정 레이어 19·20 을 넣는 곳). PADS 에서는 마스크 거버에 나오지 않는 그림이다.

    실측(2026-10-29): 샘플 U1(144핀 QFP) 디칼의 레이어 25(Layer_25, UNASSIGNED) 외곽선(폭 0.001 mm)이 F.Mask 로 들어가
    KiCad DRC 마스크 위반 약 190건을 냈다. 코퍼스 PADS 보드 31개 중 2개에 같은 그림.

    풋프린트 그림 중 마스크 레이어에 있고, 같은 이름 디칼의 해당 레이어 조각과 **모든 좌표점(앞·뒷면 뒤집기 허용, 0.005 mm)
    과 선폭(±0.0005 mm + 1 %)이 맞는** 것만 옮긴다 — 디칼에 없는 마스크 그림은 그대로. 보드 단위 그림(`*LINES*`)은
    코퍼스에 해당 사례가 없어 다루지 않는다. `layer` 토큰만 바꾸는 문자열 수정, 멱등.
    반환값: (새 텍스트, {'moved': 옮긴 그림 수, 'footprints': 바뀐 풋프린트 수, 'layers': [PADS 레이어 번호]})."""
    stats = {'moved': 0, 'footprints': 0, 'layers': []}
    layers = pads_spurious_mask_layers(asc_text)
    unit = _pads_unit_mm(asc_text)
    if not layers or unit is None:
        return pcb_text, stats
    decals = _decal_pieces(asc_text, set(layers), unit)
    if not decals:
        return pcb_text, stats
    stats['layers'] = sorted(layers)
    mask_layers = set(layers.values()) | {'F.Mask', 'B.Mask'}   # 뒷면 배치 풋프린트는 면이 뒤집힌다
    edits = []                                                  # (layer 토큰 시작, 끝)
    pos = 0
    while True:
        i = pcb_text.find('(footprint "', pos)
        if i < 0:
            break
        end = _sexp_end(pcb_text, i)
        pos = end
        name = pcb_text[i + 12:pcb_text.index('"', i + 12)].split(':')[-1]
        pieces = decals.get(name)
        if not pieces:
            continue
        before = len(edits)
        for gm in _FP_GRAPHIC_RE.finditer(pcb_text, i + 1, end):
            g_end = _sexp_end(pcb_text, gm.start())
            body = pcb_text[gm.start():g_end]
            lm = re.search(r'\(layer "([^"]+)"\)', body)
            wm = re.search(r'\(width\s+(-?[\d.]+)\)', body)
            if not lm or lm.group(1) not in mask_layers or not wm:
                continue
            w = float(wm.group(1))
            pts = [(float(x), float(y)) for x, y in _GRAPHIC_PT_RE.findall(body)]
            if not pts:
                continue
            for pw, ppts in pieces:
                if abs(w - pw) > 0.0005 + 0.01 * pw:
                    continue
                cand = [(sx * x, sy * y) for x, y in ppts for sx in (1, -1) for sy in (1, -1)]
                if all(any(abs(px - cx) <= 0.005 and abs(py - cy) <= 0.005 for cx, cy in cand) for px, py in pts):
                    edits.append((gm.start() + lm.start(1), gm.start() + lm.end(1)))
                    break
        if len(edits) > before:
            stats['footprints'] += 1
    if not edits:
        return pcb_text, stats
    parts, last = [], 0
    for a, b in edits:
        parts += [pcb_text[last:a], 'Cmts.User']
        last = b
    parts.append(pcb_text[last:])
    stats['moved'] = len(edits)
    return ''.join(parts), stats


def pads_missing_mask(pcb_text):
    """마스크 개구가 빠진 도금 패드 수(검사만): {'front': 앞면 구리는 있는데 F.Mask 없는 수, 'back': …}.
    CAM 정보가 없는 입력(직접 준 `.kicad_pcb`, CAM 문서 없는 PADS 파일)에서 경고용."""
    _, st = restore_pad_mask_openings(pcb_text, {'top': {'pads': True}, 'bottom': {'pads': True}})
    return {'front': st['pads_front'], 'back': st['pads_back']}


@dataclass
class BoardFootprint:
    ref: str
    fp_id: str                                  # 라이브러리 별명을 뗀 이름 (예: '0603')
    value: str
    layer: str
    rotation: float
    fp_lib_id: str = ''                         # 보드 파일에 적힌 원본 ID (예: 'PADS:0603')
    pads: dict = field(default_factory=dict)    # 패드 번호 -> 넷 이름 | None
    node: list = None                           # 파싱된 (footprint ...) 노드 (원본 트리 공유)


@dataclass
class Board:
    path: str
    tree: list
    footprints: list = field(default_factory=list)
    by_ref: dict = field(default_factory=dict)


def _footprint_rotation(fp):
    """보드 풋프린트의 배치 회전(도). KiCad <=10 은 `(at x y [rot])`, 10.99 나이틀리는
    `(transform (translate x y) (rotate r) (scale 1 1))` 로 쓴다 — 둘 다 읽는다."""
    at = child(fp, 'at')
    if at is not None and len(at) > 3:
        return float(at[3])
    tr = child(fp, 'transform')
    if tr is not None:
        r = child(tr, 'rotate')
        if r is not None and len(r) > 1:
            return float(r[1])
    return 0.0


def _prop(fp, name):
    for p in children(fp, 'property'):
        if len(p) > 2 and p[1] == name:
            return str(p[2])
    return ''


def load_board(pcb_path):
    """.kicad_pcb 파일(KiCad는 백슬래시를 '\\"'/'\\\\' 로 이스케이프하므로 parse()는 escape=True 기본값 사용) 읽기.

    `fp_id` 는 항상 라이브러리 별명을 뗀 이름이다. 우리가 만든 프로젝트 보드처럼 이미
    `(footprint "PADS:0603")` 인 파일을 다시 읽어도 `footprint_map`/풋프린트 필드/PADS.pretty
    파일 이름/[4] 비교가 모두 같은 기준(별명 없는 데칼 이름)을 쓰게 하기 위한 것이다. 원본 ID는
    `fp_lib_id` 에 남긴다.

    또한 파일명에 쓸 수 없는 문자는 `sanitize_fp_name` 으로 '_' 치환한다(예: PADS 데칼
    `[HSH]AMIS_AM400DR/HEADPIN_6` -> `[HSH]AMIS_AM400DR_HEADPIN_6`). KiCad 는 `PADS:이름` 을
    `PADS.pretty/이름.kicad_mod` 로 찾으므로 회로도 필드·보드 ID·파일명이 전부 같은 정규화 이름을
    써야 링크가 살아 있다(2026-09-09 회귀 시험의 한 보드에서 footprint_link_issues 로 확인)."""
    with open(pcb_path, encoding='utf-8') as f:
        tree = parse(f.read())[0]
    board = Board(path=pcb_path, tree=tree)
    for fp in children(tree, 'footprint'):
        rot = _footprint_rotation(fp)
        layer = child(fp, 'layer')
        raw_id = str(fp[1])
        bf = BoardFootprint(ref=_prop(fp, 'Reference'), fp_id=sanitize_fp_name(_fp_base(raw_id)),
                            value=_prop(fp, 'Value'),
                            layer=str(layer[1]) if layer is not None else 'F.Cu', rotation=rot,
                            fp_lib_id=raw_id, node=fp)
        for pad in children(fp, 'pad'):
            n = child(pad, 'net')
            bf.pads[str(pad[1])] = str(n[1]) if n is not None and len(n) > 1 else None
        board.footprints.append(bf)
        if bf.ref:
            board.by_ref[bf.ref] = bf
    return board


def board_netlist(board):
    """보드 패드 -> 넷 이름 집합 ({'REF.PIN', ...}). 넷 없는 패드는 제외한다."""
    nets = {}
    for f in board.footprints:
        for num, net in f.pads.items():
            if net:
                nets.setdefault(net, set()).add(f'{f.ref}.{num}')
    return nets


def footprint_map(board):
    return {f.ref: f.fp_id for f in board.footprints if f.ref}


def placeholder_parts(board, refs, canonical=None):
    """보드 전용 레퍼런스 목록 -> ([PlaceholderPart], 이슈). 보드에 없는 ref 는 건너뛴다.

    패드는 번호 순(숫자 -> 문자)으로, 부품은 레퍼런스 순으로 정렬해 출력이 결정적이게 한다.
    canonical: 넷 이름 정규화 함수(Design.canonical). 없으면 보드 넷 이름을 그대로 쓴다."""
    from .kicad_writer import PlaceholderPart, pad_sort_key
    canon = canonical or (lambda n: n)
    parts, issues = [], []
    for ref in sorted(set(refs)):
        bf = board.by_ref.get(ref)
        if bf is None:
            issues.append(f'placeholder part {ref}: not found on board; skipped')
            continue
        pads = [(num, canon(net) if net else None)
                for num, net in sorted(bf.pads.items(), key=lambda kv: pad_sort_key(kv[0]))]
        parts.append(PlaceholderPart(ref=ref, fp_id=bf.fp_id, value=bf.value or bf.fp_id, pads=pads))
    return parts, issues


def augment_reference(ref_nets, board, refs, canonical=None):
    """기준 넷리스트(사본)에 refs 의 보드 패드 넷을 더한다.

    보드에만 있던 부품을 자리표시 심볼로 회로도에 넣으면 KiCad 넷리스트에는 그 핀들이 생기므로,
    3차 검증([3])·4차 검증([4])의 기준도 같은 만큼 보강해야 비교가 맞는다. 원본은 바꾸지 않는다."""
    canon = canonical or (lambda n: n)
    out = {k: set(v) for k, v in ref_nets.items()}
    for ref in refs:
        bf = board.by_ref.get(ref)
        if bf is None:
            continue
        for num, net in bf.pads.items():
            if net:
                out.setdefault(canon(net), set()).add(f'{ref}.{num}')
    return out


@dataclass
class BoardDiff:
    only_board_refs: list = field(default_factory=list)
    only_sch_refs: list = field(default_factory=list)
    missing_pins: dict = field(default_factory=dict)     # ref -> 회로도 핀 중 보드 패드에 없는 것
    extra_pads: dict = field(default_factory=dict)       # ref -> 보드 패드 중 회로도 핀에 없는 것
    footprint_diff: list = field(default_factory=list)   # (ref, 회로도 OrCAD 풋프린트, 보드 풋프린트)
    net_compare: object = None                           # verify.NetCompare (보드 넷 vs 기준 넷)
    net_reference: str = 'reference netlist'             # net_compare 의 기준: PADS 정답 / 회로도 넷리스트
    net_renames: list = field(default_factory=list)      # 비교 전 기준 표기로 바꾼 보드 넷 (보드 이름, 기준 이름)


def schematic_pins_footprints(design):
    """회로도 객체 -> (레퍼런스별 핀 번호 집합, 레퍼런스별 풋프린트).

    `model.Design`(EDIF 경로)과 `kicad_sch_reader.SchematicView`(KiCad 프로젝트 입력 모드)를
    모두 받는다. 후자는 `pins_by_ref()`/`footprint_by_ref()` 를 직접 제공하므로 그것을 쓰고,
    Design 은 페이지의 인스턴스를 훑는다(덕 타이핑 — 두 메서드만 있으면 무엇이든 된다)."""
    if hasattr(design, 'pins_by_ref') and hasattr(design, 'footprint_by_ref'):
        return dict(design.pins_by_ref()), dict(design.footprint_by_ref())
    # 게이트 단위(unit)로 나뉜 다중 섹션 부품(예: TLP621_4 4게이트 옵토커플러)은 참조지정자가
    # U2A/U2B/U2C/U2D 여도 reference는 모두 'U2'로 합쳐진다. 이때 PCB Footprint 프로퍼티는
    # 보통 첫 섹션(unit=1)에만 실제 값이 붙고 나머지 섹션은 라이브러리 셀의 기본값(다른 값일 수
    # 있음)으로 떨어지는 경우가 있어(샘플의 U2가 그 예: unit1/3=SO16_NARROW, unit2/4=SO8),
    # 페이지 순회 순서에 좌우되는 "첫 발견" 대신 unit이 가장 작은 인스턴스의 footprint를 채택한다.
    sch_pins, sch_fp, fp_unit = {}, {}, {}
    for pg in design.pages:
        for ins in pg.instances:
            sch_pins.setdefault(ins.reference, set()).update(ins.pin_numbers.values())
            u = getattr(ins, 'unit', 1) or 1
            if ins.reference not in fp_unit or u < fp_unit[ins.reference]:
                fp_unit[ins.reference] = u
                sch_fp[ins.reference] = ins.footprint
    return sch_pins, sch_fp


def compare_board(board, design, ref_nets, added_refs=(), net_reference='reference netlist'):
    """보드(임포트 결과) vs 회로도 vs 기준 넷리스트. 차이는 정보이며 오류가 아니다.

    design: `model.Design` 또는 `pins_by_ref()`/`footprint_by_ref()` 를 제공하는 객체
    (`kicad_sch_reader.SchematicView`).
    ref_nets: 기준 넷리스트. None 이면 넷 비교를 건너뛴다(기준 넷리스트가 없는 입력 모드).
    net_reference: ref_nets 가 무엇인지 리포트에 적을 이름('reference netlist' | 'schematic netlist').
    added_refs: 자리표시 심볼로 회로도에 이미 추가한 보드 전용 레퍼런스. 회로도 원본에는
    없지만 출력 회로도에는 있으므로 only_board_refs 에서 뺀다(해소된 차이)."""
    sch_pins, sch_fp = schematic_pins_footprints(design)
    diff = BoardDiff()
    diff.only_board_refs = sorted(set(board.by_ref) - set(sch_pins) - set(added_refs))
    diff.only_sch_refs = sorted(set(sch_pins) - set(board.by_ref))
    for ref in sorted(set(sch_pins) & set(board.by_ref)):
        pads = set(board.by_ref[ref].pads)
        miss = sorted(sch_pins[ref] - pads)
        extra = sorted(pads - sch_pins[ref])
        if miss:
            diff.missing_pins[ref] = miss
        if extra:
            diff.extra_pads[ref] = extra
        # 양쪽 모두 별명을 떼고 파일명 정규화까지 한 뒤 비교한다(회로도 필드는 'PADS:<데칼>' 또는
        # OrCAD 원본 데칼 이름, 보드 fp_id 는 이미 별명 없이 정규화됨).
        if (sanitize_fp_name(_fp_base(sch_fp.get(ref))).upper()
                != sanitize_fp_name(_fp_base(board.by_ref[ref].fp_id)).upper()):
            diff.footprint_diff.append((ref, sch_fp.get(ref, ''), board.by_ref[ref].fp_id))
    if ref_nets is not None:
        renames = []
        diff.net_compare = compare_netlists(alias_nets(board_netlist(board), ref_nets, renames), ref_nets)
        diff.net_renames = renames
    diff.net_reference = net_reference
    return diff


def schematic_nets_for_board(kicad_nets, board):
    """kicad-cli 넷리스트(시트 경로 포함 이름) -> 보드 넷과 대조할 수 있는 형태.

    시트 경로 접두를 떼고, KiCad 자동 이름 넷(`Net-(R1-Pad1)`)은 핀 집합이 같은 보드 넷 이름
    (PADS 자동 이름 `N12345…`)으로 바꾼다. 기준 넷리스트(.asc)가 없을 때 [4] 의 넷 대조 기준으로
    쓴다(EDIF 경로가 `edif_netlist(design)` 을 기준으로 쓰는 것과 같은 역할).
    반환: (넷 딕셔너리, 이슈 목록)."""
    from .kicad_netlist import normalize_kicad_nets
    return normalize_kicad_nets(kicad_nets, board_netlist(board))


def format_board_diff(diff):
    extra_line = f'board pads not in schematic: {len(diff.extra_pads)} footprints ' \
                 + str(dict(list(diff.extra_pads.items())[:6]))
    if len(diff.extra_pads) > 6:
        extra_line += f' (+{len(diff.extra_pads) - 6} more)'
    lines = ['[4] PADS board vs schematic (informational)',
             f'refs only on board: {diff.only_board_refs}',
             f'refs only in schematic: {diff.only_sch_refs}',
             f'schematic pins missing on board footprint: {diff.missing_pins}',
             extra_line,
             f'footprint name differs (ref, schematic, board): {diff.footprint_diff}',
             f'board pad nets vs {diff.net_reference}:']
    if diff.net_compare is None:
        lines.append('  skipped (no reference netlist)')
    else:
        if diff.net_renames:
            shown = ', '.join(f'{a} -> {b}' for a, b in diff.net_renames[:8])
            more = f' (+{len(diff.net_renames) - 8} more)' if len(diff.net_renames) > 8 else ''
            lines.append(f'  board nets matched by pins/spelling (board -> reference): {shown}{more}')
        lines.append(format_report(diff.net_compare).replace('RESULT: ', 'BOARD NETS: '))
    return '\n'.join(lines)


_FP_BAD = re.compile(r'[\\/:*?"<>|]')
BOARD_LIB_VERSION = 20260206


def sanitize_fp_name(fp_id):
    """풋프린트 ID -> 파일명으로 쓸 수 있는 문자열 (윈도우 금지 문자를 '_' 로 치환)."""
    return _FP_BAD.sub('_', fp_id).strip() or 'UNNAMED'


def _set_at_angle(node, delta):
    """(at x y [a]) 의 각도에서 delta 를 뺀다(360 정규화). 각도가 0이 되면 항을 제거한다."""
    at = child(node, 'at')
    if at is None or delta == 0:
        return
    a = (float(at[3]) if len(at) > 3 else 0.0) - delta
    a = a % 360.0
    if abs(a) < 1e-9 or abs(a - 360.0) < 1e-9:
        del at[3:]
    else:
        if len(at) > 3:
            at[3] = int(a) if a == int(a) else a
        else:
            at.append(int(a) if a == int(a) else a)


def _library_footprint(bf, lib_nick):
    """보드 인스턴스 -> 라이브러리용 (footprint ...) 노드 (배치·넷·경로 제거, 회전 정규화)."""
    fp = copy.deepcopy(bf.node)
    fp[1] = bf.fp_id
    rot = bf.rotation
    # 헤더: name, version, generator, layer …  (기존 layer 유지, at/path/uuid 제거)
    fp[2:2] = [[Sym('version'), BOARD_LIB_VERSION], [Sym('generator'), 'orcad2kicad']]
    fp[:] = [c for c in fp if not (isinstance(c, list) and head(c) in ('at', 'transform', 'path', 'uuid'))]
    for p in children(fp, 'property'):
        if p[1] == 'Reference':
            p[2] = 'REF**'
        elif p[1] == 'Value':
            p[2] = bf.fp_id
        _set_at_angle(p, rot)
    for pad in children(fp, 'pad'):
        pad[:] = [c for c in pad if not (isinstance(c, list) and head(c) == 'net')]
        _set_at_angle(pad, rot)
    for t in children(fp, 'fp_text'):
        _set_at_angle(t, rot)
    return fp


_LAYER_SWAP = {'F.': 'B.', 'B.': 'F.'}
_Y_COORD_NODES = ('at', 'start', 'end', 'mid', 'center', 'xy', 'offset')


def _swap_layer_name(name):
    """'B.Cu' <-> 'F.Cu' (앞·뒷면 접두만 바꾼다. '*.Cu' 같은 와일드카드는 그대로)."""
    s = str(name)
    return _LAYER_SWAP[s[:2]] + s[2:] if s[:2] in _LAYER_SWAP else name


def _flip_to_front(fp):
    """뒷면(B.Cu) 인스턴스에서 만든 라이브러리 풋프린트 노드를 앞면 정의로 되돌린다(제자리 수정).

    KiCad 는 풋프린트를 뒷면에 놓을 때 로컬 좌표를 y 축 기준으로 뒤집고(y -> -y), 각도를 반전하며,
    레이어의 F./B. 접두를 바꿔 파일에 저장한다(회전과 무관하게 로컬 좌표는 항상 같다 —
    samples 보드의 0603 앞·뒷면 인스턴스 fp_line 좌표로 확인). 그 역변환:
    모든 좌표 노드(at/start/end/mid/center/xy/offset)의 y 부호 반전, at 의 각도 부호 반전,
    layer/layers 의 F./B. 교환, 텍스트 justify 의 mirror 제거. 크기(size)·drill 은 그대로."""
    def walk(n):
        if not isinstance(n, list):
            return
        h = head(n)
        if h in _Y_COORD_NODES and len(n) > 2 and isinstance(n[2], (int, float)):
            n[2] = -n[2] if n[2] != 0 else 0
            if h == 'at' and len(n) > 3 and isinstance(n[3], (int, float)):
                n[3] = -n[3] if n[3] != 0 else 0
            return
        if h == 'layer' and len(n) > 1:
            n[1] = _swap_layer_name(n[1])
            return
        if h == 'layers':
            n[1:] = [_swap_layer_name(x) for x in n[1:]]
            return
        if h == 'justify':
            n[:] = [x for x in n if not (isinstance(x, Sym) and x == 'mirror')]
            if len(n) == 1:
                n.append(Sym('left'))     # 빈 justify 는 허용되지 않으므로 기본값
            return
        for c in n:
            walk(c)

    for c in fp:
        walk(c)
    # 헤더 layer 는 walk 에서 이미 바뀌었지만 명시적으로 F.Cu 로 못박는다.
    lay = child(fp, 'layer')
    if lay is not None:
        lay[1] = 'F.Cu'
    return fp


def extract_footprint_library(board, outdir, lib_nick='PADS'):
    """고유 풋프린트 ID마다 하나씩 .kicad_mod 로 저장. 회전 0·앞면 인스턴스를 우선 고르고, 앞면
    인스턴스가 하나도 없으면 뒷면 인스턴스를 앞면 정의로 뒤집어(`_flip_to_front`) 저장한다."""
    libdir = os.path.join(outdir, f'{lib_nick}.pretty')
    os.makedirs(libdir, exist_ok=True)
    issues, files = [], {}
    by_id = {}
    for f in board.footprints:
        by_id.setdefault(f.fp_id, []).append(f)
    for fp_id, insts in sorted(by_id.items()):
        front = [f for f in insts if f.layer == 'F.Cu']
        flipped = False
        if not front:
            front = [f for f in insts if f.layer == 'B.Cu']
            flipped = True
            if not front:
                issues.append(f'footprint {fp_id}: no F.Cu/B.Cu instance; skipped')
                continue
        pick = min(front, key=lambda f: (abs(f.rotation) % 360 != 0, f.ref))
        node = _library_footprint(pick, lib_nick)
        if flipped:
            _flip_to_front(node)
            issues.append(f'footprint {fp_id}: no front-side instance; library definition derived '
                          f'from back-side {pick.ref} (flipped to front)')
        path = os.path.join(libdir, sanitize_fp_name(fp_id) + '.kicad_mod')
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(to_text(node) + '\n')
        files[fp_id] = path
    return files, issues


def _reassign_uuids(node, ref):
    """node(주로 하나의 footprint) 서브트리 안의 모든 (uuid "...") 노드를, 그 안에 등장하는
    순서(0부터)와 ref 로 정해지는 고정 UUID로 교체한다 (footprint 레벨 uuid 포함)."""
    counter = [0]

    def walk(n):
        if not isinstance(n, list):
            return
        if head(n) == 'uuid' and len(n) > 1:
            n[1] = str(uuid.uuid5(NAMESPACE, f'board|{ref}|{counter[0]}'))
            counter[0] += 1
            return
        for c in n:
            walk(c)

    walk(node)


def _reassign_top_level_uuids(tree):
    """최상위 segment/via/gr_* 노드의 uuid 를 문서상 등장 순서(0부터)로 정해지는 고정 UUID로 교체한다."""
    idx = 0
    for node in tree[1:]:
        if not (isinstance(node, list) and (head(node) in ('segment', 'via') or str(head(node) or '').startswith('gr_'))):
            continue
        u = child(node, 'uuid')
        if u is not None and len(u) > 1:
            u[1] = str(uuid.uuid5(NAMESPACE, f'board|top|{idx}'))
        idx += 1


def write_project_board(board, out_pcb, lib_nick='PADS', symbol_paths=None):
    """풋프린트 ID를 lib_nick:ID 로 바꾸고 회로도 심볼 path 를 심어 프로젝트용 보드로 저장한다.

    회로도 심볼 경로가 없는 레퍼런스(예: 보드에만 있는 커넥터)는 여기서 이슈로 남기지 않는다 -
    호출부(CLI)가 BoardDiff.only_board_refs 로 별도 보고한다.

    출력은 결정적이어야 한다: kicad-cli pcb import 가 실행마다 무작위 uuid 를 부여하므로 그대로
    저장하면 재실행마다 파일 전체가 달라져 git diff/재현 가능한 빌드가 불가능해진다. 그래서
    풋프린트를 (Reference, fp_id) 순으로 정렬해 쓰고, 모든 uuid 를 (ref, 등장 순서) 기반 고정
    UUID(uuid5, kicad_writer.NAMESPACE)로 재배정한다. 넷/지오메트리는 그대로 유지한다."""
    issues = []
    tree = copy.deepcopy(board.tree)
    symbol_paths = symbol_paths or {}
    for fp in children(tree, 'footprint'):
        # 이미 별명이 붙어 있어도(우리가 만든 보드를 다시 입력으로 준 경우) 항상 떼고 다시 붙인다.
        fp[1] = f'{lib_nick}:{_fp_base(str(fp[1]))}'
        ref = _prop(fp, 'Reference')
        if ref in symbol_paths:
            p = child(fp, 'path')
            if p is None:
                fp.append([Sym('path'), symbol_paths[ref]])
            else:
                p[1] = symbol_paths[ref]
    fp_indices = [i for i, n in enumerate(tree) if isinstance(n, list) and head(n) == 'footprint']
    ordered = sorted((tree[i] for i in fp_indices), key=lambda n: (_prop(n, 'Reference'), str(n[1])))
    for i, fp in zip(fp_indices, ordered):
        tree[i] = fp
        _reassign_uuids(fp, _prop(fp, 'Reference'))
    _reassign_top_level_uuids(tree)
    with open(out_pcb, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(to_text(tree) + '\n')
    return issues


def board_net_mapping(board_nets, sch_nets):
    """보드 넷 이름 -> 회로도 넷 이름 매핑(이름이 다른 것만)과 매칭 못 한 보드 넷 목록.

    board_nets: {이름: {'REF.PIN'}}(`board_netlist`), sch_nets: {이름: frozenset('REF.PIN')}(kicad-cli
    넷리스트). 규칙: (1) 핀 집합이 같은 회로도 넷. (2) 없으면 핀이 겹치는 회로도 넷이 정확히 하나이고,
    보드 넷의 나머지 패드가 회로도 어디에도 없는 핀(마운팅 홀·보드 전용 패드)일 때. (3) 그 밖은 매칭 안 함.
    두 보드 넷이 같은 회로도 이름으로 가면(보드에서 갈린 넷을 회로도가 합친 경우) 둘 다 매칭에서 뺀다."""
    by_pins = {}
    for name, pins in sch_nets.items():
        by_pins.setdefault(frozenset(pins), []).append(name)
    owner = {}
    for name, pins in sch_nets.items():
        for p in pins:
            owner.setdefault(p, set()).add(name)
    target = {}
    unmatched = []
    for name, pins in board_nets.items():
        pins = frozenset(pins)
        exact = by_pins.get(pins)
        if exact and len(exact) == 1:
            target[name] = exact[0]
            continue
        hit = set()
        for p in pins:
            hit |= owner.get(p, set())
        rest = [p for p in pins if p not in owner]
        if len(hit) == 1 and all(p not in owner for p in rest) and len(rest) < len(pins):
            target[name] = next(iter(hit))
        else:
            unmatched.append(name)
    counts = {}
    for t in target.values():
        counts[t] = counts.get(t, 0) + 1
    mapping = {}
    for b, t in target.items():
        if counts[t] > 1:
            unmatched.append(b)
        elif b != t:
            mapping[b] = t
    return mapping, unmatched


def rename_board_nets(pcb_path, mapping):
    """`.kicad_pcb` 의 넷 참조(`(net "이름")`, 옛 형식 `(net N "이름")`, `(net_name "이름")`)를 mapping 대로
    바꿔 제자리에 다시 쓴다. 넷이 아닌 글자(gr_text 등)는 건드리지 않는다. 반환값: 바꾼 참조 수."""
    if not mapping:
        return 0
    with open(pcb_path, encoding='utf-8') as fh:
        tree = parse(fh.read())[0]
    count = 0

    def walk(node):
        nonlocal count
        if not isinstance(node, list):
            return
        if node and head(node) in ('net', 'net_name'):
            for i in range(1, len(node)):
                v = node[i]
                if isinstance(v, str) and not isinstance(v, Sym) and mapping.get(v, v) != v:
                    node[i] = mapping[v]
                    count += 1
            return
        for c in node:
            walk(c)
    walk(tree)
    if count:
        with open(pcb_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(to_text(tree) + '\n')
    return count


def write_fp_lib_table(outdir, lib_nick='PADS'):
    """프로젝트용 fp-lib-table 작성 (${KIPRJMOD}/<lib_nick>.pretty 를 가리킴)."""
    path = os.path.join(outdir, 'fp-lib-table')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(f'(fp_lib_table (version 7)\n'
                  f'  (lib (name "{lib_nick}") (type "KiCad") (uri "${{KIPRJMOD}}/{lib_nick}.pretty") '
                  f'(options "") (descr "PADS import"))\n)\n')
    return path
