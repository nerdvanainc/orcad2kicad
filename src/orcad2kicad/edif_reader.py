"""OrCAD Capture EDIF 2.0.0 익스포트 -> model.Design.

구조는 HANDOVER.md 3장과 docs/2026-09-08/[설계]_[01] 의 "확정된 사실" 절을 따른다.
"""
from __future__ import annotations
import re
from .sexp import parse, head, child, children, walk, edif_name, edif_id
from .model import (Design, Page, Symbol, Pin, Graphic, Instance, Wire, Label,
                    PowerPort, OffPage, Text, Net, symbol_key)

TEMPLATE_LIB = 'DEFAULT'
_AUTO_NET = re.compile(r'N\d+$')
_OFFPAGE_SUFFIX = '_OFF_PAGE_CONNECTOR'
# OrCAD EDIF 익스포트는 ASCII 밖의 바이트와 제어문자를 `%십진수%` 로 이스케이프한다.
# (예: `SENSORS%13%%10%` = CR LF, `%184%%240%%181%%226%` = cp949 "모듈")
_ESCAPE_RUN = re.compile(r'(?:%(\d{1,3})%)+')
_ESCAPE_ONE = re.compile(r'%(\d{1,3})%')


def decode_orcad_text(s):
    """OrCAD EDIF 문자열의 `%십진수%` 이스케이프를 복원한다.

    연속한 `%d%` 는 한 덩어리의 바이트열로 모아 cp949(한국어 OrCAD 기본 코드페이지)로 디코드하고,
    실패하면 latin-1 로 떨어진다(바이트를 잃지 않기 위함). CR(13)/LF(10) 은 줄바꿈 `\\n` 하나로
    바꾸며(CRLF 도 하나), 문자열 끝의 줄바꿈은 지운다 - OrCAD 텍스트 상자는 마지막 줄에도 개행을
    붙여 내보내므로 그대로 두면 KiCad 텍스트에 빈 줄이 생긴다.

    그 밖의 C0 제어문자(0~31 중 탭 9, LF 10, CR 13 을 뺀 것 — 예: `%0%` NUL, `%7%` BEL)는
    버린다. 그대로 두면 `.kicad_sym`/`.kicad_sch` 의 S-식 문자열 안에 제어문자가 들어가
    KiCad 가 파일을 못 읽는다.
    """
    if not s or '%' not in s:
        return s

    def repl(m):
        raw = bytearray()      # 연속한 비ASCII 바이트 (한 번에 cp949 로 디코드해야 한다)
        out = []
        def flush():
            text = _flush(raw)
            if text:
                out.append(text)

        prev = None            # 직전 이스케이프 값 (CR 바로 뒤의 LF 만 합치기 위함)
        for one in _ESCAPE_ONE.finditer(m.group(0)):
            v = int(one.group(1))
            if v in (10, 13):
                flush()
                if not (v == 10 and prev == 13):    # CR LF 는 줄바꿈 하나로, 그 밖의 LF 는 각각 한 줄
                    out.append('\n')
            elif v < 32 and v != 9:                      # 그 밖의 C0 제어문자(%0% NUL, %7% BEL ...)
                # KiCad S-식 문자열에 그대로 들어가면 파일이 깨지므로 버린다(탭 9 는 남긴다).
                flush()
            elif v > 255:                                # 바이트가 아니므로 원문 유지
                flush()
                out.append(one.group(0))
            else:
                raw.append(v)
            prev = v
        flush()
        return ''.join(out)

    return _ESCAPE_RUN.sub(repl, s).rstrip('\n')


def _flush(raw):
    """모아둔 바이트열을 cp949(실패 시 latin-1)로 디코드하고 버퍼를 비운다."""
    if not raw:
        return ''
    b = bytes(raw)
    raw.clear()
    try:
        return b.decode('cp949')
    except UnicodeDecodeError:
        return b.decode('latin-1')


# ---------- 공용 헬퍼 ----------

def _pt(node):
    """(pt x y) -> (x, y)"""
    return (int(node[1]), int(node[2]))


def _prop_node(node, name):
    """(property (rename ID "이름") ...) 또는 (property ID ...) 를 표시 이름으로 찾는다."""
    for p in children(node, 'property'):
        if edif_name(p[1]) == name or edif_id(p[1]) == name:
            return p
    return None


