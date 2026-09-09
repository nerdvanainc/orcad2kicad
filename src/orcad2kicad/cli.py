"""명령행 진입점 — 인자를 `pipeline.PipelineOptions` 로 옮기고 결과를 출력만 한다.

실제 변환·검증 로직은 전부 `pipeline.run_pipeline` 에 있다(GUI·MCP 서버와 공유).

입력은 셋 중 하나다: EDIF 파일(위치 인자) / `--kicad-project PRO`(KiCad 프로젝트) /
`--dsn FILE`(OrCAD .DSN — kicad-cli 나이틀리의 네이티브 임포터로 변환 후 진행).

usage: python -m orcad2kicad.cli [IN.EDF | --kicad-project PRO | --dsn FILE]
                                  [--netlist X.asc] [--report out.txt] [--issues] [--explain]
                                  [-o OUTDIR] [--project NAME] [--kicad-cli PATH] [--pdf]
                                  [--board FILE] [--net-names {kicad,keep}] [--strict-board]
                                  [--add-board-part REF] [--footprint-choice REF=board|orcad]
                                  [--pin-type SYMBOL:PIN=TYPE] [--nightly-format]
       python -m orcad2kicad.cli --fetch-kicad-nightly [DIR] [--kicad-nightly-installer FILE]
                                  [--force-fetch] [--no-download-tools]
GUI 로 쓰려면 `python -m orcad2kicad` (tkinter 창).
콘솔 출력은 ASCII 영문.
exit code: 0 = 모든 검증 PASS(또는 검증을 안 했을 때), 1 = 검증 중 하나라도 FAIL,
           2 = 입력 파일을 읽을 수 없거나(OSError) 출력/리포트 파일을 쓸 수 없음(OSError)
               또는 kicad-cli 실행 자체가 실패함(RuntimeError).
"""
from __future__ import annotations
import argparse
import os
import sys
from .kicad_portable import DEFAULT_ROOT as PORTABLE_ROOT
from .pipeline import (PipelineOptions, Resolutions, run_pipeline, format_result,
                       summary)   # noqa: F401 (summary: 호환 재노출)
from .branding import CLI_BANNER, CONTACT_EN, GITHUB_URL, VERSION
from .edif_reader import load_edif
from .pads_netlist import load_pads_netlist
from .verify import edif_netlist, compare_netlists
from .geometry import derive_nets


def run_verification(edf_path, asc_path):
    """(호환용) EDIF/PADS 를 읽어 1·2차 비교 결과를 돌려준다. 새 코드는 run_pipeline 을 쓸 것."""
    design = load_edif(edf_path)
    ref = load_pads_netlist(asc_path)
    cmp1 = compare_netlists(edif_netlist(design), ref.nets)
    cmp2 = compare_netlists(derive_nets(design), ref.nets)
    return design, ref, cmp1, cmp2


def _resolutions(args):
    """해소 플래그(--add-board-part/--footprint-choice/--pin-type) -> Resolutions.
    형식이 틀리면 ValueError (콘솔 메시지는 ASCII)."""
    rez = Resolutions(add_board_only_parts=list(args.add_board_part))
    for item in args.footprint_choice:
        ref, _, choice = item.partition('=')
        if not ref or choice not in ('board', 'orcad'):
            raise ValueError(f'bad --footprint-choice {item!r} (expected REF=board or REF=orcad)')
        rez.footprint_choice[ref] = choice
    for item in args.pin_type:
        left, _, etype = item.partition('=')
        sym, _, pin = left.rpartition(':')
        if not (sym and pin and etype):
            raise ValueError(f'bad --pin-type {item!r} (expected SYMBOL:PIN=TYPE)')
        rez.pin_type_overrides[(sym, pin)] = etype
    return rez


def _fetch_nightly(args):
    """--fetch-kicad-nightly 처리. 설치는 하지 않고 압축 해제만 한다. 출력은 ASCII."""
    from .kicad_portable import (PortableError, ensure_portable_kicad, read_portable_info,
                                 verify_portable)
    root = args.fetch_kicad_nightly or PORTABLE_ROOT
    try:
        cli = ensure_portable_kicad(root=root, force=args.force_fetch,
                                    log=lambda line: print(line),
                                    installer=args.kicad_nightly_installer,
                                    download_tools=not args.no_download_tools)
        # ensure_portable_kicad 가 이미 실행해 본 결과가 portable.json 에 있다. 다시 실행하지
        # 않는다. 손으로 풀어 둔 폴더처럼 기록이 없을 때만 직접 확인한다.
        info = read_portable_info(os.path.dirname(os.path.dirname(cli))) or {}
        if not (info.get('version') and 'orcad_import' in info):
            info = verify_portable(os.path.dirname(os.path.dirname(cli)))
    except PortableError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    print(f'portable kicad-cli: {cli} ({info["version"]}, '
          f'orcad import: {"yes" if info["orcad_import"] else "no"})')
    return 0


