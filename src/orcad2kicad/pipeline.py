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
import os
from dataclasses import dataclass, field, asdict

from .edif_reader import load_edif
from .pads_netlist import load_pads_netlist
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
    net_names: str = None        # 'kicad' | 'keep' | None(자동: board 있으면 keep)
    pdf: bool = False
    strict_board: bool = False
    stable_format: bool = True   # kicad-project/dsn 모드: 나이틀리 결과를 정식 KiCad 10.0
                                  # 포맷(루트 시트 + 하위 시트)으로 재구성한다(--nightly-format 로 끔).
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
    exit_code: int = 0
    error: str = None                    # 입력/출력 오류 메시지 (exit 2, 그 시점에 중단)
    ref_nets: dict = None                # 기준 PADS 넷리스트({넷: {REF.PIN}}) — 재실행/비교용
    footprint_sources: dict = field(default_factory=dict)  # ref -> 'board'|'netlist'|'' (kicad-project/dsn 모드)


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
        ref = load_pads_netlist(opts.netlist) if opts.netlist else None
    except OSError as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result
    result.design = design
    result.issues = design.issues
    if ref is not None:
        result.ref_nets = ref.nets

    # ---- 2) 1·2차 검증 (EDIF 조인 / 지오메트리 vs PADS) ----
    if ref is not None:
        cmp1 = compare_netlists(edif_netlist(design), ref.nets)
        cmp2 = compare_netlists(derive_nets(design), ref.nets)
        result.verifications['edif'] = cmp1
        result.verifications['geometry'] = cmp2
        emit('[1] EDIF joined vs PADS')
        emit(format_report(cmp1))
        emit('')
        emit('[2] geometry vs PADS')
        emit(format_report(cmp2))
        result.exit_code = 0 if cmp1.ok and cmp2.ok else 1

    if not opts.outdir:
        return result

    # ---- 3) KiCad 출력 (+ 보드 임포트) ----
    from .kicad_writer import write_project
    from .kicad_netlist import find_kicad_cli, run_erc, export_pdf, verify_with_kicad
    # cli_path 는 --board .asc 임포트에도 필요하므로 write_project 보다 먼저 계산한다.
    cli_path = find_kicad_cli(opts.kicad_cli)
    result.kicad_cli = cli_path
    board = None
    if opts.board:
        from .kicad_board import import_pads_board, load_board
        try:
            if opts.board.lower().endswith('.asc'):
                if not cli_path:
                    emit('board: kicad-cli required for .asc import (skipped)')
                else:
                    imp_dir = os.path.join(opts.outdir, 'pads_import')
                    os.makedirs(imp_dir, exist_ok=True)
                    pcb = os.path.join(imp_dir, 'board.kicad_pcb')
                    rep = import_pads_board(cli_path, opts.board, pcb, os.path.join(imp_dir, 'board_import.json'))
                    emit(f'board import: footprints={rep["statistics"]["footprints"]} '
                         f'tracks={rep["statistics"]["tracks"]} warnings={len(rep["warnings"])}')
                    board = load_board(pcb)
            else:
                board = load_board(opts.board)
        except (OSError, RuntimeError) as e:
            result.error = f'cannot read board: {e}'
            result.exit_code = 2
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

    net_names = opts.net_names or ('keep' if board is not None else 'kicad')
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
                            netlist_footprints=(ref.parts if ref is not None else None))
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
                emit('[3] kicad-cli netlist vs PADS')
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
    return result


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
        ref = load_pads_netlist(opts.netlist) if opts.netlist else None
    except OSError as e:
        result.error = f'cannot read input: {e}'
        result.exit_code = 2
        return result
    if ref is not None:
        result.ref_nets = ref.nets

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
    result.net_names = 'keep'          # 넷 이름은 임포트된 회로도(전역 라벨) 그대로다
    emit('[1]/[2] skipped: no EDIF')
    # 이 모드에서 무시되는 옵션은 조용히 넘기지 않고 한 줄씩 알린다(사용자가 준 값과 실제 동작이
    # 다른 채로 끝나지 않도록). 프로젝트 이름은 입력 .kicad_pro 의 파일명이 정하고, 넷 이름은
    # 임포트된 회로도가 전역 라벨을 쓰므로 항상 'keep' 이다.
    if opts.kicad_project and opts.project:
        emit(f'project name taken from .kicad_pro: {os.path.splitext(os.path.basename(pro))[0]}; '
             f'--project ignored')
    if opts.net_names and opts.net_names != 'keep':
        emit('net names: keep (imported schematics use global labels; --net-names ignored)')

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
                    # (보드가 아예 없으면 비교할 대상이 없으므로 아래에서 요약 한 줄로 대신한다).
                    mapping[r] = fp_base(name)
                    view.issues.append(f'{r}: footprint {name} from netlist has no PADS library entry')
                else:
                    mapping[r] = fp_base(name)
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
        n_board = sum(1 for v in fp_sources.values() if v == 'board')
        n_netlist = sum(1 for v in fp_sources.values() if v == 'netlist')
        n_empty = sum(1 for v in fp_sources.values() if v == '')
        if board is None and n_netlist:
            # 보드가 아예 없는 실행: ref 별 "라이브러리에 없음" 이슈 대신 요약 한 줄만 남긴다
            # (비교할 보드 데칼이 없으므로 전부 접두사 없는 OrCAD 이름 그대로다).
            view.issues.append(f'footprints filled from netlist only (no board): {n_netlist} refs; '
                               f'names are bare OrCAD footprint names without a library')
        emit(f'footprint fields: {n_board} set from board, {n_netlist} from netlist, {n_empty} empty')

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
                emit('[3] kicad-cli netlist vs PADS')
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
            'kicad_cli': result.kicad_cli,
            'net_names': result.net_names,
            'exit_code': result.exit_code,
            'error': result.error}