def _display_origin(node):
    """노드 아래 첫 (display ... (origin (pt x y))) 의 origin."""
    for d in walk(node, 'display'):
        o = child(d, 'origin')
        if o is not None:
            return _pt(child(o, 'pt'))
    return None


def _string_value(valnode):
    """(string "x") | (string (stringDisplay "x" ...)) | (integer n) -> (문자열, display origin)."""
    if valnode is None or len(valnode) < 2:
        return '', None
    v = valnode[1]
    if isinstance(v, list) and head(v) == 'stringDisplay':
        return (str(v[1]) if len(v) > 1 else ''), _display_origin(v)
    return str(v), None


def _string_style(valnode):
    """(string (stringDisplay "x" (display ...))) -> (justify, orientation). 아니면 ('', 'R0')."""
    if valnode is None or len(valnode) < 2:
        return '', 'R0'
    v = valnode[1]
    if isinstance(v, list) and head(v) == 'stringDisplay':
        return _display_style(v)
    return '', 'R0'


def _value_node(propnode):
    """property 노드의 값 노드 ((string ...) / (integer ...) / (number ...))."""
    for c in propnode[2:]:
        if isinstance(c, list) and head(c) in ('string', 'integer', 'number'):
            return c
    return None


def _prop(node, name, default=''):
    p = _prop_node(node, name)
    if p is None:
        return default
    return _string_value(_value_node(p))[0]


def _prop_with_pos(node, name):
    p = _prop_node(node, name)
    if p is None:
        return '', None
    return _string_value(_value_node(p))


def _display_attrs(node):
    """(display ...) 에서 justify / orientation / textHeight 추출.

    justify 는 EDIF 에 없으면 '' 를 돌려준다(호출부가 용도별 기본값을 정한다).
    orientation 은 없으면 'R0'."""
    just, rot, height = '', 'R0', 10
    for d in walk(node, 'display'):
        j = child(d, 'justify')
        if j is not None:
            just = str(j[1])
        o = child(d, 'orientation')
        if o is not None:
            rot = str(o[1])
        for th in walk(d, 'textHeight'):
            height = int(th[1])
        break
    return just, rot, height


def _display_style(node):
    """노드 아래 첫 display 의 (justify, orientation). 없으면 ('', 'R0')."""
    just, rot, _ = _display_attrs(node)
    return just, rot


def _transform(node):
    """(transform (orientation R90) (origin (pt x y))) -> (orientation, origin)"""
    t = child(node, 'transform')
    orient, origin = 'R0', (0, 0)
    if t is not None:
        o = child(t, 'orientation')
        if o is not None:
            orient = str(o[1])
        og = child(t, 'origin')
        if og is not None:
            origin = _pt(child(og, 'pt'))
    return orient, origin


def _bool(s, default=True):
    if s == '':
        return default
    return s.strip().lower() in ('true', '1', 'yes')


# ---------- 그래픽 ----------

def _read_figure(fig, issues, where):
    """(figure GROUP prim...) 또는 (figure (figureGroupOverride GROUP ...) prim...) -> [Graphic]"""
    out = []
    for prim in fig[1:]:
        if not isinstance(prim, list):
            continue
        h = head(prim)
        if h == 'path':
            pts = [_pt(p) for p in children(child(prim, 'pointList'), 'pt')]
            if len(pts) >= 2:
                out.append(Graphic('polyline', pts))
        elif h == 'rectangle':
            out.append(Graphic('rectangle', [_pt(p) for p in children(prim, 'pt')]))
        elif h == 'circle':
            out.append(Graphic('circle', [_pt(p) for p in children(prim, 'pt')]))
        elif h == 'polygon':
            pts = [_pt(p) for p in children(child(prim, 'pointList'), 'pt')]
            out.append(Graphic('polygon', pts))
        elif h in ('openShape', 'shape'):
            curve = child(prim, 'curve')
            if curve is not None:
                for arc in children(curve, 'arc'):
                    out.append(Graphic('arc', [_pt(p) for p in children(arc, 'pt')]))
                for pl in children(curve, 'pointList'):
                    pts = [_pt(p) for p in children(pl, 'pt')]
                    out.append(Graphic('polygon' if h == 'shape' else 'polyline', pts))
            else:
                issues.append(f'{where}: unsupported {h} without curve')
        elif h in ('figureGroupOverride', 'fillPattern'):
            continue
        else:
            issues.append(f'{where}: unsupported primitive {h}')
    return out


