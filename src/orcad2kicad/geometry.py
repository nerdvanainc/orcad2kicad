"""지오메트리: orientation 행렬, 월드좌표, 와이어 토폴로지 기반 넷 유도.

목적: 우리가 계산한 핀/포트/라벨 위치와 와이어만으로 union-find 한 결과가 정답 넷리스트와 같으면
orientation 해석·접속점 오프셋·라벨 스냅이 전부 맞다는 뜻이다 (HANDOVER 6장 2번 검증).
"""
from __future__ import annotations
from .verify import UnionFind

# EDIF 심볼 좌표(y 위) -> 페이지 좌표(y 위). p' = M·p + origin
ORIENT = {
    'R0':    ((1, 0), (0, 1)),
    'R90':   ((0, -1), (1, 0)),
    'R180':  ((-1, 0), (0, -1)),
    'R270':  ((0, 1), (-1, 0)),
    'MX':    ((1, 0), (0, -1)),
    'MY':    ((-1, 0), (0, 1)),
    'MXR90': ((0, 1), (1, 0)),
    'MYR90': ((0, -1), (-1, 0)),
}


def transform_point(orientation, origin, p):
    (a, b), (c, d) = ORIENT[orientation]
    x, y = p
    return (a * x + b * y + origin[0], c * x + d * y + origin[1])


def pin_world(instance, pin):
    """인스턴스에 배치된 핀의 접속점 페이지 좌표."""
    return transform_point(instance.orientation, instance.origin, pin.connect)


def _on_segment(p, a, b):
    """p가 선분 ab 위(끝점 포함)에 있는가. 정수 좌표, 외적으로 판단."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) - (by - ay) * (px - ax) != 0:
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def _project(p, a, b):
    """p를 선분 ab에 투영한 점(정수 반올림)과 거리 제곱."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        q = a
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / L2
        t = max(0.0, min(1.0, t))
        q = (round(ax + t * dx), round(ay + t * dy))
    return q, (q[0] - px) ** 2 + (q[1] - py) ** 2


def snap_to_wires(pos, wires, tol=30):
    """pos에서 tol 이내의 가장 가까운 와이어 위 점. 없으면 None."""
    best, bd = None, tol * tol + 1
    for w in wires:
        q, d2 = _project(pos, w.a, w.b)
        if d2 < bd:
            best, bd = q, d2
    return best


