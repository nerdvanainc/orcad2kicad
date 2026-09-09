"""KiCad 회로도(.kicad_pro / .kicad_sch) 리더 — "KiCad 임포트 프로젝트 입력 모드"의 입구.

KiCad 나이틀리(>=10.99)의 네이티브 OrCAD `.DSN` 임포터가 만든 프로젝트를 읽어
`SchematicView`(레퍼런스 -> 핀 번호·풋프린트·심볼 경로·유닛)를 만든다. EDIF 경로의
`model.Design` 과 같은 역할을 하되, 원본은 KiCad 파일이므로 다음 원칙을 지킨다.

* **최소 수정**: 임포트된 시트 파일은 재직렬화하지 않는다. 풋프린트 필드를 채울 때도
  해당 심볼 블록 안의 `(property "Footprint" "…")` 값 문자열만 텍스트 단위로 바꾸고
  나머지 바이트는 그대로 둔다(`set_footprint_fields`). 파일 포맷 버전(20260830 등)도
  손대지 않는다.
* **읽기는 파서로, 쓰기는 텍스트로**: 구조 해석은 `sexp.parse`로 하고, 파일에 다시 쓸 때는
  원문 텍스트의 블록 구간(span)만 잘라 붙인다.

임포트 결과의 구조(2026-09-09 나이틀리 10.99.0.3703 실측):
  - `.kicad_pro` 의 `sheets` = `[[uuid, 이름], …]` (최상위 시트 목록, 계층 없음)
  - 루트 시트 파일은 `STEM.kicad_sch`, 나머지는 `P02_<페이지명>.kicad_sch` …
  - 심볼 인스턴스: `(symbol (lib_id "orcad_import:RESISTOR") … (uuid …)
    (property "Reference" "R6" …) (pin "1" (uuid …)) … (instances (project "MYBOARD"
    (path "/<시트 uuid>" (reference "R6") (unit 1)))))`
  - 전원 심볼의 레퍼런스는 `#PWR…` (레퍼런스 집합에서 제외한다)
  - 심볼 정의는 각 시트의 `lib_symbols` 에 내장되어 있고 별명은 `orcad_import`
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field

from .sexp import parse, head, child, children
from .kicad_writer import FORMAT_VERSION as _SYMBOL_LIB_VERSION

POWER_PREFIX = '#'
DEFAULT_LIB_NICK = 'orcad_import'


# --------------------------------------------------------------------------------------
# 모델
# --------------------------------------------------------------------------------------

@dataclass
class SchSymbol:
    """회로도의 심볼 인스턴스 하나(멀티유닛 부품은 유닛마다 하나씩)."""
    ref: str
    unit: int
    lib_id: str
    value: str
    footprint: str
    uuid: str
    sheet_uuid: str
    pins: list = field(default_factory=list)     # 이 유닛이 가진 핀 번호 문자열
    is_power: bool = False                       # 레퍼런스가 '#' 로 시작(전원/암묵 심볼)
    sheet_file: str = ''                         # 이 심볼이 있는 .kicad_sch 경로
    instance_path: str = ''                      # (instances (project … (path "<p>" …))) 의 <p>
                                                  # (정식 포맷 재구성 후: '/<root>/<sheet>')

    @property
    def path(self) -> str:
        """보드 풋프린트의 `path` 로 쓰는 심볼 인스턴스 경로.

        `instance_path` 가 있으면(정식 포맷 재구성 후에는 항상 있다) 그 값 + '/' + 심볼 uuid.
        없으면(재구성 전 나이틀리 원본, 최상위 시트뿐이라 계층이 없음) 옛 규칙
        '/<시트 uuid>/<심볼 uuid>' 로 돌아간다."""
        base = self.instance_path or f'/{self.sheet_uuid}'
        return f'{base}/{self.uuid}'


@dataclass
class SchematicView:
    """읽어들인 회로도 전체. `model.Design` 자리에 쓸 수 있는 최소 인터페이스를 제공한다."""
    project: str = ''
    pro_path: str = ''
    sheet_files: list = field(default_factory=list)      # .kicad_pro 의 sheets 순서
    sheet_uuids: dict = field(default_factory=dict)      # 파일 경로 -> 시트 uuid
    symbols: list = field(default_factory=list)
    version: int = 0                                     # 시트 파일 포맷 버전 (20260830 등)
    sheet_names: dict = field(default_factory=dict)      # 파일 경로 -> 시트 이름(.kicad_pro 기준)
    issues: list = field(default_factory=list)

    # ---- 조회 -------------------------------------------------------------------------
    def parts(self):
        """전원 심볼을 뺀 실제 부품 인스턴스."""
        return [s for s in self.symbols if not s.is_power]

    def references(self) -> set:
        """전원 심볼(`#…`)을 뺀 레퍼런스 집합."""
        return {s.ref for s in self.symbols if not s.is_power}

    def pins_by_ref(self) -> dict:
        """레퍼런스 -> 핀 번호 집합(멀티유닛은 전 유닛의 합집합)."""
        out = {}
        for s in self.parts():
            out.setdefault(s.ref, set()).update(s.pins)
        return out

    def _min_unit(self) -> dict:
        """레퍼런스 -> 유닛 번호가 가장 작은 인스턴스.

        멀티유닛 부품은 유닛마다 프로퍼티가 따로 있고 값이 다를 수 있어(3단계-A의 U2 사례)
        페이지 순회 순서에 좌우되지 않도록 유닛 최소 인스턴스를 대표로 쓴다."""
        best = {}
        for s in self.parts():
            cur = best.get(s.ref)
            if cur is None or s.unit < cur.unit:
                best[s.ref] = s
        return best

    def footprint_by_ref(self) -> dict:
        """레퍼런스 -> 풋프린트 필드 값(유닛 최소 인스턴스 기준). 임포트 직후에는 전부 ''."""
        return {r: s.footprint for r, s in self._min_unit().items()}

    def symbol_paths(self) -> dict:
        """레퍼런스 -> '/<시트 uuid>/<심볼 uuid>' (유닛 최소 인스턴스 기준).

        `kicad_board.write_project_board` 가 보드 풋프린트의 `path` 를 이 값으로 고쳐 쓴다."""
        return {r: s.path for r, s in self._min_unit().items()}

    def symbols_by_ref(self) -> dict:
        """레퍼런스 -> 인스턴스 리스트(유닛 순)."""
        out = {}
        for s in self.symbols:
            out.setdefault(s.ref, []).append(s)
        for v in out.values():
            v.sort(key=lambda s: s.unit)
        return out

    # ---- Design 호환(덕 타이핑) --------------------------------------------------------
    @property
    def pages(self):
        """`compare_board` 등이 기대하는 `design.pages[].instances[]` 모양의 어댑터."""
        return [_PageView(self)]


@dataclass
class _PageView:
    """`SchematicView` 를 `Design.pages[0]` 처럼 보이게 하는 어댑터."""
    view: object

    @property
    def instances(self):
        return [_InstanceView(s) for s in self.view.parts()]


@dataclass
class _InstanceView:
    """`SchSymbol` 을 `model.Instance` 처럼 보이게 하는 어댑터(compare_board 가 쓰는 속성만)."""
    sym: object

    @property
    def reference(self):
        return self.sym.ref

    @property
    def unit(self):
        return self.sym.unit

    @property
    def footprint(self):
        return self.sym.footprint

    @property
    def pin_numbers(self):
        return {n: n for n in self.sym.pins}


# --------------------------------------------------------------------------------------
# 읽기
# --------------------------------------------------------------------------------------

def _read(path):
    """파일을 원문 그대로 읽는다.

    newline='' 로 열어 개행 변환(universal newlines)을 끈다 — CRLF 파일을 읽어 '\\n' 으로 바꿔
    버리면 `set_footprint_fields` 가 다시 쓸 때 파일 전체의 개행이 바뀌어 "다른 바이트 불변"
    원칙이 깨진다. S-식 파서는 '\\r' 을 공백으로 취급하므로 해석에는 영향이 없다."""
    with open(path, encoding='utf-8', newline='') as fh:
        return fh.read()


def _prop_map(node):
    """(property "이름" "값" …) 들을 {이름: 값} 으로."""
    out = {}
    for p in children(node, 'property'):
        if len(p) >= 3 and isinstance(p[1], str) and isinstance(p[2], str):
            out.setdefault(str(p[1]), str(p[2]))
    return out


def _instance_ref_unit(node, sheet_uuid):
    """(instances (project "P" (path "/uuid" (reference "R6") (unit 1)))) 에서 (ref, unit, path).

    `path` 는 매칭된 (path "…") 의 문자열(선행 슬래시 포함, 예: '/<root>/<sheet>'), 없으면 ''.
    경로의 **마지막 조각**이 이 시트 uuid 와 같은 항목을 우선한다(`strip('/')` 전체 일치가 아니라
    `rsplit('/', 1)[-1]` 비교) — 최상위(`/<sheet>`)와 계층(`/<root>/<sheet>`) 경로 모두 맞는다.
    맞는 항목이 없으면 첫 항목을 쓴다."""
    inst = child(node, 'instances')
    if inst is None:
        return None, None, ''
    first = None
    for proj in children(inst, 'project'):
        for p in children(proj, 'path'):
            r = child(p, 'reference')
            u = child(p, 'unit')
            path_str = str(p[1]) if len(p) > 1 and isinstance(p[1], str) else ''
            got = (str(r[1]) if r and len(r) > 1 else None,
                   int(u[1]) if u and len(u) > 1 and isinstance(u[1], int) else None,
                   path_str)
            if first is None:
                first = got
            if path_str and path_str.rstrip('/').rsplit('/', 1)[-1] == sheet_uuid:
                return got
    return first if first else (None, None, '')


def _parse_sheet(path):
    """한 시트 파일 -> (sheet_uuid, version, [SchSymbol], root_tree)."""
    text = _read(path)
    trees = parse(text)
    if not trees or head(trees[0]) != 'kicad_sch':
        raise ValueError(f'not a .kicad_sch file: {path}')
    root = trees[0]
    u = child(root, 'uuid')
    sheet_uuid = str(u[1]) if u and len(u) > 1 else ''
    v = child(root, 'version')
    version = int(v[1]) if v and len(v) > 1 and isinstance(v[1], int) else 0

    syms = []
    for node in children(root, 'symbol'):        # lib_symbols 안의 정의는 직계 자식이 아니므로 제외된다
        props = _prop_map(node)
        ref, unit, inst_path = _instance_ref_unit(node, sheet_uuid)
        if ref is None:
            ref = props.get('Reference', '')
        if unit is None:
            un = child(node, 'unit')
            unit = int(un[1]) if un and len(un) > 1 and isinstance(un[1], int) else 1
        lib = child(node, 'lib_id')
        uid = child(node, 'uuid')
        pins = [str(p[1]) for p in children(node, 'pin') if len(p) > 1 and isinstance(p[1], str)]
        syms.append(SchSymbol(
            ref=ref, unit=unit,
            lib_id=str(lib[1]) if lib and len(lib) > 1 else '',
            value=props.get('Value', ''),
            footprint=props.get('Footprint', ''),
            uuid=str(uid[1]) if uid and len(uid) > 1 else '',
            sheet_uuid=sheet_uuid,
            pins=pins,
            is_power=ref.startswith(POWER_PREFIX),
            sheet_file=path,
            instance_path=inst_path,
        ))
    return sheet_uuid, version, syms, root


def load_kicad_sheets(sch_paths, project=None) -> SchematicView:
    """`.kicad_pro` 없이 시트 파일 목록만으로 읽는다(주어진 순서를 그대로 시트 순서로 쓴다)."""
    view = SchematicView(project=project or '')
    for path in sch_paths:
        path = os.path.abspath(path)
        sheet_uuid, version, syms, _ = _parse_sheet(path)
        if view.version and version and version != view.version:
            view.issues.append(f'sheet format version differs: {os.path.basename(path)} '
                               f'{version} != {view.version}')
        view.version = view.version or version
        view.sheet_files.append(path)
        view.sheet_uuids[path] = sheet_uuid
        view.symbols.extend(syms)
    if not view.project:
        view.project = _guess_project(view)
    return view


def _guess_project(view):
    """프로젝트 이름 추정: 시트의 (instances (project "…")) -> 첫 시트 파일 stem."""
    for path in view.sheet_files:
        m = re.search(r'\(instances\s*\(project\s+"((?:[^"\\]|\\.)*)"', _read(path))
        if m:
            return m.group(1).replace('\\"', '"').replace('\\\\', '\\')
    if view.sheet_files:
        return os.path.splitext(os.path.basename(view.sheet_files[0]))[0]
    return ''


def load_kicad_project(pro_path) -> SchematicView:
    """`.kicad_pro` 와 같은 폴더의 `.kicad_sch` 들을 읽는다.

    루트 시트는 `STEM.kicad_sch`, 나머지는 `.kicad_pro` 의 `sheets` uuid 로 파일을 매칭한다
    (임포터가 붙이는 `P02_…` 접두는 페이지 이름에서 유도되므로 파일명에 의존하지 않는다)."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    stem = os.path.splitext(os.path.basename(pro_path))[0]
    with open(pro_path, encoding='utf-8') as fh:
        pro = json.load(fh)

    # 폴더의 모든 시트를 먼저 읽어 uuid -> 파일 지도를 만든다.
    by_uuid, parsed, order = {}, {}, []
    for path in sorted(glob.glob(os.path.join(folder, '*.kicad_sch'))):
        path = os.path.abspath(path)
        sheet_uuid, version, syms, _ = _parse_sheet(path)
        parsed[path] = (sheet_uuid, version, syms)
        if sheet_uuid and sheet_uuid not in by_uuid:
            by_uuid[sheet_uuid] = path
        order.append(path)

    view = SchematicView(project=stem, pro_path=pro_path)
    used = set()
    for entry in pro.get('sheets') or []:
        if not isinstance(entry, (list, tuple)) or not entry:
            continue
        uid = str(entry[0])
        name = str(entry[1]) if len(entry) > 1 else ''
        path = by_uuid.get(uid)
        if path is None:
            view.issues.append(f'sheet uuid {uid} ("{name}") has no .kicad_sch file in {folder}')
            continue
        view.sheet_names[path] = name
        used.add(path)
        _append_sheet(view, path, parsed[path])
    # .kicad_pro 에 없는 시트 파일도 빠뜨리지 않는다(수동 편집 프로젝트 대비).
    for path in order:
        if path not in used:
            view.issues.append(f'sheet not listed in .kicad_pro: {os.path.basename(path)}')
            _append_sheet(view, path, parsed[path])
    # 루트 시트(STEM.kicad_sch)를 맨 앞으로.
    root_file = os.path.join(folder, stem + '.kicad_sch')
    if root_file in view.sheet_files and view.sheet_files[0] != root_file:
        view.sheet_files.remove(root_file)
        view.sheet_files.insert(0, root_file)
    return view