def _console_utf8_safe():
    """cp949 콘솔에서 인코딩 불가 문자(넷 이름·경로의 특수문자 등)로 print 가 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _console_utf8_safe()
    ap = argparse.ArgumentParser(
        prog='orcad2kicad',
        description=CLI_BANNER,
        epilog='GUI: run "python -m orcad2kicad" to open the tkinter GUI.\n' + CONTACT_EN)
    ap.add_argument('--version', action='version', version=CLI_BANNER + '\n' + GITHUB_URL)
    ap.add_argument('--quiet', action='store_true',
                     help='suppress the trailing banner line printed after a normal run')
    ap.add_argument('edf', nargs='?', help='OrCAD EDIF 2.0.0 file (.EDF); omit when using '
                                           '--kicad-project or --dsn')
    ap.add_argument('--kicad-project', metavar='PRO',
                     help='KiCad project (.kicad_pro) to use as input instead of EDIF '
                          '(copied into OUTDIR, then linked with the board and verified)')
    ap.add_argument('--dsn', metavar='FILE',
                     help='OrCAD .DSN input; imported with kicad-cli (KiCad 10.99+ nightly) '
                          'and then handled like --kicad-project')
    ap.add_argument('--netlist', help='PADS2000 .asc netlist exported from OrCAD (reference)')
    ap.add_argument('--report', help='write the report to this file as well')
    ap.add_argument('--issues', action='store_true', help='print all issues')
    ap.add_argument('--explain', action='store_true',
                    help='append a plain-language summary: what each check means and what to look at')
    ap.add_argument('-o', '--outdir',
                    help='write KiCad project into this directory (EDIF input: omit to verify only; '
                         '--dsn/--kicad-project: default <input dir>/<name>_kicad)')
    ap.add_argument('--project', help='project name (default: design name)')
    ap.add_argument('--kicad-cli', help='path to kicad-cli (default: auto-detect)')
    ap.add_argument('--pdf', action='store_true', help='also export PDF with kicad-cli')
    ap.add_argument('--board', help='PADS board file (.asc: kicad-cli import; .kicad_pcb: read directly)')
    ap.add_argument('--net-names', choices=['kicad', 'keep'],
                     help="net naming mode (default: 'keep' if --board else 'kicad')")
    ap.add_argument('--strict-board', action='store_true',
                     help='exit 1 if [4] board-vs-schematic net compare fails or refs differ')
    ap.add_argument('--nightly-format', dest='stable_format', action='store_false',
                     help='kicad-project/dsn mode: keep the importer native format '
                          '(KiCad 10.99+ nightly only) instead of restructuring into the stable '
                          'KiCad 10.0 hierarchy (default: restructure)')
    # 불일치 해소(3단계-B Task 2). GUI 없이도 같은 결정을 줄 수 있게 한 플래그들.
    ap.add_argument('--add-board-part', action='append', default=[], metavar='REF',
                     help='add a board-only part as a placeholder symbol on page 99-PCB-ONLY (repeatable)')
    ap.add_argument('--footprint-choice', action='append', default=[], metavar='REF=board|orcad',
                     help='footprint name to use for one reference (default: board)')
    ap.add_argument('--pin-type', action='append', default=[], metavar='SYMBOL:PIN=TYPE',
                     help='override a library pin electrical type, e.g. STM32H723ZGT6:6=power_out')
    # 포터블 KiCad 나이틀리 준비(설치 없음). 변환 파이프라인과는 무관한 별도 동작이라
    # 인자를 읽자마자 여기서 끝낸다 — 기존 stdout 출력 규약을 건드리지 않기 위해서다.
    ap.add_argument('--fetch-kicad-nightly', nargs='?', const=PORTABLE_ROOT, metavar='DIR',
                     help='download the latest KiCad nightly installer and EXTRACT it (never runs '
                          'the installer) into DIR (default: %%LOCALAPPDATA%%\\orcad2kicad\\kicad-nightly), '
                          'then print the portable kicad-cli path')
    ap.add_argument('--kicad-nightly-installer', metavar='FILE',
                     help='use this already downloaded nightly installer instead of downloading '
                          '(only with --fetch-kicad-nightly; no network access)')
    ap.add_argument('--force-fetch', action='store_true',
                     help='with --fetch-kicad-nightly: re-extract even if it is already prepared')
    ap.add_argument('--no-download-tools', action='store_true',
                     help='with --fetch-kicad-nightly: do not fetch the 7-Zip console build when '
                          'no archiver is installed; fail with guidance instead')
    args = ap.parse_args(argv)

    if args.fetch_kicad_nightly is not None:
        return _fetch_nightly(args)

    try:
        rez = _resolutions(args)
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2

    result = run_pipeline(PipelineOptions(
        edf=args.edf, kicad_project=args.kicad_project, dsn=args.dsn,
        netlist=args.netlist, board=args.board, outdir=args.outdir,
        project=args.project, kicad_cli=args.kicad_cli, net_names=args.net_names,
        pdf=args.pdf, strict_board=args.strict_board, stable_format=args.stable_format,
        resolutions=rez))
    if result.error:                       # 입력/출력 오류: stdout 에는 아무것도 쓰지 않는다
        print(f'error: {result.error}', file=sys.stderr)
        return result.exit_code

    text = format_result(result, show_issues=args.issues)
    if args.explain:
        from .explain import format_explanation
        text += '\n\n' + format_explanation(result, 'en')
    print(text)
    if args.report:
        try:
            with open(args.report, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
        except OSError as e:
            print(f'error: cannot write report: {e}', file=sys.stderr)
            return 2
    if not args.quiet:
        print(f'\n-- {CLI_BANNER} --')
    return result.exit_code


if __name__ == '__main__':
    sys.exit(main())