def _read_annotate(ann):
    """(annotate (stringDisplay "text" (display ... (origin ...)))) -> Graphic('text') 또는 None"""
    sd = child(ann, 'stringDisplay')
    if sd is None or len(sd) < 2:
        return None
    text = decode_orcad_text(str(sd[1]))
    pos = _display_origin(sd) or (0, 0)
    just, rot, height = _display_attrs(sd)
    return Graphic('text', [pos], text=text, text_height=height, justify=just or 'UPPERLEFT', rotation=rot)


# ---------- 셀 -> Symbol ----------

def _cell_type(cell, lib_id):
    ct = _prop(cell, 'CELLTYPE')
    if ct == 'pagePort':
        return 'pagePort'
    if ct == 'offPageConnector':
        return 'offPageConnector'
    if lib_id == TEMPLATE_LIB or edif_id(cell[1]).startswith('TITLEBLOCK'):
        return 'template'
    return 'part'


def _read_symbol(cell, lib_id, issues):
    cell_id = edif_id(cell[1])
    key = symbol_key(lib_id, cell_id)
    sym = Symbol(key=key, lib_id=lib_id, cell_id=cell_id, cell_name=edif_name(cell[1]),
                 cell_type=_cell_type(cell, lib_id))
    sym.source_package = _prop(cell, 'Source Package')
    sym.position_in_package = _prop(cell, 'PositionInPackage')
    view = child(cell, 'view')
    if view is None:
        return sym
    iface = child(view, 'interface')
    ports = {}
    symnode = None
    if iface is not None:
        # 포트 (핀 전기 정보)
        for port in children(iface, 'port'):
            pid = edif_id(port[1])
            d = child(port, 'designator')
            number = str(d[1]) if d is not None and len(d) > 1 else ''
            name = _prop(port, 'Name')
            ptype = _prop(port, 'Type')
            pkg = _prop(port, 'PackagePortNumbers')
            invisible = _prop(port, 'INVISIBLEPIN')
            implicit_class = _prop(port, 'IMPLICITPORTCLASS')
            ports[pid] = (number, name, ptype, [s.strip() for s in pkg.split(',')] if pkg else [],
                          invisible, implicit_class)
        d = child(iface, 'designator')
        if d is not None and len(d) > 1:
            sym.ref_prefix = str(d[1]).rstrip('?') or 'U'
        sym.footprint = _prop(iface, 'PCB Footprint')
        sym.value, sym.value_pos = _prop_with_pos(iface, 'Value')
        sym.pin_numbers_visible = _bool(_prop(iface, 'Pin Numbers Visible'))
        sym.pin_names_visible = _bool(_prop(iface, 'Pin Names Visible'))
        symnode = child(iface, 'symbol')
    # 심볼 그래픽 + 핀 위치
    if symnode is not None:
        for x in symnode[1:]:
            if not isinstance(x, list):
                continue
            h = head(x)
            if h == 'portImplementation':
                pid = edif_id(x[1])
                number, name, ptype, pkg, invisible, implicit_class = ports.get(
                    pid, ('', '', '', [], '', ''))
                connect = None
                cl = child(x, 'connectLocation')
                if cl is not None:
                    dot = next(walk(cl, 'dot'), None)
                    if dot is not None:
                        connect = _pt(child(dot, 'pt'))
                body = None
                shape = 'line'
                has_lead_figure = False
                for fig in children(x, 'figure'):
                    for g in _read_figure(fig, issues, f'{key}/{pid}'):
                        if g.kind == 'polyline' and len(g.pts) == 2:
                            has_lead_figure = True
                            if connect is None:
                                connect = g.pts[0]
                            body = g.pts[1] if g.pts[0] == connect else g.pts[0]
                        elif g.kind == 'circle':
                            shape = 'inverted'
                if connect is None:
                    issues.append(f'{key}: pin {pid} has no connect location')
                    connect = (0, 0)
                if body is None:
                    body = connect
                # 숨은 핀: INVISIBLEPIN == TRUE 이거나 리드선(figure)이 아예 없는 경우.
                # 이런 핀은 IMPLICITPORTCLASS 로 지정된 전역 넷에 와이어 없이 암묵 접속된다
                # (예: 레귤레이터의 GND 핀).
                hidden = invisible.strip().upper() == 'TRUE' or not has_lead_figure
                name_pos = _display_origin(x[1]) if isinstance(x[1], list) else None
                kd = child(x, 'keywordDisplay')
                number_pos = _display_origin(kd) if kd is not None else None
                sym.pins.append(Pin(port_id=pid, number=number, name=name, etype=ptype,
                                    connect=connect, body=body, package_numbers=pkg,
                                    shape=shape, name_pos=name_pos, number_pos=number_pos,
                                    hidden=hidden, implicit_class=implicit_class))
            elif h == 'figure':
                sym.graphics.extend(_read_figure(x, issues, key))
            elif h == 'annotate':
                g = _read_annotate(x)
                if g is not None:
                    sym.graphics.append(g)
            elif h == 'keywordDisplay' and str(x[1]) == 'designator':
                sym.ref_pos = _display_origin(x)
            elif h == 'property':
                pname = edif_name(x[1])
                if pname == 'Value':
                    v, pos = _string_value(_value_node(x))
                    if pos is not None:
                        sym.value_pos = pos
                    if v and not sym.value:
                        sym.value = v
                elif pname == 'Pin Numbers Visible':
                    sym.pin_numbers_visible = _bool(_string_value(_value_node(x))[0])
                elif pname == 'Pin Names Visible':
                    sym.pin_names_visible = _bool(_string_value(_value_node(x))[0])
    # 전원/오프페이지/템플릿 셀: 그래픽은 view > contents > figure
    cont = child(view, 'contents')
    if cont is not None and sym.cell_type in ('pagePort', 'offPageConnector', 'template'):
        for fig in children(cont, 'figure'):
            sym.graphics.extend(_read_figure(fig, issues, key))
        for ann in children(cont, 'annotate'):
            g = _read_annotate(ann)
            if g is not None:
                sym.graphics.append(g)
    return sym