def _append_sheet(view, path, parsed):
    sheet_uuid, version, syms = parsed
    if view.version and version and version != view.version:
        view.issues.append(f'sheet format version differs: {os.path.basename(path)} '
                           f'{version} != {view.version}')
    view.version = view.version or version
    view.sheet_files.append(path)
    view.sheet_uuids[path] = sheet_uuid
    view.symbols.extend(syms)


# --------------------------------------------------------------------------------------
# 원문 텍스트 스캐너 (최소 수정용)
# --------------------------------------------------------------------------------------

_HEAD_RE = re.compile(r'[^\s()"]+')


def iter_blocks(text, name=None, depth=1, start=0, end=None):
    """`text` 안에서 지정 깊이의 S-식 블록을 (블록이름, 시작, 끝) 으로 훑는다.

    깊이 0은 최상위 리스트(`(kicad_sch …)`) 자신, 깊이 1은 그 직계 자식이다. 문자열 리터럴
    안의 괄호는 세지 않는다(`\\"` 이스케이프 처리). 심볼 인스턴스는 깊이 1의 `symbol`,
    `lib_symbols` 안의 심볼 정의는 깊이 2이므로 이 한 가지 기준으로 정확히 갈린다."""
    end = len(text) if end is None else end
    stack = []
    i = start
    while i < end:
        c = text[i]
        if c == '"':
            i += 1
            while i < end:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == ';':                     # KiCad 는 쓰지 않지만 방어적으로 줄 주석 무시
            j = text.find('\n', i)
            i = len(text) if j < 0 else j + 1
            continue
        if c == '(':
            stack.append(i)
            i += 1
            continue
        if c == ')':
            if not stack:
                i += 1
                continue
            open_at = stack.pop()
            if len(stack) == depth:
                # '(' 바로 뒤 공백을 건너뛴 첫 토큰이 블록 이름
                k = open_at + 1
                while k < end and text[k] in ' \t\r\n':
                    k += 1
                m = _HEAD_RE.match(text, k)
                blk = m.group(0) if m else ''
                if name is None or blk == name:
                    yield blk, open_at, i + 1
            i += 1
            continue
        i += 1


