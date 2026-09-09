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


def write_fp_lib_table(outdir, lib_nick='PADS'):
    """프로젝트용 fp-lib-table 작성 (${KIPRJMOD}/<lib_nick>.pretty 를 가리킴)."""
    path = os.path.join(outdir, 'fp-lib-table')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(f'(fp_lib_table (version 7)\n'
                  f'  (lib (name "{lib_nick}") (type "KiCad") (uri "${{KIPRJMOD}}/{lib_nick}.pretty") '
                  f'(options "") (descr "PADS import"))\n)\n')
    return path