# ---------- 페이지 ----------

def _ref_and_unit(raw, props):
    """designator 원문과 프로퍼티(Designator, PositionInPackage) -> (reference, unit)"""
    suffix = props.get('Designator', '')
    ref = raw
    if suffix and raw.endswith(suffix) and len(raw) > len(suffix):
        ref = raw[:-len(suffix)]
    try:
        unit = int(props.get('PositionInPackage', '0') or '0') + 1
    except ValueError:
        unit = 1
    return ref, unit


def _resolve_pin_number(inst_number, pin, unit, ref, issues):
    """핀 번호 대체 순서: 인스턴스 designator -> 셀 designator -> PackagePortNumbers[unit-1] -> Name -> 포트ID."""
    if inst_number:
        return inst_number
    if pin.number:
        return pin.number
    if 0 <= unit - 1 < len(pin.package_numbers) and pin.package_numbers[unit - 1]:
        issues.append(f'{ref}: pin {pin.port_id} number from PackagePortNumbers')
        return pin.package_numbers[unit - 1]
    if pin.name:
        issues.append(f'{ref}: pin {pin.port_id} number from pin Name "{pin.name}"')
        return pin.name
    issues.append(f'{ref}: pin {pin.port_id} number from port id')
    return pin.port_id.lstrip('&')


