"""Design -> KiCad 파일 (포맷 버전 20231120).

좌표: EDIF 1단위 = 0.254 mm. 심볼 라이브러리는 y 위가 양수(EDIF와 같음), 회로도는 y 아래가 양수(부호 반전).
orientation 매핑과 문법은 docs/2026-09-08/[설계]_[02] "kicad-cli로 확인한 사실" 참조.
"""
from __future__ import annotations
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from .model import Graphic, PIN_TYPE_TO_KICAD
from .geometry import ORIENT, snap_to_wires, transform_point, pin_world

FORMAT_VERSION = 20231120
GENERATOR = 'orcad2kicad'
LIB_NICK = 'orcad'
MM = 0.254
NAMESPACE = uuid.UUID('6f2d3c1e-7b4a-4c5d-9e8f-0a1b2c3d4e5f')   # 결정적 UUID용 고정 네임스페이스

# EDIF orientation -> (KiCad 회전각, mirror 축). 근거: HANDOVER 4장 + kicad-cli 스모크 테스트.
# MXR90/MYR90: KiCad는 배치 시 "회전 후 미러"를 적용하는데(반사는 회전 방향을 뒤집는다: 미러 M에 대해
# M*R(θ)*M = R(-θ)), 회전+미러가 결합된 이 두 orientation만 90°가 아니라 270°(=-90°)를 써야
# geometry.py(ORIENT, 미러 후 회전 가정으로 유도한 pin_world)와 KiCad가 실제로 그리는 핀 위치가 일치한다.
# 3차 검증(kicad-cli 넷리스트 비교)에서 R6/R8/R10/R12/C1/C2(모두 MXR90 또는 MYR90) 6개 부품이
# 두 핀 다 unconnected-로 나와 발견 — 90°로는 핀이 배선 끝점과 어긋난다.
ORIENT_KICAD = {
    'R0': (0, None), 'R90': (90, None), 'R180': (180, None), 'R270': (270, None),
    'MX': (0, 'x'), 'MY': (0, 'y'), 'MXR90': (270, 'x'), 'MYR90': (270, 'y'),
}

_SAN = re.compile(r'[^A-Za-z0-9_.\-]')

# ---------- 심볼 필드(Reference/Value) 회전·정렬 ----------
#
# KiCad 는 심볼 필드의 각도·정렬을 **심볼 배치(회전/미러)에 상대적으로** 해석해 그린다.
# 아래 표는 tools/probe_field_rotation.py 로 KiCad 10.0.6 렌더러에서 실측한
# (배치 회전, 미러, 저장 각도, 저장 hjust, 저장 vjust) -> (그려진 각도, hjust, vjust) 이다.
# 근거·읽는 법: docs/2026-09-09/[참고]_[02]_KiCad_심볼_필드_회전_실측표.md
# 배치 8종 각각에서 저장값 8조합 -> 그려진 8조합이 전단사(bijection)라 역함수가 유일하다.
MEASURED_FIELD_DRAW = {
    (0, None, 0, 'left', 'top'): (0, 'left', 'top'),
    (0, None, 0, 'left', 'bottom'): (0, 'left', 'bottom'),
    (0, None, 0, 'right', 'top'): (0, 'right', 'top'),
    (0, None, 0, 'right', 'bottom'): (0, 'right', 'bottom'),
    (0, None, 90, 'left', 'top'): (90, 'left', 'top'),
    (0, None, 90, 'left', 'bottom'): (90, 'left', 'bottom'),
    (0, None, 90, 'right', 'top'): (90, 'right', 'top'),
    (0, None, 90, 'right', 'bottom'): (90, 'right', 'bottom'),
    (90, None, 0, 'left', 'top'): (90, 'left', 'top'),
    (90, None, 0, 'left', 'bottom'): (90, 'left', 'bottom'),
    (90, None, 0, 'right', 'top'): (90, 'right', 'top'),
    (90, None, 0, 'right', 'bottom'): (90, 'right', 'bottom'),
    (90, None, 90, 'left', 'top'): (0, 'right', 'bottom'),
    (90, None, 90, 'left', 'bottom'): (0, 'right', 'top'),
    (90, None, 90, 'right', 'top'): (0, 'left', 'bottom'),
    (90, None, 90, 'right', 'bottom'): (0, 'left', 'top'),
    (180, None, 0, 'left', 'top'): (0, 'right', 'bottom'),
    (180, None, 0, 'left', 'bottom'): (0, 'right', 'top'),
    (180, None, 0, 'right', 'top'): (0, 'left', 'bottom'),
    (180, None, 0, 'right', 'bottom'): (0, 'left', 'top'),
    (180, None, 90, 'left', 'top'): (90, 'right', 'bottom'),
    (180, None, 90, 'left', 'bottom'): (90, 'right', 'top'),
    (180, None, 90, 'right', 'top'): (90, 'left', 'bottom'),
    (180, None, 90, 'right', 'bottom'): (90, 'left', 'top'),
    (270, None, 0, 'left', 'top'): (90, 'right', 'bottom'),
    (270, None, 0, 'left', 'bottom'): (90, 'right', 'top'),
    (270, None, 0, 'right', 'top'): (90, 'left', 'bottom'),
    (270, None, 0, 'right', 'bottom'): (90, 'left', 'top'),
    (270, None, 90, 'left', 'top'): (0, 'left', 'top'),
    (270, None, 90, 'left', 'bottom'): (0, 'left', 'bottom'),
    (270, None, 90, 'right', 'top'): (0, 'right', 'top'),
    (270, None, 90, 'right', 'bottom'): (0, 'right', 'bottom'),
    (0, 'x', 0, 'left', 'top'): (0, 'left', 'bottom'),
    (0, 'x', 0, 'left', 'bottom'): (0, 'left', 'top'),
    (0, 'x', 0, 'right', 'top'): (0, 'right', 'bottom'),
    (0, 'x', 0, 'right', 'bottom'): (0, 'right', 'top'),
    (0, 'x', 90, 'left', 'top'): (90, 'right', 'top'),
    (0, 'x', 90, 'left', 'bottom'): (90, 'right', 'bottom'),
    (0, 'x', 90, 'right', 'top'): (90, 'left', 'top'),
    (0, 'x', 90, 'right', 'bottom'): (90, 'left', 'bottom'),
    (0, 'y', 0, 'left', 'top'): (0, 'right', 'top'),
    (0, 'y', 0, 'left', 'bottom'): (0, 'right', 'bottom'),
    (0, 'y', 0, 'right', 'top'): (0, 'left', 'top'),
    (0, 'y', 0, 'right', 'bottom'): (0, 'left', 'bottom'),
    (0, 'y', 90, 'left', 'top'): (90, 'left', 'bottom'),
    (0, 'y', 90, 'left', 'bottom'): (90, 'left', 'top'),
    (0, 'y', 90, 'right', 'top'): (90, 'right', 'bottom'),
    (0, 'y', 90, 'right', 'bottom'): (90, 'right', 'top'),
    (270, 'x', 0, 'left', 'top'): (90, 'left', 'bottom'),
    (270, 'x', 0, 'left', 'bottom'): (90, 'left', 'top'),
    (270, 'x', 0, 'right', 'top'): (90, 'right', 'bottom'),
    (270, 'x', 0, 'right', 'bottom'): (90, 'right', 'top'),
    (270, 'x', 90, 'left', 'top'): (0, 'left', 'bottom'),
    (270, 'x', 90, 'left', 'bottom'): (0, 'left', 'top'),
    (270, 'x', 90, 'right', 'top'): (0, 'right', 'bottom'),
    (270, 'x', 90, 'right', 'bottom'): (0, 'right', 'top'),
    (270, 'y', 0, 'left', 'top'): (90, 'right', 'top'),
    (270, 'y', 0, 'left', 'bottom'): (90, 'right', 'bottom'),
    (270, 'y', 0, 'right', 'top'): (90, 'left', 'top'),
    (270, 'y', 0, 'right', 'bottom'): (90, 'left', 'bottom'),
    (270, 'y', 90, 'left', 'top'): (0, 'right', 'top'),
    (270, 'y', 90, 'left', 'bottom'): (0, 'right', 'bottom'),
    (270, 'y', 90, 'right', 'top'): (0, 'left', 'top'),
    (270, 'y', 90, 'right', 'bottom'): (0, 'left', 'bottom'),
}

_FLIP_H = {'left': 'right', 'right': 'left', '': '', None: ''}
_FLIP_V = {'top': 'bottom', 'bottom': 'top', '': '', None: ''}


def justify_parts(justify):
    """EDIF justify 문자열 -> (hjust, vjust). 없는 축은 ''(가운데 정렬)."""
    j = (justify or '').upper()
    h = 'left' if 'LEFT' in j else ('right' if 'RIGHT' in j else '')
    v = 'top' if 'UPPER' in j else ('bottom' if 'LOWER' in j else '')
    return h, v