def _sym_block_ref(block):
    """심볼 블록 텍스트에서 레퍼런스(인스턴스 우선, 없으면 Reference 프로퍼티)를 뽑는다."""
    m = re.search(r'\(reference\s+"((?:[^"\\]|\\.)*)"', block)
    if m:
        return _unq(m.group(1))
    m = re.search(r'\(property\s+"Reference"\s+"((?:[^"\\]|\\.)*)"', block)
    return _unq(m.group(1)) if m else None


def _unq(s):
    return s.replace('\\"', '"').replace('\\\\', '\\')


def _q(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


_FOOTPRINT_PROP = re.compile(r'(\(property\s+"Footprint"\s+")((?:[^"\\]|\\.)*)(")')
_ANY_PROP = re.compile(r'\(property\s+"(?:[^"\\]|\\.)*"\s+"(?:[^"\\]|\\.)*"')


def set_footprint_fields(view, mapping, only_empty=False):
    """각 시트 파일에서 `mapping` 의 레퍼런스에 해당하는 심볼의 Footprint 값만 바꿔 다시 쓴다.

    텍스트 단위 최소 수정: 해당 심볼 블록 안의 `(property "Footprint" "…")` 값 문자열만
    치환하고 그 밖의 바이트는 한 글자도 건드리지 않는다(개행 방식·들여쓰기·포맷 버전 유지).
    멀티유닛 부품은 모든 유닛 인스턴스에 같은 값을 넣는다.

    only_empty=True 면 값이 비어 있는 인스턴스만 채운다.
    반환값: 실제로 값이 바뀐 레퍼런스 목록(정렬)."""
    changed = set()
    for path in view.sheet_files:
        text = _read(path)
        edits = []      # (시작, 끝, 새 텍스트)
        for _, s, e in iter_blocks(text, 'symbol', depth=1):
            block = text[s:e]
            ref = _sym_block_ref(block)
            if ref is None or ref not in mapping:
                continue
            new = mapping[ref]
            m = _FOOTPRINT_PROP.search(block)
            if m is None:
                ins_at, ins_text = _footprint_insert(block, new)
                if ins_at is None:
                    view.issues.append(f'{os.path.basename(path)}: {ref} has no Footprint '
                                       f'property and no place to insert one; skipped')
                    continue
                edits.append((s + ins_at, s + ins_at, ins_text))
                changed.add(ref)
                continue
            cur = _unq(m.group(2))
            if cur == new or (only_empty and cur):
                continue
            edits.append((s + m.start(2), s + m.end(2), _q(new)))
            changed.add(ref)
        if not edits:
            continue
        out = []
        pos = 0
        for a, b, rep in edits:
            out.append(text[pos:a])
            out.append(rep)
            pos = b
        out.append(text[pos:])
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(''.join(out))
    # 메모리 상의 뷰도 갱신해 둔다(파일을 다시 읽지 않아도 되도록).
    for sym in view.symbols:
        if sym.ref in changed and (not only_empty or not sym.footprint):
            sym.footprint = mapping[sym.ref]
    return sorted(changed)


def _footprint_insert(block, value):
    """Footprint 프로퍼티가 없는 심볼 블록에 넣을 (삽입 위치, 텍스트). 없으면 (None, '')."""
    last = None
    for m in _ANY_PROP.finditer(block):
        last = m
    if last is None:
        return None, ''
    # 마지막 프로퍼티 블록의 끝(깊이 1의 property 블록)을 찾는다.
    nl = '\r\n' if '\r\n' in block else '\n'          # 원문 개행 방식을 따라간다
    for _, s, e in iter_blocks(block, 'property', depth=1):
        if s == last.start():
            indent = '\t'
            line_start = block.rfind('\n', 0, s)
            if line_start >= 0:
                indent = block[line_start + 1:s]
            return e, (f'{nl}{indent}(property "Footprint" "{_q(value)}"'
                       f'{nl}{indent}\t(at 0 0 0)'
                       f'{nl}{indent}\t(hide yes)'
                       f'{nl}{indent}\t(effects{nl}{indent}\t\t(font'
                       f'{nl}{indent}\t\t\t(size 1.27 1.27)'
                       f'{nl}{indent}\t\t){nl}{indent}\t){nl}{indent})')
    return None, ''


# --------------------------------------------------------------------------------------
# 내장 심볼 라이브러리 추출
# --------------------------------------------------------------------------------------

_LIBSYM_NAME = re.compile(r'\(symbol\s+"((?:[^"\\]|\\.)*)"')


def extract_embedded_symbols(view, outdir, lib_nick=DEFAULT_LIB_NICK):
    """모든 시트의 `lib_symbols` 를 모아 `<lib_nick>.kicad_sym` 하나로 저장한다.

    임포트된 회로도에는 `sym-lib-table` 이 없어 ERC 가 심볼마다 `lib_symbol_issues` 를 낸다.
    시트에 내장된 정의를 그대로(텍스트 그대로) 뽑아 프로젝트 라이브러리로 만들면 해소된다.
    바깥 심볼 이름의 `<nick>:` 접두는 떼어낸다(라이브러리 파일 안에서는 별명이 없다).
    같은 이름이 여러 시트에 있으면 첫 번째를 쓰고, 내용이 다르면 이슈로 남긴다.

    반환값: (라이브러리 파일 경로, 심볼 개수)"""
    os.makedirs(outdir, exist_ok=True)
    seen = {}        # 이름 -> 블록 텍스트(정규화된 것)
    order = []
    prefix = lib_nick + ':'
    for path in view.sheet_files:
        text = _read(path)
        for _, ls, le in iter_blocks(text, 'lib_symbols', depth=1):
            # lib_symbols 의 '(' 를 건너뛰고 스캔하므로 그 직계 자식은 상대 깊이 0이다
            for _, s, e in iter_blocks(text, 'symbol', depth=0, start=ls + 1, end=le):
                block = text[s:e]
                m = _LIBSYM_NAME.match(block)
                if not m:
                    continue
                name = _unq(m.group(1))
                if name.startswith(prefix):
                    name = name[len(prefix):]
                elif ':' in name:
                    name = name.split(':', 1)[1]
                body = block[:m.start(1)] + _q(name) + block[m.end(1):]
                body = _dedent(body)
                if name in seen:
                    if seen[name] != body:
                        view.issues.append(
                            f'embedded symbol "{name}" differs between sheets; kept the first')
                    continue
                seen[name] = body
                order.append(name)

    # 심볼 라이브러리(.kicad_sym) 파일 포맷 버전은 회로도(.kicad_sch) 포맷 버전과 별도 상한을 쓴다.
    # 실측(2026-09-09, 정식 kicad-cli 10.0.6): view.version 그대로(예: 20260306, 20260830)를 쓰면
    # "The symbol library 'orcad_import' was not found" 로 ERC 가 통째로 못 읽고(lib_symbol_issues
    # 353건), 20231120 으로 낮추면 같은 파일이 정상 로드된다(0건) — 심볼 안의 나이틀리 전용
    # 토큰(do_not_autoplace/in_pos_files/duplicate_pin_numbers_are_jumpers/body_style/unit_name 등)은
    # 버전과 무관하게 그대로 유지되며 문제되지 않는다. EDIF 경로가 이미 쓰는 값(kicad_writer.
    # FORMAT_VERSION)과 같아 정식·나이틀리 양쪽에서 검증된 안전한 값이므로 고정으로 쓴다.
    version = _SYMBOL_LIB_VERSION
    lines = ['(kicad_symbol_lib',
             f'\t(version {version})',
             '\t(generator "orcad2kicad")']
    for name in order:
        lines.append('\t' + seen[name])
    lines.append(')')
    lib_path = os.path.join(outdir, f'{lib_nick}.kicad_sym')
    with open(lib_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    return lib_path, len(order)


def _dedent(block):
    """시트의 lib_symbols 안(탭 2단) 블록을 라이브러리 파일용(탭 1단)으로 한 단 줄인다.

    KiCad 는 문자열 안의 개행을 `\\n` 으로 이스케이프하므로 줄 시작의 탭만 안전하게 지워진다.
    출력 라이브러리는 LF 로 통일하므로 CRLF 시트에서 뽑아 온 블록도 여기서 LF 로 맞춘다."""
    return block.replace('\r\n', '\n').replace('\n\t', '\n')


def write_sym_lib_table(outdir, lib_nick=DEFAULT_LIB_NICK, filename=None):
    """프로젝트용 sym-lib-table 작성 (${KIPRJMOD}/<filename> 을 가리킴)."""
    filename = filename or f'{lib_nick}.kicad_sym'
    path = os.path.join(outdir, 'sym-lib-table')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(f'(sym_lib_table (version 7)\n'
                 f'  (lib (name "{lib_nick}") (type "KiCad") (uri "${{KIPRJMOD}}/{filename}") '
                 f'(options "") (descr "OrCAD import"))\n)\n')
    return path