def _read_instance(node, design):
    d = child(node, 'designator')
    if d is None or len(d) < 2:
        return None                       # TITLEBLOCK / PAGE_BORDER
    if isinstance(d[1], list) and head(d[1]) == 'stringDisplay':
        raw, ref_pos = str(d[1][1]), _display_origin(d[1])
        ref_justify, ref_rot = _display_style(d[1])
    else:
        raw, ref_pos = str(d[1]), None
        ref_justify, ref_rot = '', 'R0'
    vr = child(node, 'viewRef')
    cell_ref = child(vr, 'cellRef')
    cell_id = edif_id(cell_ref[1])
    lib_id = edif_id(child(cell_ref, 'libraryRef')[1])
    key = symbol_key(lib_id, cell_id)
    sym = design.symbols.get(key)
    if sym is None:
        design.issues.append(f'{raw}: unknown cell {key}')
        sym = Symbol(key=key, lib_id=lib_id, cell_id=cell_id, cell_name=cell_id, cell_type='part')
        design.symbols[key] = sym
    props = {}
    value_pos = None
    value_justify, value_rot = '', 'R0'
    for p in children(node, 'property'):
        name = edif_name(p[1])
        vnode = _value_node(p)
        v, pos = _string_value(vnode)
        props[name] = v
        if name == 'Value' and pos is not None:
            value_pos = pos
            value_justify, value_rot = _string_style(vnode)
    ref, unit = _ref_and_unit(raw, props)
    orient, origin = _transform(node)
    ins = Instance(edif_id=edif_id(node[1]), symbol=key, designator_raw=raw, reference=ref, unit=unit,
                   value=props.get('Value', sym.value), footprint=props.get('PCB Footprint', sym.footprint),
                   origin=origin, orientation=orient, ref_pos=ref_pos, value_pos=value_pos,
                   ref_rot=ref_rot, ref_justify=ref_justify,
                   value_rot=value_rot, value_justify=value_justify, props=props)
    inst_numbers = {}
    for pi in children(node, 'portInstance'):
        pid = edif_id(pi[1])
        dd = child(pi, 'designator')
        num = ''
        if dd is not None and len(dd) > 1:
            num = str(dd[1][1]) if isinstance(dd[1], list) and len(dd[1]) > 1 else ('' if isinstance(dd[1], list) else str(dd[1]))
        inst_numbers[pid] = num
    for pin in sym.pins:
        ins.pin_numbers[pin.port_id] = _resolve_pin_number(inst_numbers.get(pin.port_id, ''), pin, unit, ref, design.issues)
    for pid, num in inst_numbers.items():
        if pid not in ins.pin_numbers:
            ins.pin_numbers[pid] = num or pid.lstrip('&')
            design.issues.append(f'{ref}: portInstance {pid} not in cell {key}')
    return ins


def _read_port_impl(node, design, page):
    """페이지 레벨 portImplementation -> PowerPort 또는 OffPage"""
    pid = edif_id(node[1])
    label_pos = _display_origin(node[1]) if isinstance(node[1], list) else None
    label_justify, label_rot = _display_style(node[1]) if isinstance(node[1], list) else ('', 'R0')
    ins = child(node, 'instance')
    if ins is None:
        design.issues.append(f'page {page.name}: portImplementation {pid} without instance')
        return
    vr = child(ins, 'viewRef')
    cell_ref = child(vr, 'cellRef')
    key = symbol_key(edif_id(child(cell_ref, 'libraryRef')[1]), edif_id(cell_ref[1]))
    sym = design.symbols.get(key)
    net_name = edif_name(ins[1])
    orient, origin = _transform(ins)
    pos = origin
    cl = child(node, 'connectLocation')
    if cl is not None:
        dot = next(walk(cl, 'dot'), None)
        if dot is not None:
            pos = _pt(child(dot, 'pt'))
    ctype = sym.cell_type if sym is not None else 'pagePort'
    if ctype == 'offPageConnector':
        page.offpages.append(OffPage(net=net_name, pos=pos, symbol=key, origin=origin,
                                     orientation=orient, label_pos=label_pos,
                                     label_rot=label_rot, label_justify=label_justify, port_id=pid))
    else:
        page.power_ports.append(PowerPort(net=net_name, pos=pos, symbol=key, origin=origin,
                                          orientation=orient, label_pos=label_pos,
                                          label_rot=label_rot, label_justify=label_justify, port_id=pid))