def justify_str(h, v):
    """(hjust, vjust) -> KiCad (justify ...) 인자 문자열. 둘 다 비면 None."""
    s = ' '.join(x for x in (h or '', v or '') if x)
    return s or None


# EDIF orientation -> 텍스트가 놓이는 회전각(도, 반시계). 미러 orientation 은 KiCad 텍스트에
# 대응물이 없어(KiCad 는 글자를 항상 정상 방향으로 그린다) 회전 성분만 쓴다. 샘플에는 없다.
_EDIF_TEXT_ROT = {'R0': 0, 'R90': 90, 'R180': 180, 'R270': 270,
                  'MX': 0, 'MY': 0, 'MXR90': 90, 'MYR90': 90}


def edif_text_style(orientation, justify='', default=('left', 'top')):
    """EDIF 표시 (orientation, justify) -> KiCad 절대 (각도, hjust, vjust).

    EDIF 의 display origin 은 텍스트 상자의 justify 점이고 orientation 은 그 점을 중심으로
    상자를 (y 위가 양수인 좌표계에서 반시계로) 돌린다. KiCad 텍스트는 항상 읽을 수 있는
    방향으로 그려지므로 각도는 0/90 둘 중 하나로 정규화하고, 180 도분의 회전은 hjust·vjust 를
    각각 뒤집는 것으로 표현한다. justify 가 ''(EDIF 에 없음)면 default 를 쓴다."""
    h, v = justify_parts(justify) if justify else default
    rot = _EDIF_TEXT_ROT.get((orientation or 'R0').upper(), 0)
    if rot >= 180:
        h, v = _FLIP_H[h], _FLIP_V[v]
    return rot % 180, h, v


def _rotate_style(angle, h, v, rot):
    """텍스트 (각도, 정렬) 에 배치 회전 rot 을 더하고 0/90 으로 정규화한다."""
    a = (angle + rot) % 360
    if a >= 180:
        a -= 180
        h, v = _FLIP_H[h], _FLIP_V[v]
    return a, h, v


def _mirror_style(angle, h, v, mirror):
    """배치 미러가 필드 정렬에 주는 영향(실측표에서 유도). 각도는 바뀌지 않는다.

    mirror x(가로축 대칭): 가로 텍스트는 vjust 가, 세로 텍스트는 hjust 가 뒤집힌다.
    mirror y(세로축 대칭): 그 반대."""
    if mirror == 'x':
        return (angle, _FLIP_H[h], v) if angle == 90 else (angle, h, _FLIP_V[v])
    if mirror == 'y':
        return (angle, h, _FLIP_V[v]) if angle == 90 else (angle, _FLIP_H[h], v)
    return angle, h, v


def drawn_field(kicad_rot, mirror, angle, h, v):
    """저장값 -> 그려지는 (각도, hjust, vjust). 실측표를 재현한다(회전 먼저, 그 다음 미러)."""
    a, hh, vv = _rotate_style(angle, h, v, kicad_rot)
    return _mirror_style(a, hh, vv, mirror)


def field_placement(kicad_rot, mirror, angle, h, v):
    """원하는 **절대** (각도, hjust, vjust) 를 배치 (kicad_rot, mirror) 에서 얻기 위한 저장값.

    drawn_field 의 역함수. 미러 단계는 각도를 바꾸지 않는 대합(involution)이라 그대로 되돌리고,
    회전은 -kicad_rot 만큼 되돌린다."""
    a, hh, vv = _mirror_style(angle % 180, h, v, mirror)
    return _rotate_style(a, hh, vv, -kicad_rot)


def mm(v):
    return round(v * MM, 4)


def sym_xy(p):
    """심볼 좌표(y 위) -> .kicad_sym 좌표(y 위)"""
    return (mm(p[0]), mm(p[1]))


def sch_xy(p):
    """페이지 좌표(y 위) -> .kicad_sch 좌표(y 아래)"""
    return (mm(p[0]), mm(-p[1]))


def sanitize(name):
    s = _SAN.sub('_', name.strip())
    return s or 'UNNAMED'


def fp_base(name):
    """풋프린트 이름에서 라이브러리 별명을 뗀다('PADS:0603' -> '0603').

    보드가 이미 별명이 붙은 `.kicad_pcb`(우리가 앞선 실행에서 만든 프로젝트 보드 등)일 때
    접두사를 두 번 붙여 'PADS:PADS:0603' 이 되는 것을 막는 방어용. OrCAD 풋프린트 이름에는
    ':' 가 없어 EDIF 경로에는 영향이 없다. (kicad_board 도 이 함수를 쓴다 - 그쪽이 이 모듈을
    임포트하므로 정의는 여기 한 곳에만 둔다.)"""
    return str(name or '').rsplit(':', 1)[-1]


def stable_uuid(*parts):
    return str(uuid.uuid5(NAMESPACE, '|'.join(str(p) for p in parts)))


