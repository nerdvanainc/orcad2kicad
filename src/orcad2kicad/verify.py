"""넷리스트 검증: (1) EDIF joined 기반, (공용) 비교기와 리포트.

넷리스트 표현은 {정규 넷 이름: {"REF.PIN", ...}}. 지오메트리 유도(geometry.derive_nets)도 같은 형태를 만든다.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


class UnionFind:
    """해시 가능한 임의 키에 대한 union-find."""
    def __init__(self):
        self.parent = {}

    def find(self, x):
        p = self.parent.setdefault(x, x)
        while p != x:
            self.parent[x] = self.parent[p]
            x = p
            p = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self):
        out = {}
        for x in list(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return out


def net_keys(design, net):
    """페이지 넷 하나가 전역에서 어떤 이름(들)으로 묶이는지. 항상 1개 이상."""
    keys = set()
    for pid in net.page_ports:
        if pid in design.page_port_names:
            keys.add(design.canonical(design.page_port_names[pid]))
        else:
            design.issues.append(f'net {net.name}: unknown page port {pid}')
    if net.named or not keys:
        keys.add(design.canonical(net.name))
    return keys


def edif_netlist(design):
    """EDIF (joined ...) 기반 넷리스트. 페이지 간은 오프페이지/전원 포트 이름으로 병합."""
    inst = design.instance_by_id()
    uf = UnionFind()
    members = {}     # 키 -> set(REF.PIN)
    for pg in design.pages:
        for net in pg.nets:
            keys = sorted(net_keys(design, net))
            first = keys[0]
            uf.find(first)
            for k in keys[1:]:
                uf.union(first, k)
            pins = members.setdefault(first, set())
            for inst_id, pid in net.pins:
                i = inst.get(inst_id)
                if i is None:
                    design.issues.append(f'net {net.name}: unknown instance {inst_id}')
                    continue
                num = i.pin_numbers.get(pid)
                if num is None:
                    design.issues.append(f'net {net.name}: {i.reference} has no pin {pid}')
                    continue
                pins.add(f'{i.reference}.{num}')
    merged = {}
    for key, pins in members.items():
        merged.setdefault(uf.find(key), set()).update(pins)
    groups = uf.groups()
    out = {}
    for root, pins in merged.items():
        keys = groups.get(root, [root])
        name = min(keys)
        if len(keys) > 1:
            design.issues.append(f'net {name}: merged aliases {sorted(keys)}')
        out[name] = pins
    return out


@dataclass
class NetDiff:
    net: str
    missing: set = field(default_factory=set)   # 정답에는 있는데 우리 결과에 없는 핀
    extra: set = field(default_factory=set)     # 우리 결과에만 있는 핀


@dataclass
class NetCompare:
    matched: int = 0
    mismatches: list = field(default_factory=list)
    only_ours: list = field(default_factory=list)
    only_ref: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.mismatches and not self.only_ours and not self.only_ref


_OVERBAR = re.compile(r'^~\{(.+)\}$')


def alias_net_name(name: str, ref: dict) -> str:
    """KiCad 쪽 넷 이름을 PADS 정답의 표기로 맞춘다(정답에 그 이름이 있을 때만).

    OrCAD 가 PADS 넷리스트로 내보낼 때 오버바 이름(백슬래시로 감싼 RESET)은 `/RESET` 이 된다.
    같은 넷을 KiCad 나이틀리 .DSN 임포터는 `{slash}RESET`(회로도 라벨) 으로, kicad-cli 넷리스트
    익스포트는 `RESET` 으로, PADS 보드 임포터는 `~{RESET}` 으로 적는다. 정답에 `name` 이 없고
    `/이름` 이 있으면 그것으로 본다. 그 밖에는 이름을 바꾸지 않는다."""
    if name in ref:
        return name
    m = _OVERBAR.match(name)
    base = m.group(1) if m else name.replace('{slash}', '/')
    for cand in (base, '/' + base.lstrip('/')):
        if cand in ref:
            return cand
    # 대소문자만 다른 경우(OrCAD 의 PADS 넷리스트 익스포트는 대문자화, KiCad .DSN 임포터는 원문
    # 유지: `VBUS_nRF` vs `VBUS_NRF`). 정답에 대소문자만 다른 이름이 정확히 하나면 그것으로 본다.
    lower = base.lower()
    hits = [k for k in ref if k.lower() == lower or k.lower() == '/' + lower.lstrip('/')]
    if len(hits) == 1:
        return hits[0]
    return name


def alias_nets(nets: dict, ref: dict, renames: list = None) -> dict:
    """넷 딕셔너리 전체의 이름을 정답 표기에 맞춘다(이름이 겹치면 핀 집합을 합친다).

    1) 핀 집합이 정답의 어떤 넷과 **완전히 같으면** 그 넷 이름을 쓴다 — 연결이 같으니 이름만 다른
       것이고(같은 라벨을 여러 시트에서 쓰면 OrCAD 와 KiCad 가 `XTAL1`/`XTAL1_1642…` 접미 번호를
       서로 다른 쪽에 붙인다), 한 핀은 한 넷에만 속하므로 이 대응은 모호하지 않다.
    2) 아니면 `alias_net_name`(오버바 표기·대소문자).
    renames 리스트를 주면 이름이 실제로 바뀐 (원래 이름, 정답 이름) 쌍을 채워 준다."""
    by_pins = {frozenset(v): k for k, v in ref.items() if v}
    out = {}
    for name, pins in nets.items():
        key = by_pins.get(frozenset(pins)) if pins else None
        if key is None:
            key = alias_net_name(name, ref)
        if key != name and renames is not None:
            renames.append((name, key))
        out.setdefault(key, set()).update(pins)
    return out


def compare_netlists(ours: dict, ref: dict) -> NetCompare:
    cmp = NetCompare()
    for name in sorted(set(ours) | set(ref)):
        if name not in ref:
            cmp.only_ours.append(name)
        elif name not in ours:
            cmp.only_ref.append(name)
        elif ours[name] == ref[name]:
            cmp.matched += 1
        else:
            cmp.mismatches.append(NetDiff(name, ref[name] - ours[name], ours[name] - ref[name]))
    return cmp


def format_report(cmp: NetCompare) -> str:
    lines = [f'matched nets: {cmp.matched}', f'mismatched nets: {len(cmp.mismatches)}',
             f'only in ours: {len(cmp.only_ours)}', f'only in reference: {len(cmp.only_ref)}']
    for d in cmp.mismatches:
        lines.append(f'  [DIFF] {d.net}: missing={sorted(d.missing)} extra={sorted(d.extra)}')
    for n in cmp.only_ours:
        lines.append(f'  [OURS ONLY] {n}')
    for n in cmp.only_ref:
        lines.append(f'  [REF ONLY] {n}')
    lines.append('RESULT: ' + ('PASS' if cmp.ok else 'FAIL'))
    return '\n'.join(lines)