def _read_net(node, design, page):
    nd = node[1]
    nid = edif_id(nd)
    name = edif_name(nd)
    label_positions = []
    label_styles = []                      # label_positions 와 같은 순서의 (justify, orientation)
    if isinstance(nd, list) and head(nd) == 'name':
        for disp in children(nd, 'display'):
            o = child(disp, 'origin')
            if o is not None:
                label_positions.append(_pt(child(o, 'pt')))
                j = child(disp, 'justify')
                r = child(disp, 'orientation')
                label_styles.append((str(j[1]) if j is not None else '',
                                     str(r[1]) if r is not None else 'R0'))
    named = not _AUTO_NET.fullmatch(name)
    net = Net(edif_id=nid, name=name, named=named, label_positions=label_positions)
    joined = child(node, 'joined')
    if joined is not None:
        for pr in children(joined, 'portRef'):
            ir = child(pr, 'instanceRef')
            if ir is not None:
                net.pins.append((edif_id(ir[1]), edif_id(pr[1])))
            else:
                net.page_ports.append(edif_id(pr[1]))
    for fig in children(node, 'figure'):
        for g in _read_figure(fig, design.issues, f'page {page.name} net {name}'):
            if g.kind == 'polyline':
                for a, b in zip(g.pts, g.pts[1:]):
                    if a != b:
                        net.wires.append(Wire(a, b, nid))
    for ins in children(node, 'instance'):
        orient, origin = _transform(ins)
        net.junctions.append(origin)
    for pos, (just, rot) in zip(label_positions, label_styles):
        page.labels.append(Label(name, pos, nid, rotation=rot, justify=just))
    page.wires.extend(net.wires)
    page.junctions.extend(net.junctions)
    page.nets.append(net)


def _read_page(node, design):
    name = edif_name(node[1])
    size = (0, 0)
    ps = child(node, 'pageSize')
    if ps is not None:
        pts = [_pt(p) for p in children(child(ps, 'rectangle'), 'pt')]
        size = (abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1]))
    page = Page(name=name, size=size)
    cg = child(node, 'commentGraphics')
    if cg is not None:
        for ann in walk(cg, 'annotate'):
            g = _read_annotate(ann)
            if g is not None:
                page.texts.append(Text(g.text, g.pts[0], g.text_height, g.justify, g.rotation))
    for ins in children(node, 'instance'):
        i = _read_instance(ins, design)
        if i is not None:
            page.instances.append(i)
    for pi in children(node, 'portImplementation'):
        _read_port_impl(pi, design, page)
    for net in children(node, 'net'):
        _read_net(net, design, page)
    return page


def _read_root(root_cell, design):
    view = child(root_cell, 'view')
    iface = child(view, 'interface')
    # 루트 포트: ID -> (표시 이름, 클래스). 클래스가 이름과 다르면 alias.
    for port in children(iface, 'port'):
        pid = edif_id(port[1])
        pname = edif_name(port[1])
        cls = _prop(port, 'IMPLICITPORTCLASS') or pname
        design.root_ports[pid] = (pname, cls)
        design.page_port_names[pid] = cls
        if cls != pname:
            design.power_aliases[pname] = cls
    cont = child(view, 'contents')
    for o in children(cont, 'offPageConnector'):
        oid = edif_id(o[1])
        if oid.endswith(_OFFPAGE_SUFFIX):
            base = oid[:-len(_OFFPAGE_SUFFIX)]
            design.page_port_names[oid] = design.root_ports[base][1] if base in design.root_ports else base
        else:
            design.page_port_names[oid] = edif_name(o[1])
    for pg in children(cont, 'page'):
        design.pages.append(_read_page(pg, design))


def _find_root_cell(design_lib):
    for cell in children(design_lib, 'cell'):
        view = child(cell, 'view')
        if view is None:
            continue
        cont = child(view, 'contents')
        if cont is not None and children(cont, 'page'):
            return cell
    return None


# ---------- 최상위 ----------

def read_design(tree) -> Design:
    """parse() 결과 -> Design. 라이브러리/셀을 Symbol로 읽은 뒤 루트 스키매틱 셀의 페이지를 읽는다."""
    edif = tree[0] if isinstance(tree[0], list) and head(tree[0]) == 'edif' else tree
    libs = children(edif, 'library')
    if not libs:
        raise ValueError('no (library ...) in EDIF')
    design_lib = libs[-1]
    design = Design(name=edif_name(design_lib[1]))
    for lib in libs:
        lib_id = edif_id(lib[1])
        for cell in children(lib, 'cell'):
            sym = _read_symbol(cell, lib_id, design.issues)
            design.symbols[sym.key] = sym
    root = _find_root_cell(design_lib)
    if root is None:
        raise ValueError('no root schematic cell with pages')
    _read_root(root, design)
    return design


def load_edif(path: str) -> Design:
    with open(path, encoding='latin-1') as f:
        text = f.read()
    return read_design(parse(text, escape=False))  # EDIF는 백슬래시 이스케이프 규약이 없음(리터럴 그대로)