def fnum(v):
    """S-식 숫자 출력: 정수면 정수로, 아니면 소수 4자리 이내."""
    if isinstance(v, int):
        return str(v)
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def qstr(s):
    r"""KiCad S-식 문자열 리터럴. 줄바꿈은 `\n` 으로 이스케이프한다 - 생 개행을 그대로 쓰면
    파일 문법이 깨지고, KiCad 는 텍스트 안의 `\n` 을 줄바꿈으로 렌더링한다."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


# ---------- KiCad 심볼 모델 ----------

@dataclass
class KPin:
    number: str
    name: str
    etype_kicad: str
    at: tuple                  # (x_mm, y_mm) 접속점 (심볼 좌표)
    angle: int                 # 0 몸체가 오른쪽, 90 위, 180 왼쪽, 270 아래
    length: float
    hidden: bool = False
    shape: str = 'line'


@dataclass
class KUnit:
    graphics: list = field(default_factory=list)   # [Graphic] (EDIF 단위 좌표, 심볼 좌표계)
    pins: list = field(default_factory=list)       # [KPin]


@dataclass
class KSymbol:
    name: str
    ref_prefix: str = 'U'
    value: str = ''
    footprint: str = ''
    units: dict = field(default_factory=dict)      # unit -> KUnit
    pin_numbers_visible: bool = True
    pin_names_visible: bool = True
    is_power: bool = False
    power_net: str = ''
    ref_pos: tuple = None
    value_pos: tuple = None


@dataclass
class SymbolSet:
    symbols: dict = field(default_factory=dict)          # name -> KSymbol
    instance_symbol: dict = field(default_factory=dict)  # instance edif id -> (name, unit)
    power_symbol: dict = field(default_factory=dict)     # (cell_key, canonical net) -> name
    power_offset: dict = field(default_factory=dict)     # cell_key -> (ox, oy) 심볼 좌표
    offpage_offset: dict = field(default_factory=dict)   # cell_key -> (ox, oy)
    issues: list = field(default_factory=list)


def _inverse(orientation, v):
    """페이지 벡터 v 를 orientation 역변환(직교행렬 -> 전치)해 심볼 좌표 벡터로."""
    (a, b), (c, d) = ORIENT[orientation]
    return (a * v[0] + c * v[1], b * v[0] + d * v[1])


def _pin_angle(connect, body):
    dx, dy = body[0] - connect[0], body[1] - connect[1]
    if dx == 0 and dy == 0:
        return 0
    if abs(dx) >= abs(dy):
        return 0 if dx > 0 else 180
    return 90 if dy > 0 else 270


def _pin_length(connect, body):
    return mm(abs(body[0] - connect[0]) + abs(body[1] - connect[1]))


def _make_unit(sym, numbers, design, issues=None):
    """model.Symbol 의 그래픽·핀을 KUnit 으로. numbers: port_id -> 핀 번호.
    issues 를 넘기면, 숨은 전원핀인데 IMPLICITPORTCLASS 도 핀 이름도 없어 접속 대상 넷을
    알 수 없는 경우(이름이 빈 문자열로 남음) 이슈로 남긴다."""
    u = KUnit(graphics=list(sym.graphics))
    for pin in sym.pins:
        hidden = bool(pin.hidden and pin.etype.upper() in ('POWER', 'PWR'))
        name = pin.name or '~'
        if hidden:
            base = pin.implicit_class or pin.name
            name = design.canonical(base)
            if not name and issues is not None:
                issues.append(f'symbol {sym.cell_name}: hidden power pin {pin.port_id} has no name '
                              f'(no implicit_class, no pin name) — connected net unknown')
        u.pins.append(KPin(number=numbers.get(pin.port_id, pin.number or pin.port_id.lstrip('&')),
                           name=name, etype_kicad=PIN_TYPE_TO_KICAD.get(pin.etype.upper(), 'passive'),
                           at=sym_xy(pin.connect), angle=_pin_angle(pin.connect, pin.body),
                           length=0.0 if hidden else _pin_length(pin.connect, pin.body),
                           hidden=hidden, shape='inverted' if pin.shape == 'inverted' else 'line'))
    return u


def _unit_signature(unit):
    return (tuple(sorted((p.number, p.name, p.at, p.angle, p.etype_kicad, p.hidden, p.length, p.shape)
                         for p in unit.pins)),
            tuple((g.kind, tuple(g.pts), g.text) for g in unit.graphics))


def _variant_name(base, unit_cells):
    """base 심볼 이름에 대해 아직 쓰이지 않은 접미사가 붙은 변형 이름을 만든다 (base_2, base_3, ...)."""
    n = 2
    while f'{base}_{n}' in unit_cells:
        n += 1
    return f'{base}_{n}'


def build_symbols(design):
    """인스턴스를 Source Package 로 묶어 KiCad 멀티유닛 심볼을 만든다."""
    ss = SymbolSet()
    # 1) 부품 심볼: (그룹 이름, 유닛) -> 셀 키. 같은 유닛에 다른 셀/다른 핀 번호가 오면 변형 심볼로 분리한다.
    order = []                     # 그룹 이름 등장 순서
    unit_cells = {}                # group -> {unit: cell_key}
    unit_numbers = {}              # (group, unit) -> {port_id: number} (그 그룹·유닛을 만든 인스턴스 기준)
    cell_group = {}                # cell_key -> group (첫 배정)
    base_groups = {}               # base 이름 -> 그 base 에서 파생된 그룹 이름들 (변형 재사용 탐색용)
    for pg in design.pages:
        for ins in pg.instances:
            sym = design.symbols[ins.symbol]
            base = sanitize(sym.source_package or sym.cell_name)
            group = cell_group.get(ins.symbol)
            if group is None:
                group = base
                # 같은 그룹·같은 유닛을 이미 다른 셀이 차지하면 내용 비교 후 분리
                taken = unit_cells.get(group, {}).get(ins.unit)
                if taken is not None and taken != ins.symbol:
                    a = _make_unit(design.symbols[taken], unit_numbers[(group, ins.unit)], design, ss.issues)
                    b = _make_unit(sym, ins.pin_numbers, design, ss.issues)
                    if _unit_signature(a) != _unit_signature(b):
                        group = _variant_name(base, unit_cells)
                        ss.issues.append(f'{ins.reference}: cell {ins.symbol} differs from {taken}; written as symbol {group}')
                    else:
                        ss.issues.append(f'{ins.reference}: cell {ins.symbol} identical to {taken}; merged into {group}')
                        cell_group[ins.symbol] = group
                        ss.instance_symbol[ins.edif_id] = (group, ins.unit)
                        continue
                cell_group[ins.symbol] = group
            if group not in unit_cells:
                unit_cells[group] = {}
                order.append(group)
                base_groups.setdefault(base, []).append(group)
            if ins.unit not in unit_cells[group]:
                unit_cells[group][ins.unit] = ins.symbol
                unit_numbers[(group, ins.unit)] = dict(ins.pin_numbers)
            elif unit_numbers[(group, ins.unit)] != ins.pin_numbers:
                # 같은 셀이라도 이 인스턴스만 핀 번호가 다르면(예: fallback 채번 차이) 별도 변형
                # 심볼로 분리한다 — 인스턴스가 매핑된 심볼 유닛의 핀 번호는 항상 그 인스턴스의
                # pin_numbers 와 같아야 넷리스트가 맞는다. 같은 번호를 쓰는 변형이 이미 있으면 재사용.
                variant = next((g for g in base_groups.get(base, [])
                               if unit_cells.get(g, {}).get(ins.unit) == ins.symbol
                               and unit_numbers.get((g, ins.unit)) == ins.pin_numbers), None)
                if variant is None:
                    variant = _variant_name(base, unit_cells)
                    unit_cells[variant] = {ins.unit: ins.symbol}
                    unit_numbers[(variant, ins.unit)] = dict(ins.pin_numbers)
                    order.append(variant)
                    base_groups.setdefault(base, []).append(variant)
                    ss.issues.append(f'{ins.reference}: pin numbers differ from first instance of {group} unit {ins.unit}; written as symbol {variant}')
                else:
                    ss.issues.append(f'{ins.reference}: pin numbers differ from first instance of {group} unit {ins.unit}; reused symbol {variant}')
                group = variant
            ss.instance_symbol[ins.edif_id] = (group, ins.unit)
    for group in order:
        cells = unit_cells[group]
        first = design.symbols[cells[min(cells)]]
        ks = KSymbol(name=group, ref_prefix=first.ref_prefix, value=first.value or first.source_package or first.cell_name,
                     footprint=first.footprint, pin_numbers_visible=first.pin_numbers_visible,
                     pin_names_visible=first.pin_names_visible, ref_pos=first.ref_pos, value_pos=first.value_pos)
        # homogeneous 멀티유닛: 한 셀의 PackagePortNumbers 길이만큼 유닛을 만든다 (배치 안 된 유닛 포함)
        npkg = max((len(p.package_numbers) for p in first.pins), default=1)
        units = set(cells)
        if len(set(cells.values())) == 1 and npkg > 1:
            units |= set(range(1, npkg + 1))
        for u in sorted(units):
            cell_key = cells.get(u, cells[min(cells)])
            sym = design.symbols[cell_key]
            numbers = unit_numbers.get((group, u))
            if numbers is None:
                numbers = {p.port_id: (p.package_numbers[u - 1] if u - 1 < len(p.package_numbers) and p.package_numbers[u - 1]
                                       else (p.number or p.name or p.port_id.lstrip('&'))) for p in sym.pins}
            ks.units[u] = _make_unit(sym, numbers, design, ss.issues)
        lo, hi = min(ks.units), max(ks.units)
        for u in range(1, hi + 1):
            if u not in ks.units:
                ks.units[u] = KUnit()
                ss.issues.append(f'symbol {group}: unit {u} not present in design; empty unit written')
        ss.symbols[group] = ks
    # 2) 전원 심볼: (셀, 정규 넷) 마다 하나. 접속점이 원점이 되도록 그래픽 이동.
    # 이름은 PWR_{cell_name}_{net} 인데, 서로 다른 pagePort 셀이 같은 cell_name·net 을 가지면
    # (예: 여러 GND_POWER/GND 계열 심볼) 이름이 충돌한다. 이동된 그래픽이 같으면 같은 이름으로
    # 합치고, 다르면 접미사를 붙인 변형 이름으로 분리해 앞서 쓴 심볼을 덮어쓰지 않게 한다.
    power_variants = {}   # base_name -> [(name, owner_cell_key, gfx_sig), ...]
    for pg in design.pages:
        for pp in pg.power_ports:
            off = _inverse(pp.orientation, (pp.pos[0] - pp.origin[0], pp.pos[1] - pp.origin[1]))
            prev = ss.power_offset.setdefault(pp.symbol, off)
            if prev != off:
                ss.issues.append(f'page {pg.name}: power symbol {pp.symbol} connect offset {off} differs from {prev}')
            net = design.canonical(pp.net)
            if net != pp.net and not any(pp.net in i and 'renamed' in i for i in ss.issues):
                ss.issues.append(f'power symbol {pp.net} renamed to canonical net {net} (same net per IMPLICITPORTCLASS)')
            key = (pp.symbol, net)
            if key in ss.power_symbol:
                continue
            cell = design.symbols[pp.symbol]
            base_name = sanitize(f'PWR_{cell.cell_name}_{net}')
            gfx = [Graphic(g.kind, [(x - off[0], y - off[1]) for (x, y) in g.pts], g.text, g.width, g.text_height, g.justify, g.rotation)
                   for g in cell.graphics]
            gfx_sig = [(g.kind, tuple(g.pts)) for g in gfx]
            variants = power_variants.setdefault(base_name, [])
            match = next((v for v in variants if v[2] == gfx_sig), None)
            if match is not None:
                ss.power_symbol[key] = match[0]
                continue
            if not variants:
                name = base_name
            else:
                name = _variant_name(base_name, ss.symbols)
                ss.issues.append(f'power symbol {base_name}: cell {pp.symbol} differs from {variants[0][1]}; '
                                 f'written as {name}')
            ks = KSymbol(name=name, ref_prefix='#PWR', value=net, is_power=True, power_net=net,
                         pin_numbers_visible=False, pin_names_visible=False)
            ks.units[1] = KUnit(graphics=gfx, pins=[KPin(number='1', name=net, etype_kicad='power_in', at=(0.0, 0.0),
                                                         angle=0, length=0.0, hidden=True)])
            ks.ref_pos = (0, -20)
            ks.value_pos = (0, 15)
            ss.symbols[name] = ks
            variants.append((name, pp.symbol, gfx_sig))
            ss.power_symbol[key] = name
        for op in pg.offpages:
            off = _inverse(op.orientation, (op.pos[0] - op.origin[0], op.pos[1] - op.origin[1]))
            prev = ss.offpage_offset.setdefault(op.symbol, off)
            if prev != off:
                ss.issues.append(f'page {pg.name}: offpage {op.symbol} connect offset {off} differs from {prev}')
    return ss


# ---------- 보드 전용 부품의 자리표시 심볼 (3단계-B Task 2) ----------
#
# PADS 보드에만 있고 OrCAD 회로도에는 없는 부품(샘플의 J19/J20 전원 터미널)을 KiCad 회로도에
# 넣기 위한 최소 심볼이다. 사각형 몸체 + 왼쪽에 2.54 mm 간격의 핀들만 있고, 핀 이름은 보드
# 패드가 물린 넷 이름(없으면 패드 번호)을 쓴다. 이 부품들은 Design.pages 가 아니라 writer 의
# 추가 입력(write_project(extra_parts=...))으로 들어와 별도 페이지 99-PCB-ONLY 에만 나타나므로
# 1·2차 검증(EDIF/지오메트리)에는 전혀 영향을 주지 않는다.

PLACEHOLDER_PAGE = '99-PCB-ONLY'
PLACEHOLDER_PITCH = 10        # 핀 간격 (EDIF 단위) = 2.54 mm
PLACEHOLDER_BODY_W = 30       # 몸체 가로 폭 (EDIF 단위) = 7.62 mm
PLACEHOLDER_PIN_LEN = 10      # 핀 길이 (EDIF 단위) = 2.54 mm
# 페이지 상단 안내문. A4 폭을 넘지 않게 두 줄로 나눈다(qstr 이 개행을 '\n' 으로 이스케이프하고
# KiCad 가 이를 줄바꿈으로 렌더링한다). 파일에 들어가는 문자열이므로 ASCII 영문.
PLACEHOLDER_NOTE = ('Board-only parts: on the PADS board but not in the OrCAD schematic.\n'
                    'These placeholders keep the PCB nets when KiCad updates the board.')


@dataclass
class PlaceholderPart:
    """보드에만 있는 부품 하나 (kicad_board.placeholder_parts 가 Board 에서 만들어 준다)."""
    ref: str
    fp_id: str
    value: str = ''
    pads: list = field(default_factory=list)    # [(패드 번호, 넷 이름 | None)]


def pad_sort_key(num):
    """패드 번호 정렬 키: 숫자 패드를 수치 순으로 먼저, 그다음 문자 패드를 사전 순으로."""
    s = str(num)
    return (0, int(s), '') if s.isdigit() else (1, 0, s)


def placeholder_symbol(part):
    """PlaceholderPart -> 자리표시 KSymbol. 이름은 PCB_ONLY_<sanitize(풋프린트 ID)>."""
    pads = sorted(part.pads, key=lambda kv: pad_sort_key(kv[0]))
    n = max(1, len(pads))
    top = (n - 1) * PLACEHOLDER_PITCH / 2.0                 # 첫 핀의 y (심볼 좌표, 단위)
    edge = top + PLACEHOLDER_PITCH / 2.0                    # 몸체 위/아래 모서리
    unit = KUnit()
    unit.graphics.append(Graphic(kind='rectangle', pts=[(0, edge), (PLACEHOLDER_BODY_W, -edge)]))
    for i, (num, net) in enumerate(pads):
        y = top - i * PLACEHOLDER_PITCH
        unit.pins.append(KPin(number=str(num), name=net or str(num), etype_kicad='passive',
                              at=sym_xy((-PLACEHOLDER_PIN_LEN, y)), angle=0,
                              length=mm(PLACEHOLDER_PIN_LEN)))
    m = re.match(r'[A-Za-z]+', part.ref or '')
    return KSymbol(name='PCB_ONLY_' + sanitize(part.fp_id), ref_prefix=m.group(0) if m else 'U',
                   value=part.fp_id, units={1: unit},
                   ref_pos=(0, edge + PLACEHOLDER_PITCH / 2.0),
                   value_pos=(0, -edge - PLACEHOLDER_PITCH / 2.0))


def add_placeholder_symbols(symset, parts):
    """자리표시 심볼들을 SymbolSet 에 등록하고 {ref: 심볼 이름} 을 돌려준다.

    풋프린트가 같아도 패드가 물린 넷(=핀 이름)이 다르면 내용이 다른 심볼이므로 기존 부품 심볼과
    같은 규칙(_variant_name)으로 `..._2` 변형 이름으로 분리한다. 내용이 같으면 재사용한다."""
    names = {}
    for part in parts:
        sym = placeholder_symbol(part)
        base = sym.name
        while True:
            cur = symset.symbols.get(sym.name)
            if cur is None:
                symset.symbols[sym.name] = sym
                break
            if cur.units.get(1) is not None and _unit_signature(cur.units[1]) == _unit_signature(sym.units[1]):
                break
            sym.name = _variant_name(base, symset.symbols)
        names[part.ref] = sym.name
    return names


def apply_pin_type_overrides(symset, overrides):
    """{(심볼 이름, 핀 번호): KiCad 전기 타입} 을 SymbolSet 에 적용하고 이슈 목록을 돌려준다.

    build_symbols 결과를 쓰기 직전에 고치는 용도다(라이브러리와 배치 심볼 양쪽에 반영된다)."""
    issues = []
    for (sym_name, number), etype in sorted(overrides.items()):
        sym = symset.symbols.get(sym_name)
        if sym is None:
            issues.append(f'pin type override: symbol {sym_name} not found')
            continue
        old = None
        hit = 0
        for u in sorted(sym.units):
            for p in sym.units[u].pins:
                if p.number == str(number):
                    old = p.etype_kicad if old is None else old
                    p.etype_kicad = etype
                    hit += 1
        if hit == 0:
            issues.append(f'pin type override: symbol {sym_name} has no pin {number}')
        else:
            issues.append(f'{sym_name} pin {number}: pin type {old} -> {etype} (override)')
    return issues


# ---------- 심볼 S-식 ----------

def _effects(size=1.27, hide=False, justify=None):
    s = f'(effects (font (size {fnum(size)} {fnum(size)}))'
    if justify:
        s += f' (justify {justify})'
    if hide:
        s += ' hide'
    return s + ')'


def _prop(name, value, at, hide=False, justify=None, rot=0):
    return f'(property {qstr(name)} {qstr(value)} (at {fnum(at[0])} {fnum(at[1])} {rot}) {_effects(hide=hide, justify=justify)})'


def _stroke_fill(fill='none'):
    return f'(stroke (width 0) (type default)) (fill (type {fill}))'


def graphic_text(g, to_xy):
    """Graphic -> KiCad 그래픽 S-식 (심볼용). to_xy: 좌표 변환 함수."""
    if g.kind == 'polyline':
        pts = ' '.join(f'(xy {fnum(x)} {fnum(y)})' for x, y in map(to_xy, g.pts))
        return f'(polyline (pts {pts}) {_stroke_fill()})'
    if g.kind == 'polygon':
        pts = list(g.pts)
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        s = ' '.join(f'(xy {fnum(x)} {fnum(y)})' for x, y in map(to_xy, pts))
        return f'(polyline (pts {s}) {_stroke_fill("outline")})'
    if g.kind == 'rectangle' and len(g.pts) >= 2:
        (x1, y1), (x2, y2) = to_xy(g.pts[0]), to_xy(g.pts[1])
        return f'(rectangle (start {fnum(x1)} {fnum(y1)}) (end {fnum(x2)} {fnum(y2)}) {_stroke_fill()})'
    if g.kind == 'circle' and len(g.pts) >= 2:
        (x1, y1), (x2, y2) = to_xy(g.pts[0]), to_xy(g.pts[1])
        cx, cy = round((x1 + x2) / 2, 4), round((y1 + y2) / 2, 4)
        r = round(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 / 2, 4)
        return f'(circle (center {fnum(cx)} {fnum(cy)}) (radius {fnum(r)}) {_stroke_fill()})'
    if g.kind == 'arc' and len(g.pts) >= 3:
        (sx, sy), (mx, my), (ex, ey) = (to_xy(p) for p in g.pts[:3])
        return f'(arc (start {fnum(sx)} {fnum(sy)}) (mid {fnum(mx)} {fnum(my)}) (end {fnum(ex)} {fnum(ey)}) {_stroke_fill()})'
    if g.kind == 'text' and g.pts:
        x, y = to_xy(g.pts[0])
        ang, h, v = edif_text_style(g.rotation, g.justify or 'UPPERLEFT')
        return (f'(text {qstr(g.text)} (at {fnum(x)} {fnum(y)} {ang}) '
                f'{_effects(size=text_size(g.text_height), justify=justify_str(h, v))})')
    return ''


def text_size(height_units):
    """EDIF textHeight(단위) -> KiCad 글자 크기(mm). 렌더링 보고 조정 가능."""
    return max(1.0, round(height_units * MM * 0.6, 2))


def _pin_text(p):
    hide = ' hide' if p.hidden else ''
    return (f'(pin {p.etype_kicad} {p.shape} (at {fnum(p.at[0])} {fnum(p.at[1])} {p.angle}) (length {fnum(p.length)}){hide} '
            f'(name {qstr(p.name)} {_effects()}) (number {qstr(p.number)} {_effects()}))')


def symbol_text(sym, lib_prefix=''):
    """KSymbol -> (symbol "NAME" ...) S-식. lib_prefix 는 lib_symbols 안에서 'orcad:'."""
    name = lib_prefix + sym.name
    lines = [f'(symbol {qstr(name)}']
    if sym.is_power:
        lines.append('  (power)')
    lines.append('  (pin_numbers hide)' if not sym.pin_numbers_visible else '  (pin_numbers)')
    lines.append('  (pin_names (offset 0.254) hide)' if not sym.pin_names_visible else '  (pin_names (offset 0.254))')
    lines.append('  (exclude_from_sim no) (in_bom yes) (on_board yes)')
    ref_at = sym_xy(sym.ref_pos) if sym.ref_pos else (0, 2.54)
    val_at = sym_xy(sym.value_pos) if sym.value_pos else (0, -2.54)
    lines.append('  ' + _prop('Reference', sym.ref_prefix, ref_at, hide=sym.is_power, justify='left'))
    lines.append('  ' + _prop('Value', sym.value, val_at, justify='left'))
    lines.append('  ' + _prop('Footprint', sym.footprint, (0, 0), hide=True))
    lines.append('  ' + _prop('Datasheet', '', (0, 0), hide=True))
    for u in sorted(sym.units):
        unit = sym.units[u]
        # 유닛 서브심볼 이름은 라이브러리 접두사(lib_prefix) 없이 순수 셀 이름만 써야 한다.
        # 접두사를 붙이면(예: "orcad:CAP_1_1") kicad-cli/eeschema가 .kicad_sch에 내장된
        # lib_symbols를 조용히 로드 실패시킨다 (실제 로드되는지 kicad-cli로 확인해 찾은 문제).
        lines.append(f'  (symbol {qstr(f"{sym.name}_{u}_1")}')
        for g in unit.graphics:
            t = graphic_text(g, sym_xy)
            if t:
                lines.append('    ' + t)
        for p in unit.pins:
            lines.append('    ' + _pin_text(p))
        lines.append('  )')
    lines.append(')')
    return '\n'.join(lines)


def symbol_lib_text(symset):
    parts = [f'(kicad_symbol_lib (version {FORMAT_VERSION}) (generator {qstr(GENERATOR)})']
    for name in sorted(symset.symbols):
        parts.append(symbol_text(symset.symbols[name]))
    parts.append(')')
    return '\n'.join(parts) + '\n'


# ---------- 회로도 ----------

_FNAME_BAD = re.compile(r'[\\/:*?"<>|]')
PAPER_SIZES = {(1654, 1169): 'A3', (1169, 827): 'A4', (2339, 1654): 'A2', (827, 1169): 'A4', (1169, 1654): 'A3'}


def page_filename(page_name):
    return _FNAME_BAD.sub('_', page_name).strip() + '.kicad_sch'


def paper_for(size):
    return PAPER_SIZES.get(tuple(size), 'A3')


@dataclass
class WriteResult:
    root_sch: str = ''
    page_files: list = field(default_factory=list)
    lib_file: str = ''
    pro_file: str = ''
    issues: list = field(default_factory=list)
    symset: SymbolSet = None
    symbol_paths: dict = field(default_factory=dict)   # reference -> "/ROOT_UUID/SHEET_UUID/SYMBOL_UUID"


def _sch_prop(name, value, at, hide=False, justify='left', rot=0):
    return _prop(name, value, at, hide=hide, justify=None if hide else justify, rot=0 if hide else rot)


def _symbol_instance_text(lib_name, at, rot, mirror, unit, ref, value, footprint, ref_at, val_at, uid, project, path,
                          pin_numbers=(), hide_ref=False, hide_value=False,
                          ref_style=(0, 'left'), val_style=(0, 'left')):
    """배치 심볼 하나의 S-식. ref_style/val_style 은 (저장 각도, justify 문자열) —
    field_placement 로 계산한 값이라 KiCad 가 배치 회전/미러를 적용한 뒤 OrCAD 와 같은
    절대 방향으로 그려진다."""
    # 포맷 20231120은 배치 심볼마다 핀별 uuid 항목이 있어야 kicad-cli/eeschema가 로드한다
    # (없으면 하위 시트가 조용히 빈 시트로 로드되고 sch export netlist가 빈 넷리스트를 낸다).
    m = f' (mirror {mirror})' if mirror else ''
    lines = [f'(symbol (lib_id {qstr(LIB_NICK + ":" + lib_name)}) (at {fnum(at[0])} {fnum(at[1])} {rot}){m} (unit {unit})',
             '  (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
             f'  (uuid {qstr(uid)})',
             '  ' + _sch_prop('Reference', ref, ref_at, hide=hide_ref, justify=ref_style[1], rot=ref_style[0]),
             '  ' + _sch_prop('Value', value, val_at, hide=hide_value, justify=val_style[1], rot=val_style[0]),
             '  ' + _sch_prop('Footprint', footprint, at, hide=True),
             '  ' + _sch_prop('Datasheet', '', at, hide=True)]
    for pnum in pin_numbers:
        lines.append(f'  (pin {qstr(pnum)} (uuid {qstr(stable_uuid("pin", uid, pnum))}))')
    lines.append(f'  (instances (project {qstr(project)} (path {qstr(path)} (reference {qstr(ref)}) (unit {unit}))))')
    lines.append(')')
    return '\n'.join(lines)


def _field_style(kicad_rot, mirror, orientation, justify, default=('left', 'top')):
    """EDIF 표시 회전·정렬 -> 배치 (kicad_rot, mirror) 에서 쓸 (저장 각도, justify 문자열).

    KiCad 가 심볼 필드를 배치 변환에 상대적으로 그리므로, OrCAD 와 같은 절대 방향으로
    보이게 하려면 저장할 때 배치 변환의 역을 적용해야 한다(field_placement)."""
    a, h, v = edif_text_style(orientation, justify, default)
    sa, sh, sv = field_placement(kicad_rot, mirror, a, h, v)
    return sa, justify_str(sh, sv)


def _label_angle_and_justify(vec_screen):
    """앵커에서 본체 중심으로 향하는 화면 벡터(y 아래) -> (KiCad 각도, justify)"""
    vx, vy = vec_screen
    if abs(vx) >= abs(vy):
        return (0, 'left') if vx >= 0 else (180, 'right')
    return (90, 'left') if vy < 0 else (270, 'right')


def _collect_name_kinds(design):
    """net_names='keep' 충돌 판정용 이름->종류 전역 맵: {이름: {kind: {나타나는 페이지 이름, ...}}}.
    kind: 'power net' | 'offpage net' | 'label' | 'auto net'. 전원넷/오프페이지넷은 설계 전체에
    걸친 이름이라 페이지 구분을 두지 않고 '*' 로 표기한다(어느 페이지의 label/auto net과 겹쳐도
    항상 충돌로 본다)."""
    kinds = {}

    def claim(name, kind, page_name):
        kinds.setdefault(name, {}).setdefault(kind, set()).add(page_name)

    for pg in design.pages:
        for pp in pg.power_ports:
            claim(design.canonical(pp.net), 'power net', '*')
        for op in pg.offpages:
            claim(design.canonical(op.net), 'offpage net', '*')
        for lb in pg.labels:
            claim(lb.text, 'label', pg.name)
        for net in pg.nets:
            if re.fullmatch(r'N\d+', net.name or ''):
                claim(net.name, 'auto net', pg.name)
    return kinds


def _collision_kind(name, page_name, own_kind, name_kinds):
    """name 을 own_kind 로 (페이지 page_name 에서) global_label 로 써도 되는지 판정한다.
    다른 종류가 이미 그 이름을 쓰고 있거나, 같은 종류가 다른 페이지에도 나타나면 충돌이며,
    그때 충돌한 종류 이름(kind 문자열)을 돌려준다. 충돌이 없으면 None."""
    for kind, pages in name_kinds.get(name, {}).items():
        if kind != own_kind or (pages - {page_name}):
            return kind
    return None


def _page_text(design, page, symset, project, root_uuid, sheet_uuid, pwr_counter, issues,
               footprint_map=None, footprint_lib='PADS', net_names='kicad', symbol_paths=None,
               name_kinds=None, ref_fp=None, footprint_issue_refs=None, footprint_choice=None,
               netlist_footprints=None):
    """페이지 하나의 .kicad_sch 전체 텍스트.

    footprint_map: {ref: 보드 풋프린트 id} — 있으면 인스턴스 Footprint 프로퍼티를 덮어쓴다.
    footprint_choice: {ref: 'board'|'orcad'} — 'orcad' 인 ref 만 보드 대신 OrCAD 이름을
    라이브러리 접두사 없이 그대로 쓴다(사용자 해소 결정, 이슈로 남긴다).
    netlist_footprints: {ref: PADS .asc *PART* 풋프린트 이름} — footprint_map 이 있을 때 보드에
    없는 ref 중 OrCAD 풋프린트가 비어 있는 것만 이 이름으로 채운다(폴백, 이슈로 남긴다).
    net_names: 'kicad'(기존 동작) | 'keep' — 이름 있는 넷은 global_label 로, 자동 이름
    (N\\d+) 넷도 첫 와이어 시작점에 global_label 을 추가해 KiCad 넷 이름이 OrCAD/PADS 이름과
    같아지게 한다. 다만 그 이름이 다른 종류(전원넷/오프페이지넷/다른 페이지의 같은 종류)와
    겹치면(name_kinds, _collision_kind) 이름 충돌을 피하기 위해 로컬 label 로 남긴다."""
    path = f'/{root_uuid}/{sheet_uuid}'
    used = set()
    body = []
    if symbol_paths is None:
        symbol_paths = {}
    if footprint_issue_refs is None:
        footprint_issue_refs = set()
    name_kinds = name_kinds or {}
    # 정션
    for i, j in enumerate(page.junctions):
        x, y = sch_xy(j)
        body.append(f'(junction (at {fnum(x)} {fnum(y)}) (diameter 0) (color 0 0 0 0) (uuid {qstr(stable_uuid("junction", page.name, i))}))')
    # 와이어
    for i, w in enumerate(page.wires):
        (x1, y1), (x2, y2) = sch_xy(w.a), sch_xy(w.b)
        body.append(f'(wire (pts (xy {fnum(x1)} {fnum(y1)}) (xy {fnum(x2)} {fnum(y2)})) (stroke (width 0) (type default)) '
                    f'(uuid {qstr(stable_uuid("wire", page.name, i, w.a, w.b))}))')
    # 로컬 라벨 (alias): 같은 넷의 와이어에 스냅. net_names='keep' 이면 페이지 하나에만 나타나고
    # 전원 넷 이름과 겹치지 않는 라벨은 global_label(shape passive) 로 써 KiCad 넷리스트 이름이
    # OrCAD/PADS 이름과 같아지게 한다. 그 외(이름이 두 페이지 이상에 나타남/전원 넷과 충돌)는
    # 기존처럼 로컬 label 로 남기고 이슈를 남긴다.
    net_wires = {n.edif_id: n.wires for n in page.nets}
    for i, lb in enumerate(page.labels):
        q = snap_to_wires(lb.pos, net_wires.get(lb.net_id, [])) or snap_to_wires(lb.pos, page.wires)
        if q is None:
            issues.append(f'page {page.name}: label {lb.text} at {lb.pos} not on a wire; written at original position')
            q = lb.pos
        x, y = sch_xy(q)
        uid = stable_uuid('label', page.name, i, lb.text, lb.pos)
        # 라벨 텍스트 방향은 EDIF 표시 그대로(절대). EDIF alias 표시에는 justify 가 없는 것이
        # 보통이라(샘플 40개 전부) 그 경우 KiCad 관례인 "와이어 위(left bottom)"를 기본으로 쓴다.
        lb_ang, lb_h, lb_v = edif_text_style(lb.rotation, lb.justify, default=('left', 'bottom'))
        collision = _collision_kind(lb.text, page.name, 'label', name_kinds) if net_names == 'keep' else True
        if not collision:
            body.append(f'(global_label {qstr(lb.text)} (shape passive) (at {fnum(x)} {fnum(y)} {lb_ang}) '
                        f'{_effects(justify="left")} (uuid {qstr(uid)}))')
        else:
            if net_names == 'keep':
                issues.append(f'page {page.name}: net name {lb.text} collides with {collision}; kept local')
            body.append(f'(label {qstr(lb.text)} (at {fnum(x)} {fnum(y)} {lb_ang}) '
                        f'{_effects(justify=justify_str(lb_h, lb_v))} (uuid {qstr(uid)}))')
    # net_names='keep': 자동 이름(N\d+) 넷도 global_label 을 추가해 이름을 보존한다.
    # 와이어가 있으면 첫 와이어의 시작점에 붙인다. 와이어가 전혀 없는 넷도 있다(핀끼리 직접
    # 맞닿아 배선을 그리지 않은 경우 — 예: 인덕터-레귤레이터 VOUT 직결). 이 경우 넷의 첫 핀
    # 접속점(pin_world)에 앵커한다. 그마저 없으면(핀도 못 찾으면) 이슈로 남긴다.
    if net_names == 'keep':
        inst_by_id = {ins.edif_id: ins for ins in page.instances}
        for net in page.nets:
            if re.fullmatch(r'N\d+', net.name or ''):
                pos = net.wires[0].a if net.wires else None
                if pos is None:
                    for inst_id, pid in net.pins:
                        ins = inst_by_id.get(inst_id)
                        sym = design.symbols.get(ins.symbol) if ins else None
                        pin = next((p for p in sym.pins if p.port_id == pid), None) if sym else None
                        if pin is not None:
                            pos = pin_world(ins, pin)
                            break
                if pos is not None:
                    x, y = sch_xy(pos)
                    uid = stable_uuid('autolabel', page.name, net.edif_id)
                    collision = _collision_kind(net.name, page.name, 'auto net', name_kinds)
                    if collision is None:
                        body.append(f'(global_label {qstr(net.name)} (shape passive) (at {fnum(x)} {fnum(y)} 0) '
                                    f'{_effects(justify="left")} (uuid {qstr(uid)}))')
                    else:
                        issues.append(f'page {page.name}: net name {net.name} collides with {collision}; kept local')
                        body.append(f'(label {qstr(net.name)} (at {fnum(x)} {fnum(y)} 0) {_effects(justify="left bottom")} '
                                    f'(uuid {qstr(uid)}))')
                else:
                    issues.append(f'page {page.name}: auto net {net.name} has no wire; name not preserved')
    # 오프페이지 -> 전역 라벨 (앵커 = 접속점, 방향 = 본체 쪽)
    for i, op in enumerate(page.offpages):
        cell = design.symbols.get(op.symbol)
        off = symset.offpage_offset.get(op.symbol, (0, 0))
        pts = [p for g in (cell.graphics if cell else []) for p in g.pts]
        if pts:
            cx = sum(p[0] for p in pts) / len(pts) - off[0]
            cy = sum(p[1] for p in pts) / len(pts) - off[1]
            vx, vy = transform_point(op.orientation, (0, 0), (cx, cy))
            vec = (vx, -vy)
        else:
            vec = (1, 0)
        ang, just = _label_angle_and_justify(vec)
        # 오프페이지는 지오메트리(본체 방향)로 각도를 정하는 게 KiCad 에서 자연스럽다.
        # EDIF 가 명시적으로 R0 이 아닌 회전을 줬을 때만 그 값을 따른다(샘플은 전부 R0).
        if (op.label_rot or 'R0').upper() != 'R0':
            ang = edif_text_style(op.label_rot, op.label_justify)[0]
        x, y = sch_xy(op.pos)
        name = design.canonical(op.net)
        body.append(f'(global_label {qstr(name)} (shape bidirectional) (at {fnum(x)} {fnum(y)} {ang}) {_effects(justify=just)} '
                    f'(uuid {qstr(stable_uuid("offpage", page.name, i, op.net, op.pos))})\n'
                    f'  (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {fnum(x)} {fnum(y)} 0) {_effects(hide=True)}))')
    # 자유 텍스트
    for i, t in enumerate(page.texts):
        x, y = sch_xy(t.pos)
        t_ang, t_h, t_v = edif_text_style(t.rotation, t.justify)
        body.append(f'(text {qstr(t.text)} (at {fnum(x)} {fnum(y)} {t_ang}) '
                    f'{_effects(size=text_size(t.height), justify=justify_str(t_h, t_v) or "left top")} '
                    f'(uuid {qstr(stable_uuid("text", page.name, i, t.text, t.pos))}))')
    # 부품 인스턴스
    for ins in page.instances:
        name, unit = symset.instance_symbol[ins.edif_id]
        used.add(name)
        rot, mirror = ORIENT_KICAD[ins.orientation]
        at = sch_xy(ins.origin)
        ref_at = sch_xy(ins.ref_pos) if ins.ref_pos else at
        val_at = sch_xy(ins.value_pos) if ins.value_pos else at
        pin_numbers = [p.number for p in symset.symbols[name].units[unit].pins]
        fp_text = ins.footprint
        if footprint_map is not None:
            if ins.reference in footprint_map:
                bfp = fp_base(footprint_map[ins.reference])
                # 여러 유닛에서 이슈가 중복되지 않도록 ref 당 한 번만: OrCAD 이름은 compare_board 와
                # 같은 규칙(유닛이 가장 작은 인스턴스)으로 결정된 값을 쓴다 (U2 처럼 유닛별 값이
                # 달라도 모순되는 이슈가 나오지 않게).
                orcad_fp = (ref_fp or {}).get(ins.reference, ins.footprint)
                if (footprint_choice or {}).get(ins.reference) == 'orcad':
                    # 사용자가 OrCAD 풋프린트를 고른 ref: 라이브러리 접두사 없이 이름 그대로 쓴다.
                    fp_text = fp_base(orcad_fp)
                    if ins.reference not in footprint_issue_refs:
                        issues.append(f'{ins.reference}: footprint choice orcad; {fp_text} kept (board {bfp})')
                        footprint_issue_refs.add(ins.reference)
                else:
                    fp_text = f'{footprint_lib}:{bfp}'
                    if (orcad_fp or '').upper() != bfp.upper() and ins.reference not in footprint_issue_refs:
                        issues.append(f'{ins.reference}: footprint {orcad_fp} -> {fp_text} (board)')
                        footprint_issue_refs.add(ins.reference)
            else:
                # 보드에 없는 ref: OrCAD 풋프린트가 비어 있고 넷리스트(.asc *PART*)에 이름이
                # 있으면 그 이름으로 폴백한다(라이브러리 접두사 없이 그대로).
                nl_fp = (netlist_footprints or {}).get(ins.reference) or ''
                if not ins.footprint and nl_fp:
                    fp_text = fp_base(nl_fp)
                    if ins.reference not in footprint_issue_refs:
                        issues.append(f'{ins.reference}: not on board; footprint from netlist: {fp_text}')
                        footprint_issue_refs.add(ins.reference)
                elif ins.reference not in footprint_issue_refs:
                    issues.append(f'{ins.reference}: not on board; footprint {ins.footprint} kept')
                    footprint_issue_refs.add(ins.reference)
        uid = stable_uuid('inst', page.name, ins.edif_id)
        cur = symbol_paths.get(ins.reference)
        if cur is None or ins.unit < cur[0]:
            symbol_paths[ins.reference] = (ins.unit, f'{path}/{uid}')
        ref_style = _field_style(rot, mirror, ins.ref_rot, ins.ref_justify)
        val_style = _field_style(rot, mirror, ins.value_rot, ins.value_justify)
        body.append(_symbol_instance_text(name, at, rot, mirror, unit, ins.reference, ins.value, fp_text,
                                          ref_at, val_at, uid, project, path,
                                          pin_numbers=pin_numbers, hide_value=(ins.value_pos is None),
                                          ref_style=ref_style, val_style=val_style))
    # 전원 심볼: 접속점에 배치 (심볼 원점 = 접속점)
    for pp in page.power_ports:
        net = design.canonical(pp.net)
        name = symset.power_symbol[(pp.symbol, net)]
        used.add(name)
        rot, mirror = ORIENT_KICAD[pp.orientation]
        at = sch_xy(pp.pos)
        pwr_counter[0] += 1
        ref = f'#PWR{pwr_counter[0]:04d}'
        val_at = sch_xy(pp.label_pos) if pp.label_pos else at
        pin_numbers = [p.number for p in symset.symbols[name].units[1].pins]
        val_style = _field_style(rot, mirror, pp.label_rot, pp.label_justify)
        body.append(_symbol_instance_text(name, at, rot, mirror, 1, ref, net, '', at, val_at,
                                          stable_uuid('pwr', page.name, pp.port_id, pp.pos), project, path,
                                          pin_numbers=pin_numbers, hide_ref=True, val_style=val_style))
    lib = '\n'.join(symbol_text(symset.symbols[n], LIB_NICK + ':') for n in sorted(used))
    head_lines = [f'(kicad_sch (version {FORMAT_VERSION}) (generator {qstr(GENERATOR)})',
                  f'  (uuid {qstr(sheet_uuid)})',
                  f'  (paper {qstr(paper_for(page.size))})',
                  f'  (title_block (title {qstr(page.name)}))',
                  '  (lib_symbols', lib, '  )']
    return '\n'.join(head_lines + body + ['  (sheet_instances (path "/" (page "1")))', ')']) + '\n'


def _placeholder_page_text(parts, sym_names, symset, project, root_uuid, sheet_uuid,
                           symbol_paths, footprint_lib='PADS'):
    """보드 전용 자리표시 부품 페이지(99-PCB-ONLY)의 .kicad_sch 전체 텍스트.

    부품을 A4 격자에 배치하고, 핀마다 왼쪽으로 짧은 와이어를 그은 뒤 그 끝에 보드 패드 넷 이름의
    global_label 을 놓아 회로도의 나머지(그리고 보드)와 같은 넷에 연결한다. 넷이 없는 패드는
    와이어만 두고 라벨은 붙이지 않는다. symbol_paths 에는 {ref: (unit, path)} 를 채워 넣어
    kicad_board.write_project_board 가 보드 풋프린트에 (path ...) 를 심을 수 있게 한다."""
    path = f'/{root_uuid}/{sheet_uuid}'
    pitch = mm(PLACEHOLDER_PITCH)                       # 2.54 mm
    body = [f'(text {qstr(PLACEHOLDER_NOTE)} (at 20.32 15.24 0) '
            f'{_effects(size=2.0, justify="left top")} (uuid {qstr(stable_uuid("pcbonly-note", project))}))']
    used = set()
    cell_w = 76.2                                       # 열 간격(라벨 텍스트가 들어갈 만큼)
    cell_h = max([(len(p.pads) + 4) * pitch for p in parts] + [25.4])
    cols = max(1, int((279.4 - 50.8) // cell_w))        # A4 가로 297 mm - 여백
    for i, part in enumerate(parts):
        name = sym_names[part.ref]
        used.add(name)
        pins = symset.symbols[name].units[1].pins
        nets = {str(n): net for n, net in part.pads}
        x = round(50.8 + (i % cols) * cell_w, 4)
        y = round(38.1 + (i // cols) * cell_h, 4)
        half = (len(pins) - 1) * pitch / 2.0
        uid = stable_uuid('pcbonly', part.ref)
        symbol_paths[part.ref] = (1, f'{path}/{uid}')
        body.append(_symbol_instance_text(name, (x, y), 0, None, 1, part.ref, part.value or part.fp_id,
                                          f'{footprint_lib}:{fp_base(part.fp_id)}',
                                          (x, round(y - half - pitch, 4)), (x, round(y + half + pitch, 4)),
                                          uid, project, path,
                                          pin_numbers=[p.number for p in pins],
                                          ref_style=(0, ''), val_style=(0, '')))
        for k, p in enumerate(pins):
            # 심볼은 y 위가 양수, 회로도는 y 아래가 양수라 핀 k 의 회로도 y 는 y - (half - k*pitch).
            py = round(y - half + k * pitch, 4)
            px = round(x - pitch, 4)                    # 핀 접속점 (심볼 좌표 x = -pitch)
            lx = round(px - pitch, 4)                   # 와이어 바깥 끝 = 라벨 앵커
            net = nets.get(p.number)
            if not net:
                # 넷이 없는 패드(샘플 J19/J20 의 15/16 번 = 고정용 패드)는 와이어도 긋지 않는다.
                # 끝이 뜬 와이어는 ERC unconnected_wire_endpoint 를 만들기만 하고 의미가 없다.
                continue
            body.append(f'(wire (pts (xy {fnum(lx)} {fnum(py)}) (xy {fnum(px)} {fnum(py)})) '
                        f'(stroke (width 0) (type default)) '
                        f'(uuid {qstr(stable_uuid("pcbonly-wire", part.ref, p.number))}))')
            body.append(f'(global_label {qstr(net)} (shape passive) (at {fnum(lx)} {fnum(py)} 180) '
                        f'{_effects(justify="right")} (uuid {qstr(stable_uuid("pcbonly-label", part.ref, p.number))})\n'
                        f'  (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {fnum(lx)} {fnum(py)} 0) '
                        f'{_effects(hide=True)}))')
    lib = '\n'.join(symbol_text(symset.symbols[n], LIB_NICK + ':') for n in sorted(used))
    head_lines = [f'(kicad_sch (version {FORMAT_VERSION}) (generator {qstr(GENERATOR)})',
                  f'  (uuid {qstr(sheet_uuid)})',
                  '  (paper "A4")',
                  f'  (title_block (title {qstr(PLACEHOLDER_PAGE)}))',
                  '  (lib_symbols', lib, '  )']
    return '\n'.join(head_lines + body + ['  (sheet_instances (path "/" (page "1")))', ')']) + '\n'


def _root_text(design, project, root_uuid, sheet_uuids, extra_pages=()):
    """루트 시트. extra_pages 는 Design.pages 에 없는 추가 페이지 이름들(자리표시 부품 페이지)로,
    설계 페이지 뒤에 이어 붙는다(그래야 하위 시트로 인식되어 넷리스트/ERC 에 포함된다)."""
    lines = [f'(kicad_sch (version {FORMAT_VERSION}) (generator {qstr(GENERATOR)})',
             f'  (uuid {qstr(root_uuid)})', '  (paper "A4")', f'  (title_block (title {qstr(design.name)}))', '  (lib_symbols)']
    cols, w, h, gap = 3, 45.72, 20.32, 12.7
    for i, name in enumerate([p.name for p in design.pages] + list(extra_pages)):
        x = 25.4 + (i % cols) * (w + gap)
        y = 25.4 + (i // cols) * (h + gap)
        su = sheet_uuids[name]
        lines.append(f'  (sheet (at {fnum(x)} {fnum(y)}) (size {fnum(w)} {fnum(h)}) (fields_autoplaced yes) '
                     f'(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0)) (uuid {qstr(su)})')
        lines.append(f'    (property "Sheetname" {qstr(name)} (at {fnum(x)} {fnum(y - 0.7)} 0) {_effects(justify="left bottom")})')
        lines.append(f'    (property "Sheetfile" {qstr(page_filename(name))} (at {fnum(x)} {fnum(y + h + 0.6)} 0) {_effects(justify="left top")})')
        lines.append(f'    (instances (project {qstr(project)} (path {qstr("/" + root_uuid)} (page {qstr(str(i + 2))})))))')
    lines.append('  (sheet_instances (path "/" (page "1")))')
    lines.append(')')
    return '\n'.join(lines) + '\n'


def _project_json(project, root_uuid, design, sheet_uuids, extra_pages=()):
    return {
        'board': {'design_settings': {}, 'layer_presets': [], 'viewports': []},
        'boards': [], 'cvpcb': {'equivalence_files': []},
        'libraries': {'pinned_footprint_libs': [], 'pinned_symbol_libs': []},
        'meta': {'filename': f'{project}.kicad_pro', 'version': 1},
        'net_settings': {'classes': [{'name': 'Default', 'priority': 2147483647, 'wire_width': 6, 'bus_width': 12,
                                      'line_style': 0, 'clearance': 0.2, 'track_width': 0.2, 'via_diameter': 0.6,
                                      'via_drill': 0.3, 'microvia_diameter': 0.3, 'microvia_drill': 0.1,
                                      'diff_pair_width': 0.2, 'diff_pair_gap': 0.25, 'diff_pair_via_gap': 0.25}],
                         'meta': {'version': 3}},
        'pcbnew': {'page_layout_descr_file': ''},
        'schematic': {'legacy_lib_dir': '', 'legacy_lib_list': []},
        'sheets': ([[root_uuid, 'Root']]
                   + [[sheet_uuids[n], n] for n in [p.name for p in design.pages] + list(extra_pages)]),
        'text_variables': {},
    }


def write_project(design, outdir, project_name=None, symset=None, footprint_map=None, footprint_lib='PADS',
                  net_names='kicad', extra_parts=None, pin_type_overrides=None, footprint_choice=None,
                  netlist_footprints=None):
    """Design -> outdir 에 KiCad 프로젝트 전체를 쓴다.

    footprint_map: {ref: 보드 풋프린트 id}. 있으면 인스턴스 Footprint 프로퍼티가
    f'{footprint_lib}:{fp_id}' 로 바뀐다 (보드에 없는 ref 는 OrCAD 이름을 유지).
    net_names: 'kicad'(기본) | 'keep' — 넷 이름을 라벨/global_label 로 최대한 보존한다.
    extra_parts: [PlaceholderPart] — 보드에만 있는 부품. Design.pages 를 건드리지 않고 추가
      페이지 99-PCB-ONLY 에만 배치되므로 1·2차 검증에는 영향이 없다. 자리표시 부품의 넷 연결은
      global_label 로만 이루어지므로 이때 net_names 는 'keep' 으로 강제된다.
    pin_type_overrides: {(심볼 이름, 핀 번호): KiCad 전기 타입} — build_symbols 결과에 적용.
    footprint_choice: {ref: 'board'|'orcad'} — 'orcad' 인 ref 만 OrCAD 풋프린트 이름을 쓴다.
    netlist_footprints: {ref: PADS .asc *PART* 풋프린트 이름} — footprint_map 이 있을 때 보드에
      없는 ref 중 OrCAD 풋프린트가 비어 있는 것만 이 이름으로 채운다(넷리스트 폴백)."""
    os.makedirs(outdir, exist_ok=True)
    project = sanitize(project_name or design.name)
    symset = symset or build_symbols(design)
    res = WriteResult(symset=symset, issues=list(symset.issues))
    if pin_type_overrides:
        res.issues.extend(apply_pin_type_overrides(symset, pin_type_overrides))
    root_uuid = stable_uuid('root', design.name)
    sheet_uuids = {p.name: stable_uuid('sheet', design.name, p.name) for p in design.pages}
    pwr_counter = [0]
    # ref 별 OrCAD 풋프린트: 유닛이 가장 작은 인스턴스의 값을 채택한다 (kicad_board.compare_board
    # 와 같은 규칙 — 같은 ref 의 유닛마다 풋프린트 문자열이 다를 수 있어(U2 등) 페이지 순회 순서에
    # 좌우되지 않게, 그리고 여러 이슈가 모순되지 않게 하나로 고정한다).
    ref_fp, ref_unit_seen = {}, {}
    for pg in design.pages:
        for ins in pg.instances:
            u = ins.unit or 1
            if ins.reference not in ref_unit_seen or u < ref_unit_seen[ins.reference]:
                ref_unit_seen[ins.reference] = u
                ref_fp[ins.reference] = ins.footprint
    footprint_issue_refs = set()
    symbol_paths_tmp = {}   # ref -> (unit, path); 유닛이 가장 작은 인스턴스만 남긴다
    # 자리표시 부품(보드 전용): 회로도에 이미 있는 ref 는 중복 배치가 되므로 제외하고, 넷 이름은
    # 전원 alias 를 적용한 정규 이름으로 통일한다(3차 검증 기준 넷리스트 보강과 이름이 같아야 한다).
    parts = []
    for part in (extra_parts or []):
        if part.ref in ref_fp:
            res.issues.append(f'placeholder part {part.ref}: already in the schematic; skipped')
            continue
        parts.append(PlaceholderPart(ref=part.ref, fp_id=part.fp_id, value=part.value,
                                     pads=[(n, design.canonical(net) if net else None) for n, net in part.pads]))
    if parts and net_names != 'keep':
        res.issues.append(f"net names: 'keep' forced by {len(parts)} board-only placeholder part(s)")
        net_names = 'keep'
    extra_pages = [PLACEHOLDER_PAGE] if parts else []
    if parts:
        sheet_uuids[PLACEHOLDER_PAGE] = stable_uuid('sheet', design.name, PLACEHOLDER_PAGE)
    # 이름 -> 종류 전역 맵 (net_names='keep' 에서 label/auto net 을 global_label 로 승격해도
    # 되는지 판단; _collect_name_kinds/_collision_kind 참조).
    name_kinds = _collect_name_kinds(design)
    for page in design.pages:
        text = _page_text(design, page, symset, project, root_uuid, sheet_uuids[page.name], pwr_counter, res.issues,
                          footprint_map=footprint_map, footprint_lib=footprint_lib, net_names=net_names,
                          symbol_paths=symbol_paths_tmp, name_kinds=name_kinds,
                          ref_fp=ref_fp, footprint_issue_refs=footprint_issue_refs,
                          footprint_choice=footprint_choice, netlist_footprints=netlist_footprints)
        path = os.path.join(outdir, page_filename(page.name))
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        res.page_files.append(path)
    if parts:
        sym_names = add_placeholder_symbols(symset, parts)
        text = _placeholder_page_text(parts, sym_names, symset, project, root_uuid,
                                      sheet_uuids[PLACEHOLDER_PAGE], symbol_paths_tmp,
                                      footprint_lib=footprint_lib)
        path = os.path.join(outdir, page_filename(PLACEHOLDER_PAGE))
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        res.page_files.append(path)
    res.symbol_paths = {r: p for r, (u, p) in symbol_paths_tmp.items()}
    res.root_sch = os.path.join(outdir, f'{project}.kicad_sch')
    with open(res.root_sch, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_root_text(design, project, root_uuid, sheet_uuids, extra_pages))
    res.lib_file = os.path.join(outdir, f'{LIB_NICK}.kicad_sym')
    with open(res.lib_file, 'w', encoding='utf-8', newline='\n') as f:
        f.write(symbol_lib_text(symset))
    with open(os.path.join(outdir, 'sym-lib-table'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'(sym_lib_table (version 7)\n  (lib (name {qstr(LIB_NICK)}) (type "KiCad") (uri "${{KIPRJMOD}}/{LIB_NICK}.kicad_sym") (options "") (descr "OrCAD import"))\n)\n')
    res.pro_file = os.path.join(outdir, f'{project}.kicad_pro')
    with open(res.pro_file, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(_project_json(project, root_uuid, design, sheet_uuids, extra_pages), f, indent=2)
        f.write('\n')
    return res
