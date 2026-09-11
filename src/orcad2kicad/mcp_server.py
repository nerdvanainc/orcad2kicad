"""stdio MCP 서버 — 외부 AI(Claude Code 등)가 변환·검증 파이프라인을 툴로 호출한다.

전송은 stdin/stdout, **한 줄에 JSON-RPC 2.0 메시지 하나**(UTF-8, 개행 종결). 로그는 stderr 로만
나가며(ASCII 영문) stdout 에는 JSON-RPC 응답 외의 어떤 글자도 쓰지 않는다 — 한 글자라도 섞이면
클라이언트의 파서가 깨진다. SDK 의존 없이 표준 라이브러리만으로 프로토콜을 직접 구현한다.

지원 메서드:
  initialize                  -> protocolVersion / capabilities / serverInfo
  notifications/initialized   -> 응답 없음(알림에는 절대 응답하지 않는다)
  ping                        -> {}
  tools/list                  -> TOOLS
  tools/call                  -> {"content": [{"type": "text", "text": <JSON 문자열>}], "isError": bool}
  그 밖                       -> JSON-RPC 오류 -32601

툴: convert / verify / board_diff / list_issues / read_file / find_kicad_cli / suggest.
툴 설명(description)만 예외적으로 영문이다 — 그대로 AI 클라이언트에게 전달되는 프롬프트이기 때문.

등록 방법(Claude Code):
  # 소스에서 바로 (PYTHONPATH 에 src 를 넣어야 orcad2kicad 패키지가 보인다)
  claude mcp add orcad2kicad -e PYTHONPATH=<repo>/src -- python -m orcad2kicad.mcp_server
  # PYTHONPATH 를 셸에서 미리 잡아둔 경우
  claude mcp add orcad2kicad -- python -m orcad2kicad.mcp_server
  # 단일 exe(Task 6 에서 제공)
  claude mcp add orcad2kicad -- orcad2kicad-cli.exe --mcp

실행 옵션:
  --root DIR    read_file 이 읽을 수 있는 루트(여러 번 지정 가능). 환경변수 O2K_MCP_ROOT
                (os.pathsep 구분)로도 지정할 수 있다. 지정이 없으면 마지막 convert 의
                outdir 만 읽을 수 있다.
  --selftest    툴 목록을 JSON 으로 찍고 즉시 종료(exit 0). 패키징 스모크 테스트용.

주의: 파이프라인은 동기 실행이라 convert 한 번이 수십 초 걸릴 수 있고 그동안 다음 요청을 읽지
않는다(MCP stdio 서버로서 허용되는 동작). 진행 상황은 stderr 로그로 확인한다.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile

from .pipeline import (PipelineOptions, Resolutions, run_pipeline, result_to_json,
                       _board_diff_to_json, _net_compare_to_json, summary)
from .kicad_netlist import find_kicad_cli

from .branding import COMPANY, PRODUCT, VERSION as SERVER_VERSION

SERVER_NAME = PRODUCT                             # 로그 접두사(ASCII)
SERVER_INFO_NAME = f'{PRODUCT} ({COMPANY})'       # initialize 응답의 serverInfo.name

# 우리가 아는 MCP 프로토콜 개정판. 클라이언트가 이 중 하나를 보내면 그대로 되돌려주고,
# 모르는 값(또는 문자열이 아닌 값)이면 기본값으로 협상한다.
PROTOCOL_VERSION = '2025-06-18'
KNOWN_PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')

READ_FILE_MAX_BYTES = 200000            # read_file 기본 상한(초과분은 잘리고 truncated=True)

# JSON-RPC 오류 코드
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """툴 인자 오류/실행 실패. JSON-RPC 오류가 아니라 isError=True 결과로 되돌아간다."""


def _ascii(text):
    """stderr 로그와 오류 메시지는 ASCII 만 — 경로에 한글이 있어도 콘솔이 깨지지 않게."""
    return str(text).encode('ascii', 'replace').decode('ascii')


def log(msg):
    sys.stderr.write(f'{SERVER_NAME}-mcp: {_ascii(msg)}\n')
    sys.stderr.flush()


# ---------- 툴 정의(설명은 AI 클라이언트에게 그대로 전달되므로 영문) ----------

_EDF_DESC = ('Absolute path to the OrCAD Capture EDIF 2.0.0 export (.EDF). One of edf, dsn or '
            'kicad_project is required for convert (exactly one).')
_NETLIST_DESC = ('Optional absolute path to the reference PADS2000 netlist (.asc). '
                 'When given, net membership (REF.PIN sets) is compared against it.')

TOOLS = [
    {
        'name': 'convert',
        'description': (
            'Convert a design to a KiCad project (.kicad_sym/.kicad_sch/.kicad_pro) in outdir, '
            'optionally importing a PADS board, and run every available verification. Input is '
            'exactly one of: edf (OrCAD EDIF, runs [1] EDIF-joined nets and [2] geometry-derived '
            'nets vs the PADS netlist in addition to the checks below), dsn (an OrCAD .DSN file, '
            'imported natively with a KiCad nightly 10.99+ kicad-cli before conversion; [1]/[2] '
            'are skipped since there is no EDIF), or kicad_project (an existing KiCad project, '
            'e.g. produced by importing a .DSN yourself in KiCad; [1]/[2] are also skipped). All '
            'three modes then run [3] kicad-cli exported netlist vs the PADS netlist (needs a '
            'kicad-cli able to read the schematic format) and [4] board vs schematic. Writes '
            'files to disk. Returns the full pipeline result as JSON: options, input_mode, '
            'summary, text (the human report), verifications, board_diff, erc, files, issues, '
            'log, kicad_cli, net_names, import_cleanup (sheets/gaps_merged/worksheet when applied '
            'in dsn/kicad_project mode), exit_code (0 pass / 1 verification failed / 2 io error), '
            'error. This can take tens of seconds on a large design (longer for a .DSN import).'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'edf': {'type': 'string', 'description': _EDF_DESC},
                'dsn': {'type': 'string',
                       'description': ('Optional absolute path to an OrCAD .DSN file. Imported '
                                       'with a KiCad nightly (10.99+) kicad-cli native importer '
                                       'before conversion (set kicad_cli or the O2K_KICAD_NIGHTLY '
                                       'environment variable to point at one). Alternative to edf/'
                                       'kicad_project; exactly one of the three is required.')},
                'kicad_project': {'type': 'string',
                                  'description': ('Optional absolute path to an existing KiCad '
                                                  'project (.kicad_pro), such as one produced by '
                                                  'importing a .DSN in KiCad itself. Alternative '
                                                  'to edf/dsn; exactly one of the three is '
                                                  'required. EDIF-based verifications [1]/[2] are '
                                                  'skipped in this mode; add_board_only_parts, '
                                                  'footprint_choice and pin_type_overrides are '
                                                  'not supported here (they are noted as an '
                                                  'issue and ignored).')},
                'outdir': {'type': 'string', 'description': 'Output directory for the KiCad project (created if missing).'},
                'netlist': {'type': 'string', 'description': _NETLIST_DESC},
                'board': {'type': 'string', 'description': 'Optional PADS Layout ASCII (.asc, imported with kicad-cli) or an existing .kicad_pcb.'},
                'project': {'type': 'string', 'description': 'Project name (default: the EDIF design name, or the .DSN stem for dsn mode).'},
                'net_names': {'type': 'string', 'enum': ['kicad', 'keep'],
                              'description': "Net naming: 'keep' writes global labels so PADS net names survive (default when board is given); 'kicad' lets KiCad name nets."},
                'pdf': {'type': 'boolean', 'description': 'Also export a schematic PDF with kicad-cli.'},
                'strict_board': {'type': 'boolean', 'description': 'Treat board/schematic differences as a failure (exit_code 1).'},
                'import_cleanup': {'type': 'boolean',
                                   'description': 'dsn/kicad_project input only: clean up nightly importer '
                                                  'defects before ERC/PDF - merge hop-gap wire fragments '
                                                  '(dangling-end markers, unconnected_wire_endpoint ERC noise) '
                                                  'and set a blank drawing sheet (removes the duplicated title '
                                                  'block). Default true; set false to keep the raw import.'},
                'kicad_cli': {'type': 'string', 'description': 'Explicit kicad-cli path; autodetected when omitted (dsn needs one with native OrCAD import support).'},
                'add_board_only_parts': {'type': 'array', 'items': {'type': 'string'},
                                         'description': 'References that exist only on the board; each gets a placeholder symbol on an extra schematic page. edf input only.'},
                'footprint_choice': {'type': 'object', 'additionalProperties': {'type': 'string', 'enum': ['board', 'orcad']},
                                     'description': "Per reference: which footprint wins, 'board' or 'orcad'. edf input only."},
                'pin_type_overrides': {'type': 'object', 'additionalProperties': {'type': 'string'},
                                       'description': 'Pin electrical type overrides keyed "SYMBOL:PIN" (e.g. "LM317:3"), value is a KiCad pin type such as power_in or output. edf input only.'},
                'ignore_refs': {'type': 'array', 'items': {'type': 'string'},
                                'description': 'References to hide from the board/schematic difference report.'},
            },
            'required': ['outdir'],
        },
    },
    {
        'name': 'verify',
        'description': (
            'Read the EDIF and compare its nets with the reference PADS netlist without writing any '
            'file: [1] nets joined from EDIF connectivity and [2] nets derived from wire geometry. '
            'Fast (a few seconds) and side-effect free. Returns summary, verifications '
            '(edif/geometry: matched, mismatches, only_ours, only_ref, ok), issues, ok, exit_code and '
            'the human-readable report text.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'edf': {'type': 'string', 'description': _EDF_DESC},
                'netlist': {'type': 'string', 'description': 'Absolute path to the reference PADS2000 netlist (.asc).'},
            },
            'required': ['edf', 'netlist'],
        },
    },
    {
        'name': 'board_diff',
        'description': (
            'Compare a PCB against the schematic without writing a KiCad project: references only on '
            'the board, references only on the schematic, pins missing from footprints, extra pads, '
            'footprint differences, and a net comparison. The board may be a PADS Layout ASCII (.asc, '
            'imported into a temporary directory with kicad-cli) or a .kicad_pcb (read directly, no '
            'kicad-cli needed). Returns board_diff JSON plus the report text.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'edf': {'type': 'string', 'description': _EDF_DESC},
                'board': {'type': 'string', 'description': 'Absolute path to the PADS Layout ASCII (.asc) or .kicad_pcb board.'},
                'netlist': {'type': 'string', 'description': _NETLIST_DESC + ' Without it the EDIF-joined nets are used as the reference.'},
                'kicad_cli': {'type': 'string', 'description': 'Explicit kicad-cli path (only needed for .asc boards).'},
            },
            'required': ['edf', 'board'],
        },
    },
    {
        'name': 'list_issues',
        'description': (
            'List the issues collected by the most recent convert/verify/board_diff call in this '
            'session (unsupported EDIF constructs, writer warnings, board mapping notes). Returns '
            'count, issues, the tool that produced them and the last outdir. Empty before the first run.'),
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'read_file',
        'description': (
            'Read a text file produced by the conversion (e.g. the .kicad_sch, erc.json, '
            'kicad_netlist.txt). Restricted for safety: only inside the outdir of the last convert '
            'call or a root passed with --root/O2K_MCP_ROOT; ".." is rejected. A relative path is '
            'resolved against the first allowed root. Returns path, size, truncated and text '
            '(decoded as UTF-8, undecodable bytes replaced).'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'File path, absolute or relative to the allowed root.'},
                'max_bytes': {'type': 'integer', 'minimum': 1,
                              'description': f'Read at most this many bytes (default {READ_FILE_MAX_BYTES}).'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'find_kicad_cli',
        'description': (
            'Locate the kicad-cli executable (argument, then KICAD_CLI, then PATH, then the standard '
            'install locations, newest version first). Returns found (boolean) and path (null when '
            'missing). Without it verification [3], ERC, PDF export and .asc board import are skipped.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'kicad_cli': {'type': 'string', 'description': 'Candidate path to check first.'},
            },
        },
    },
    {
        'name': 'prepare_kicad_nightly',
        'description': (
            'Prepare a portable KiCad nightly kicad-cli WITHOUT INSTALLING anything: download the '
            'official nightly installer and extract it with 7-Zip or Bandizip, trim the parts '
            'kicad-cli does not need, then verify it runs. The installer itself is never executed. '
            'Windows only. If no archiver is installed it provisions the 7-Zip console build from '
            'www.7-zip.org the same way (download + extract only, never installed). Returns cli, '
            'version and orcad_import. '
            'Takes a few minutes on the first call (about 234 MB download, about 300 MB on disk); '
            'later calls return immediately unless force is true.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'root': {'type': 'string',
                         'description': 'Target directory (default: %LOCALAPPDATA%/orcad2kicad/kicad-nightly).'},
                'installer': {'type': 'string',
                              'description': 'Path to an already downloaded nightly installer (.exe); '
                                             'skips the download.'},
                'force': {'type': 'boolean',
                          'description': 'Re-extract even if it is already prepared (default false).'},
                'download_tools': {'type': 'boolean',
                                   'description': 'When no archiver is installed, download the 7-Zip '
                                                  'console build (about 6 MB, extracted only, never '
                                                  'installed). Default true; set false to fail instead.'},
            },
        },
    },
    {
        'name': 'suggest',
        'description': (
            'Run the optional AI agents (pin types, board/schematic net differences, review) over a '
            'pipeline result and return their suggestions - suggestions only, nothing is applied. The '
            'backend comes from the backend argument or the O2K_AGENT_BACKEND environment variable '
            "(none|api|claude-cli|codex-cli, default none); with 'none' the call returns an empty list "
            'and a note, so the deterministic pipeline never depends on it. Apply a suggestion by '
            'passing it back to convert as add_board_only_parts / footprint_choice / pin_type_overrides.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'edf': {'type': 'string', 'description': _EDF_DESC},
                'netlist': {'type': 'string', 'description': _NETLIST_DESC},
                'board': {'type': 'string', 'description': 'Optional board (.asc or .kicad_pcb); needed for board-related suggestions.'},
                'outdir': {'type': 'string', 'description': 'Optional output directory; when given the full conversion runs first so that board and ERC data are available.'},
                'backend': {'type': 'string', 'enum': ['none', 'api', 'claude-cli', 'codex-cli'],
                            'description': 'AI backend to use; defaults to O2K_AGENT_BACKEND or none.'},
                'agents': {'type': 'array', 'items': {'type': 'string', 'enum': ['pin_type', 'net_diff', 'review']},
                           'description': 'Which agents to run (default: all).'},
            },
            'required': ['edf'],
        },
    },
]

TOOL_NAMES = tuple(t['name'] for t in TOOLS)


# ---------- 인자 검증 헬퍼 ----------

def _arg_str(args, key, required=False, default=None):
    if key not in args or args[key] is None:
        if required:
            raise ToolError(f'missing required argument: {key}')
        return default
    val = args[key]
    if not isinstance(val, str) or not val.strip():
        raise ToolError(f'argument "{key}" must be a non-empty string')
    return val


def _arg_bool(args, key, default=False):
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if not isinstance(val, bool):
        raise ToolError(f'argument "{key}" must be a boolean')
    return val


def _arg_int(args, key, default=None, minimum=1):
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ToolError(f'argument "{key}" must be an integer')
    if val < minimum:
        raise ToolError(f'argument "{key}" must be >= {minimum}')
    return val


def _arg_str_list(args, key):
    if key not in args or args[key] is None:
        return []
    val = args[key]
    if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
        raise ToolError(f'argument "{key}" must be an array of strings')
    return [x for x in val if x.strip()]


def _arg_str_map(args, key):
    if key not in args or args[key] is None:
        return {}
    val = args[key]
    if not isinstance(val, dict) or any(not isinstance(v, str) for v in val.values()):
        raise ToolError(f'argument "{key}" must be an object with string values')
    return {str(k): v for k, v in val.items()}


def _arg_choice(args, key, choices, default=None):
    val = _arg_str(args, key, default=default)
    if val is not None and val not in choices:
        raise ToolError(f'argument "{key}" must be one of: {", ".join(choices)}')
    return val


def _is_under(path, root):
    """path 가 root 이거나 그 아래인가(대소문자 무시 — Windows)."""
    p, r = os.path.normcase(path), os.path.normcase(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


class Server:
    """MCP 서버 상태 + 메시지 처리. 전송(stdio)과 분리해 두어 테스트에서 직접 부를 수 있다."""

    def __init__(self, roots=None):
        env_roots = [r for r in (os.environ.get('O2K_MCP_ROOT') or '').split(os.pathsep) if r.strip()]
        self.roots = [os.path.abspath(r) for r in list(roots or []) + env_roots]
        self.last_outdir = None      # 마지막 convert 의 출력 디렉터리(read_file 허용 루트가 된다)
        self.last_issues = []
        self.last_tool = None
        self.initialized = False

    # ---------- 경로 제한 ----------

    def allowed_roots(self):
        roots = list(self.roots)
        if self.last_outdir:
            roots.append(self.last_outdir)
        out = []
        for r in roots:
            real = os.path.realpath(r)
            if real not in out:
                out.append(real)
        return out

    def resolve_read_path(self, raw):
        """read_file 의 경로를 검사해 실제 경로를 돌려준다. 위반이면 ToolError."""
        roots = self.allowed_roots()
        if not roots:
            raise ToolError('read_file: no allowed root yet; run convert first or start the server '
                            'with --root DIR (or set O2K_MCP_ROOT)')
        if '..' in raw.replace('\\', '/').split('/'):
            raise ToolError('read_file: ".." is not allowed in path')
        path = raw if os.path.isabs(raw) else os.path.join(roots[0], raw)
        real = os.path.realpath(path)
        if not any(_is_under(real, root) for root in roots):
            raise ToolError('read_file: path is outside the allowed roots: ' + _ascii(raw))
        if os.path.isdir(real):
            raise ToolError('read_file: path is a directory: ' + _ascii(raw))
        if not os.path.isfile(real):
            raise ToolError('read_file: not a file: ' + _ascii(raw))
        return real

    # ---------- 툴 ----------

    def call_tool(self, name, args):
        """툴 하나를 실행하고 JSON 직렬화 가능한 dict 를 돌려준다. 실패는 ToolError."""
        if name not in TOOL_NAMES:
            raise ToolError(f'unknown tool: {_ascii(name)} (available: {", ".join(TOOL_NAMES)})')
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ToolError('arguments must be an object')
        return getattr(self, '_tool_' + name)(args)

    def _remember(self, tool, issues, outdir=None):
        self.last_tool = tool
        self.last_issues = list(issues or [])
        if outdir:
            self.last_outdir = os.path.abspath(outdir)

    def _tool_convert(self, args):
        rez = Resolutions(
            add_board_only_parts=_arg_str_list(args, 'add_board_only_parts'),
            footprint_choice=_arg_str_map(args, 'footprint_choice'),
            pin_type_overrides=_parse_pin_type_overrides(_arg_str_map(args, 'pin_type_overrides')),
            ignore_refs=_arg_str_list(args, 'ignore_refs'))
        for ref, choice in rez.footprint_choice.items():
            if choice not in ('board', 'orcad'):
                raise ToolError(f'footprint_choice[{_ascii(ref)}] must be "board" or "orcad"')
        opts = PipelineOptions(
            edf=_arg_str(args, 'edf'),
            dsn=_arg_str(args, 'dsn'),
            kicad_project=_arg_str(args, 'kicad_project'),
            netlist=_arg_str(args, 'netlist'),
            board=_arg_str(args, 'board'),
            outdir=_arg_str(args, 'outdir', required=True),
            project=_arg_str(args, 'project'),
            kicad_cli=_arg_str(args, 'kicad_cli'),
            net_names=_arg_choice(args, 'net_names', ('kicad', 'keep')),
            pdf=_arg_bool(args, 'pdf'),
            strict_board=_arg_bool(args, 'strict_board'),
            import_cleanup=_arg_bool(args, 'import_cleanup', True),
            resolutions=rez)
        given = [n for n in ('edf', 'dsn', 'kicad_project') if getattr(opts, n)]
        if len(given) != 1:
            raise ToolError('exactly one of "edf", "dsn", "kicad_project" is required'
                            + (f' (got: {", ".join(given)})' if given else ' (got none)'))
        input_path = opts.edf or opts.dsn or opts.kicad_project
        log(f'convert: {os.path.basename(input_path)} -> {opts.outdir}')
        result = run_pipeline(opts, log=log)
        self._remember('convert', result.issues, opts.outdir)
        log(f'convert: done (exit_code={result.exit_code})')
        return result_to_json(result)

    def _tool_verify(self, args):
        opts = PipelineOptions(edf=_arg_str(args, 'edf', required=True),
                               netlist=_arg_str(args, 'netlist', required=True))
        log(f'verify: {os.path.basename(opts.edf)}')
        result = run_pipeline(opts)
        if result.error:
            raise ToolError(result.error)
        self._remember('verify', result.issues)
        checks = {k: _net_compare_to_json(v) for k, v in result.verifications.items()}
        return {'summary': summary(result.design),
                'verifications': checks,
                'ok': all(v.ok for v in result.verifications.values()),
                'exit_code': result.exit_code,
                'issues': list(result.issues),
                'text': '\n'.join(result.log)}

    def _tool_board_diff(self, args):
        from .edif_reader import load_edif
        from .pads_netlist import load_pads_netlist
        from .verify import edif_netlist
        from .kicad_board import compare_board, format_board_diff, load_board, import_pads_board
        edf = _arg_str(args, 'edf', required=True)
        board_path = _arg_str(args, 'board', required=True)
        netlist = _arg_str(args, 'netlist')
        cli_path = find_kicad_cli(_arg_str(args, 'kicad_cli'))
        log(f'board_diff: {os.path.basename(edf)} vs {os.path.basename(board_path)}')
        try:
            design = load_edif(edf)
            ref_nets = load_pads_netlist(netlist).nets if netlist else None
            if board_path.lower().endswith('.asc'):
                if not cli_path:
                    raise ToolError('board_diff: kicad-cli is required to import a PADS .asc board')
                # 임포트 결과는 비교에만 쓰므로 임시 디렉터리에 만들고 바로 버린다.
                with tempfile.TemporaryDirectory(prefix='o2k_mcp_board_') as tmp:
                    pcb = os.path.join(tmp, 'board.kicad_pcb')
                    import_pads_board(cli_path, board_path, pcb, os.path.join(tmp, 'report.json'))
                    board = load_board(pcb)
            else:
                board = load_board(board_path)
        except OSError as e:
            raise ToolError(f'board_diff: cannot read input: {e}')
        except RuntimeError as e:
            raise ToolError(f'board_diff: {e}')
        base_nets = ref_nets if ref_nets is not None else edif_netlist(design)
        diff = compare_board(board, design, base_nets)
        self._remember('board_diff', design.issues)
        return {'board': board_path,
                'reference': 'pads_netlist' if ref_nets is not None else 'edif',
                'board_diff': _board_diff_to_json(diff),
                'ok': bool(diff.net_compare.ok),
                'issues': list(design.issues),
                'text': format_board_diff(diff)}

    def _tool_list_issues(self, args):
        return {'count': len(self.last_issues),
                'issues': list(self.last_issues),
                'source': self.last_tool,
                'outdir': self.last_outdir}

    def _tool_read_file(self, args):
        raw = _arg_str(args, 'path', required=True)
        limit = _arg_int(args, 'max_bytes', default=READ_FILE_MAX_BYTES)
        real = self.resolve_read_path(raw)
        try:
            size = os.path.getsize(real)
            with open(real, 'rb') as f:
                data = f.read(limit)
        except OSError as e:
            raise ToolError(f'read_file: {e}')
        return {'path': real, 'size': size, 'truncated': size > len(data),
                'text': data.decode('utf-8', 'replace')}

    def _tool_find_kicad_cli(self, args):
        path = find_kicad_cli(_arg_str(args, 'kicad_cli'))
        return {'found': path is not None, 'path': path}

    def _tool_prepare_kicad_nightly(self, args):
        """설치 없이 나이틀리 kicad-cli 를 준비한다(다운로드 + 압축 해제만)."""
        from .kicad_portable import DEFAULT_ROOT, PortableError, ensure_portable_kicad
        root = _arg_str(args, 'root') or DEFAULT_ROOT
        installer = _arg_str(args, 'installer')
        try:
            cli = ensure_portable_kicad(root=root, force=_arg_bool(args, 'force'),
                                        log=log, installer=installer,
                                        download_tools=_arg_bool(args, 'download_tools', True))
        except PortableError as e:
            raise ToolError(f'prepare_kicad_nightly: {_ascii(e)}')
        from .kicad_netlist import cli_version, supports_orcad_import
        return {'cli': cli, 'root': os.path.abspath(root), 'version': cli_version(cli),
                'orcad_import': bool(supports_orcad_import(cli))}

    def _tool_suggest(self, args):
        from . import agents as agents_mod
        # 인자 > 환경변수 O2K_AGENT_BACKEND > 'none'. 환경변수가 이상하면 그 사실을 알려준다.
        kind = _arg_choice(args, 'backend', agents_mod.BACKEND_KINDS)
        if kind is None:
            kind = (os.environ.get('O2K_AGENT_BACKEND') or 'none').strip() or 'none'
            if kind not in agents_mod.BACKEND_KINDS:
                raise ToolError(f'O2K_AGENT_BACKEND must be one of: '
                                f'{", ".join(agents_mod.BACKEND_KINDS)} (got {_ascii(kind)})')
        enabled = tuple(_arg_str_list(args, 'agents')) or agents_mod.DEFAULT_AGENTS
        opts = PipelineOptions(edf=_arg_str(args, 'edf', required=True),
                               netlist=_arg_str(args, 'netlist'),
                               board=_arg_str(args, 'board'),
                               outdir=_arg_str(args, 'outdir'))
        result = run_pipeline(opts, log=log)
        if result.error:
            raise ToolError(result.error)
        self._remember('suggest', result.issues, opts.outdir)
        notes = []
        try:
            backend = agents_mod.make_backend(kind)
        except agents_mod.AgentError as e:
            raise ToolError(str(e))
        if backend is None:
            notes.append('no AI backend selected (set O2K_AGENT_BACKEND or pass backend); '
                         'returning no suggestions')
        suggestions = agents_mod.run_agents(result, backend, enabled=enabled, log=log)
        return {'backend': kind, 'agents': list(enabled), 'notes': notes,
                'suggestions': agents_mod.suggestions_to_json(suggestions),
                'exit_code': result.exit_code}

    # ---------- JSON-RPC ----------

    def handle(self, msg):
        """메시지 하나를 처리해 응답 dict 를 돌려준다. 알림(id 없음)이면 None."""
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST, 'Invalid Request')
        method = msg.get('method')
        has_id = 'id' in msg and msg['id'] is not None
        msg_id = msg.get('id')
        if not isinstance(method, str) or not method:
            return _error(msg_id, INVALID_REQUEST, 'Invalid Request') if has_id else None
        params = msg.get('params')
        if params is not None and not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, 'Invalid params') if has_id else None
        params = params or {}
        if not has_id:
            # 알림: 응답 금지. 아는 알림만 상태에 반영하고 나머지는 조용히 버린다.
            if method == 'notifications/initialized':
                self.initialized = True
            return None
        try:
            if method == 'initialize':
                return _ok(msg_id, self._initialize(params))
            if method == 'ping':
                return _ok(msg_id, {})
            if method == 'tools/list':
                return _ok(msg_id, {'tools': TOOLS})
            if method == 'tools/call':
                name = params.get('name')
                if not isinstance(name, str) or not name:
                    return _error(msg_id, INVALID_PARAMS, 'Invalid params: name is required')
                args = params.get('arguments')
                if args is not None and not isinstance(args, dict):
                    return _error(msg_id, INVALID_PARAMS, 'Invalid params: arguments must be an object')
                return _ok(msg_id, self._call(name, args or {}))
            return _error(msg_id, METHOD_NOT_FOUND, 'Method not found')
        except Exception as e:                       # 서버가 죽으면 세션 전체가 끊긴다
            log(f'internal error in {method}: {type(e).__name__}: {e}')
            return _error(msg_id, INTERNAL_ERROR, f'Internal error: {type(e).__name__}')

    def _initialize(self, params):
        want = params.get('protocolVersion')
        version = want if isinstance(want, str) and want in KNOWN_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        client = params.get('clientInfo')
        if isinstance(client, dict):
            log(f'initialize: client={client.get("name")} {client.get("version")} protocol={version}')
        return {'protocolVersion': version,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': SERVER_INFO_NAME, 'version': SERVER_VERSION}}

    def _call(self, name, args):
        """tools/call 결과. 툴 오류는 JSON-RPC 오류가 아니라 isError=True 로 돌려준다."""
        try:
            payload = self.call_tool(name, args)
        except ToolError as e:
            return _content(_ascii(e), is_error=True)
        except Exception as e:
            log(f'tool {name} failed: {type(e).__name__}: {e}')
            return _content(_ascii(f'{name} failed: {type(e).__name__}: {e}'), is_error=True)
        return _content(json.dumps(payload, ensure_ascii=True, sort_keys=False, default=str))


def _parse_pin_type_overrides(mapping):
    """{"SYMBOL:PIN": "type"} -> {(symbol, pin): type} (Resolutions 가 쓰는 형태)."""
    out = {}
    for key, val in mapping.items():
        if ':' not in key:
            raise ToolError(f'pin_type_overrides key must look like "SYMBOL:PIN": {_ascii(key)}')
        sym, num = key.rsplit(':', 1)
        if not sym.strip() or not num.strip():
            raise ToolError(f'pin_type_overrides key must look like "SYMBOL:PIN": {_ascii(key)}')
        out[(sym, num)] = val
    return out


def _ok(msg_id, result):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def _error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def _content(text, is_error=False):
    return {'content': [{'type': 'text', 'text': text}], 'isError': bool(is_error)}


def serve(server, stdin, stdout):
    """stdin 을 EOF 까지 한 줄씩 읽어 처리하고, 응답이 있으면 한 줄로 써서 즉시 flush 한다."""
    while True:
        line = stdin.readline()
        if not line:                       # EOF: 클라이언트가 stdin 을 닫았다 -> 정상 종료
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _error(None, PARSE_ERROR, 'Parse error')
        else:
            resp = server.handle(msg)
        if resp is None:
            continue
        stdout.write(json.dumps(resp, ensure_ascii=True, default=str) + '\n')
        stdout.flush()


USAGE = """usage: python -m orcad2kicad.mcp_server [--root DIR] [--selftest]
       orcad2kicad-cli.exe --mcp [--root DIR] [--selftest]   (single-exe build)

