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
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PadsNetlist:
    parts: dict = field(default_factory=dict)   # ref -> footprint
    nets: dict = field(default_factory=dict)    # net name -> set('REF.PIN')

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