def _page_components(design, page):
    """페이지 하나의 연결 성분.
    반환: names{root: set(전역 이름)}, pins{root: set(REF.PIN)}, wnets{root: set(EDIF 넷 이름)}"""
    uf = UnionFind()
    wires = page.wires
    # 1) 와이어 양 끝
    for w in wires:
        uf.union(w.a, w.b)
    # 2) 단자점: 핀, 전원/오프페이지 접속점, 정션, 라벨(스냅)
    pins = {}
    names_at = {}
    pin_point = {}   # (instance_edif_id, port_id) -> 월드 좌표 (아래 EDIF 넷 매칭용 폴백에 사용)
    for ins in page.instances:
        sym = design.symbols[ins.symbol]
        for pin in sym.pins:
            p = pin_world(ins, pin)
            uf.find(p)
            pins.setdefault(p, set()).add(f'{ins.reference}.{ins.pin_numbers[pin.port_id]}')
            pin_point[(ins.edif_id, pin.port_id)] = p
            # 숨은 POWER 핀(EDIF INVISIBLEPIN==TRUE 이거나 리드선 figure가 없음)은 와이어 없이
            # OrCAD가 IMPLICITPORTCLASS 로 지정한 전역 넷에 암묵 접속시킨다 (예: 레귤레이터 GND 핀).
            # IMPLICITPORTCLASS도 핀 이름도 없으면 접속시킬 넷 이름이 없다는 뜻이므로 이슈로 남긴다.
            if pin.hidden and pin.etype.upper() in ('POWER', 'PWR'):
                cls = pin.implicit_class or pin.name
                if cls:
                    names_at.setdefault(p, set()).add(design.canonical(cls))
                else:
                    design.issues.append(
                        f'page {page.name}: hidden power pin {ins.reference}.{ins.pin_numbers[pin.port_id]} has no net name')
    for pp in page.power_ports:
        uf.find(pp.pos)
        names_at.setdefault(pp.pos, set()).add(design.canonical(pp.net))
    for op in page.offpages:
        uf.find(op.pos)
        names_at.setdefault(op.pos, set()).add(design.canonical(op.net))
    for j in page.junctions:
        uf.find(j)
    wire_net = {}
    net_wires = {}
    for net in page.nets:
        net_wires[net.edif_id] = net.wires
        for w in net.wires:
            wire_net.setdefault(w.a, set()).add(net.name)
        # 와이어 없이 핀끼리 직접 맞닿아 라벨도 없는 넷(예: 인덕터-레귤레이터 VOUT 직결)은
        # EDIF 넷의 핀 목록으로 이름 폴백을 채운다. PADS 쪽도 이런 넷은 원 OrCAD 넷 ID를
        # 그대로 자동 이름(N수字...)으로 쓰므로, EDIF net.name 이 이미 그 이름과 같다.
        for inst_id, pid in net.pins:
            p = pin_point.get((inst_id, pid))
            if p is not None:
                wire_net.setdefault(p, set()).add(net.name)
    for lb in page.labels:
        q = snap_to_wires(lb.pos, net_wires.get(lb.net_id, []))
        if q is None:
            q = snap_to_wires(lb.pos, wires)
        if q is None:
            design.issues.append(f'page {page.name}: label {lb.text} at {lb.pos} not near any wire')
            continue
        uf.find(q)
        names_at.setdefault(q, set()).add(design.canonical(lb.text))
    # 3) 점이 세그먼트 내부에 닿으면 연결 (T 접합, 와이어 중간의 핀/포트)
    points = list(uf.parent.keys())
    for p in points:
        for w in wires:
            if p != w.a and p != w.b and _on_segment(p, w.a, w.b):
                uf.union(p, w.a)
    # 4) 성분별 집계
    names, pinsets, wnets = {}, {}, {}
    for p, s in pins.items():
        pinsets.setdefault(uf.find(p), set()).update(s)
    for p, s in names_at.items():
        names.setdefault(uf.find(p), set()).update(s)
    for p, s in wire_net.items():
        wnets.setdefault(uf.find(p), set()).update(s)
    return names, pinsets, wnets


def derive_nets(design):
    """전 페이지 지오메트리에서 {정규 넷 이름: {REF.PIN}} 유도."""
    guf = UnionFind()        # 키: ('name', 이름) 또는 ('comp', 페이지idx, root)
    comp_pins = {}
    comp_fallback = {}
    for pi, page in enumerate(design.pages):
        names, pinsets, wnets = _page_components(design, page)
        roots = set(pinsets) | set(names) | set(wnets)
        for r in roots:
            ck = ('comp', pi, r)
            guf.find(ck)
            for nm in names.get(r, ()):
                guf.union(('name', nm), ck)
            comp_pins[ck] = pinsets.get(r, set())
            comp_fallback[ck] = sorted(design.canonical(n) for n in wnets.get(r, ()))
    out = {}
    unnamed = 0
    for root, keys in guf.groups().items():
        names = sorted(k[1] for k in keys if k[0] == 'name')
        pins = set()
        fallback = []
        for k in keys:
            if k[0] == 'comp':
                pins.update(comp_pins.get(k, ()))
                fallback.extend(comp_fallback.get(k, ()))
        if not pins:
            continue
        if names:
            name = names[0]
            if len(names) > 1:
                design.issues.append(f'geometry: names {names} are shorted')
        elif fallback:
            name = sorted(set(fallback))[0]
        else:
            if len(pins) < 2:
                continue            # 미연결 단일 핀
            unnamed += 1
            name = f'N_GEOM_{unnamed}'
            design.issues.append(f'geometry: unnamed net {name} pins {sorted(pins)}')
        if name in out:
            design.issues.append(f'geometry: net {name} appears as separate components; merging')
            out[name].update(pins)
        else:
            out[name] = pins
    return out