stdio MCP server for orcad2kicad (JSON-RPC 2.0, one message per line).
  --root DIR   allow read_file inside DIR (repeatable; also O2K_MCP_ROOT)
  --selftest   print the tool list as JSON and exit

register with Claude Code:
  claude mcp add orcad2kicad -e PYTHONPATH=<repo>/src -- python -m orcad2kicad.mcp_server
  claude mcp add orcad2kicad -- orcad2kicad-cli.exe --mcp
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    roots, selftest = [], False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--selftest':
            selftest = True
        elif arg == '--root':
            i += 1
            if i >= len(argv):
                sys.stderr.write('--root needs a directory\n')
                return 2
            roots.append(argv[i])
        elif arg.startswith('--root='):
            roots.append(arg.split('=', 1)[1])
        elif arg in ('-h', '--help'):
            sys.stdout.write(USAGE)
            return 0
        else:
            sys.stderr.write(f'unknown option: {_ascii(arg)}\n{USAGE}')
            return 2
        i += 1

    if selftest:
        sys.stdout.write(json.dumps({'serverInfo': {'name': SERVER_INFO_NAME, 'version': SERVER_VERSION},
                                     'protocolVersion': PROTOCOL_VERSION,
                                     'tools': TOOLS}, ensure_ascii=True, indent=2) + '\n')
        return 0

    # stdio 는 반드시 UTF-8 + LF. Windows 콘솔 기본 코드페이지로는 JSON 이 깨질 수 있다.
    for stream, extra in ((sys.stdin, {}), (sys.stdout, {'newline': '\n'}), (sys.stderr, {})):
        try:
            stream.reconfigure(encoding='utf-8', **extra)
        except (AttributeError, ValueError):        # 파이프가 바이너리로 감싸인 드문 경우
            pass
    server = Server(roots=roots)
    log(f'ready (stdio, version {SERVER_VERSION}, {len(TOOLS)} tools)')
    try:
        serve(server, sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass
    log('stdin closed, exiting')
    return 0


if __name__ == '__main__':
    sys.exit(main())
