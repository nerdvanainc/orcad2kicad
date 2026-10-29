"""헤드리스 변환 파이프라인 — CLI·GUI·MCP 서버가 공통으로 쓰는 유일한 진입점.

`run_pipeline(PipelineOptions) -> PipelineResult` 하나가 EDIF 읽기부터
(선택) PADS 넷리스트 1·2차 검증, KiCad 프로젝트 출력, kicad-cli 3차 검증·ERC·PDF,
(선택) PADS 보드 임포트·풋프린트 매핑·4차 검증(보드 vs 회로도)까지 수행한다.
표현(콘솔 텍스트/JSON)은 `format_result` / `result_to_json` 가 맡는다.

exit code 규약(기존 CLI와 동일):
  0 = 모든 검증 PASS(또는 검증을 안 했을 때)
  1 = 검증 중 하나라도 FAIL
  2 = 입력 파일을 읽을 수 없거나(OSError) 출력 파일을 쓸 수 없음, 또는 kicad-cli 실행 실패
      (입력/출력 오류는 `error` 에 메시지가 담기고 그 시점에 중단된다)
콘솔에 나가는 문자열은 전부 ASCII 영문.
"""
from __future__ import annotations
import glob
import os
from dataclasses import dataclass, field, asdict

from .edif_reader import load_edif
from .pads_netlist import load_reference_netlist
from .verify import edif_netlist, compare_netlists, format_report
from .geometry import derive_nets


@dataclass
class Resolutions:
    """보드/회로도 불일치에 대한 사용자 결정(3단계-B Task 2에서 writer 단계에 적용된다)."""
    add_board_only_parts: list = field(default_factory=list)   # 보드에만 있는 레퍼런스 -> 자리표시 심볼 페이지에 추가
    footprint_choice: dict = field(default_factory=dict)       # ref -> 'board' | 'orcad' (기본 board)
    pin_type_overrides: dict = field(default_factory=dict)     # (symbol_name, pin_number) -> kicad type
    ignore_refs: list = field(default_factory=list)            # 차이 리포트에서 숨길 레퍼런스


@dataclass
class PipelineOptions:
    # 입력은 셋 중 정확히 하나: EDIF 파일 / KiCad 프로젝트(.kicad_pro) / OrCAD .DSN
    edf: str = None
    kicad_project: str = None    # KiCad 프로젝트(.kicad_pro) — 나이틀리 임포트 결과 등
    dsn: str = None              # OrCAD .DSN — kicad-cli(나이틀리)로 임포트한 뒤 위와 같은 경로
    netlist: str = None          # PADS2000 .asc (정답 넷리스트)
    board: str = None            # PADS Layout ASCII 또는 .kicad_pcb
    outdir: str = None
    project: str = None
    kicad_cli: str = None
    net_names: str = None        # 'kicad'(기본, None 도 kicad) | 'keep' — 인위적 전역화 여부.
                                  # kicad: OrCAD 별칭은 지역 라벨, 자동 이름 넷은 라벨 없음(.DSN 이면 임포터의
                                  # 투명 전역 라벨을 지움). keep: 넷 이름 = PADS/OrCAD 이름(전역 라벨로 승격·
                                  # 임포터 결과 그대로). docs/2026-10-27/[설계]_[03].
    board_net_names: str = None  # 'schematic'(기본, None 도) | 'pads' — 출력 보드 넷 이름을 회로도에 맞출지.
    hidden_label_style: str = 'subtle'  # 'subtle' | 'normal' — show 로 드러낸 라벨 모양(subtle: 작고 옅은 회색)
    hidden_labels: str = 'keep'  # 'keep' | 'show' | 'shrink' — 정리 뒤 남은 투명 라벨(.DSN, 연결을 실어 나름):
                                  # 그대로 / 보이는 전역 라벨로 / PDF 에서도 안 보이게 글자 축소. 남은 목록은 로그·
                                  # result.hidden_labels 로 알린다.
    duplicate_alias_labels: str = 'keep'  # 'keep' | 'remove' — 같은 배선에 같은 이름 보이는 전역 라벨이 있는
                                  # 지역 별칭 라벨(OrCAD: 별칭 + 같은 이름 오프페이지를 나란히)을 지울지.
                                  # remove 면 ERC same_local_global_label 이 줄지만 도면 글자 하나가 빠진다.
    pdf: bool = False
    strict_board: bool = False
    stable_format: bool = True   # kicad-project/dsn 모드: 나이틀리 결과를 정식 KiCad 10.0
                                  # 포맷(루트 시트 + 하위 시트)으로 재구성한다(--nightly-format 로 끔).
    import_cleanup: bool = True  # kicad-project/dsn 모드: 임포터 결함 후처리(hop 갭 병합 +
                                  # 빈 도면 양식, kicad_cleanup.cleanup_project) — --no-import-cleanup 로 끔.
    default_wire_width: bool = True  # import_cleanup 의 일부: 임포터가 박은 배선 폭(0.254 mm)을
                                      # KiCad 기본 폭(0)으로 — --keep-wire-width 로 끔.
    split_bus_junctions: bool = True  # import_cleanup 의 일부: 배선이 버스에 직접 닿는 점의 junction 을 지워
                                       # 임포터가 버스로 잘못 합친 넷을 나눈다(나뉘기만 할 때만, 넷리스트
                                       # 대조) — 연결이 바뀌므로 자동 변경으로 고지. --keep-bus-junctions 로 끔.
    korean_font: str = 'Malgun Gothic'  # 한글이 든 글자·라벨·속성에 지정할 글꼴(EDIF·DSN 공통,
                                         # kicad_fonts). KiCad 기본/영문 글꼴은 한글을 못 그린다. '' = 끔.
    resolutions: Resolutions = field(default_factory=Resolutions)


@dataclass
class PipelineResult:
    options: PipelineOptions
    input_mode: str = 'edif'             # 'edif' | 'kicad-project' | 'dsn'
    design: object = None
    view: object = None                  # kicad_sch_reader.SchematicView (KiCad 프로젝트 입력 모드)
    write_result: object = None          # kicad_writer.WriteResult
    board: object = None                 # kicad_board.Board
    board_diff: object = None            # kicad_board.BoardDiff
    verifications: dict = field(default_factory=dict)   # 'edif' | 'geometry' | 'kicad' -> verify.NetCompare
    erc: dict = None
    files: dict = field(default_factory=dict)           # 'root_sch','pro','lib','board','pdf','erc_json','netlist_txt'
    issues: list = field(default_factory=list)
    log: list = field(default_factory=list)             # 리포트 본문 줄(= 진행 메시지). format_result 가 그대로 쓴다
    kicad_cli: str = None
    net_names: str = 'kicad'
    import_cleanup: dict = None          # kicad_cleanup.cleanup_project() 의 반환값(적용했으면)
    board_sync: dict = None              # 보드 넷 이름 동기화 결과(_sync_board_net_names)
    localize: dict = None                # .DSN 별칭 지역화 결과(localize_project_guarded)
    hidden_labels: dict = None           # 남은 숨은 라벨과 처리(_report_and_apply_hidden_labels)
    duplicate_alias_removed: int = None  # --duplicate-alias-labels remove 로 지운 지역 별칭 라벨 수
    auto_changes: list = None            # 도구가 자동으로 바꾼 것 목록(_report_auto_changes) — 사용자 고지용
    exit_code: int = 0
    error: str = None                    # 입력/출력 오류 메시지 (exit 2, 그 시점에 중단)
    ref_nets: dict = None                # 기준 넷리스트({넷: {REF.PIN}}) — 재실행/비교용
    ref_format: str = None               # 기준 넷리스트 형식 표시 이름('PADS ASCII' | 'IPC-D-356')
    footprint_sources: dict = field(default_factory=dict)  # ref -> 'board'|'netlist'|'' (kicad-project/dsn 모드)
    footprint_names_unset: int = 0       # 보드 없이 --netlist 만 줘서 넷리스트 풋프린트 이름을 비워 둔 ref 수


def summary(design):
    """설계 요약 블록(기존 CLI 첫 블록과 동일)."""
    n_inst = sum(len(p.instances) for p in design.pages)
    refs = {i.reference for p in design.pages for i in p.instances}
    lines = [f'design: {design.name}', f'pages: {len(design.pages)}', f'instances: {n_inst}',
             f'references: {len(refs)}', f'symbols: {len(design.symbols)}',
             f'wires: {sum(len(p.wires) for p in design.pages)}',
             f'junctions: {sum(len(p.junctions) for p in design.pages)}',
             f'issues: {len(design.issues)}']
    return '\n'.join(lines)


def summary_view(view, issues=None):
    """KiCad 프로젝트 입력 모드의 요약 블록(`summary` 와 같은 자리에 온다)."""
    n_issues = len(view.issues if issues is None else issues)
    return '\n'.join([f'project: {view.project}',
                      f'sheets: {len(view.sheet_files)}',
                      f'symbols: {len(view.symbols)}',
                      f'references: {len(view.references())}',
                      f'sch format: {view.version}',
                      f'issues: {n_issues}'])


def result_summary(result):
    """입력 모드에 맞는 요약 블록(없으면 '')."""
    if result.design is not None:
        return summary(result.design)
    if result.view is not None:
        return summary_view(result.view, result.issues)
    return ''


def schematic_references(design):
    """회로도(Design.pages)에 이미 들어 있는 레퍼런스 집합."""
    return {ins.reference for pg in design.pages for ins in pg.instances}


def default_outdir(input_path):
    """입력 파일(.DSN/.kicad_pro/.EDF) 옆의 `<이름>_kicad` 폴더 — 출력 폴더를 비웠을 때의 기본값.

    같은 폴더에 버전이 다른 .DSN 이 여럿 있어도 서로 겹치지 않도록 파일 이름을 접두로 쓴다.
    (.DSN 폴더 아래여도 된다 — kicad-cli 임포트는 임시 폴더에서 하고 옮기므로 서브시트 유실 없음.)"""
    p = os.path.abspath(input_path)
    stem = os.path.splitext(os.path.basename(p))[0]
    return os.path.join(os.path.dirname(p), stem + '_kicad')


_PADS_NETLIST_MAGIC = b'*PADS2000*'
_PADS_BOARD_MAGIC = b'!PADS-POWERPCB'


