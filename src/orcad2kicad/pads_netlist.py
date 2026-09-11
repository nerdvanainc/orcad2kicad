"""PADS2000 ASCII 넷리스트(.asc, OrCAD Capture가 내보낸 것) 파서.

형식:
    *PADS2000*
    *PART*
    REF   FOOTPRINT
    ...
    *NET*
    *SIGNAL* NETNAME
    REF.PIN REF.PIN ...   (여러 줄 가능)
    *END*

`load_reference_netlist(path)` 는 이 PADS 형식과 IPC-D-356/A(`ipc356.py`) 를 확장자·내용으로
구분해 읽는 공용 진입점이다 — GUI/CLI/MCP 의 "기준 넷리스트" 입력은 전부 이걸 쓴다.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class PadsNetlist:
    parts: dict = field(default_factory=dict)   # ref -> footprint
    nets: dict = field(default_factory=dict)    # net name -> set('REF.PIN')
    source_format: str = 'PADS ASCII'           # 로그용 표시 이름('PADS ASCII' | 'IPC-D-356')

    def pin_to_net(self) -> dict:
        """REF.PIN -> 넷 이름 역인덱스."""
        out = {}
        for net, pins in self.nets.items():
            for p in pins:
                out[p] = net
        return out


def parse_pads_netlist(text: str) -> PadsNetlist:
    nl = PadsNetlist()
    section = None
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('*'):
            tag = line.split()[0]
            if tag == '*PART*':
                section = 'part'
            elif tag == '*NET*':
                section = 'net'
            elif tag == '*SIGNAL*':
                current = line.split(None, 1)[1].strip()
                nl.nets.setdefault(current, set())
            elif tag == '*END*':
                break
            # *PADS2000* 등 기타 헤더는 무시
            continue
        if section == 'part':
            fields = line.split()
            if len(fields) >= 2:
                nl.parts[fields[0]] = fields[1]
            elif fields:
                nl.parts[fields[0]] = ''
        elif section == 'net' and current is not None:
            for tok in line.split():
                if '.' in tok:
                    nl.nets[current].add(tok)
    return nl


def load_pads_netlist(path: str) -> PadsNetlist:
    with open(path, encoding='latin-1') as f:
        return parse_pads_netlist(f.read())


_IPC356_EXTS = ('.ipc', '.356', '.d356', '.ipc356')


def load_reference_netlist(path: str) -> PadsNetlist:
    """기준 넷리스트를 확장자·내용으로 판별해 읽는다: PADS2000 ASCII(.asc) 또는
    IPC-D-356/IPC-D-356A(Cadence Allegro 등에서 내보낸 것, 보통 .ipc/.356/.d356 확장자).

    확장자가 IPC 쪽이면 바로 그걸로, 아니면 내용을 살짝 들여다봐서(`ipc356.is_ipc356_file`)
    IPC-D-356 인지 판별하고, 둘 다 아니면 PADS2000 ASCII 로 읽는다."""
    from .ipc356 import load_ipc356, is_ipc356_file
    ext = os.path.splitext(path)[1].lower()
    if ext in _IPC356_EXTS or is_ipc356_file(path):
        return load_ipc356(path)
    return load_pads_netlist(path)
