"""나이틀리(KiCad 10.99+) 임포터 출력을 정식 KiCad 10.0 이 읽는 계층 구조로 재구성한다.

배경(docs/2026-09-09/[설계]_[05]): `--dsn`/`--kicad-project` 결과는 나이틀리 임포터가 만든 그대로라
시트 포맷이 `20260830`이고 최상위 시트가 여러 개(계층 루트가 없다) — 정식 kicad-cli 10.0.6 은
포맷 상한(20260306)을 넘는 파일을 거부하고, 최상위 시트가 여러 개라는 개념이 없어 첫 시트만
인식한다. 아래 규칙으로 재구성하면 정식 kicad-cli 10.0.6 이 그대로 읽는다(실측 2026-09-09):

  1. 루트 시트 `<stem>.kicad_sch` 를 새로 만들고 페이지마다 계층 `(sheet …)` 심볼을 둔다.
  2. 나이틀리의 첫 페이지 파일(= `<stem>.kicad_sch`)은 `P01_<페이지이름>.kicad_sch` 로 이름을
     바꿔 하위 시트로 내린다. 나머지 `P02_…` 파일은 이름 유지.
  3. 각 페이지 파일: `(version 20260830)` -> `(version 20260306)`, `(generator_version "10.99")`
     -> `"10.0"`, 심볼 인스턴스 경로 `(path "/<시트uuid>"` -> `(path "/<루트uuid>/<시트uuid>"`,
     최상위 `(sheet_instances (path "/" (page "N")))` 블록 제거.
  4. `.kicad_pro` 는 최소 템플릿(meta version 1, `sheets` = Root + 페이지들)으로 새로 쓴다.

모든 수정은 outdir 에 복사된 사본에 대해 제자리로 이루어진다(원본은 이미 `_stage_project`/
`_import_dsn` 이 복사해 둔 뒤). 페이지 파일 수정은 트리로 다시 직렬화하지 않고 **문자열 치환**만
한다 — 나이틀리 파일의 서식·내용을 그대로 보존하기 위해서다.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field

from .kicad_sch_reader import _parse_sheet
from .kicad_writer import fnum, qstr, _effects, stable_uuid, _FNAME_BAD
from .sexp import children

# 정식 KiCad 10.0 이 읽는 시트 포맷 상한 (kicad_netlist.SCH_FORMAT_CEILING[(10, 0)] 과 같은 값).
STABLE_SCH_VERSION = 20260306
STABLE_GENERATOR_VERSION = '10.0'


@dataclass
class RestructureResult:
    pro_path: str = ''
    root_sch: str = ''
    root_uuid: str = ''
    sheet_files: list = field(default_factory=list)   # 하위 시트 파일 경로(페이지 순)
    renamed: dict = field(default_factory=dict)        # 옛 파일명 -> 새 파일명 (P01 만 해당)
    changed: bool = False                              # False 면 이미 정식 포맷/계층 구조라 손대지 않음
    issues: list = field(default_factory=list)


_VERSION_RE = re.compile(r'\(version\s+\d+\)')
_GEN_VERSION_RE = re.compile(r'\(generator_version\s+"[^"]*"\)')
_SHEET_INSTANCES_RE = re.compile(r'\(sheet_instances\s*\(path\s+"/"\s*\(page\s+"[^"]*"\)\s*\)\s*\)')


def _restructure_page_text(text, sheet_uuid, root_uuid, target_version):
    """페이지 파일 텍스트 하나를 정식 포맷으로 문자열 치환한다(멱등).

    치환은 전부 원문 그대로 두고 필요한 부분만 바꾼다: 최상위 `sheet_instances` 블록을 먼저
    지워야(이 블록 안에도 `(path "/" …)` 가 있어 나중에 지우면 아래 경로 치환에 먼저 걸린다)
    이어지는 경로 치환이 다른 블록을 잘못 건드리지 않는다."""
    text = _SHEET_INSTANCES_RE.sub('', text)
    text = _VERSION_RE.sub(f'(version {target_version})', text, count=1)
    text = _GEN_VERSION_RE.sub(f'(generator_version {qstr(STABLE_GENERATOR_VERSION)})', text, count=1)
    old_path = f'"/{sheet_uuid}"'
    new_path = f'"/{root_uuid}/{sheet_uuid}"'
    text = text.replace(old_path, new_path)
    bare = '(path "/" (reference'
    if bare in text:
        text = text.replace(bare, f'(path {new_path} (reference')
    return text


def _unique_page_filename(prefix, name, folder, exclude=()):
    """`P01_<name>.kicad_sch` 형태의 새 파일명(충돌 회피)."""
    safe = _FNAME_BAD.sub('_', name).strip() or 'PAGE'
    base = f'{prefix}_{safe}'
    existing = {os.path.basename(p) for p in glob.glob(os.path.join(folder, '*.kicad_sch'))} - set(exclude)
    candidate = base + '.kicad_sch'
    n = 2
    while candidate in existing:
        candidate = f'{base}_{n}.kicad_sch'
        n += 1
    return candidate


def _stable_root_text(stem, root_uuid, entries):
    """루트 시트 텍스트. entries: [(시트uuid, 시트이름, 파일명), …] (페이지 순, 2번부터 번호 매김)."""
    lines = [f'(kicad_sch (version {STABLE_SCH_VERSION}) (generator {qstr("orcad2kicad")}) '
             f'(generator_version {qstr(STABLE_GENERATOR_VERSION)})',
             f'  (uuid {qstr(root_uuid)})', '  (paper "A4")',
             f'  (title_block (title {qstr(stem)}))', '  (lib_symbols)']
    cols, w, h, gap = 3, 45.72, 20.32, 12.7
    for i, (su, name, fname) in enumerate(entries):
        x = 25.4 + (i % cols) * (w + gap)
        y = 25.4 + (i // cols) * (h + gap)
        lines.append(f'  (sheet (at {fnum(x)} {fnum(y)}) (size {fnum(w)} {fnum(h)}) (fields_autoplaced yes) '
                     f'(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0)) (uuid {qstr(su)})')
        lines.append(f'    (property "Sheetname" {qstr(name)} (at {fnum(x)} {fnum(y - 0.7)} 0) '
                     f'{_effects(justify="left bottom")})')
        lines.append(f'    (property "Sheetfile" {qstr(fname)} (at {fnum(x)} {fnum(y + h + 0.6)} 0) '
                     f'{_effects(justify="left top")})')
        lines.append(f'    (instances (project {qstr(stem)} (path {qstr("/" + root_uuid)} '
                     f'(page {qstr(str(i + 2))})))))')
    lines.append('  (sheet_instances (path "/" (page "1")))')
    lines.append(')')
    return '\n'.join(lines) + '\n'


def project_json(project, root_uuid, entries):
    """`.kicad_pro` 최소 템플릿(`kicad_writer._project_json` 과 같은 내용).

    entries: [(uuid, name), …] (루트 제외, 페이지 순). 나이틀리 pro 의
    `component_class_settings`/`tuning_profiles` 등 정식판이 모르는 키는 버린다."""
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
        'sheets': [[root_uuid, 'Root']] + [[u, n] for (u, n) in entries],
        'text_variables': {},
    }


def restructure_project(pro_path, target_version=STABLE_SCH_VERSION) -> RestructureResult:
    """`.kicad_pro`(+ 같은 폴더의 `.kicad_sch`)를 정식 계층 구조로 제자리 재구성한다.

    이미 정식 구조(루트 파일에 최상위 `(sheet …)` 블록이 있고 모든 시트 버전이 target 이하)면
    아무것도 건드리지 않고 `changed=False` 를 돌려준다(`--kicad-project` 로 이미 재구성된
    프로젝트나 EDIF 경로 결과를 다시 넣는 경우)."""
    pro_path = os.path.abspath(pro_path)
    folder = os.path.dirname(pro_path)
    stem = os.path.splitext(os.path.basename(pro_path))[0]
    with open(pro_path, encoding='utf-8') as fh:
        pro = json.load(fh)
    sheets_entries = [(str(e[0]), str(e[1]) if len(e) > 1 else '') for e in (pro.get('sheets') or [])
                      if isinstance(e, (list, tuple)) and e]

    # 폴더의 모든 시트 파일을 한 번 파싱해 uuid -> 파일 지도를 만든다(이미 정식 구조인지
    # 판정하는 데도 같은 정보가 쓰인다).
    info = {}     # path -> (sheet_uuid, version, root_tree)
    by_uuid = {}
    for path in sorted(glob.glob(os.path.join(folder, '*.kicad_sch'))):
        path = os.path.abspath(path)
        sheet_uuid, version, _syms, root_tree = _parse_sheet(path)
        info[path] = (sheet_uuid, version, root_tree)
        if sheet_uuid and sheet_uuid not in by_uuid:
            by_uuid[sheet_uuid] = path

    root_file = os.path.join(folder, stem + '.kicad_sch')
    if root_file in info:
        r_uuid, _r_version, r_root = info[root_file]
        top_sheets = children(r_root, 'sheet')
        all_versions_ok = all((v or 0) <= target_version for (_u, v, _r) in info.values())
        if top_sheets and all_versions_ok:
            return RestructureResult(pro_path=pro_path, root_sch=root_file, root_uuid=r_uuid,
                                     sheet_files=sorted(p for p in info if p != root_file),
                                     renamed={}, changed=False, issues=[])

    issues = []
    entries_resolved = []   # (path, sheet_uuid, name)
    if sheets_entries:
        for uid, name in sheets_entries:
            path = by_uuid.get(uid)
            if path is None:
                issues.append(f'sheet uuid {uid} ("{name}") has no .kicad_sch file in {folder}')
                continue
            entries_resolved.append((path, uid, name))
    else:
        # sheets 가 비어 있으면(단일 시트 프로젝트 등) 폴더의 시트 파일 정렬 순서를 쓴다 — 이
        # 경우에도 루트 + 하위 시트 1개로 만든다(항상 같은 구조여야 [4] 경로가 일관된다).
        for path in sorted(info):
            uid, _v, _r = info[path]
            entries_resolved.append((path, uid, os.path.splitext(os.path.basename(path))[0]))

    root_uuid = stable_uuid('root', stem)
    renamed = {}
    if entries_resolved:
        first_path, first_uuid, first_name = entries_resolved[0]
        if os.path.basename(first_path) == stem + '.kicad_sch':
            new_name = _unique_page_filename('P01', first_name, folder, exclude={os.path.basename(first_path)})
            new_path = os.path.join(folder, new_name)
            os.rename(first_path, new_path)
            renamed[os.path.basename(first_path)] = new_name
            entries_resolved[0] = (new_path, first_uuid, first_name)

    for path, uid, _name in entries_resolved:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        new_text = _restructure_page_text(text, uid, root_uuid, target_version)
        if new_text != text:
            with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(new_text)

    root_entries = [(uid, name, os.path.basename(path)) for path, uid, name in entries_resolved]
    with open(root_file, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(_stable_root_text(stem, root_uuid, root_entries))

    with open(pro_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(project_json(stem, root_uuid, [(uid, name) for _p, uid, name in entries_resolved]), fh, indent=2)
        fh.write('\n')

    return RestructureResult(pro_path=pro_path, root_sch=root_file, root_uuid=root_uuid,
                             sheet_files=[p for p, _u, _n in entries_resolved],
                             renamed=renamed, changed=True, issues=issues)