def _asc_kind(path):
    """.asc 파일의 종류: 'netlist'(OrCAD 가 내보낸 PADS2000 넷리스트) | 'board'(PADS Layout ASCII) | None."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(64).lstrip()
    except OSError:
        return None
    if head.startswith(_PADS_NETLIST_MAGIC):
        return 'netlist'
    if head.startswith(_PADS_BOARD_MAGIC):
        return 'board'
    return None


def suggest_inputs(input_path):
    """입력 파일 경로 -> 나머지 칸의 제안값 {'outdir', 'project', 'netlist', 'board'}(없으면 None).

    같은 폴더의 `.asc` 파일을 머리글로 분류해(넷리스트/보드) 입력 파일과 이름이 같은 것 > 입력
    파일 이름으로 시작하는 것 > 그 종류가 그것 하나뿐인 것 순으로 고른다(후보가 여럿이고 이름이
    안 맞으면 제안하지 않는다 — 엉뚱한 보드를 집는 것보다 비워 두는 편이 안전하다). GUI 가 입력
    파일을 고를 때 빈 칸만 채우는 데 쓴다."""
    p = os.path.abspath(input_path)
    d = os.path.dirname(p)
    stem = os.path.splitext(os.path.basename(p))[0]
    out = {'outdir': default_outdir(p), 'project': stem, 'netlist': None, 'board': None}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    cands = {'netlist': [], 'board': []}
    for fn in names:
        if not fn.lower().endswith('.asc'):
            continue
        kind = _asc_kind(os.path.join(d, fn))
        if kind:
            cands[kind].append(fn)
    low = stem.lower()
    for kind, files in cands.items():
        exact = [f for f in files if os.path.splitext(f)[0].lower() == low]
        prefix = [f for f in files if f.lower().startswith(low)]
        pick = (exact or prefix or (files if len(files) == 1 else []))
        if pick:
            out[kind] = os.path.join(d, pick[0])
    return out


def _pick_by_name(cands, primary_stem):
    """같은 종류 후보 중 하나 고르기: 기준 이름과 같은 것 > 그 이름으로 시작하는 것 > 정렬 첫 번째."""
    cands = sorted(cands, key=lambda p: (os.path.basename(p).lower(), p))
    if primary_stem:
        low = primary_stem.lower()
        exact = [p for p in cands if os.path.splitext(os.path.basename(p))[0].lower() == low]
        prefix = [p for p in cands if os.path.basename(p).lower().startswith(low)]
        cands = exact or prefix or cands
    return cands[0]


def classify_dropped(paths):
    """창에 끌어다 놓은 파일/폴더 목록 -> 입력 칸별 배정.

    반환: {'edf','dsn','kicad_project','netlist','board'(경로 또는 None), 'mode'('edif'|'dsn'|
    'kicad_project'|None), 'ignored'(무시한 경로 목록), 'notes'(로그용 짧은 영문 문장 목록)}.
    폴더는 **바로 아래 파일만**(재귀 없음) 펼친다 — 이전 변환 출력 폴더(`KICAD\\*.kicad_pro`)를
    잘못 집지 않기 위해서다. 확장자(대소문자 무시)로 분류하고 `.asc` 만 머리글로 넷리스트/보드를
    구분한다. 같은 종류가 여럿이면 주 입력과 이름이 같은 것 > 그 이름으로 시작하는 것 > 정렬 첫
    번째를 고르고 나머지는 notes 에 적는다. 모드는 EDIF > DSN > KiCad 프로젝트 순으로 우선한다."""
    from .pads_netlist import _IPC356_EXTS
    files = []
    notes = []
    for p in paths or []:
        p = os.path.abspath(str(p))
        if os.path.isdir(p):
            try:
                for fn in sorted(os.listdir(p)):
                    fp = os.path.join(p, fn)
                    if os.path.isfile(fp):
                        files.append(fp)
            except OSError:
                notes.append('cannot read folder: ' + p)
        elif os.path.isfile(p):
            files.append(p)
        else:
            notes.append('not found: ' + p)
    files = list(dict.fromkeys(files))                 # 중복 제거(순서 유지)
    cands = {k: [] for k in ('edf', 'dsn', 'kicad_project', 'netlist', 'board')}
    ignored = []
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in ('.edf', '.edif'):
            cands['edf'].append(fp)
        elif ext == '.dsn':
            cands['dsn'].append(fp)
        elif ext == '.kicad_pro':
            cands['kicad_project'].append(fp)
        elif ext == '.asc':
            kind = _asc_kind(fp)
            if kind:
                cands[kind].append(fp)
            else:
                ignored.append(fp)
                notes.append('unknown .asc header: ' + os.path.basename(fp))
        elif ext in _IPC356_EXTS:
            cands['netlist'].append(fp)
        elif ext == '.kicad_pcb':
            cands['board'].append(fp)
        else:
            ignored.append(fp)
    out = {k: None for k in cands}
    mode = next((m for m, k in (('edif', 'edf'), ('dsn', 'dsn'),
                                ('kicad_project', 'kicad_project')) if cands[k]), None)
    if cands['edf'] and cands['dsn']:
        notes.append('both EDIF and DSN dropped: EDIF mode used')
    primary_stem = None
    for kind in ('edf', 'dsn', 'kicad_project', 'netlist', 'board'):
        if not cands[kind]:
            continue
        pick = _pick_by_name(cands[kind], primary_stem)
        out[kind] = pick
        if kind == {'edif': 'edf', 'dsn': 'dsn', 'kicad_project': 'kicad_project'}.get(mode):
            primary_stem = os.path.splitext(os.path.basename(pick))[0]
        rest = [os.path.basename(p) for p in sorted(cands[kind]) if p != pick]
        if rest:
            notes.append(f'{kind}: using {os.path.basename(pick)}, not used: ' + ', '.join(rest))
    if ignored:
        names = [os.path.basename(p) for p in ignored]
        shown = ', '.join(names[:5]) + (', ...' if len(names) > 5 else '')
        notes.append(f'ignored {len(names)} files: {shown}')
    out.update(mode=mode, ignored=ignored, notes=notes)
    return out


def input_mode(opts):
    """옵션의 입력 종류 -> ('edif'|'kicad-project'|'dsn', 오류 메시지 또는 None)."""
    given = [name for name, val in (('edif', opts.edf), ('kicad-project', opts.kicad_project),
                                    ('dsn', opts.dsn)) if val]
    if len(given) == 1:
        return given[0], None
    if not given:
        return 'edif', 'no input given: pass an EDIF file, --kicad-project PRO or --dsn FILE'
    return given[0], ('only one input allowed (got: ' + ', '.join(given) +
                      '): pass an EDIF file, --kicad-project PRO or --dsn FILE')


def run_pipeline(opts: PipelineOptions, log=None) -> PipelineResult:
    """옵션대로 변환·검증을 수행하고 결과를 담은 PipelineResult 를 돌려준다.

    log 는 진행 메시지를 받을 callable(str) (GUI 로그 창용). 리포트 본문 줄이 만들어지는 대로
    그대로 전달되므로, 콘솔 출력과 진행 로그가 항상 같은 내용이 된다.
    """
    result = PipelineResult(options=opts)
    out = result.log

    def emit(line):
        out.append(line)
        if log is not None and line:
            for one in line.split('\n'):
                log(one)

    # ---- 0) 입력 모드 결정 ----
    mode, err = input_mode(opts)
    result.input_mode = mode
    if err:
        result.error = err
        result.exit_code = 2
        return result
    if mode != 'edif':
        return run_kicad_project(opts, result, emit)

    # ---- 1) EDIF 읽기 + (선택) 기준 넷리스트 ----
    try:
        design = load_edif(opts.edf)
        ref = load_reference_netlist(opts.netlist) if opts.netlist else None
    except OSError as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result
    result.design = design
    result.issues = design.issues
    if ref is not None:
        result.ref_nets = ref.nets
        result.ref_format = ref.source_format
        emit(f'reference netlist: {ref.source_format} ({len(ref.nets)} nets)')

    # ---- 2) 1·2차 검증 (EDIF 조인 / 지오메트리 vs PADS) ----
    if ref is not None:
        cmp1 = compare_netlists(edif_netlist(design), ref.nets)
        cmp2 = compare_netlists(derive_nets(design), ref.nets)
        result.verifications['edif'] = cmp1
        result.verifications['geometry'] = cmp2
        emit(f'[1] EDIF joined vs reference ({result.ref_format or "PADS"})')
        emit(format_report(cmp1))
        emit('')
        emit(f'[2] geometry vs reference ({result.ref_format or "PADS"})')
        emit(format_report(cmp2))
        result.exit_code = 0 if cmp1.ok and cmp2.ok else 1

    if not opts.outdir:
        return result

    # ---- 3) KiCad 출력 (+ 보드 임포트) ----
    from .kicad_writer import write_project
    from .kicad_netlist import find_kicad_cli, run_erc, export_pdf, verify_with_kicad
    cli_path = find_kicad_cli(opts.kicad_cli)
    result.kicad_cli = cli_path
    # 보드(.asc) 임포트는 kicad-project/dsn 모드와 같은 규칙: 정식 kicad-cli 우선. kicad-cli 칸이
    # 나이틀리(GUI 포터블 준비가 채움)여도 보드는 정식판이 여는 버전으로 나와야 한다.
    board = _load_board_for_view(opts, result, emit, None)
    if result.error:
        return result
    result.board = board

    fp_map = None
    if board is not None:
        from .kicad_board import footprint_map
        fp_map = footprint_map(board)

    # ---- 3b) 불일치 해소(Resolutions) 준비 ----
    # 보드 전용 부품을 자리표시 심볼로 추가하면 3·4차 검증의 기준 넷리스트도 그 패드 넷만큼
    # 보강해야 한다(augment_reference). 1·2차 검증은 이미 끝났고 Design.pages 는 건드리지
    # 않으므로 영향이 없다.
    rez = opts.resolutions or Resolutions()
    sch_refs = schematic_references(design)
    extra_parts, added_refs = [], []
    if rez.add_board_only_parts:
        if board is None:
            design.issues.append('add board-only parts requested but no board given; ignored')
        else:
            from .kicad_board import placeholder_parts
            extra_parts, iss = placeholder_parts(board, rez.add_board_only_parts, design.canonical)
            design.issues.extend(iss)
            # 이미 회로도에 있는 레퍼런스는 writer 가 자리표시 심볼을 건너뛴다(중복 배치 방지).
            # 그러면 출력 회로도에 새 핀이 생기지 않으므로 기준 넷리스트도 보강하면 안 된다 —
            # 보강하면 회로도에 없는 REF.PIN 이 기준에 들어가 [3]/[4] 가 잘못 FAIL 한다.
            added_refs = [p.ref for p in extra_parts if p.ref not in sch_refs]
    # augment_reference 는 여기(3b)와 아래 4차 검증에서 함께 쓴다. added_refs 가 비어 있지 않을
    # 때만 필요하므로 import 도 여기 한 곳에서만 한다(아래에서는 그대로 재사용).
    augment_reference = None
    ref_nets_eff = ref.nets if ref is not None else None
    if added_refs:
        from .kicad_board import augment_reference
        if ref_nets_eff is not None:
            ref_nets_eff = augment_reference(ref_nets_eff, board, added_refs, design.canonical)

    net_names = opts.net_names or 'kicad'
    if extra_parts and net_names != 'keep':
        # 자리표시 부품은 global_label 로만 넷에 붙으므로 이름 보존 모드가 아니면 연결이 끊긴다.
        design.issues.append("net names: 'keep' forced by board-only placeholder parts")
        net_names = 'keep'
    result.net_names = net_names
    try:
        res = write_project(design, opts.outdir, opts.project,
                            footprint_map=fp_map, net_names=net_names,
                            extra_parts=extra_parts,
                            pin_type_overrides=rez.pin_type_overrides,
                            footprint_choice=rez.footprint_choice,
                            netlist_footprints=(ref.parts if ref is not None else None),
                            korean_font=opts.korean_font)
    except OSError as e:
        result.error = f'cannot write output: {e}'
        result.exit_code = 2
        return result
    result.write_result = res
    design.issues.extend(res.issues)
    result.files['root_sch'] = res.root_sch
    result.files['lib'] = res.lib_file
    result.files['pro'] = res.pro_file
    emit('')
    emit(f'written: {res.root_sch} (+{len(res.page_files)} pages, {res.lib_file})')
    emit(f'net names: {net_names}')
    _apply_label_tidy(opts, result, emit, res.root_sch, cli_path, edif=True)
    # 보드 풋프린트 라이브러리(PADS.pretty)/프로젝트 보드/fp-lib-table 은 ERC 실행 전에 써야 한다.
    # ERC 뒤에 쓰면(이전 순서) 라이브러리가 아직 없어 회로도 심볼의 Footprint 프로퍼티가
    # 가리키는 라이브러리를 ERC가 못 찾아 footprint_link_issues 위반이 잘못 잡혔다.
    if board is not None:
        from .kicad_board import extract_footprint_library, write_project_board, write_fp_lib_table
        try:
            files, issues = extract_footprint_library(board, opts.outdir, 'PADS')
            design.issues.extend(issues)
            project = os.path.basename(res.root_sch)[:-len('.kicad_sch')]
            board_pcb = os.path.join(opts.outdir, project + '.kicad_pcb')
            issues2 = write_project_board(board, board_pcb, 'PADS', res.symbol_paths)
            design.issues.extend(issues2)
            write_fp_lib_table(opts.outdir, 'PADS')
        except OSError as e:
            result.error = f'cannot write output: {e}'
            result.exit_code = 2
            return result
        result.files['board'] = board_pcb
        emit(f'board: {len(files)} footprints -> PADS.pretty, project board written')

    # ---- 4) kicad-cli: ERC / 3차 검증 / PDF ----
    if not cli_path:
        emit('kicad-cli: not found (skipped ERC/netlist)')
    else:
        emit(f'kicad-cli: {cli_path}')
        try:
            erc_json = os.path.join(opts.outdir, 'erc.json')
            erc = run_erc(cli_path, res.root_sch, erc_json)
            result.erc = erc
            result.files['erc_json'] = erc_json
            emit(f'erc violations: {erc["count"]} ' + ' '.join(f'{k}={v}' for k, v in sorted(erc['by_type'].items())))
            if ref is not None:
                cmp3, issues3 = verify_with_kicad(cli_path, res.root_sch, ref_nets_eff, opts.outdir)
                design.issues.extend(issues3)
                result.verifications['kicad'] = cmp3
                result.files['netlist_txt'] = os.path.join(opts.outdir, 'kicad_netlist.txt')
                emit('')
                emit(f'[3] kicad-cli netlist vs reference ({result.ref_format or "PADS"})')
                emit(format_report(cmp3))
                if not cmp3.ok:
                    result.exit_code = 1
            if opts.pdf:
                pdf = export_pdf(cli_path, res.root_sch,
                                 os.path.join(opts.outdir, f'{os.path.basename(res.root_sch)[:-10]}.pdf'))
                result.files['pdf'] = pdf
                emit(f'pdf: {pdf}')
        except RuntimeError as e:
            emit(f'kicad-cli error: {e}')
            result.exit_code = 2

    # ---- 5) 4차 검증: 보드 vs 회로도 ----
    if board is not None:
        from .kicad_board import compare_board, format_board_diff
        base_nets = ref_nets_eff if ref is not None else edif_netlist(design)
        if ref is None and added_refs:
            base_nets = augment_reference(base_nets, board, added_refs, design.canonical)
        diff = compare_board(board, design, base_nets, added_refs=added_refs)
        result.board_diff = diff
        emit('')
        emit(format_board_diff(diff))
        if opts.strict_board and (not diff.net_compare.ok or diff.only_sch_refs or diff.only_board_refs):
            # 이전에 exit_code=2(kicad-cli 실패)였으면 downgrade 되지 않게 max 를 쓴다.
            result.exit_code = max(result.exit_code, 1)
    _sync_board_net_names(opts, result, emit, cli_path, res.root_sch)
    return result


def _guarded_sheet_edit(root, fn, sig):
    """프로젝트 시트 전부에 `fn(text) -> (text, n)` 을 적용하고, 넷리스트(넷 이름+핀, `sig(root)`)가 그대로면
    남긴다. 반환값: (적용 수, 'ok' | 'reverted' | 'none')."""
    sheets = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(root)), '*.kicad_sch')))
    texts = {}
    for p in sheets:
        with open(p, encoding='utf-8', newline='') as fh:
            texts[p] = fh.read()
    new, total = {}, 0
    for p, t in texts.items():
        new[p], n = fn(t)
        total += n
    if not total:
        return 0, 'none'
    base = sig(root)
    for p, t in new.items():
        if t != texts[p]:
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(t)
    if sig(root) == base:
        return total, 'ok'
    for p, t in texts.items():
        with open(p, 'w', encoding='utf-8', newline='') as fh:
            fh.write(t)
    return total, 'reverted'


def _apply_label_tidy(opts, result, emit, root, cli, edif=False):
    """라벨 위치·중복 정리(넷리스트 대조, 다르면 원복).

    - EDIF 출력(`edif=True`): 우리 작성기는 별칭 라벨을 OrCAD 표시 위치(배선 중간)에 둔다 → `.DSN` 경로와 같은
      `snap_labels_to_wire_ends` 로 빈 배선 끝으로(ERC unconnected_wire_endpoint 감소).
    - `--duplicate-alias-labels remove`: 같은 배선에 같은 이름 전역 라벨이 있는 지역 별칭 라벨 삭제."""
    from .kicad_cleanup import remove_alias_labels_duplicating_globals, snap_labels_to_wire_ends
    from .kicad_netlist import schematic_net_signature
    if not cli:
        if edif or opts.duplicate_alias_labels == 'remove':
            emit('label tidy: skipped (no kicad-cli to verify)')
        return

    def sig(r):
        return schematic_net_signature(cli, r, opts.outdir)
    if edif:
        n, st = _guarded_sheet_edit(root, snap_labels_to_wire_ends, sig)
        if st == 'ok':
            emit(f'label tidy: {n} alias labels moved to the free wire end (netlist names+pins verified identical)')
        elif st == 'reverted':
            emit('label tidy: moving alias labels to wire ends reverted (netlist changed)')
    if opts.duplicate_alias_labels == 'remove':
        n, st = _guarded_sheet_edit(root, remove_alias_labels_duplicating_globals, sig)
        result.duplicate_alias_removed = n if st == 'ok' else 0
        if st == 'ok':
            emit(f'label tidy: {n} local alias labels removed next to a global label of the same name '
                 '(netlist names+pins verified identical)')
        elif st == 'reverted':
            emit('label tidy: removing duplicate alias labels reverted (netlist changed)')
        else:
            emit('label tidy: no duplicate alias labels')


def _apply_net_scope_options(opts, result, emit, pro, cli):
    """.DSN/KiCad 프로젝트 입력: `--net-names kicad` 면 임포터의 투명 전역 라벨(별칭·자동 넷)을 지워 지역화
    (`localize_project_guarded`, 넷 핀 분할 대조), `--hidden-labels shrink` 면 남은 투명 라벨 글자를 축소
    (넷 이름+핀 대조). kicad-cli 가 없으면 아무것도 바꾸지 않고 알린다."""
    from .kicad_cleanup import localize_project_guarded, shrink_transparent_labels
    from .kicad_netlist import schematic_net_signature

    def sig(root):
        return schematic_net_signature(cli, root, opts.outdir)
    if result.net_names == 'kicad':
        if not cli:
            result.net_names = 'keep'
            emit('net names: keep (no kicad-cli to verify removing importer global labels)')
        else:
            loc = localize_project_guarded(pro, sig)
            result.localize = loc
            if loc['reverted']:
                result.net_names = 'keep'
                emit('net names: keep (removing importer global labels changed connectivity; reverted)')
            else:
                kept = f", names kept: {', '.join(loc['kept_names'])}" if loc['kept_names'] else ''
                emit(f"net names: kicad - {loc['removed']} importer hidden labels removed "
                     f"(aliases local, unnamed nets KiCad-named; {len(loc['renamed'])} nets renamed; "
                     f"net pins verified identical{kept})")
    else:
        emit('net names: keep (importer global labels kept; names match PADS/OrCAD)')
    _report_and_apply_hidden_labels(opts, result, emit, pro, cli, sig)


AUTO_CHANGES_FILE = 'auto_changes.txt'


def _auto_change_items(result):
    """도구가 회로도에 **자동으로** 한 변경 목록: [{'key', 'count', 'en', 'ko', 'check'}] — 0 개인 항목은 뺀다.

    `en` 은 로그(ASCII), `ko` 는 출력 폴더의 `auto_changes.txt`·GUI 설명용, `check` 는 사람이 확인할 점(한국어).
    연결(핀 분할·넷 이름)은 각 단계가 넷리스트로 이미 대조했다 — 넷 이름이 바뀌는 단계(넷 이름 정리)만 따로 적는다."""
    cl = result.import_cleanup or {}
    hl = result.hidden_labels or {}
    loc = result.localize or {}
    dd = cl.get('dedupe') or {}
    bs = cl.get('bus_split') or {}
    items = []

    def add(key, n, en, ko, check=''):
        if n:
            items.append({'key': key, 'count': n, 'en': en, 'ko': ko, 'check': check})
    # 연결이 바뀌는 유일한 단계라 맨 앞에(임포터가 버스로 합친 넷을 나눔)
    if not bs.get('reverted'):
        add('bus_junctions', bs.get('removed'),
            'junctions where wires touch a bus removed - CONNECTIONS CHANGED: importer-merged nets split',
            '배선이 버스에 직접 닿는 점의 접속점(junction) 삭제 - 연결이 바뀜: 임포터가 버스로 합친 넷을 나눔',
            '아래 "나뉜 넷" 이 원본 OrCAD/PCB 와 맞는지(PADS 정답 대조 2개 설계에서 100% 일치 확인)')
    add('bus_entries', bs.get('entries_added'), 'wire ends touching a bus redrawn with bus entries',
        '버스에 엔트리 없이 닿은 배선 끝을 KiCad 식으로 다시 그림(배선을 2.54 mm 줄이고 45도 버스 엔트리)',
        '그림 모양만 바뀜(넷 이름·핀 동일 확인)')
    add('korean_fixed', cl.get('korean_fixed'), 'garbled Korean strings restored',
        '깨진 한글 문자열 복원', '한글 글자(부품 값·주석)가 원본과 같은지')
    add('korean_font', cl.get('korean_font_set'), 'texts set to a Korean-capable font',
        '한글이 든 글자에 한글 글꼴 지정', '')
    add('offpage', cl.get('offpage_converted'), 'OrCAD off-page connector drawings -> global labels',
        'OrCAD 오프페이지 커넥터 그림(갈매기+글자)을 KiCad 전역 라벨로 바꿈', '라벨 모양·방향이 원래 커넥터 자리와 맞는지')
    add('hop_gaps', cl.get('gaps_merged'), 'wire hop gaps merged into one wire',
        '교차점마다 끊긴 배선(hop 갭)을 한 배선으로 합침', '')
    add('labels_snapped', (cl.get('labels_moved') or 0) + (cl.get('globals_snapped') or 0),
        'labels moved to the free end of their wire', '라벨을 같은 배선의 빈 끝으로 옮김', '')
    add('hidden_oriented', cl.get('hidden_oriented'), 'hidden labels turned to point away from their wire',
        '숨은 라벨(PDF 에만 찍힘)을 배선 밖을 향하게 돌리거나 같은 배선의 빈 끝으로 옮김', '')
    add('pin_labels', cl.get('pin_labels_faced'), 'labels on bare pins turned to face away from the symbol',
        '배선 없이 핀 끝에 붙은 라벨의 글자 방향을 심볼 바깥으로', '')
    add('pin_numbers', cl.get('pin_numbers_fitted'), 'pin numbers longer than their pin shrunk',
        '핀 길이보다 긴 핀 번호 글자를 줄임(핀 길이의 70%, 최소 0.6 mm)', '핀 번호가 읽을 만한지')
    add('dedupe', (dd.get('removed') or 0) + (hl.get('second_dedupe_removed') or 0),
        'redundant hidden labels removed', '중복 숨은 라벨 삭제(같은 이름이 같은 배선에 이미 있음)', '')
    add('wire_width', cl.get('wires_normalized'), 'wires set to the default width', '배선 폭을 KiCad 기본값으로', '')
    if cl.get('worksheet'):
        add('worksheet', 1, 'blank drawing sheet (OrCAD title block kept as drawn)',
            '빈 도면 양식 적용(OrCAD 타이틀 블록과 겹치지 않게)', '')
    if loc and not loc.get('reverted'):
        add('localize', loc.get('removed'), 'importer hidden global labels removed (net names: kicad)',
            '임포터의 숨은 전역 라벨 삭제(별칭은 시트 안 지역 넷으로, 이름 없는 넷은 KiCad 자동 이름)',
            '넷 이름이 바뀜 - 아래 "이름이 바뀐 넷" 참고. 핀 연결은 동일 확인')
        add('nets_renamed', len(loc.get('renamed') or {}), 'nets renamed by the net-name cleanup',
            '넷 이름이 바뀐 넷', 'PCB 를 Update PCB from Schematic 할 때 넷 이름이 바뀜')
    add('power_symbols', len(hl.get('power_symbols_placed') or []),
        'hidden power labels on part pins replaced by power symbols',
        '부품 핀에 바로 붙은 숨은 전원 라벨을 같은 이름 전원 심볼로 바꿈', '전원 심볼 위치')
    if hl.get('mode') in ('show', 'shrink') and hl.get('verified'):
        add('hidden_' + hl['mode'], hl.get('applied'),
            'remaining hidden labels made visible' if hl['mode'] == 'show' else 'remaining hidden labels shrunk',
            '남은 숨은 라벨을 보이는 라벨로' if hl['mode'] == 'show' else '남은 숨은 라벨 글자를 0.01 mm 로 줄임', '')
    add('dup_alias', result.duplicate_alias_removed, 'duplicate local alias labels removed',
        '같은 배선의 같은 이름 전역 라벨과 겹치는 지역 별칭 라벨 삭제', '도면에서 글자가 하나씩 사라짐')
    return items


def _report_auto_changes(opts, result, emit):
    """자동 변경을 사용자가 **확실히 알도록**: 로그에 `AUTO CHANGES` 블록, 출력 폴더에 `auto_changes.txt`(한국어,
    항목·개수·확인할 점·이름 바뀐 넷·그대로 둔 오프페이지 커넥터), 바꾸지 않은 오프페이지 커넥터는 이슈 경고로."""
    items = _auto_change_items(result)
    result.auto_changes = items
    cl = result.import_cleanup or {}
    unconv = cl.get('offpage_unconverted') or []
    if not items and not unconv:
        return
    split_note = ('; bus junction removal SPLIT importer-merged nets' if (cl.get('bus_split') or {}).get('splits')
                  else '')
    emit(f'AUTO CHANGES: the tool changed the schematic automatically in {len(items)} ways '
         f'(connections verified with the netlist{split_note}) - see {AUTO_CHANGES_FILE}:')
    for it in items:
        emit(f"  - {it['count']} {it['en']}")
    splits = (cl.get('bus_split') or {}).get('splits') or []
    for sp in splits:
        shown = ', '.join(sp['into'][:8]) + (f", ... ({len(sp['into'])} nets)" if len(sp['into']) > 8 else '')
        msg = (f"net '{sp['net']}' split into {shown}: the importer had joined bus members through "
               "junctions on the bus (check against the PCB)")
        emit('WARNING ' + msg)
        result.issues.append('WARNING ' + msg)
    for u in unconv:
        msg = (f"off-page connector '{u['text']}' ({u['sheet']}) left as a drawing: its net is "
               f"'{u['net']}' (OrCAD gave this net two names)")
        emit('WARNING ' + msg)
        result.issues.append('WARNING ' + msg)
    lines = ['자동 변경 내역 (orcad2kicad)', '',
             '이 도구가 임포트 결과 회로도를 자동으로 고친 내용입니다. 핀 연결은 각 단계마다 kicad-cli 넷리스트로',
             '변경 전후를 대조했고, 다르면 그 단계를 되돌렸습니다. 넷 이름이 바뀌는 단계는 따로 표시했습니다.',
             '단, 버스 접속점 삭제는 임포터가 잘못 합친 넷을 나누므로 연결이 바뀝니다("나뉜 넷" 참고).', '',
             '[바꾼 것]']
    for it in items:
        lines.append(f"- {it['ko']}: {it['count']}개" + (f"  (확인: {it['check']})" if it['check'] else ''))
    if splits:
        lines += ['', f'[나뉜 넷 - 연결이 바뀜] {len(splits)}개 (전 넷 -> 나뉜 넷들)',
                  'OrCAD 에서 버스에 닿은 배선은 그림일 뿐이고 연결은 넷 별칭 이름으로 됩니다. 임포터가 그 닿는 점에',
                  '접속점을 찍어 KiCad 에서는 버스를 통해 여러 넷이 하나로 합쳐져 있었습니다.']
        lines += [f"- {sp['net']} -> {', '.join(sp['into'])}" for sp in splits]
    renamed = (result.localize or {}).get('renamed') or {}
    if renamed and not (result.localize or {}).get('reverted'):
        lines += ['', f'[이름이 바뀐 넷] {len(renamed)}개 (전 이름 -> 후 이름)']
        lines += [f'- {a} -> {b}' for a, b in sorted(renamed.items())]
    if unconv:
        lines += ['', f'[그대로 둔 것 - 확인 필요] 오프페이지 커넥터 {len(unconv)}개',
                  '보이는 커넥터 글자와 실제 넷 이름이 다릅니다(OrCAD 원본에서 한 넷에 커넥터 이름이 둘).',
                  '라벨 이름을 커넥터 글자로 바꾸면 넷이 갈라질 수 있어 그림(갈매기+글자)으로 두었습니다.']
        lines += [f"- {u['sheet']}: 화면 글자 '{u['text']}' / 실제 넷 '{u['net']}' (x={u['at'][0]}, y={u['at'][1]})"
                  for u in unconv]
    path = os.path.join(opts.outdir, AUTO_CHANGES_FILE)
    with open(path, 'w', encoding='utf-8-sig', newline='\r\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    result.files['auto_changes'] = path


# 남은 숨은 라벨 종류별 한 줄 설명(로그용, ASCII) — kicad_cleanup._transparent_label_survey 의 kind
_HIDDEN_KIND_TEXT = {
    'alias': 'OrCAD alias joined to the global net of the same name',
    'renamed': 'net shown under another alias name',
    'wire': 'only label naming this wire',
    'free': 'not on a wire',
    'local': 'hidden local label naming this wire',
}


def _report_and_apply_hidden_labels(opts, result, emit, pro, cli, sig):
    """정리 뒤에도 남은 투명(숨은) 전역 라벨을 **이름·위치·이유까지** 알리고, `--hidden-labels` 선택을 적용한다.

    남은 것은 중복이 아니라 연결을 실어 나른다(지우면 넷이 끊기거나 이름이 바뀜). KiCad 화면에는 안 보이고
    PDF/SVG 에는 검게 찍힌다. 선택: keep(그대로) / show(보이는 전역 라벨로, 넷리스트 대조) / shrink(글자를
    0.01 mm 로, 넷리스트 대조). 결과는 `result.hidden_labels` 에 남는다(GUI·요약 설명용)."""
    from .kicad_cleanup import (classify_transparent_labels, dedupe_project_guarded,
                                find_hidden_label_conflicts, place_power_symbols_for_hidden_labels,
                                show_transparent_labels, shrink_transparent_labels)
    folder = os.path.dirname(os.path.abspath(pro))
    root = os.path.splitext(os.path.abspath(pro))[0] + '.kicad_sch'
    # 중복 제거를 한 번 더: 첫 중복 제거는 넷 이름이 바뀐 이름을 **프로젝트 전체에서** 보호한다(예: VCC). 넷 이름
    # 정리(지역화) 뒤에는 그 이름 후보가 줄어 같은 배선의 전원 심볼과 겹치는 확실한 중복이 드러난다(코퍼스 101:
    # 전원 심볼 VCC 와 같은 배선의 숨은 VCC 3개 등 42개, 넷리스트 이름+핀 동일). 넷리스트 대조는 같다.
    second_dedupe = 0
    if opts.import_cleanup and cli:
        dd2 = dedupe_project_guarded(pro, sig)
        second_dedupe = dd2['removed']
        if second_dedupe:
            emit(f'hidden labels: {second_dedupe} more redundant hidden labels removed after net-name cleanup '
                 '(same name already given by a power symbol or label on the same wire; '
                 'netlist names+pins verified identical)')
    sheets = sorted(glob.glob(os.path.join(folder, '*.kicad_sch')))
    texts = {}
    for p in sheets:
        with open(p, encoding='utf-8', newline='') as fh:
            texts[p] = fh.read()

    def stem(p):
        return os.path.splitext(os.path.basename(p))[0]
    # 부품 핀에 바로 얹힌 숨은 전원 라벨 → 같은 이름 전원 심볼(보이는 연결로). 넷리스트 대조, 다르면 원복.
    placed_info = []
    if opts.import_cleanup and cli:
        new, placed = place_power_symbols_for_hidden_labels(texts)
        if placed:
            base = sig(root)
            for p, t in new.items():
                if t != texts[p]:
                    with open(p, 'w', encoding='utf-8', newline='') as fh:
                        fh.write(t)
            if sig(root) == base:
                texts = new
                placed_info = [{'sheet': stem(p), 'name': n, 'at': at, 'ref': ref} for p, n, at, ref in placed]
                emit(f'hidden labels: {len(placed)} hidden power labels sitting on part pins replaced by '
                     f'power symbols (' + ', '.join(f"{d['name']}@{d['sheet']}" for d in placed_info[:6])
                     + (' ...' if len(placed_info) > 6 else '') + '; netlist names+pins verified identical)')
            else:
                for p, t in texts.items():
                    with open(p, 'w', encoding='utf-8', newline='') as fh:
                        fh.write(t)
                emit('hidden labels: power symbol placement reverted (netlist changed)')
    # 숨은 라벨 이름 ≠ 같은 배선의 전원 심볼/보이는 라벨 이름 → 두 넷이 하나로 합쳐져 있다(현장 사고 신호)
    conflicts = []
    for p in sheets:
        for c in find_hidden_label_conflicts(texts[p]):
            conflicts.append(dict(c, sheet=stem(p)))
    if conflicts:
        pairs = {}
        for c in conflicts:
            k = (c['name'], '/'.join(c['others']), c['sheet'])
            pairs[k] = pairs.get(k, 0) + 1
        shown = [f"{n} = {o} ({s}{f', {cnt} wires' if cnt > 1 else ''})" for (n, o, s), cnt in pairs.items()]
        # 이슈 목록에도(CLI --issues·GUI 이슈 탭·MCP 공통) — 같은 배선의 이름 둘 = 한 넷으로 합쳐짐
        for (n, o, s), _cnt in pairs.items():
            result.issues.append(f'WARNING hidden label name conflict: {n} = {o} ({s}) - two names on one '
                                 'wire are ONE net')
        emit(f'WARNING hidden label name conflicts: {len(pairs)} - a hidden label and a power symbol or '
             'label with ANOTHER name sit on the same wire, so KiCad joins them into ONE net: '
             + ', '.join(shown[:8]) + (f' (+{len(shown) - 8} more)' if len(shown) > 8 else '')
             + '. If that is not intended (for example a power symbol was swapped), delete or rename one side; '
             '--hidden-labels show makes the hidden side visible')
    remaining = []
    for p in sheets:
        for d in classify_transparent_labels(texts[p]):
            remaining.append(dict(d, sheet=os.path.splitext(os.path.basename(p))[0]))
    mode = opts.hidden_labels or 'keep'
    info = {'mode': mode, 'remaining': remaining, 'applied': 0, 'verified': None, 'reverted': False,
            'conflicts': conflicts, 'power_symbols_placed': placed_info,
            'second_dedupe_removed': second_dedupe}
    result.hidden_labels = info
    if not remaining:
        emit('hidden labels: none left (every importer hidden label was a duplicate or localized)')
        return
    names = ', '.join(f"{d['name']} ({d['sheet']}, {_HIDDEN_KIND_TEXT.get(d['kind'], d['kind'])}"
                      + (f" '{d['alias']}'" if d['kind'] == 'renamed' and d['alias'] else '') + ')'
                      for d in remaining[:8])
    more = f' (+{len(remaining) - 8} more)' if len(remaining) > 8 else ''
    why = ('kept on purpose by --net-names keep (aliases and unnamed nets stay global so names match '
           'PADS/OrCAD)' if result.net_names == 'keep'
           else 'they carry real connections (removing one would cut or rename a net)')
    emit(f'hidden labels: {len(remaining)} left - {why}; invisible in KiCad, printed black in PDF/SVG: '
         f'{names}{more}')
    if mode == 'keep':
        emit('hidden labels: kept as imported. Options: --hidden-labels show (turn them into visible '
             'global labels; the drawing gains labels the OrCAD page did not show) | --hidden-labels shrink '
             '(hide them in PDF/SVG too; the connection then has no visible label anywhere)')
        return
    if not cli:
        emit(f'hidden labels: {mode} not applied (no kicad-cli to verify)')
        return
    style = getattr(opts, 'hidden_label_style', 'subtle') or 'subtle'
    if mode == 'show':
        def fn(text):
            return show_transparent_labels(text, style)
    else:
        fn = shrink_transparent_labels
    base = sig(root)
    total = 0
    for p in sheets:
        text, n = fn(texts[p])
        if n:
            total += n
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(text)
    if total and sig(root) != base:
        for p, t in texts.items():
            with open(p, 'w', encoding='utf-8', newline='') as fh:
                fh.write(t)
        info.update(verified=False, reverted=True)
        emit(f'hidden labels: {mode} reverted (netlist changed) - kept as imported')
        return
    info.update(applied=total, verified=True)
    if mode == 'show':
        emit(f'hidden labels: {total} made visible as global labels'
             + (' (subtle: small light-grey text)' if style == 'subtle' else '')
             + ' (netlist names+pins verified identical)')
    else:
        emit(f'hidden labels: {total} shrunk to 0.01 mm (invisible in PDF; netlist names+pins verified identical)')


def _sync_board_net_names(opts, result, emit, cli, root_sch):
    """[4b] 출력 보드(.kicad_pcb)의 넷 이름을 최종 회로도 넷리스트에 맞춘다(`--board-net-names schematic`).

    [4] 는 PADS 원래 이름의 보드(메모리)로 비교해 두고, 이 단계는 **출력 파일만** 바꾼다. 바꾼 뒤 보드를
    다시 읽어 넷마다 회로도에 같은 이름이 있고 보드 패드가 그 넷의 핀(또는 회로도에 없는 패드)인지 센다 —
    전부 맞으면 Update PCB from Schematic 에서 넷 이름이 바뀌지 않는다. 핀 분할이 바뀌면(있어서는 안 됨)
    원래 파일로 되돌린다."""
    board_pcb = result.files.get('board')
    if not board_pcb or not os.path.isfile(board_pcb):
        return
    mode = opts.board_net_names or 'schematic'
    if mode == 'pads':
        result.board_sync = {'mode': 'pads'}
        emit('[4b] board net names: kept as in PADS (--board-net-names pads)')
        return
    if not cli:
        result.board_sync = {'mode': 'schematic', 'skipped': 'no kicad-cli'}
        emit('[4b] board net names: not synced (no kicad-cli to read schematic netlist)')
        return
    from .kicad_board import load_board, board_netlist, board_net_mapping, rename_board_nets
    from .kicad_netlist import schematic_net_signature
    try:
        sch = schematic_net_signature(cli, root_sch, opts.outdir)
        with open(board_pcb, encoding='utf-8', newline='') as fh:
            original = fh.read()
        before = board_netlist(load_board(board_pcb))
        mapping, unmatched = board_net_mapping(before, sch)
        refs = rename_board_nets(board_pcb, mapping)
        after = board_netlist(load_board(board_pcb))
    except (OSError, RuntimeError) as e:
        emit(f'[4b] board net names: not synced ({e})')
        return
    if sorted(sorted(v) for v in after.values()) != sorted(sorted(v) for v in before.values()):
        with open(board_pcb, 'w', encoding='utf-8', newline='') as fh:
            fh.write(original)
        result.board_sync = {'mode': 'schematic', 'reverted': True}
        emit('[4b] board net names: reverted (pad connectivity changed — should not happen)')
        return
    sch_pins = set().union(*sch.values()) if sch else set()
    consistent = sorted(k for k, v in after.items()
                        if k in sch and all(p in sch[k] or p not in sch_pins for p in v))
    differ = sorted(set(after) - set(consistent))
    result.board_sync = {'mode': 'schematic', 'renamed': dict(sorted(mapping.items())), 'references': refs,
                         'unmatched': sorted(unmatched), 'consistent': len(consistent),
                         'total': len(after), 'differ': differ}
    emit('')
    emit(f'[4b] board net names -> schematic: {len(mapping)} renamed ({refs} references), '
         f'{len(unmatched)} unmatched')
    emit(f'board nets with the same name as the schematic: {len(consistent)}/{len(after)}'
         + ('  (Update PCB keeps all net names)' if not differ else f'  differ: {differ[:8]}'))


# ---------- KiCad 프로젝트 / .DSN 입력 모드 ----------

def _stage_project(pro_path, outdir, result, emit):
    """입력 KiCad 프로젝트(.kicad_pro + 같은 폴더의 .kicad_sch)를 outdir 로 복사한다.

    풋프린트 필드를 채우면 시트 파일이 바뀌므로 **원본은 절대 건드리지 않는다**. outdir 가
    입력 폴더와 같으면 복사할 것이 없으므로 경고만 하고 그대로 쓴다(그 경우에만 원본이 바뀐다).
    반환값: outdir 안의 .kicad_pro 경로."""
    import shutil
    src_dir = os.path.dirname(os.path.abspath(pro_path))
    dst_dir = os.path.abspath(outdir)
    name = os.path.basename(pro_path)
    if os.path.normcase(src_dir) == os.path.normcase(dst_dir):
        emit('warning: outdir is the input project folder; sheets are edited in place')
        return os.path.join(dst_dir, name)
    copied = 0
    for fn in [name] + sorted(f for f in os.listdir(src_dir) if f.lower().endswith('.kicad_sch')):
        shutil.copy2(os.path.join(src_dir, fn), os.path.join(dst_dir, fn))
        copied += 1
    emit(f'copied project: {copied} files -> {dst_dir}')
    return os.path.join(dst_dir, name)


def _remove_previous_sheets(outdir, stem, emit):
    """같은 outdir 에 같은 프로젝트 이름으로 **다시** 변환할 때, 이전 실행이 남긴 시트 파일을 지운다.

    나이틀리 임포트 결과는 루트(`<stem>.kicad_sch`)와 `P02_…` 이후 시트만 덮어쓰고, 재구성
    (`kicad_stable`)이 루트에서 떼어 낸 첫 페이지 `P01_….kicad_sch` 는 임포터가 모르는 이름이라
    그대로 남는다. 그러면 재구성이 그 잔재를 첫 페이지로 다시 집어 쓰고(이름 충돌 회피 규칙 때문에
    새 첫 페이지는 만들어지지 않음), 포맷 판정도 흔들린다(2026-09-11 실제 사고: 루트가 20260830 인
    채 남아 정식 KiCad 가 열지 못함). 이전 `.kicad_pro` 를 읽어 그 프로젝트 뷰가 시트로 보는 파일을
    지운다. 리더와 재구성은 폴더의 모든 `.kicad_sch` 를 이 프로젝트의 페이지로 보므로(출력 폴더는
    프로젝트 하나 전용이 전제) 사실상 폴더의 시트 전부다. 이전 `.kicad_pro` 가 없거나 읽지 못하면
    아무 것도 지우지 않는다."""
    old_pro = os.path.join(outdir, stem + '.kicad_pro')
    if not os.path.isfile(old_pro):
        return 0
    try:
        from .kicad_sch_reader import load_kicad_project
        old_view = load_kicad_project(old_pro)
    except (OSError, ValueError):
        return 0
    removed = 0
    for path in old_view.sheet_files:
        if os.path.normcase(os.path.dirname(os.path.abspath(path))) != os.path.normcase(os.path.abspath(outdir)):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    if removed:
        emit(f'previous output: removed {removed} sheet files of {stem} before re-import')
    return removed


_IMPORT_EXTS = ('.kicad_pro', '.kicad_sch', '.kicad_pcb', '.kicad_prl')


def _move_import_output(src_dir, dst_dir):
    """kicad-cli import 가 임시 폴더에 만든 프로젝트 파일들을 dst_dir 로 옮긴다(같은 이름은 덮어씀).

    혹시 임포터가 하위 폴더에 서브시트를 써 두었더라도(위 주석의 상대경로 버그) 재귀로 모두 걷어
    평평하게 옮긴다. 옮긴 파일 수를 돌려준다."""
    import shutil
    os.makedirs(dst_dir, exist_ok=True)
    moved = 0
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if fn.lower().endswith(_IMPORT_EXTS):
                shutil.move(os.path.join(root, fn), os.path.join(dst_dir, fn))
                moved += 1
    return moved


def _import_dsn(opts, result, emit):
    """`.DSN`(+ 보드 .asc)을 kicad-cli 네이티브 임포터로 프로젝트로 변환한다.

    반환값: (.kicad_pro 경로, 임포트가 함께 만든 .kicad_pcb 경로 또는 None). 실패하면
    result.error 를 채우고 (None, None)."""
    from .kicad_netlist import find_cli_with_orcad_import, cli_version, import_kicad_project
    cli = find_cli_with_orcad_import(opts.kicad_cli)
    if not cli:
        result.error = ('no kicad-cli with OrCAD import support found '
                        '(KiCad 10.99+ nightly required for --dsn; set O2K_KICAD_NIGHTLY)')
        result.exit_code = 2
        return None, None
    result.kicad_cli = cli
    emit(f'kicad-cli (import): {cli} ({cli_version(cli)})')
    stem = opts.project or os.path.splitext(os.path.basename(opts.dsn))[0]
    # kicad-cli 10.99 의 OrCAD 임포터는 출력 폴더가 .DSN 이 있는 폴더 **아래**이면 서브시트
    # (`P02_….kicad_sch`)를 `<outdir>/<DSN 폴더 기준 상대경로>/` 에 다시 써서(상대경로가 두 번
    # 적용됨) 프로젝트에서 첫 시트만 남는다(2026-09-09 회귀 시험의 다중 시트 프로젝트 5건 전부
    # 재현). 그래서 임포트는 항상 DSN 폴더와 무관한 임시 폴더에서
    # 하고 결과 파일을 outdir 로 옮긴다. 경로는 모두 절대 경로로 넘긴다.
    import shutil
    import tempfile
    outdir = os.path.abspath(opts.outdir)
    inputs = [os.path.abspath(opts.dsn)]
    board_from_import = None
    # stable_format(기본값)이면 보드는 나이틀리 임포터에 넘기지 않는다 — 나이틀리가 임포트한
    # `.kicad_pcb` 는 포맷 버전이 정식 10.0 이 열 수 있는 상한을 넘는다(예: 20260831). 보드는
    # 3.4(`_load_board_for_view`)에서 정식 kicad-cli 로 따로 임포트한다.
    if opts.board and opts.board.lower().endswith('.asc') and not opts.stable_format:
        inputs.append(os.path.abspath(opts.board))
        board_from_import = os.path.join(outdir, stem + '.kicad_pcb')
    tmp = tempfile.mkdtemp(prefix='o2k_dsn_import_')
    try:
        try:
            _, text = import_kicad_project(cli, inputs, os.path.join(tmp, stem))
        except RuntimeError as e:
            result.error = str(e)
            result.exit_code = 2
            return None, None
        _remove_previous_sheets(outdir, stem, emit)
        moved = _move_import_output(tmp, outdir)
        pro = os.path.join(outdir, stem + '.kicad_pro')
        if not os.path.isfile(pro):
            result.error = f'kicad-cli import produced no {stem}.kicad_pro (moved {moved} files)'
            result.exit_code = 2
            return None, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 임포트 출력은 로캘에 따라 비ASCII 가 섞이므로 파일로만 남기고 콘솔에는 요약만 찍는다.
    log_path = os.path.join(opts.outdir, 'dsn_import.log')
    try:
        with open(log_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        result.files['import_log'] = log_path
    except OSError:
        pass
    if board_from_import and not os.path.isfile(board_from_import):
        result.issues.append(f'dsn import: board {opts.board} produced no .kicad_pcb; '
                             f'importing it separately')
        board_from_import = None
    emit(f'dsn import: {os.path.basename(pro)} '
         f'(board: {"yes" if board_from_import else "no"}, log: dsn_import.log)')
    return pro, board_from_import


def _load_board_for_view(opts, result, emit, board_from_import):
    """이 모드에서 쓸 보드(.kicad_pcb)를 읽는다. 없으면 None, 오류면 result.error 설정.

    `.asc` 임포트는 정식(고정 상한이 있는) kicad-cli 를 먼저 쓴다(`find_stable_cli`) — 정식
    빌드로 임포트해야 `.kicad_pcb` 버전이 정식 10.0 이 여는 값이 된다. 사용자가 `--kicad-cli` 로
    나이틀리를 직접 줬어도(ERC/[3]은 그 나이틀리로 돌 수 있다) 보드만은 자동 탐지한 정식 빌드를
    우선한다. 정식 빌드가 없으면 기존 `find_kicad_cli`(explicit 존중)로 폴백하고 알린다."""
    from .kicad_board import import_pads_board, load_board
    from .kicad_netlist import find_kicad_cli, find_stable_cli
    if not opts.board:
        return None
    try:
        if board_from_import:
            # `.DSN` 과 함께 임포트한 보드를 그대로 쓴다(같은 임포터, 한 번만 실행).
            emit(f'board: {os.path.basename(board_from_import)} (from dsn import)')
            return load_board(board_from_import)
        if opts.board.lower().endswith('.asc'):
            cli_path = find_stable_cli(opts.kicad_cli)
            if not cli_path:
                cli_path = find_kicad_cli(opts.kicad_cli)
                if cli_path:
                    emit('board: imported with nightly kicad-cli (no stable build found; '
                         'stable KiCad may not open the board)')
            if not cli_path:
                emit('board: kicad-cli required for .asc import (skipped)')
                return None
            imp_dir = os.path.join(opts.outdir, 'pads_import')
            os.makedirs(imp_dir, exist_ok=True)
            pcb = os.path.join(imp_dir, 'board.kicad_pcb')
            rep = import_pads_board(cli_path, opts.board, pcb, os.path.join(imp_dir, 'board_import.json'))
            emit(f'board import: footprints={rep["statistics"]["footprints"]} '
                 f'tracks={rep["statistics"]["tracks"]} warnings={len(rep["warnings"])}')
            return load_board(pcb)
        return load_board(opts.board)
    except (OSError, RuntimeError) as e:
        result.error = f'cannot read board: {e}'
        result.exit_code = 2
        return None


def run_kicad_project(opts, result, emit):
    """KiCad 프로젝트(.kicad_pro) 또는 `.DSN` 을 입력으로 하는 경로.

    EDIF 가 없으므로 1·2차 검증([1]/[2])은 건너뛴다. 하는 일:
      입력 복사(원본 보존) -> 회로도 읽기 -> (보드가 있으면) 풋프린트 필드 채우기·PADS.pretty·
      프로젝트 보드 작성 -> 내장 심볼 라이브러리 추출(sym-lib-table) -> ERC/[3]/PDF ->
      [4] 보드 vs 회로도. 회로도 포맷 버전을 읽을 수 있는 kicad-cli 를 골라 쓴다."""
    from .kicad_sch_reader import (load_kicad_project, set_footprint_fields,
                                   extract_embedded_symbols, write_sym_lib_table,
                                   DEFAULT_LIB_NICK)
    from .kicad_netlist import (pick_cli_for_schematic, cli_version, run_erc, export_pdf, verify_with_kicad,
                                export_netlist, parse_kicad_netlist)
    from .kicad_stable import STABLE_SCH_VERSION as STABLE_SCH_VERSION_TARGET

    emit(f'input mode: {result.input_mode}')
    if not opts.outdir:
        # 이 모드는 파일을 써야만 의미가 있으므로 출력 폴더를 비우면 입력 파일 옆 `<이름>_kicad`.
        opts.outdir = default_outdir(opts.dsn or opts.kicad_project)
        emit(f'outdir: {opts.outdir} (default: next to the input file)')
    try:
        os.makedirs(opts.outdir, exist_ok=True)
        ref = load_reference_netlist(opts.netlist) if opts.netlist else None
    except OSError as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result
    if ref is not None:
        result.ref_nets = ref.nets
        result.ref_format = ref.source_format
        emit(f'reference netlist: {ref.source_format} ({len(ref.nets)} nets)')

    # ---- 1) 입력 준비(.DSN 임포트 또는 프로젝트 복사) ----
    board_from_import = None
    try:
        if opts.dsn:
            pro, board_from_import = _import_dsn(opts, result, emit)
            if result.error:
                return result
        else:
            pro = _stage_project(opts.kicad_project, opts.outdir, result, emit)
    except OSError as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result

    # ---- 2) 회로도 읽기 ----
    try:
        view = load_kicad_project(pro)
    except (OSError, ValueError) as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result

    # ---- 2b) 정식 KiCad 10.0 포맷으로 재구성(기본 동작, --nightly-format 로 끔) ----
    # 나이틀리 임포터 결과(최상위 시트 여러 개, 포맷 20260830 등)를 루트 시트 + 하위 시트 구조로
    # 바꾸면 정식 kicad-cli 가 그대로 읽는다(docs/2026-09-09/[설계]_[05]). 이미 정식 구조면
    # (changed=False) 아무 것도 건드리지 않는다.
    if opts.stable_format and view.version and view.version > STABLE_SCH_VERSION_TARGET:
        from .kicad_stable import restructure_project
        try:
            rr = restructure_project(pro)
        except (OSError, ValueError) as e:
            result.error = f'cannot restructure output: {e}'
            result.exit_code = 2
            return result
        prior_issues = list(view.issues) + rr.issues
        if rr.changed:
            try:
                view = load_kicad_project(pro)
            except (OSError, ValueError) as e:
                result.error = f'cannot read input: {e}'
                result.exit_code = 2
                return result
            view.issues = prior_issues + view.issues
            emit(f'stable format: restructured {len(rr.sheet_files)} sheets under '
                 f'{os.path.basename(rr.root_sch)} (format {STABLE_SCH_VERSION_TARGET}, '
                 f'opens in KiCad 10.0)')
        else:
            view.issues = prior_issues

    result.view = view
    result.issues = view.issues
    result.files['pro'] = pro
    root_sch = view.sheet_files[0] if view.sheet_files else None
    if root_sch is None:
        result.error = f'cannot read input: no .kicad_sch next to {pro}'
        result.exit_code = 2
        return result
    result.files['root_sch'] = root_sch
    # 넷 이름: kicad(기본) 이면 4b) 에서 임포터의 투명 전역 라벨(별칭·자동 넷)을 지워 지역화한다 —
    # 검증할 kicad-cli 가 없으면 keep 으로 남는다(그때 result.net_names 를 고친다).
    result.net_names = opts.net_names or 'kicad'
    emit('[1]/[2] skipped: no EDIF')
    # 이 모드에서 무시되는 옵션은 조용히 넘기지 않고 한 줄씩 알린다(사용자가 준 값과 실제 동작이
    # 다른 채로 끝나지 않도록). 프로젝트 이름은 입력 .kicad_pro 의 파일명이 정한다.
    if opts.kicad_project and opts.project:
        emit(f'project name taken from .kicad_pro: {os.path.splitext(os.path.basename(pro))[0]}; '
             f'--project ignored')

    # 자리표시 부품(보드 전용 레퍼런스 추가)은 이 모드에서 아직 지원하지 않는다.
    rez = opts.resolutions or Resolutions()
    if rez.add_board_only_parts:
        view.issues.append('add board-only parts is not supported in kicad-project/dsn mode '
                           '(no page writer); ignored')
    if rez.footprint_choice:
        view.issues.append('footprint choice is not supported in kicad-project/dsn mode '
                           '(board footprints are always used); ignored')
    if rez.pin_type_overrides:
        view.issues.append('pin type overrides are not supported in kicad-project/dsn mode '
                           '(symbols come from the import); ignored')

    # ---- 3) 보드 연동 ----
    board = _load_board_for_view(opts, result, emit, board_from_import)
    if result.error:
        return result
    result.board = board
    project = os.path.splitext(os.path.basename(pro))[0]

    # ---- 3a) 풋프린트 필드 채우기: (a) 보드 데칼 -> (b) 넷리스트(.asc *PART*) -> (c) 빈 채로 둠.
    # 보드가 없어도 --netlist 만 있으면 (b)/(c) 만으로 채운다(넷리스트 단독 실행 지원).
    if board is not None or ref is not None:
        from .kicad_board import footprint_map
        from .kicad_writer import fp_base
        fp_map = footprint_map(board) if board is not None else {}
        # fp_map 값은 이미 별명이 없다(load_board 가 뗀다). 접두사를 두 번 붙이는 일이 없도록
        # 여기서도 한 번 더 뗀다 — 'PADS:PADS:0603' 은 KiCad 에서 해석되지 않는다.
        # 넷리스트 이름 -> 보드 데칼 대소문자 무시 조회(예: 'so8' 로는 'SO8NB' 를 못 찾는다).
        # 보드 데칼 두 개가 대소문자만 다르면(정상적인 PADS 라이브러리에서는 안 생기지만 방어적으로)
        # 정렬 순서상 먼저 오는 쪽을 결정적으로 고르고 이슈를 남긴다(사전순: 대문자가 소문자보다
        # 앞이므로 'SO8' 이 'so8' 보다 먼저 선택된다).
        decal_by_lower = {}
        for d in sorted(set(fp_map.values())):
            key = d.lower()
            if key in decal_by_lower:
                view.issues.append(f'board decals differ only by case: {decal_by_lower[key]}, {d} '
                                   f'(keeping {decal_by_lower[key]})')
            else:
                decal_by_lower[key] = d
        mapping, fp_sources = {}, {}
        names_unset = 0                  # 보드가 없어 넷리스트 이름을 채우지 않은 ref 수
        for r in sorted(view.references()):
            if r in fp_map:
                mapping[r] = f'PADS:{fp_base(fp_map[r])}'
                fp_sources[r] = 'board'
            elif ref is not None and ref.parts.get(r):
                name = ref.parts[r]
                decal = decal_by_lower.get(name.lower())
                if decal:
                    mapping[r] = f'PADS:{decal}'
                elif board is not None:
                    # 보드는 있는데 그 안에 일치하는 데칼이 없는 경우에만 ref 별로 알린다
                    mapping[r] = fp_base(name)
                    view.issues.append(f'{r}: footprint {name} from netlist has no PADS library entry')
                else:
                    # 보드가 아예 없으면 이름을 가리킬 라이브러리(PADS.pretty)도 없다 — 라이브러리 없는 이름은
                    # KiCad ERC footprint_link_issues 만 부품 수만큼 낳으므로 비워 두고 요약 한 줄로 알린다
                    # (2026-10-29 사용자 결정).
                    fp_sources[r] = ''
                    names_unset += 1
                    continue
                fp_sources[r] = 'netlist'
            else:
                fp_sources[r] = ''
                view.issues.append(f'{r}: no footprint source (not on board, not in netlist)')
        try:
            set_footprint_fields(view, mapping)
        except OSError as e:
            result.error = f'cannot write output: {e}'
            result.exit_code = 2
            return result
        result.footprint_sources = fp_sources
        result.footprint_names_unset = names_unset
        n_board = sum(1 for v in fp_sources.values() if v == 'board')
        n_netlist = sum(1 for v in fp_sources.values() if v == 'netlist')
        n_empty = sum(1 for v in fp_sources.values() if v == '')
        if names_unset:
            view.issues.append(f'footprints left empty (no board): {names_unset} refs have a footprint name in '
                               'the netlist but there is no footprint library to point to; give --board '
                               '(PADS board) to fill them')
        emit(f'footprint fields: {n_board} set from board, {n_netlist} from netlist, {n_empty} empty'
             + (f' ({names_unset} netlist names not used: no board library)' if names_unset else ''))

    # ---- 3b) 보드 종속 산출물: PADS.pretty / 프로젝트 보드 / fp-lib-table (보드가 있을 때만).
    if board is not None:
        from .kicad_board import extract_footprint_library, write_project_board, write_fp_lib_table
        try:
            files, issues = extract_footprint_library(board, opts.outdir, 'PADS')
            board_pcb = os.path.join(opts.outdir, project + '.kicad_pcb')
            board_issues = write_project_board(board, board_pcb, 'PADS', view.symbol_paths())
            write_fp_lib_table(opts.outdir, 'PADS')
        except OSError as e:
            result.error = f'cannot write output: {e}'
            result.exit_code = 2
            return result
        view.issues.extend(issues)
        view.issues.extend(board_issues)
        result.files['board'] = board_pcb
        emit(f'board: {len(files)} footprints -> PADS.pretty, project board written')

    # ---- 4) 내장 심볼 라이브러리 추출(sym-lib-table) ----
    try:
        lib_path, n_sym = extract_embedded_symbols(view, opts.outdir, DEFAULT_LIB_NICK)
        write_sym_lib_table(opts.outdir, DEFAULT_LIB_NICK, os.path.basename(lib_path))
    except OSError as e:
        result.error = f'cannot write output: {e}'
        result.exit_code = 2
        return result
    result.files['lib'] = lib_path
    emit(f'symbols: {n_sym} extracted -> {os.path.basename(lib_path)} (sym-lib-table written)')

    # ---- 4b) 임포트 결함 후처리(hop 갭 병합 + 빈 도면 양식) ----
    # 문자열 수준 수정이라 시트 포맷 버전과 무관하게 적용된다(--nightly-format 로 재구성을 꺼도
    # 그대로 동작) — kicad_stable 이 건드리는 (version …)/generator_version/경로 치환과 겹치지
    # 않는다. ERC/[3]/PDF 보다 먼저 해야 그 결과가 정리된 파일을 반영한다.
    if opts.import_cleanup:
        from .kicad_cleanup import (cleanup_project, cleanup_project_guarded, dedupe_project_guarded,
                                    split_bus_wire_junctions_guarded)
        from .kicad_netlist import schematic_net_signature
        # 모든 정리는 kicad-cli 넷리스트(넷 이름+핀)를 전후 대조하며 한다. 달라지면 위험한 단계를 빼며 다시
        # (cleanup_project_guarded). 중복 투명 라벨 제거는 이름 보호 재시도가 있어 따로 한다.
        guard_cli = pick_cli_for_schematic(view.version, opts.kicad_cli)
        # 버스 위 junction 은 연결을 일부러 나누는 단계라 "동일" 가드보다 먼저, "나뉘기만" 가드로 한다.
        bus_split = None
        if opts.split_bus_junctions and guard_cli:
            bus_split = split_bus_wire_junctions_guarded(
                pro, lambda root: schematic_net_signature(guard_cli, root, opts.outdir))
        kw = dict(default_wire_width=opts.default_wire_width, dedupe_labels=False,
                  korean_font=opts.korean_font)
        if guard_cli:
            cleanup = cleanup_project_guarded(
                pro, lambda root: schematic_net_signature(guard_cli, root, opts.outdir), **kw)
        else:
            cleanup = cleanup_project(pro, **kw)
            cleanup['guard'] = None
        if guard_cli:
            dd = dedupe_project_guarded(
                pro, lambda root: schematic_net_signature(guard_cli, root, opts.outdir))
            cleanup['transparent_removed'] = dd['removed']
            cleanup['dedupe'] = dd
        else:
            cleanup['dedupe'] = None
        cleanup['bus_split'] = bus_split
        result.import_cleanup = cleanup
        guard = cleanup['guard']
        if guard is None:
            parts = ['netlist not verified (no kicad-cli)']
        elif not guard['verified']:
            parts = ['REVERTED - every attempt changed the netlist (' + ' / '.join(guard['tried']) + ')']
        elif guard['level'] != 'full':
            parts = [f"netlist changed with the full cleanup, applied '{guard['level']}' instead"]
        else:
            parts = []
        parts += [f"{cleanup['korean_fixed']} garbled Korean strings restored",
                 f"Korean font on {cleanup['korean_font_set']} texts",
                 f"{cleanup['offpage_converted']} off-page connectors -> global labels",
                 f"{cleanup['gaps_merged']} hop gaps merged in {cleanup['sheets']} sheets",
                 f"{cleanup['labels_moved']} labels snapped to wire ends"]
        if cleanup.get('globals_snapped'):
            parts.append(f"{cleanup['globals_snapped']} global labels moved onto the wire end they fell short of")
        if cleanup.get('hidden_oriented'):
            parts.append(f"{cleanup['hidden_oriented']} hidden labels turned to point away from their wire "
                         "(they print in PDF/SVG)")
        if cleanup.get('pin_numbers_fitted'):
            parts.append(f"{cleanup['pin_numbers_fitted']} pin numbers longer than their pin shrunk to fit")
        if cleanup.get('pin_labels_faced'):
            parts.append(f"{cleanup['pin_labels_faced']} labels on bare part pins turned to face away from "
                         "the symbol body")
        dd = cleanup['dedupe']
        if dd is None:
            parts.append('hidden label dedupe skipped (no kicad-cli to verify)')
        elif dd['reverted']:
            parts.append('hidden label dedupe reverted (netlist changed)')
        elif dd['removed']:
            kept = f", names kept: {', '.join(dd['kept_names'])}" if dd['kept_names'] else ''
            parts.append(f"{dd['removed']} redundant hidden labels removed "
                         f"(netlist names+pins verified identical{kept})")
        if bus_split is None and opts.split_bus_junctions:
            parts.append('bus junction check skipped (no kicad-cli to verify)')
        elif bus_split and bus_split['reverted']:
            parts.append('bus junction removal reverted (netlist did not only split)')
        elif bus_split and bus_split['removed']:
            parts.append(f"{bus_split['removed']} junctions where wires touch a bus removed, "
                         f"{len(bus_split['splits'])} importer-merged nets split")
        if bus_split and bus_split.get('entries_added'):
            parts.append(f"{bus_split['entries_added']} wire ends touching a bus redrawn with bus entries "
                         "(netlist names+pins verified identical)")
        elif bus_split and bus_split.get('entries_reverted'):
            parts.append('bus entry drawing reverted (netlist changed)')
        if cleanup['wires_normalized']:
            parts.append(f"{cleanup['wires_normalized']} wires set to default width")
        if cleanup['worksheet']:
            parts.append('blank worksheet set')
        emit('import cleanup: ' + ', '.join(parts))

    # ---- 4c) 넷 이름 범위(--net-names) / 남은 숨은 라벨(--hidden-labels) ----
    # 둘 다 kicad-cli 넷리스트로 대조할 수 있을 때만 한다(검증 못 하면 바꾸지 않는다).
    _apply_net_scope_options(opts, result, emit, pro, pick_cli_for_schematic(view.version, opts.kicad_cli))
    _apply_label_tidy(opts, result, emit, os.path.splitext(os.path.abspath(pro))[0] + '.kicad_sch',
                      pick_cli_for_schematic(view.version, opts.kicad_cli))
    _report_auto_changes(opts, result, emit)

    # ---- 5) kicad-cli: ERC / [3] / PDF ----
    cli_path = pick_cli_for_schematic(view.version, opts.kicad_cli)
    result.kicad_cli = cli_path
    board_ref_nets = ref.nets if ref is not None else None
    board_net_reference = 'reference netlist'
    if not cli_path:
        emit(f'kicad-cli: no build can read schematic format {view.version} '
             f'(skipped ERC/netlist)')
        emit('erc: skipped (no compatible kicad-cli)')
        if ref is not None:
            emit('[3] skipped: no compatible kicad-cli')
    else:
        emit(f'kicad-cli: {cli_path} ({cli_version(cli_path)})')
        try:
            erc_json = os.path.join(opts.outdir, 'erc.json')
            erc = run_erc(cli_path, root_sch, erc_json)
            result.erc = erc
            result.files['erc_json'] = erc_json
            emit(f'erc violations: {erc["count"]} ' + ' '.join(f'{k}={v}' for k, v in sorted(erc['by_type'].items())))
            if ref is not None:
                cmp3, issues3 = verify_with_kicad(cli_path, root_sch, ref.nets, opts.outdir)
                view.issues.extend(issues3)
                result.verifications['kicad'] = cmp3
                result.files['netlist_txt'] = os.path.join(opts.outdir, 'kicad_netlist.txt')
                emit('')
                emit(f'[3] kicad-cli netlist vs reference ({result.ref_format or "PADS"})')
                emit(format_report(cmp3))
                if not cmp3.ok:
                    result.exit_code = 1
            elif board is not None:
                # 정답 넷리스트가 없으면 회로도 넷리스트(kicad-cli 익스포트)를 [4] 넷 대조의 기준으로
                # 쓴다 — 보드가 회로도와 같은 연결인지 최소한 확인할 수 있다(EDIF 경로와 같은 역할).
                from .kicad_board import schematic_nets_for_board
                net_txt = export_netlist(cli_path, root_sch, os.path.join(opts.outdir, 'kicad_netlist.txt'))
                result.files['netlist_txt'] = net_txt
                with open(net_txt, encoding='utf-8') as fh:
                    sch_nets, issues_n = schematic_nets_for_board(parse_kicad_netlist(fh.read()), board)
                view.issues.extend(issues_n)
                board_ref_nets, board_net_reference = sch_nets, 'schematic netlist'
            if opts.pdf:
                pdf = export_pdf(cli_path, root_sch, os.path.join(opts.outdir, project + '.pdf'))
                result.files['pdf'] = pdf
                emit(f'pdf: {pdf}')
        except RuntimeError as e:
            emit(f'kicad-cli error: {e}')
            result.exit_code = 2

    # 정식 KiCad 가 못 여는 포맷이면 어떤 실행 파일로 열어야 하는지 알려 준다(설치 없이 포터블 kicad.exe).
    from .explain import STABLE_SCH_CEILING, kicad_gui_next_to
    if view.version and view.version > STABLE_SCH_CEILING:
        gui_exe = kicad_gui_next_to(cli_path)
        emit(f'note: schematic format {view.version} needs KiCad 10.99+ (stable 10 reads up to '
             f'{STABLE_SCH_CEILING}); open with: {gui_exe or "portable nightly kicad.exe (not found)"}')

    # ---- 6) 4차 검증: 보드 vs 회로도 ----
    if board is not None:
        from .kicad_board import compare_board, format_board_diff
        diff = compare_board(board, view, board_ref_nets, net_reference=board_net_reference)
        result.board_diff = diff
        emit('')
        emit(format_board_diff(diff))
        if opts.strict_board and ((diff.net_compare is not None and not diff.net_compare.ok)
                                  or diff.only_sch_refs or diff.only_board_refs):
            result.exit_code = max(result.exit_code, 1)
    _sync_board_net_names(opts, result, emit, cli_path, root_sch)
    return result


# ---------- 표현 ----------

def format_result(result: PipelineResult, show_issues=False) -> str:
    """기존 CLI stdout 과 동일한 리포트 텍스트(ASCII 영문 + 설계 텍스트).

    `error` 가 있으면(입력/출력 오류, exit 2) 맨 앞에 `error: ...` 줄을 넣는다 - EDIF 를 읽은
    뒤에 난 오류(보드 읽기/출력 쓰기 실패)라도 리포트만 보고 정상 완료로 오해하지 않도록.
    CLI 는 이 경우 stdout 을 쓰지 않고 stderr 로만 알리므로(기존 동작 유지) 영향받지 않는다.
    """
    if result.design is None and result.view is None:
        return f'error: {result.error}' if result.error else ''
    head_lines = [f'error: {result.error}', ''] if result.error else []
    # summary 는 write_project/ERC/3차 검증이 design.issues 에 이슈를 추가한 뒤 맨 앞에 붙인다
    # (issues: 카운트가 writer 이슈까지 반영하도록; [1]/[2]/[3] 순서 자체는 바뀌지 않음).
    lines = head_lines + [result_summary(result)]
    if result.options.netlist or result.view is not None:
        lines.append('')
    lines.extend(result.log)
    if show_issues:
        lines.append('')
        lines.append('issues:')
        lines.extend('  ' + i for i in result.issues)
    return '\n'.join(lines)


def _net_compare_to_json(cmp):
    if cmp is None:
        return None
    return {'matched': cmp.matched,
            'mismatches': [{'net': d.net, 'missing': sorted(d.missing), 'extra': sorted(d.extra)}
                           for d in cmp.mismatches],
            'only_ours': list(cmp.only_ours),
            'only_ref': list(cmp.only_ref),
            'ok': cmp.ok}


def _board_diff_to_json(diff):
    if diff is None:
        return None
    return {'only_board_refs': list(diff.only_board_refs),
            'only_sch_refs': list(diff.only_sch_refs),
            'missing_pins': {k: sorted(v) for k, v in diff.missing_pins.items()},
            'extra_pads': {k: sorted(v) for k, v in diff.extra_pads.items()},
            'footprint_diff': [list(x) for x in diff.footprint_diff],
            'net_reference': getattr(diff, 'net_reference', 'reference netlist'),
            'net_renames': [list(x) for x in getattr(diff, 'net_renames', [])],
            'net_compare': _net_compare_to_json(diff.net_compare)}


def _resolutions_to_json(rez):
    """Resolutions(dict 로 펼친 것) -> JSON 가능한 dict.

    `pin_type_overrides` 의 키는 `(symbol, pin)` 튜플이라 그대로는 JSON 이 못 된다. MCP `convert`
    의 입력 형식과 같은 `"SYMBOL:PIN"` 문자열로 되돌려 왕복(round trip)이 되게 한다
    (튜플 repr 을 쓰면 그 JSON 을 다시 convert 에 넣을 수 없다).
    """
    out = {}
    for key, val in (rez or {}).items():
        if isinstance(val, list):
            out[key] = list(val)
        elif key == 'pin_type_overrides':
            out[key] = {(f'{k[0]}:{k[1]}' if isinstance(k, tuple) and len(k) == 2 else str(k)): v
                        for k, v in (val or {}).items()}
        elif isinstance(val, dict):
            out[key] = {str(k): v for k, v in val.items()}
        else:
            out[key] = val
    return out


def result_to_json(result: PipelineResult) -> dict:
    """MCP/GUI 용 직렬화(json.dumps 가능한 dict). 넷 비교는 목록으로 펼친다."""
    opts = asdict(result.options)
    opts['resolutions'] = _resolutions_to_json(opts['resolutions'])
    from .explain import explain_result
    return {'options': opts,
            'input_mode': result.input_mode,
            'summary': result_summary(result),
            'explanation': explain_result(result, 'en'),
            'text': format_result(result),
            'verifications': {k: _net_compare_to_json(v) for k, v in result.verifications.items()},
            'board_diff': _board_diff_to_json(result.board_diff),
            'erc': ({'count': result.erc['count'], 'by_type': dict(result.erc['by_type']),
                     'items': [list(i) for i in result.erc['items']]} if result.erc else None),
            'files': dict(result.files),
            'issues': list(result.issues),
            'log': list(result.log),
            'footprint_sources': dict(result.footprint_sources),
            'footprint_names_unset': result.footprint_names_unset,
            'kicad_cli': result.kicad_cli,
            'net_names': result.net_names,
            'ref_format': result.ref_format,
            'import_cleanup': dict(result.import_cleanup) if result.import_cleanup else None,
            'localize': dict(result.localize) if result.localize else None,
            'hidden_labels': dict(result.hidden_labels) if result.hidden_labels else None,
            'duplicate_alias_removed': result.duplicate_alias_removed,
            'auto_changes': list(result.auto_changes) if result.auto_changes else None,
            'board_sync': dict(result.board_sync) if result.board_sync else None,
            'exit_code': result.exit_code,
            'error': result.error}
