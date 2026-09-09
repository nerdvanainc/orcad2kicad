"""중간 모델: OrCAD EDIF -> (이 모델) -> KiCad.

단위: OrCAD 회로도 단위 (1 단위 = 10 mil = 0.254 mm).
좌표계는 EDIF 그대로: y는 위가 양수 (페이지 내용은 y <= 0 영역).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# OrCAD 핀 타입 문자열 -> KiCad 전기 타입. 샘플에서 실제 관측: PAS, IO, POWER, DBO_IN, DBO_OUT, HIZ.
PIN_TYPE_TO_KICAD = {
    'PAS': 'passive', 'PASSIVE': 'passive',
    'IO': 'bidirectional', 'BI': 'bidirectional',
    'IN': 'input', 'INPUT': 'input', 'DBO_IN': 'input',
    'OUT': 'output', 'OUTPUT': 'output', 'DBO_OUT': 'output',
    'POWER': 'power_in', 'PWR': 'power_in',
    'OC': 'open_collector', 'OE': 'open_emitter',
    '3ST': 'tri_state', 'HIZ': 'tri_state',
}


def symbol_key(lib_id: str, cell_id: str) -> str:
    """심볼 딕셔너리 키. 셀 ID는 라이브러리 간 중복될 수 있어 라이브러리 ID를 붙인다."""
    return f'{lib_id}:{cell_id}'


@dataclass
class Graphic:
    kind: str                      # 'polyline' | 'rectangle' | 'circle' | 'arc' | 'polygon' | 'text'
    pts: list = field(default_factory=list)   # [(x, y)] — rectangle: 대각 2점, circle: 지름 양끝 2점, arc: 시작·경유·끝
    text: str = ''
    width: int = 0
    text_height: int = 10
    justify: str = 'UPPERLEFT'
    rotation: str = 'R0'


@dataclass
class Pin:
    port_id: str                   # EDIF 포트 ID (&1, VSS_3, BOOT0 ...) — portRef/portInstance가 참조
    number: str                    # 셀 port designator (비어 있을 수 있음)
    name: str
    etype: str                     # OrCAD 타입 문자열 원문
    connect: tuple                 # 접속점 (심볼 좌표)
    body: tuple                    # 몸체 쪽 끝점
    package_numbers: list = field(default_factory=list)   # PackagePortNumbers를 ','로 나눈 것 (유닛별 핀 번호)
    shape: str = 'line'            # 'line' | 'inverted'
    name_pos: Optional[tuple] = None
    number_pos: Optional[tuple] = None
    hidden: bool = False           # INVISIBLEPIN == TRUE 이거나 portImplementation에 리드선(figure)이 없음
    implicit_class: str = ''       # port property IMPLICITPORTCLASS (없으면 ''). 숨은 전원핀의 암묵 접속 대상 넷

    @property
    def kicad_type(self) -> str:
        return PIN_TYPE_TO_KICAD.get(self.etype.upper(), 'passive')


@dataclass
class Symbol:
    key: str                       # symbol_key(lib_id, cell_id)
    lib_id: str
    cell_id: str
    cell_name: str                 # 원래 셀 이름 ("CAP NP")
    cell_type: str                 # 'part' | 'pagePort' | 'offPageConnector' | 'template'
    source_package: str = ''
    position_in_package: str = ''  # 셀 프로퍼티 원문 ("-1,_", "A,B,C,D", "" ...)
    ref_prefix: str = 'U'          # designator "C?" -> "C"
    pins: list = field(default_factory=list)
    graphics: list = field(default_factory=list)
    pin_numbers_visible: bool = True
    pin_names_visible: bool = True
    footprint: str = ''            # 셀 레벨 PCB Footprint (인스턴스가 덮어씀)
    value: str = ''
    ref_pos: Optional[tuple] = None
    value_pos: Optional[tuple] = None
    notes: list = field(default_factory=list)

    @property
    def pin_by_port(self) -> dict:
        return {p.port_id: p for p in self.pins}


@dataclass
class Instance:
    edif_id: str
    symbol: str                    # Symbol.key
    designator_raw: str            # "U1-1", "U2A", "C20"
    reference: str                 # "U1", "U2", "C20"
    unit: int                      # PositionInPackage + 1
    value: str
    footprint: str
    origin: tuple
    orientation: str               # R0 R90 R180 R270 MX MY MXR90 MYR90
    ref_pos: Optional[tuple] = None
    value_pos: Optional[tuple] = None
    # Reference/Value 텍스트의 EDIF 표시 회전·정렬(절대). justify 가 ''이면 EDIF에 없었다는 뜻.
    ref_rot: str = 'R0'
    ref_justify: str = ''
    value_rot: str = 'R0'
    value_justify: str = ''
    props: dict = field(default_factory=dict)
    pin_numbers: dict = field(default_factory=dict)   # port id -> 최종 핀 번호 (fallback 적용 후)


@dataclass
class Wire:
    a: tuple
    b: tuple
    net_id: str = ''               # 이 와이어가 속한 EDIF 넷 ID (페이지 로컬)


@dataclass
class Label:
    text: str                      # 넷 이름 (표시 이름)
    pos: tuple                     # EDIF alias display origin (와이어 위가 아닐 수 있음)
    net_id: str = ''
    rotation: str = 'R0'
    justify: str = ''              # EDIF justify 원문 ('' = EDIF에 없음 -> writer 기본값 사용)


@dataclass
class PowerPort:
    net: str                       # 표시 넷 이름 ("GND", "VCC_3.3V")
    pos: tuple                     # 접속점 (connectLocation)
    symbol: str                    # Symbol.key (pagePort 셀)
    origin: tuple
    orientation: str
    label_pos: Optional[tuple] = None
    label_rot: str = 'R0'          # 넷 이름 텍스트의 EDIF 표시 회전
    label_justify: str = ''        # 넷 이름 텍스트의 EDIF justify ('' = 없음)
    port_id: str = ''              # portImplementation ID


@dataclass
class OffPage:
    net: str
    pos: tuple
    symbol: str                    # Symbol.key (offPageConnector 셀)
    origin: tuple
    orientation: str
    label_pos: Optional[tuple] = None
    label_rot: str = 'R0'
    label_justify: str = ''
    port_id: str = ''


@dataclass
class Text:
    text: str
    pos: tuple
    height: int = 10
    justify: str = 'UPPERLEFT'
    rotation: str = 'R0'


@dataclass
class Net:
    edif_id: str
    name: str                      # 표시 이름 (rename 원문) 또는 자동 이름 N...
    named: bool                    # 사용자가 이름을 붙였거나 포트로 이름이 정해진 넷
    pins: list = field(default_factory=list)        # [(instance_edif_id, port_id)]
    page_ports: list = field(default_factory=list)  # instanceRef 없는 portRef ID (오프페이지/루트 포트)
    wires: list = field(default_factory=list)        # [Wire]
    junctions: list = field(default_factory=list)   # [(x, y)]
    label_positions: list = field(default_factory=list)  # alias display origin 들


@dataclass
class Page:
    name: str
    size: tuple                    # (w, h) 단위
    instances: list = field(default_factory=list)
    wires: list = field(default_factory=list)
    junctions: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    power_ports: list = field(default_factory=list)
    offpages: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    nets: list = field(default_factory=list)


@dataclass
class Design:
    name: str
    pages: list = field(default_factory=list)
    symbols: dict = field(default_factory=dict)         # key -> Symbol
    root_ports: dict = field(default_factory=dict)      # 루트 port ID -> (표시 이름, IMPLICITPORTCLASS)
    page_port_names: dict = field(default_factory=dict) # 페이지 포트 ID(offPageConnector/루트 port) -> 정규 넷 이름
    power_aliases: dict = field(default_factory=dict)   # 표시 이름 -> 정규 이름 (예: 'VCC_3.3V' -> 'VCC')
    issues: list = field(default_factory=list)          # 사람이 읽는 경고

    def canonical(self, net_name: str) -> str:
        """전원 alias를 적용한 정규 넷 이름."""
        return self.power_aliases.get(net_name, net_name)

    def instance_by_id(self) -> dict:
        out = {}
        for pg in self.pages:
            for ins in pg.instances:
                out[ins.edif_id] = ins
        return out
