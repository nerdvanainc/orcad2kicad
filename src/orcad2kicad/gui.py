"""tkinter GUI — 파일을 고르고 변환·검증을 돌린 뒤 결과를 확인하고 불일치를 해소한다.

실행: `python -m orcad2kicad` (또는 `python -m orcad2kicad.gui`).

구조 원칙
  * 변환 로직은 여기에 없다. `pipeline.run_pipeline` 하나만 부르고 결과를 표현한다
    (CLI/MCP 서버와 완전히 같은 경로).
  * 오래 걸리는 작업(파이프라인 실행, 에이전트 호출)은 `threading.Thread` 에서 돌리고,
    화면 갱신은 오직 Tk 스레드에서만 한다 — 워커는 `queue.Queue` 에 메시지를 넣고
    `after(100, ...)` 폴링(`drain_queue`)이 꺼내 반영한다.
  * 표에 들어가는 계산은 전부 비-tk 순수 함수(`diff_rows`, `resolutions_from_rows`,
    `verification_rows`)로 빼서 창 없이도 테스트할 수 있게 했다.
  * AI 는 제안만 한다. 적용은 사용자가 체크한 것만 `apply_suggestions` 로 옵션에 반영한 뒤
    파이프라인을 다시 돌리는 형태다. 백엔드가 없어도 전부 동작한다.

문자열 규칙: 사용자에게 보이는 GUI 문자열은 `i18n.t()` 로 조회한다(한국어/영어, 언어 선택은
입력 프레임 오른쪽 위 콤보박스). 파이프라인/에이전트가 만들어 로그 창에 흘러드는 진행 메시지는
(콘솔과 같은 내용이므로) 언어와 무관하게 ASCII 영문 그대로 둔다.

설정은 `~/.orcad2kicad/settings.json` 에 저장한다(환경변수 `O2K_SETTINGS` 로 경로 변경 가능).
**API 키는 저장하지 않는다** — 환경변수 `ANTHROPIC_API_KEY` 를 기본값으로 읽기만 한다.
"""
from __future__ import annotations
import copy
import json
import os
import re
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser

from .pipeline import PipelineOptions, Resolutions, run_pipeline
from .kicad_netlist import find_kicad_cli
from .branding import PRODUCT, VERSION, COMPANY, SITE_URL, SITE_SHORT, GITHUB_URL, about_text
from .i18n import t, get_language, detect_default_language, set_language as _set_language

# 언어 선택 콤보박스에 보일 두 선택지(이 표시 이름 자체는 언어와 무관 — 늘 두 언어 이름 그대로).
LANG_DISPLAY = {'ko': '한국어', 'en': 'English'}
LANG_DISPLAY_TO_CODE = {v: k for k, v in LANG_DISPLAY.items()}
LANG_CHOICES = ('auto', 'ko', 'en')     # 설정 파일의 'language' 값. 'auto' = 시작할 때마다 OS 로캘 감지


def lang_choice_label(choice):
    """콤보에 보여줄 언어 선택지 문구('auto' 는 현재 언어로 번역된다)."""
    return t('lang_auto') if choice == 'auto' else LANG_DISPLAY.get(choice, LANG_DISPLAY['ko'])


def lang_choice_from_label(label):
    if label == t('lang_auto'):
        return 'auto'
    return LANG_DISPLAY_TO_CODE.get(label, 'auto')

# ---------- 표 선택지 문자열(순수 함수와 GUI가 함께 쓴다) ----------
# 현재 언어에 따라 바뀌므로 함수다 — 모듈 임포트 시점 값을 고정해 버리면(평범한 상수) 언어를
# 나중에 바꿔도 이미 `from .gui import IGNORE` 로 들여온 이름은 옛 언어 문자열에 묶여 버린다.
def IGNORE(): return t('choice_ignore')
def ADD_TO_SCH(): return t('choice_add_to_sch')
def FP_BOARD(): return t('choice_fp_board')
def FP_ORCAD(): return t('choice_fp_orcad')
def HIDE(): return t('choice_hide')


LOG_FONT = ('Consolas', 10)
# 로그 줄 종류별 표시(색·굵기). 로그는 파이프라인의 콘솔 출력 그대로라 줄 첫머리/키워드로 종류를 나눈다.
LOG_TAGS = {
    'section': {'font': ('Consolas', 11, 'bold'), 'foreground': '#1a3d7c', 'background': '#e8eef8',
                'spacing1': 8, 'spacing3': 3},
    'step': {'font': ('Consolas', 10, 'bold'), 'foreground': '#1a3d7c'},
    'pass': {'font': ('Consolas', 10, 'bold'), 'foreground': '#1e7d32'},
    'fail': {'font': ('Consolas', 10, 'bold'), 'foreground': '#c62828'},
    'warn': {'foreground': '#b26a00'},
    'error': {'foreground': '#c62828', 'background': '#fdecea'},
    'muted': {'foreground': '#6b6b6b'},
    'conclusion': {'font': ('Consolas', 10, 'bold'), 'background': '#fff8dc', 'spacing1': 4},
}
_STEP_RE = re.compile(r'\[(\d|DIFF|REF ONLY|OURS ONLY)\]')

# 상태 문구(실행 버튼 옆) 단계별 색. 밋밋한 회색 한 줄이라 완료를 알아채기 어렵다는 지적에 따라
# 굵게 + 결과에 따라 색을 달리한다.
STATUS_COLORS = {
    'idle': '#606060', 'running': '#1a3d7c', 'pass': '#1e7d32', 'check': '#b26a00',
    'fail': '#c62828', 'error': '#c62828',
}


ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'orcad2kicad.ico')

# UI 글꼴: 한국어면 맑은 고딕, 그 외는 Segoe UI(둘 다 Windows 기본 탑재). 없는 PC 에서는 Tk 가
# 알아서 대체한다. 크기 10 — Tk 기본(9)보다 한 단계 크다.
UI_FONT_FAMILY = {'ko': 'Malgun Gothic', 'en': 'Segoe UI'}
UI_FONT_SIZE = 10
ACCENT = '#1a3d7c'


def log_line_tag(line):
    """로그 한 줄 -> `LOG_TAGS` 키(없으면 None). 우선순위: 오류 > 결론 > FAIL > PASS > 경고 > 단계 > 정보."""
    s = (line or '').rstrip()
    ls = s.lstrip()
    if not ls:
        return None
    low = ls.lower()
    if ls.startswith('==='):
        return 'section'
    if low.startswith(('error', 'traceback')) or '[fix]' in low:
        return 'error'
    if ls.startswith(('conclusion:', '결론:')):
        return 'conclusion'
    if re.search(r'\bFAIL\b', s):
        return 'fail'
    if re.search(r'\bPASS\b', s):
        return 'pass'
    if '[check]' in low or low.startswith('warning'):
        return 'warn'
    if _STEP_RE.match(ls):
        return 'step'
    if '[info' in low:
        return 'muted'
    return None


def KIND_LABELS():
    """보드 차이 종류 -> 표에 보일 이름."""
    return {
        'board_only_part': t('kind_board_only_part'),
        'sch_only_net': t('kind_sch_only_net'),
        'footprint_diff': t('kind_footprint_diff'),
        'missing_pins': t('kind_missing_pins'),
        'net_diff': t('kind_net_diff'),
    }


def VERIFY_TITLES():
    return {
        'edif': t('verify_title_edif'),
        'geometry': t('verify_title_geometry'),
        'kicad': t('verify_title_kicad'),
    }


VERIFY_ORDER = ('edif', 'geometry', 'kicad')     # 언어와 무관한 순서용 키


def BACKEND_LABELS():
    """AI 백엔드 표시 이름 <-> agents.make_backend 의 종류 문자열."""
    return {
        'none': t('backend_none'),
        'api': 'Anthropic API',
        'claude-cli': 'Claude Code CLI',
        'codex-cli': 'Codex CLI',
    }


def AGENT_LABELS():
    return (('pin_type', t('agent_pin_type')), ('net_diff', t('agent_net_diff')),
            ('review', t('agent_review')))


def FILE_LABELS():
    """결과 탭에 보일 파일 종류 이름."""
    return {'root_sch': t('file_root_sch'), 'lib': t('file_lib'), 'pro': t('file_pro'),
            'board': t('file_board'), 'pdf': 'PDF', 'erc_json': t('file_erc_json'),
            'netlist_txt': t('file_netlist_txt')}


# ---------- 순수 함수: 보드 차이 표 ----------

def diff_rows(board_diff, resolutions=None):
    """`kicad_board.BoardDiff` 를 표 행 목록으로 바꾼다.

    각 행은 {'kind', 'target', 'detail', 'choices', 'current'} 이고, `current` 는 지금 옵션
    (`resolutions`)에 이미 들어 있는 결정을 반영한다. 보드에만 있는 패드(`extra_pads`)는
    정보량이 많고 사용자가 결정할 것이 없어 표에는 넣지 않는다(로그 탭의 `[4]` 리포트에 있다).

    '회로도에 추가'로 이미 해소한 부품은 다음 실행의 `only_board_refs` 에서 빠진다(파이프라인이
    `compare_board(..., added_refs=...)` 로 제외하므로). 그래도 행은 남겨야 한다 — 행이 사라지면
    `resolutions_from_rows` 가 그 결정을 잃어버려 '적용 후 재출력'을 한 번 더 누르는 순간
    자리표시 페이지가 조용히 없어지고, 사용자가 결정을 되돌릴 방법도 없어진다.
    """
    if board_diff is None:
        return []
    ignore, add_to_sch, fp_board, fp_orcad, hide = IGNORE(), ADD_TO_SCH(), FP_BOARD(), FP_ORCAD(), HIDE()
    rez = resolutions or Resolutions()
    added = set(rez.add_board_only_parts or ())
    fp_choice = dict(rez.footprint_choice or {})
    hidden = set(rez.ignore_refs or ())
    rows = []
    only_board = list(board_diff.only_board_refs or ())
    for ref in only_board:
        rows.append({'kind': 'board_only_part', 'target': ref,
                     'detail': t('diff_detail_board_only_part'),
                     'choices': [ignore, add_to_sch],
                     'current': add_to_sch if ref in added else ignore})
    for ref in sorted(added - set(only_board)):
        rows.append({'kind': 'board_only_part', 'target': ref,
                     'detail': t('diff_detail_board_only_added'),
                     'choices': [ignore, add_to_sch],
                     'current': add_to_sch})
    for ref in board_diff.only_sch_refs or ():
        rows.append({'kind': 'sch_only_net', 'target': ref,
                     'detail': t('diff_detail_sch_only_net'),
                     'choices': [ignore, hide],
                     'current': hide if ref in hidden else ignore})
    for ref, sch_fp, board_fp in board_diff.footprint_diff or ():
        rows.append({'kind': 'footprint_diff', 'target': ref,
                     'detail': t('diff_detail_footprint_diff',
                                 sch_fp=sch_fp or t('none_value'),
                                 board_fp=board_fp or t('none_value')),
                     'choices': [fp_board, fp_orcad],
                     'current': fp_orcad if fp_choice.get(ref) == 'orcad' else fp_board})
    for ref, pins in sorted((board_diff.missing_pins or {}).items()):
        rows.append({'kind': 'missing_pins', 'target': ref,
                     'detail': t('diff_detail_missing_pins', pins=', '.join(sorted(pins))),
                     'choices': [ignore, hide],
                     'current': hide if ref in hidden else ignore})
    cmp = getattr(board_diff, 'net_compare', None)
    if cmp is not None:
        for d in cmp.mismatches:
            rows.append({'kind': 'net_diff', 'target': d.net,
                         'detail': t('diff_detail_net_mismatch',
                                     missing=sorted(d.missing), extra=sorted(d.extra)),
                         'choices': [ignore], 'current': ignore})
        for name in cmp.only_ours:
            rows.append({'kind': 'net_diff', 'target': name,
                         'detail': t('diff_detail_net_only_ours'),
                         'choices': [ignore], 'current': ignore})
        for name in cmp.only_ref:
            rows.append({'kind': 'net_diff', 'target': name,
                         'detail': t('diff_detail_net_only_ref'),
                         'choices': [ignore], 'current': ignore})
    return rows


def resolutions_from_rows(rows, base=None):
    """표에서 고른 처리 -> `Resolutions`. `base` 는 표로 표현되지 않는 결정(핀 타입 등)을
    이어받기 위한 것이며 절대 변형하지 않는다(깊은 복사)."""
    rez = copy.deepcopy(base) if base is not None else Resolutions()
    rez.add_board_only_parts = []
    rez.footprint_choice = dict(rez.footprint_choice or {})
    rez.ignore_refs = []
    add_to_sch, fp_orcad, hide = ADD_TO_SCH(), FP_ORCAD(), HIDE()
    for r in rows or ():
        kind, target, current = r.get('kind'), r.get('target', ''), r.get('current')
        if not target:
            continue
        if kind == 'board_only_part':
            if current == add_to_sch and target not in rez.add_board_only_parts:
                rez.add_board_only_parts.append(target)
        elif kind == 'footprint_diff':
            if current == fp_orcad:
                rez.footprint_choice[target] = 'orcad'
            else:
                rez.footprint_choice.pop(target, None)     # '보드'가 기본이라 항목을 넣지 않는다
        elif kind in ('sch_only_net', 'missing_pins'):
            if current == hide and target not in rez.ignore_refs:
                rez.ignore_refs.append(target)
    return rez


# ---------- 순수 함수: 검증 표 ----------

def verification_rows(cmp, ref_nets=None):
    """`verify.NetCompare` -> 표 행 목록 {'net','status','missing','extra'}.

    `NetCompare` 는 일치한 넷의 이름을 갖고 있지 않다(개수만). 그래서 기준 넷리스트
    (`PipelineResult.ref_nets`)를 함께 주면 일치 넷도 OK 행으로 모두 나열하고,
    주지 않으면 문제 있는 넷만 나열한다."""
    if cmp is None:
        return []
    diffs = {d.net: d for d in cmp.mismatches}
    only_ours, only_ref = set(cmp.only_ours), set(cmp.only_ref)

    def row(net, status, missing=(), extra=()):
        return {'net': net, 'status': status,
                'missing': ', '.join(sorted(missing)), 'extra': ', '.join(sorted(extra))}

    if ref_nets is None:
        rows = [row(d.net, 'DIFF', d.missing, d.extra) for d in cmp.mismatches]
        rows += [row(n, 'ONLY_OURS') for n in cmp.only_ours]
        rows += [row(n, 'ONLY_REF') for n in cmp.only_ref]
        return rows
    rows = []
    for net in sorted(set(ref_nets) | only_ours | set(diffs)):
        if net in diffs:
            d = diffs[net]
            rows.append(row(net, 'DIFF', d.missing, d.extra))
        elif net in only_ref:
            rows.append(row(net, 'ONLY_REF'))
        elif net in only_ours:
            rows.append(row(net, 'ONLY_OURS'))
        else:
            rows.append(row(net, 'OK'))
    return rows


def missing_edif_verify_note(input_mode):
    """`kicad-project`/`dsn` 입력 모드에서는 EDIF 가 없어 [1][2] 검증을 건너뛴다.

    검증 탭 맨 위에 보여줄 안내 한 줄(현재 언어) 또는 None(EDIF 입력 모드일 때)."""
    if input_mode in ('kicad-project', 'dsn'):
        return t('note_missing_edif')
    return None


def net_names_note(selected, effective):
    """라디오에서 고른 넷 이름 모드 vs 파이프라인이 실제로 쓴 모드.

    자리표시(보드 전용) 부품이 있으면 파이프라인이 'keep' 을 강제하므로 화면 선택과 결과가
    어긋날 수 있다. 어긋나면 로그에 남길 한 줄(ASCII)을, 같으면 None 을 돌려준다.
    'auto'(자동)는 파이프라인에 맡긴다는 뜻이므로 어긋남이 아니다."""
    if not selected or not effective or selected == 'auto' or selected == effective:
        return None
    return f'net names: pipeline used {effective} (selected {selected})'


def verification_summary(cmp):
    """검증 요약 한 줄(현재 언어)."""
    if cmp is None:
        return t('verify_no_result')
    return t('verify_summary', matched=cmp.matched, mismatches=len(cmp.mismatches),
             only_ours=len(cmp.only_ours), only_ref=len(cmp.only_ref),
             status=('PASS' if cmp.ok else 'FAIL'))


# ---------- 설정 저장/복원 ----------

SETTINGS_KEYS = ('edf', 'dsn', 'kicad_project', 'input_mode', 'netlist', 'board', 'outdir',
                 'project', 'kicad_cli', 'net_names', 'pdf', 'strict_board', 'backend', 'model',
                 'agents', 'language')
INPUT_MODES = ('edif', 'dsn', 'kicad_project')


def settings_path():
    """설정 파일 경로. 환경변수 `O2K_SETTINGS` 가 있으면 그 경로(테스트/이식용)."""
    env = os.environ.get('O2K_SETTINGS')
    if env:
        return env
    return os.path.join(os.path.expanduser('~'), '.orcad2kicad', 'settings.json')


def load_settings(path=None):
    """설정을 읽는다. 파일이 없거나 깨졌으면 빈 dict (GUI 는 어떤 경우에도 떠야 한다)."""
    path = path or settings_path()
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data, path=None):
    """설정을 저장한다. API 키 등 비밀은 절대 저장하지 않는다(허용 키만 남긴다).
    저장 실패(권한 등)는 조용히 무시한다 — 저장 못 한다고 GUI 가 멈출 이유는 없다."""
    path = path or settings_path()
    clean = {k: v for k, v in (data or {}).items() if k in SETTINGS_KEYS}
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError:
        return False
    return True


def open_path(path):
    """탐색기/연결 프로그램으로 열기. 윈도우가 아니면 open/xdg-open 을 시도한다.
    성공 여부를 bool 로 돌려준다(실패해도 예외를 올리지 않는다)."""
    if not path or not os.path.exists(path):
        return False
    try:
        if hasattr(os, 'startfile'):            # 윈도우
            os.startfile(path)                  # noqa: S606
            return True
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        subprocess.Popen([opener, path])
        return True
    except OSError:
        return False


# ---------- GUI ----------

def _import_tk():
    """tkinter 를 늦게 import 한다(순수 함수만 쓰는 테스트/환경에서 import 실패를 피한다)."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, font as tkfont
    return tk, ttk, filedialog, messagebox, tkfont


try:
    tk, ttk, filedialog, messagebox, tkfont = _import_tk()
    _TK_ERROR = None
except Exception as _e:                        # tkinter 미설치 환경
    tk = ttk = filedialog = messagebox = tkfont = None
    _TK_ERROR = _e


class App(tk.Tk if tk is not None else object):
    """메인 창. 위: 입력/AI 프레임 + 실행 버튼, 아래: 결과 탭(Notebook)."""

    def __init__(self):
        if tk is None:
            raise RuntimeError(f'tkinter not available: {_TK_ERROR}')
        super().__init__()

        self.queue = queue.Queue()
        self.result = None                 # 마지막 PipelineResult
        self.resolutions = Resolutions()   # 사용자가 고른 불일치 해소
        self.suggestions = []              # 마지막 에이전트 제안
        self.issues = []
        self.files = {}
        self.diff_data = []
        self.verify_trees = {}
        self.running = False

        settings = load_settings()
        # 언어: 사용자가 콤보에서 명시적으로 고른 값('ko'/'en')만 그대로 쓰고, 그 외('auto' 또는
        # 없음)는 시작할 때마다 OS 로캘을 다시 감지한다 — 감지 결과를 설정에 굳혀 두지 않는다.
        lang = settings.get('language')
        self.lang_choice = lang if lang in ('ko', 'en') else 'auto'
        _set_language(lang if lang in ('ko', 'en') else detect_default_language())

        self.geometry('1180x900')
        self.minsize(900, 700)
        self._apply_theme()
        self._set_window_icon()
        self._build_vars()
        self._build_input_frame()
        self._build_ai_frame()
        self._build_run_frame()
        self._build_notebook()

        self.apply_settings(settings)
        if not self.var_kicad_cli.get():
            self.var_kicad_cli.set(find_kicad_cli() or '')
        self.refresh_backend_status()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._poll_id = self.after(100, self._poll)

    def _apply_theme(self):
        """기본 Tk 모양(9pt 글꼴, 얇은 여백, 평평한 탭)을 손본다 — 글꼴 한 단계 키우고, 프레임
        제목·표 머리글은 굵게, 버튼·입력칸 여백을 넓히고, 실행 버튼은 강조 스타일을 쓴다.
        언어 전환(rebuild_ui)에서도 다시 불러 글꼴 가족을 언어에 맞춘다."""
        family = UI_FONT_FAMILY.get(get_language(), UI_FONT_FAMILY['en'])
        try:
            for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont',
                         'TkCaptionFont', 'TkTooltipFont'):
                f = tkfont.nametofont(name)
                f.configure(family=family, size=UI_FONT_SIZE)
            tkfont.nametofont('TkHeadingFont').configure(weight='bold')
            tkfont.nametofont('TkFixedFont').configure(family=LOG_FONT[0], size=LOG_FONT[1])
        except tk.TclError:
            pass
        style = ttk.Style(self)
        try:
            if 'vista' in style.theme_names():
                style.theme_use('vista')
        except tk.TclError:
            pass
        style.configure('.', font=(family, UI_FONT_SIZE))
        style.configure('TLabelframe.Label', font=(family, UI_FONT_SIZE, 'bold'), foreground=ACCENT)
        style.configure('TButton', padding=(10, 4))
        style.configure('Run.TButton', font=(family, UI_FONT_SIZE + 1, 'bold'), padding=(18, 6))
        style.configure('TEntry', padding=3)
        style.configure('TCombobox', padding=2)
        style.configure('Treeview', rowheight=UI_FONT_SIZE * 2 + 6, font=(family, UI_FONT_SIZE))
        style.configure('Treeview.Heading', font=(family, UI_FONT_SIZE, 'bold'))
        style.configure('TNotebook.Tab', padding=(16, 7), font=(family, UI_FONT_SIZE))
        style.configure('O2K.TNotebook.Tab', padding=(16, 7), font=(family, UI_FONT_SIZE))
        style.map('O2K.TNotebook.Tab', font=[('selected', (family, UI_FONT_SIZE, 'bold'))])
        style.configure('TCheckbutton', padding=(2, 2))
        style.configure('TRadiobutton', padding=(2, 2))

    def _set_window_icon(self):
        """창 제목줄/작업 표시줄 아이콘(패키지 안 `assets/orcad2kicad.ico`). 없거나 실패해도 조용히 넘어간다."""
        try:
            if os.path.isfile(ICON_PATH):
                self.iconbitmap(default=ICON_PATH)
        except tk.TclError:
            pass

    def set_language(self, lang):
        """언어를 바꾸고 창 전체를 새 언어로 다시 그린다(재시작 없이).

        위젯 개수가 많지 않은 도구용 GUI라 통째로 다시 그리는 쪽이 각 위젯의 텍스트를
        일일이 따라다니며 갱신하는 것보다 훨씬 덜 깨진다 — `rebuild_ui`가 현재 값/결과/
        제안을 그대로 이어받는다."""
        if lang not in ('ko', 'en'):
            return
        choice_changed = getattr(self, 'lang_choice', None) != lang
        self.lang_choice = lang                     # 명시적 선택 — 설정에 그대로 저장된다
        if lang == get_language():
            if choice_changed:
                save_settings(self.settings_dict())
            return
        _set_language(lang)
        save_settings(self.settings_dict())
        self.rebuild_ui()

    def set_language_choice(self, choice):
        """콤보 선택 처리. 'ko'/'en' 은 `set_language`, 'auto' 는 OS 로캘을 다시 감지해 적용하고
        설정에는 'auto' 를 남긴다(다음 시작 때도 다시 감지)."""
        if choice in ('ko', 'en'):
            self.set_language(choice)
            return
        self.lang_choice = 'auto'
        lang = detect_default_language()
        if lang == get_language():
            save_settings(self.settings_dict())
            self.var_language_label.set(lang_choice_label('auto'))
            return
        _set_language(lang)
        save_settings(self.settings_dict())
        self.rebuild_ui()

    def rebuild_ui(self):
        """현재 위젯 값·결과·제안을 보존한 채 창 내용을 전부 다시 만든다(언어 전환용)."""
        self._apply_theme()
        opts = self.build_options()
        backend_kind = self.var_backend_kind.get()
        model = self.var_model.get()
        api_key = self.var_api_key.get()
        enabled_agents = [n for n, _ in AGENT_LABELS() if self.var_agents[n].get()]
        issue_filter = self.var_issue_filter.get()
        for child in list(self.winfo_children()):
            child.destroy()
        self._build_vars()
        self._build_input_frame()
        self._build_ai_frame()
        self._build_run_frame()
        self._build_notebook()
        self.apply_options(opts)
        self.var_backend_kind.set(backend_kind)
        self.var_model.set(model)
        self.var_api_key.set(api_key)
        for name, _ in AGENT_LABELS():
            self.var_agents[name].set(name in enabled_agents)
        if not self.var_kicad_cli.get():
            self.var_kicad_cli.set(find_kicad_cli() or '')
        self.refresh_backend_status()
        if self.result is not None:
            self.on_result(self.result)
        self.var_issue_filter.set(issue_filter)
        self.refresh_issues()
        if self.suggestions:
            self._render_suggestions()

    # ----- 변수 -----
    def _build_vars(self):
        self.title(t('window_title', product=PRODUCT, version=VERSION, company=COMPANY))
        self.var_input_mode = tk.StringVar(value='edif')   # edif | dsn | kicad_project
        self.var_edf = tk.StringVar()
        self.var_dsn = tk.StringVar()
        self.var_kicad_project = tk.StringVar()
        for v in (self.var_edf, self.var_dsn, self.var_kicad_project):
            v.trace_add('write', lambda *_a, var=v: self._autofill_from_input(var))
        self.var_netlist = tk.StringVar()
        self.var_board = tk.StringVar()
        self.var_outdir = tk.StringVar()
        self.var_project = tk.StringVar()
        self.var_kicad_cli = tk.StringVar()
        self.var_net_names = tk.StringVar(value='auto')      # auto | keep | kicad
        self.var_pdf = tk.BooleanVar(value=False)
        self.var_strict = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value=t('status_idle'))
        self.var_issue_filter = tk.StringVar()
        self.var_diff_choice = tk.StringVar()
        self.var_backend_kind = tk.StringVar(value='none')
        self.var_backend_label = tk.StringVar(value=BACKEND_LABELS()['none'])
        self.var_backend_status = tk.StringVar(value='')
        self.var_api_key = tk.StringVar(value=os.environ.get('ANTHROPIC_API_KEY', ''))
        self.var_model = tk.StringVar()
        self.var_agents = {name: tk.BooleanVar(value=True) for name, _ in AGENT_LABELS()}
        self.var_backend_kind.trace_add('write', self._sync_backend_label)
        self.var_language_label = tk.StringVar(value=lang_choice_label(self.lang_choice))

    def _sync_backend_label(self, *_a):
        kind = self.var_backend_kind.get()
        self.var_backend_label.set(BACKEND_LABELS().get(kind, kind))
        self.refresh_backend_status()

    def _on_language_selected(self, _event=None):
        self.set_language_choice(lang_choice_from_label(self.var_language_label.get()))

    # ----- 입력 프레임 -----
    def _row_entry(self, parent, row, label, var, browse=None, width=78):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='e', padx=4, pady=2)
        ent = ttk.Entry(parent, textvariable=var, width=width)
        ent.grid(row=row, column=1, sticky='we', padx=4, pady=2)
        btn = None
        if browse is not None:
            btn = ttk.Button(parent, text=t('btn_browse'), width=8, command=browse)
            btn.grid(row=row, column=2, padx=4, pady=2)
        return ent, btn

    def _build_input_frame(self):
        f = ttk.LabelFrame(self, text=t('frame_input'))
        f.pack(fill='x', padx=8, pady=(8, 4))
        f.columnconfigure(1, weight=1)

        mode_row = ttk.Frame(f)
        mode_row.grid(row=0, column=0, columnspan=3, sticky='we', padx=4, pady=(4, 2))
        mode_row.columnconfigure(1, weight=1)
        mode_left = ttk.Frame(mode_row)
        mode_left.grid(row=0, column=0, sticky='w')
        ttk.Label(mode_left, text=t('label_input_mode')).pack(side='left')
        for value, text in (('edif', 'EDIF'),
                            ('dsn', t('input_mode_dsn')),
                            ('kicad_project', t('input_mode_kicad_project'))):
            ttk.Radiobutton(mode_left, text=text, value=value,
                            variable=self.var_input_mode).pack(side='left', padx=(4, 10))
        lang_row = ttk.Frame(mode_row)
        lang_row.grid(row=0, column=1, sticky='e')
        ttk.Label(lang_row, text=t('label_language') + ':').pack(side='left', padx=(0, 4))
        lang_combo = ttk.Combobox(lang_row, textvariable=self.var_language_label, state='readonly',
                                  width=20, values=[lang_choice_label(c) for c in LANG_CHOICES])
        lang_combo.pack(side='left')
        lang_combo.bind('<<ComboboxSelected>>', self._on_language_selected)
        self.lang_combo = lang_combo

        self.ent_edf, self.btn_edf = self._row_entry(
            f, 1, t('label_edf_file'), self.var_edf,
            lambda: self._pick_file(self.var_edf, [('EDIF', '*.EDF *.edf'), (t('filetype_all'), '*.*')]))
        self.ent_dsn, self.btn_dsn = self._row_entry(
            f, 2, t('label_dsn_file'), self.var_dsn,
            lambda: self._pick_file(self.var_dsn, [('OrCAD .DSN', '*.DSN *.dsn'), (t('filetype_all'), '*.*')]))
        self.ent_kicad_project, self.btn_kicad_project = self._row_entry(
            f, 3, t('label_kicad_project_file'), self.var_kicad_project,
            lambda: self._pick_file(self.var_kicad_project,
                                    [(t('filetype_kicad_project'), '*.kicad_pro'), (t('filetype_all'), '*.*')]))
        self._row_entry(f, 4, t('label_netlist_file'), self.var_netlist,
                        lambda: self._pick_file(self.var_netlist,
                                                [(t('filetype_netlist'), '*.asc'), (t('filetype_all'), '*.*')]))
        self._row_entry(f, 5, t('label_board_file'), self.var_board,
                        lambda: self._pick_file(self.var_board,
                                                [(t('filetype_board'), '*.asc *.kicad_pcb'), (t('filetype_all'), '*.*')]))
        self._row_entry(f, 6, t('label_outdir'), self.var_outdir, lambda: self._pick_dir(self.var_outdir))
        self._row_entry(f, 7, t('label_project'), self.var_project, None, width=30)
        self._row_entry(f, 8, t('label_kicad_cli'), self.var_kicad_cli,
                        lambda: self._pick_file(self.var_kicad_cli,
                                                [(t('filetype_exe'), '*.exe'), (t('filetype_all'), '*.*')]))
        nightly = ttk.Frame(f)
        nightly.grid(row=9, column=1, columnspan=2, sticky='w', padx=4, pady=(0, 4))
        self.btn_portable = ttk.Button(nightly, text=t('btn_portable_kicad'),
                                       command=self.start_portable_kicad)
        self.btn_portable.pack(side='left')
        ttk.Label(nightly, text=t('note_portable_kicad'),
                  foreground='#606060').pack(side='left', padx=(10, 0))

        opt = ttk.Frame(f)
        opt.grid(row=10, column=0, columnspan=3, sticky='w', padx=4, pady=(4, 4))
        ttk.Label(opt, text=t('label_net_names')).pack(side='left')
        for value, text in (('auto', t('net_names_auto')), ('keep', t('net_names_keep')),
                            ('kicad', t('net_names_kicad'))):
            ttk.Radiobutton(opt, text=text, value=value,
                            variable=self.var_net_names).pack(side='left', padx=(4, 8))
        ttk.Checkbutton(opt, text=t('chk_pdf'), variable=self.var_pdf).pack(side='left', padx=8)
        ttk.Checkbutton(opt, text=t('chk_strict'),
                        variable=self.var_strict).pack(side='left', padx=8)

        self.var_input_mode.trace_add('write', self._sync_input_mode)
        self._sync_input_mode()

    def _sync_input_mode(self, *_a):
        """라디오에서 고른 입력 종류에 맞는 파일 입력칸만 활성화한다."""
        mode = self.var_input_mode.get()
        for key, ent, btn in (('edif', self.ent_edf, self.btn_edf),
                              ('dsn', self.ent_dsn, self.btn_dsn),
                              ('kicad_project', self.ent_kicad_project, self.btn_kicad_project)):
            state = 'normal' if key == mode else 'disabled'
            ent.configure(state=state)
            if btn is not None:
                btn.configure(state=state)

    # ----- AI 프레임 -----
    def _build_ai_frame(self):
        f = ttk.LabelFrame(self, text=t('frame_ai'))
        f.pack(fill='x', padx=8, pady=4)
        f.columnconfigure(5, weight=1)
        ttk.Label(f, text=t('label_backend')).grid(row=0, column=0, sticky='e', padx=4, pady=2)
        combo = ttk.Combobox(f, textvariable=self.var_backend_label, state='readonly', width=22,
                             values=[BACKEND_LABELS()[k] for k in ('none', 'api', 'claude-cli', 'codex-cli')])
        combo.grid(row=0, column=1, sticky='w', padx=4, pady=2)
        combo.bind('<<ComboboxSelected>>', self._on_backend_selected)
        ttk.Label(f, text=t('label_api_key')).grid(row=0, column=2, sticky='e', padx=4)
        ttk.Entry(f, textvariable=self.var_api_key, show='*', width=28).grid(row=0, column=3, sticky='w', padx=4)
        ttk.Label(f, text=t('label_model')).grid(row=0, column=4, sticky='e', padx=4)
        ttk.Entry(f, textvariable=self.var_model, width=24).grid(row=0, column=5, sticky='w', padx=4)

        row2 = ttk.Frame(f)
        row2.grid(row=1, column=0, columnspan=6, sticky='we', padx=4, pady=(2, 4))
        ttk.Label(row2, text=t('label_agents')).pack(side='left')
        for name, text in AGENT_LABELS():
            ttk.Checkbutton(row2, text=text, variable=self.var_agents[name]).pack(side='left', padx=6)
        self.btn_suggest = ttk.Button(row2, text=t('btn_suggest'), command=self.run_agents_async)
        self.btn_suggest.pack(side='left', padx=12)
        ttk.Label(row2, textvariable=self.var_backend_status,
                  foreground='#606060').pack(side='left', padx=8)

    def _on_backend_selected(self, _event=None):
        label = self.var_backend_label.get()
        for kind, text in BACKEND_LABELS().items():
            if text == label:
                self.var_backend_kind.set(kind)
                break
        self.refresh_backend_status()

    def refresh_backend_status(self):
        """선택한 백엔드를 쓸 수 있는지 한 줄로 보여준다(이유 문자열 자체는 ASCII 영문 —
        예외 메시지·백엔드 실행 파일 경로 등을 그대로 옮기므로 번역하지 않는다)."""
        if not hasattr(self, 'var_backend_status'):
            return
        try:
            from .agents import backend_available
            ok, why = backend_available(self.var_backend_kind.get())
        except Exception as e:                       # 백엔드 조회 실패가 GUI 를 막지 않게
            ok, why = False, f'{type(e).__name__}: {e}'
        prefix = t('backend_available_prefix') if ok else t('backend_unavailable_prefix')
        self.var_backend_status.set(prefix + str(why))

    # ----- 실행 프레임 -----
    def _build_run_frame(self):
        f = ttk.Frame(self)
        f.pack(fill='x', padx=8, pady=4)
        self.btn_run = ttk.Button(f, text=t('btn_run'), command=self.start_run, style='Run.TButton')
        self.btn_run.pack(side='left')
        self.progress = ttk.Progressbar(f, mode='indeterminate', length=180)
        self.progress.pack(side='left', padx=10)
        self.lbl_status = tk.Label(f, textvariable=self.var_status,
                                   font=(UI_FONT_FAMILY.get(get_language(), 'Segoe UI'), UI_FONT_SIZE, 'bold'),
                                   foreground=STATUS_COLORS['idle'])
        self.lbl_status.pack(side='left', padx=6)
        ttk.Button(f, text=t('btn_about'), command=self.show_about).pack(side='right')
        site_label = ttk.Label(f, text=SITE_SHORT, foreground='blue', cursor='hand2')
        font = tkfont.Font(site_label, site_label.cget('font'))
        font.configure(underline=True)
        site_label.configure(font=font)
        site_label.pack(side='right', padx=(0, 10))
        site_label.bind('<Button-1>', lambda _e: webbrowser.open(SITE_URL))

    def show_about(self):
        """'정보'/'About' 버튼 — 제품/버전/회사/사이트/GitHub/라이선스 요약 + 열기 버튼."""
        top = tk.Toplevel(self)
        top.title(t('btn_about'))
        top.resizable(False, False)
        ttk.Label(top, text=about_text(get_language()), justify='left', padding=16).pack()
        btns = ttk.Frame(top, padding=(0, 0, 16, 16))
        btns.pack(anchor='e')
        ttk.Button(btns, text=t('btn_site'), command=lambda: webbrowser.open(SITE_URL)).pack(side='left', padx=4)
        ttk.Button(btns, text=t('btn_github'), command=lambda: webbrowser.open(GITHUB_URL)).pack(side='left', padx=4)
        ttk.Button(btns, text=t('btn_close'), command=top.destroy).pack(side='left', padx=4)

    # ----- 탭 -----
    TAB_KEYS = ('tab_log', 'tab_verify', 'tab_issues', 'tab_diff', 'tab_suggestions', 'tab_results')

    def _build_notebook(self):
        # 기본 ttk 탭은 작고 평평해서 어떤 탭이 있는지 눈에 잘 안 띈다 — 여백·글꼴을 키운다.
        style = ttk.Style(self)
        style.configure('O2K.TNotebook.Tab', padding=(16, 7), font=('Segoe UI', 10))
        nb = ttk.Notebook(self, style='O2K.TNotebook')
        nb.pack(fill='both', expand=True, padx=8, pady=(4, 8))
        self.notebook = nb
        self._build_log_tab(nb)
        self._build_verify_tab(nb)
        self._build_issue_tab(nb)
        self._build_diff_tab(nb)
        self._build_sugg_tab(nb)
        self._build_files_tab(nb)

    def _build_log_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t('tab_log'))
        self.log_text = tk.Text(f, wrap='word', height=10, font=LOG_FONT)
        for tag, cfg in LOG_TAGS.items():
            self.log_text.tag_configure(tag, **cfg)
        sb = ttk.Scrollbar(f, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_verify_tab(self, nb):
        self.verify_frame = ttk.Frame(nb)
        nb.add(self.verify_frame, text=t('tab_verify'))
        self.verify_placeholder = ttk.Label(
            self.verify_frame, text=t('verify_tab_placeholder'))
        self.verify_placeholder.pack(padx=10, pady=10, anchor='w')

    def _build_issue_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t('tab_issues'))
        top = ttk.Frame(f)
        top.pack(fill='x', padx=4, pady=4)
        ttk.Label(top, text=t('label_filter')).pack(side='left')
        ent = ttk.Entry(top, textvariable=self.var_issue_filter, width=40)
        ent.pack(side='left', padx=4)
        ent.bind('<KeyRelease>', lambda _e: self.refresh_issues())
        self.var_issue_count = tk.StringVar(value=t('issue_count', shown=0, total=0))
        ttk.Label(top, textvariable=self.var_issue_count).pack(side='left', padx=8)
        body = ttk.Frame(f)
        body.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self.issue_list = tk.Listbox(body)
        sb = ttk.Scrollbar(body, orient='vertical', command=self.issue_list.yview)
        self.issue_list.configure(yscrollcommand=sb.set)
        self.issue_list.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_diff_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t('tab_diff'))
        # 아래쪽(처리 콤보 줄 → 상세 창)을 먼저 bottom 으로 붙여 공간을 확보하고, 표가 나머지를 쓴다.
        # (표를 먼저 expand 로 붙이면 창이 낮을 때 아래 줄들이 잘려 안 보인다.)
        bar = ttk.Frame(f)
        bar.pack(side='bottom', fill='x', padx=4, pady=(0, 6))
        ttk.Label(bar, text=t('label_row_choice')).pack(side='left')
        self.diff_combo = ttk.Combobox(bar, textvariable=self.var_diff_choice,
                                       state='readonly', width=18, values=[IGNORE()])
        self.diff_combo.pack(side='left', padx=6)
        self.diff_combo.bind('<<ComboboxSelected>>', lambda _e: self.on_diff_choice())
        ttk.Button(bar, text=t('btn_apply_rerun'),
                   command=self.apply_resolutions_and_rerun).pack(side='left', padx=12)
        ttk.Label(bar, text=t('note_placeholder_page'),
                  foreground='#606060').pack(side='left', padx=6)
        # 상세 열은 넷 차이처럼 긴 내용이 잘려 보인다 — 선택한 행의 상세를 아래에 전부 펼쳐 보인다.
        detail_box = ttk.LabelFrame(f, text=t('label_diff_detail'))
        detail_box.pack(side='bottom', fill='x', padx=4, pady=(0, 4))
        self.diff_detail = tk.Text(detail_box, wrap='word', height=4, font=LOG_FONT,
                                   state='disabled', relief='flat', background='#f7f7f7')
        self.diff_detail.pack(fill='x', padx=4, pady=4)
        body = ttk.Frame(f)
        body.pack(side='top', fill='both', expand=True, padx=4, pady=4)
        cols = ('kind', 'target', 'detail', 'choice')
        self.diff_tree = ttk.Treeview(body, columns=cols, show='headings', selectmode='browse', height=8)
        for col, text, width in (('kind', t('col_kind'), 140), ('target', t('col_target'), 90),
                                 ('detail', t('col_detail'), 620), ('choice', t('col_choice'), 130)):
            self.diff_tree.heading(col, text=text)
            self.diff_tree.column(col, width=width, anchor='w')
        sb = ttk.Scrollbar(body, orient='vertical', command=self.diff_tree.yview)
        self.diff_tree.configure(yscrollcommand=sb.set)
        self.diff_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.diff_tree.bind('<<TreeviewSelect>>', lambda _e: self.on_diff_select())

    def _build_sugg_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t('tab_suggestions'))
        cols = ('sel', 'agent', 'kind', 'target', 'payload', 'reason', 'conf')
        self.sugg_tree = ttk.Treeview(f, columns=cols, show='headings', selectmode='browse',
                                      height=10)
        for col, text, width in (('sel', t('col_sel'), 46), ('agent', t('col_agent'), 90),
                                 ('kind', t('col_kind'), 130), ('target', t('col_target'), 150),
                                 ('payload', t('col_payload'), 260), ('reason', t('col_reason'), 330),
                                 ('conf', t('col_conf'), 50)):
            self.sugg_tree.heading(col, text=text)
            self.sugg_tree.column(col, width=width, anchor='w')
        self.sugg_tree.pack(fill='both', expand=True, padx=4, pady=4)
        self.sugg_tree.bind('<Button-1>', self._on_sugg_click)
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=4)
        ttk.Button(bar, text=t('btn_suggest'), command=self.run_agents_async).pack(side='left')
        ttk.Button(bar, text=t('btn_apply_selected'),
                   command=self.apply_selected_suggestions).pack(side='left', padx=8)
        ttk.Label(bar, text=t('note_suggestion_toggle'),
                  foreground='#606060').pack(side='left', padx=6)
        ttk.Label(f, text=t('agent_review')).pack(anchor='w', padx=6, pady=(6, 0))
        self.review_text = tk.Text(f, wrap='word', height=8)
        self.review_text.pack(fill='both', expand=False, padx=4, pady=(0, 6))

    def _build_files_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t('tab_results'))
        # [1][2] 가 없는 입력 모드(kicad-project/dsn)에서는 검증 탭과 같은 안내를 여기에도 둔다
        # (결과 파일만 보는 사용자가 "검증 파일이 왜 없지?" 로 헤매지 않도록).
        self.files_note = ttk.Label(f, text='', foreground='#606060')
        self.files_note.pack(padx=10, pady=(8, 0), anchor='w')
        ttk.Label(f, text=t('label_explain_summary')).pack(anchor='w', padx=6, pady=(6, 0))
        self.explain_text = tk.Text(f, wrap='word', height=12)
        self.explain_text.pack(fill='x', expand=False, padx=4, pady=(0, 6))
        self.file_tree = ttk.Treeview(f, columns=('kind', 'path'), show='headings')
        self.file_tree.heading('kind', text=t('col_kind'))
        self.file_tree.heading('path', text=t('col_path'))
        self.file_tree.column('kind', width=160)
        self.file_tree.column('path', width=860)
        self.file_tree.pack(fill='both', expand=True, padx=4, pady=4)
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=4, pady=(0, 6))
        ttk.Button(bar, text=t('btn_open_outdir'), command=self.open_outdir).pack(side='left')
        ttk.Button(bar, text=t('btn_open_project'), command=self.open_project).pack(side='left', padx=8)

    def _autofill_from_input(self, var):
        """입력 파일(EDIF/.DSN/.kicad_pro) 칸이 실제 파일을 가리키게 되면 **비어 있는** 칸만
        제안값으로 채운다: 출력 폴더(`<이름>_kicad`), 프로젝트 이름, 같은 폴더의 PADS 넷리스트/
        보드 `.asc`(머리글로 판별, 이름이 맞는 것만). 이미 적힌 값은 건드리지 않는다."""
        path = var.get().strip()
        if not path or not os.path.isfile(path):
            return
        try:
            from .pipeline import suggest_inputs
            sug = suggest_inputs(path)
        except Exception:
            return
        for target, key in ((self.var_outdir, 'outdir'), (self.var_project, 'project'),
                            (self.var_netlist, 'netlist'), (self.var_board, 'board')):
            if not target.get().strip() and sug.get(key):
                target.set(os.path.normpath(sug[key]))

    # ----- 파일 선택 -----
    def _pick_file(self, var, filetypes):
        path = filedialog.askopenfilename(filetypes=filetypes, initialdir=self._initial_dir(var))
        if path:
            var.set(os.path.normpath(path))

    def _pick_dir(self, var):
        path = filedialog.askdirectory(initialdir=self._initial_dir(var))
        if path:
            var.set(os.path.normpath(path))

    def _initial_dir(self, var):
        cur = var.get().strip()
        if cur:
            d = cur if os.path.isdir(cur) else os.path.dirname(cur)
            if os.path.isdir(d):
                return d
        edf = self.var_edf.get().strip()
        if edf and os.path.isdir(os.path.dirname(edf)):
            return os.path.dirname(edf)
        return os.getcwd()

    # ----- 옵션 <-> 위젯 -----
    def build_options(self) -> PipelineOptions:
        """현재 위젯 값으로 `PipelineOptions` 를 만든다(빈 칸은 None).

        입력 종류 라디오에 따라 `edf`/`dsn`/`kicad_project` 중 선택된 것 하나만 채우고
        나머지 둘은 None 이다(파이프라인이 셋 중 정확히 하나만 허용한다)."""
        def s(var):
            v = var.get().strip()
            return v or None
        mode = self.var_net_names.get()
        input_mode = self.var_input_mode.get()
        return PipelineOptions(edf=s(self.var_edf) if input_mode == 'edif' else None,
                               dsn=s(self.var_dsn) if input_mode == 'dsn' else None,
                               kicad_project=(s(self.var_kicad_project)
                                             if input_mode == 'kicad_project' else None),
                               netlist=s(self.var_netlist),
                               board=s(self.var_board), outdir=s(self.var_outdir),
                               project=s(self.var_project), kicad_cli=s(self.var_kicad_cli),
                               net_names=(None if mode == 'auto' else mode),
                               pdf=bool(self.var_pdf.get()),
                               strict_board=bool(self.var_strict.get()),
                               resolutions=copy.deepcopy(self.resolutions))

    def apply_options(self, opts: PipelineOptions):
        """`PipelineOptions` 를 위젯에 되돌린다(`build_options` 의 역).

        입력 종류는 `dsn`/`kicad_project`/`edf` 중 채워진 필드로 판단한다(우선순위는
        파이프라인의 `input_mode()`와 같다)."""
        if opts.dsn:
            self.var_input_mode.set('dsn')
        elif opts.kicad_project:
            self.var_input_mode.set('kicad_project')
        else:
            self.var_input_mode.set('edif')
        self.var_edf.set(opts.edf or '')
        self.var_dsn.set(opts.dsn or '')
        self.var_kicad_project.set(opts.kicad_project or '')
        self.var_netlist.set(opts.netlist or '')
        self.var_board.set(opts.board or '')
        self.var_outdir.set(opts.outdir or '')
        self.var_project.set(opts.project or '')
        self.var_kicad_cli.set(opts.kicad_cli or '')
        self.var_net_names.set(opts.net_names or 'auto')
        self.var_pdf.set(bool(opts.pdf))
        self.var_strict.set(bool(opts.strict_board))
        self.resolutions = copy.deepcopy(opts.resolutions or Resolutions())

    def settings_dict(self):
        """저장할 설정(dict). **API 키는 포함하지 않는다.**"""
        return {'edf': self.var_edf.get(), 'dsn': self.var_dsn.get(),
                'kicad_project': self.var_kicad_project.get(),
                'input_mode': self.var_input_mode.get(),
                'netlist': self.var_netlist.get(),
                'board': self.var_board.get(), 'outdir': self.var_outdir.get(),
                'project': self.var_project.get(), 'kicad_cli': self.var_kicad_cli.get(),
                'net_names': self.var_net_names.get(), 'pdf': bool(self.var_pdf.get()),
                'strict_board': bool(self.var_strict.get()),
                'backend': self.var_backend_kind.get(), 'model': self.var_model.get(),
                'agents': [n for n, _ in AGENT_LABELS() if self.var_agents[n].get()],
                'language': self.lang_choice}

    def apply_settings(self, data):
        """저장된 설정을 위젯에 반영한다(없는 키는 그대로 둔다)."""
        data = data or {}
        for key, var in (('edf', self.var_edf), ('dsn', self.var_dsn),
                         ('kicad_project', self.var_kicad_project),
                         ('netlist', self.var_netlist),
                         ('board', self.var_board), ('outdir', self.var_outdir),
                         ('project', self.var_project), ('kicad_cli', self.var_kicad_cli),
                         ('net_names', self.var_net_names), ('model', self.var_model)):
            if isinstance(data.get(key), str):
                var.set(data[key])
        for key, var in (('pdf', self.var_pdf), ('strict_board', self.var_strict)):
            if key in data:
                var.set(bool(data[key]))
        if data.get('input_mode') in INPUT_MODES:
            self.var_input_mode.set(data['input_mode'])
        kind = data.get('backend')
        if kind in BACKEND_LABELS():
            self.var_backend_kind.set(kind)
        if isinstance(data.get('agents'), list):
            for name, _ in AGENT_LABELS():
                self.var_agents[name].set(name in data['agents'])
        if data.get('language') in ('ko', 'en'):
            self.set_language(data['language'])       # 이미 같은 언어면 조용히 무시(rebuild 없음)
        elif data.get('language') == 'auto':
            self.lang_choice = 'auto'

    # ----- 실행(스레드) -----
    def _log(self, line):
        """워커 스레드에서 부르는 로그 콜백 — 큐에만 넣는다(위젯 접근 금지)."""
        self.queue.put(('log', line))

    def run_pipeline_sync(self, opts=None):
        """파이프라인을 동기로 실행한다(워커 스레드와 테스트가 함께 쓰는 진입점).

        `opts` 를 주지 않으면 위젯에서 만든다 — 그 경우 반드시 Tk 스레드에서 불러야 한다."""
        if opts is None:
            opts = self.build_options()
        return run_pipeline(opts, log=self._log)

    def start_run(self):
        """'변환 실행' — 옵션을 Tk 스레드에서 만들고 워커 스레드에 넘긴다."""
        if self.running:
            return
        opts = self.build_options()
        mode = self.var_input_mode.get()
        path, label = {'edif': (opts.edf, t('label_edf_file')),
                      'dsn': (opts.dsn, t('label_dsn_file')),
                      'kicad_project': (opts.kicad_project, t('label_kicad_project_file'))}.get(
                          mode, (None, t('label_input_file_generic')))
        if not path:
            messagebox.showwarning(t('dlg_input_required_title'), t('dlg_input_required_msg', label=label))
            return
        if not os.path.isfile(path):
            messagebox.showerror(t('dlg_input_error_title'), t('dlg_input_error_msg', label=label, path=path))
            return
        save_settings(self.settings_dict())
        self.clear_log()
        self._set_running(True, t('status_running'))
        threading.Thread(target=self._worker_run, args=(opts,), daemon=True).start()

    def _worker_run(self, opts):
        try:
            result = self.run_pipeline_sync(opts)
            self.queue.put(('result', result))
        except Exception:                      # 어떤 예외도 mainloop 를 죽이지 않는다
            self.queue.put(('error', traceback.format_exc()))
        finally:
            self.queue.put(('done', None))

    def start_portable_kicad(self):
        """'나이틀리 KiCad 포터블 준비' — 설치 없이 나이틀리 kicad-cli 만 확보한다.

        설치 프로그램은 실행하지 않는다. 공식 나이틀리 설치본을 내려받아 압축만 풀고, 성공하면
        kicad-cli 경로 칸을 채운 뒤 설정에 저장한다. 이 PC 에 7-Zip/Bandizip 이 없으면 7-Zip
        콘솔판(약 6 MB)도 공식 사이트에서 받아 역시 풀기만 해서 쓴다(그것도 설치하지 않는다)."""
        if self.running:
            return
        from .kicad_portable import DEFAULT_ROOT, find_extractor
        extra = '' if find_extractor() else t('note_no_extractor')
        if not messagebox.askokcancel(
                t('dlg_portable_title'),
                t('dlg_portable_confirm', extra=extra, root=DEFAULT_ROOT)):
            return
        self.clear_log()
        self._set_running(True, t('status_portable_running'))
        threading.Thread(target=self._worker_portable, args=(None,), daemon=True).start()

    def _worker_portable(self, root=None):
        try:
            from .kicad_portable import DEFAULT_ROOT, ensure_portable_kicad
            cli = ensure_portable_kicad(root=root or DEFAULT_ROOT, log=self._log)
            self.queue.put(('portable', cli))
        except Exception:                      # 어떤 예외도 mainloop 를 죽이지 않는다
            self.queue.put(('error', traceback.format_exc()))
        finally:
            self.queue.put(('done', None))

    def on_portable_ready(self, cli):
        """포터블 준비 성공 — kicad-cli 경로 칸을 채우고 설정에 저장한다(Tk 스레드 전용)."""
        if not cli:
            return
        self.var_kicad_cli.set(cli)
        save_settings(self.settings_dict())
        self.append_log(f'kicad-cli: {cli}')
        self.set_status(t('status_portable_done'), 'pass')

    def run_agents_async(self):
        """'제안 생성' — 마지막 결과를 입력으로 에이전트를 워커 스레드에서 돌린다."""
        if self.running:
            return
        if self.result is None:
            messagebox.showinfo(t('dlg_need_run_title'), t('dlg_need_run_msg'))
            return
        kind = self.var_backend_kind.get()
        if kind in (None, '', 'none'):
            messagebox.showinfo(t('dlg_no_backend_title'), t('dlg_no_backend_msg'))
            return
        enabled = tuple(n for n, _ in AGENT_LABELS() if self.var_agents[n].get())
        if not enabled:
            messagebox.showinfo(t('dlg_no_agent_title'), t('dlg_no_agent_msg'))
            return
        api_key = self.var_api_key.get().strip() or None
        model = self.var_model.get().strip() or None
        result = self.result
        self._set_running(True, t('status_suggest_running'))
        threading.Thread(target=self._worker_agents,
                         args=(result, kind, api_key, model, enabled), daemon=True).start()

    def _worker_agents(self, result, kind, api_key, model, enabled):
        try:
            from .agents import make_backend, run_agents
            backend = make_backend(kind, api_key=api_key, model=model)
            sugg = run_agents(result, backend, enabled=enabled, log=self._log)
            self.queue.put(('suggestions', sugg))
        except Exception:
            self.queue.put(('error', traceback.format_exc()))
        finally:
            self.queue.put(('done', None))

    def _set_running(self, running, status=''):
        self.running = running
        state = 'disabled' if running else 'normal'
        for btn in (self.btn_run, self.btn_suggest, getattr(self, 'btn_portable', None)):
            if btn is not None:
                btn.configure(state=state)
        if running:
            self.progress.start(60)
        else:
            self.progress.stop()
        if status:
            self.set_status(status, 'running' if running else 'idle')

    # ----- 큐 폴링 -----
    def _poll(self):
        self.drain_queue()
        self._poll_id = self.after(100, self._poll)

    def drain_queue(self):
        """큐에 쌓인 워커 메시지를 전부 반영한다(Tk 스레드 전용, 테스트에서도 직접 부른다)."""
        while True:
            try:
                what, payload = self.queue.get_nowait()
            except queue.Empty:
                return
            try:
                if what == 'log':
                    self.append_log(payload)
                elif what == 'result':
                    self.on_result(payload)
                elif what == 'suggestions':
                    self.set_suggestions(payload)
                elif what == 'portable':
                    self.on_portable_ready(payload)
                elif what == 'error':
                    self.on_error(payload)
                elif what == 'done':
                    self._set_running(False)
            except Exception:                  # 표현 단계 오류도 mainloop 를 죽이지 않는다
                self.append_log('gui error: ' + traceback.format_exc().splitlines()[-1])

    # ----- 결과 반영 -----
    def clear_log(self):
        self.log_text.delete('1.0', 'end')

    def append_log(self, line):
        tag = log_line_tag(line)
        self.log_text.insert('end', (line or '') + '\n', tag if tag else ())
        self.log_text.see('end')

    def _set_tab_count(self, key, n):
        """탭 제목에 항목 수를 붙인다('이슈 (3)') — 내용이 있는 탭이 한눈에 보이게."""
        try:
            idx = self.TAB_KEYS.index(key)
            self.notebook.tab(idx, text=t(key) + (f' ({n})' if n else ''))
        except (ValueError, tk.TclError, AttributeError):
            pass

    def set_status(self, text, level='idle'):
        """상태 문구 + 단계별 색('idle'|'running'|'pass'|'check'|'fail'|'error')."""
        self.var_status.set(text)
        lbl = getattr(self, 'lbl_status', None)
        if lbl is not None:
            lbl.configure(foreground=STATUS_COLORS.get(level, STATUS_COLORS['idle']))

    def on_error(self, text):
        self.append_log('error: ' + (text or '').strip().splitlines()[-1])
        self.set_status(t('dlg_error_title'), 'error')
        messagebox.showerror(t('dlg_error_title'), text)

    def on_result(self, result):
        """파이프라인 결과를 각 탭에 채운다(Tk 스레드에서만 호출)."""
        self.result = result
        self.set_verifications(result)
        self.issues = list(result.issues or ())
        self.refresh_issues()
        self.set_diff_rows(diff_rows(result.board_diff, self.resolutions))
        self.set_files(result.files, getattr(result, 'input_mode', 'edif'))
        self.sync_net_names(result)
        codes = {0: t('status_done_pass'), 1: t('status_done_fail'), 2: t('status_done_error')}
        levels = {0: 'pass', 1: 'fail', 2: 'error'}
        status = codes.get(result.exit_code, t('status_done_exit', code=result.exit_code))
        level = levels.get(result.exit_code, 'check')
        if result.exit_code == 0:
            # exit 0 이라도 [4] 넷 차이·회로도 전용 부품·ERC check 가 있으면 "모든 검증 PASS" 는 거짓말이다.
            try:
                from .explain import attention_items
                if attention_items(result, get_language()):
                    status, level = t('status_done_check'), 'check'
            except Exception:                        # 설명 생성 실패는 상태 표시를 막지 않는다
                pass
        self.set_status(status, level)
        self.set_explanation(result)
        if result.error:
            self.append_log(f'error: {result.error}')
            messagebox.showerror(t('dlg_error_title'), f'{result.error}')

    def set_explanation(self, result):
        """결과 탭의 요약 설명(현재 언어)을 채우고 로그 끝에도 같은 내용을 붙인다."""
        try:
            from .explain import format_explanation
            text = format_explanation(result, get_language())
        except Exception as e:                       # 설명 생성이 실패해도 결과 표시는 계속
            text = t('explain_failed', error=e)
        if hasattr(self, 'explain_text'):
            self.explain_text.delete('1.0', 'end')
            self.explain_text.insert('1.0', text)
        self.append_log('')
        for line in text.splitlines():
            self.append_log(line)

    def sync_net_names(self, result):
        """파이프라인이 실제로 쓴 넷 이름 모드를 라디오에 되돌리고 로그에 남긴다.

        (보드 전용 자리표시 부품이 있으면 'keep' 이 강제된다 — 화면에는 'KiCad 방식'이 켜져
        있는데 결과는 keep 인 상태를 그대로 두면 다음 실행 옵션이 사실과 달라진다.)"""
        note = net_names_note(self.var_net_names.get(), getattr(result, 'net_names', None))
        if note:
            self.var_net_names.set(result.net_names)
            self.append_log(note)
        return note

    def set_verifications(self, result):
        """검증 탭: 있는 검증마다 요약 줄 + 넷 표를 만든다.

        `kicad-project`/`dsn` 입력 모드에는 EDIF 가 없어 [1][2] 검증이 없다 — 그 사실을
        맨 위에 안내 한 줄로 표시한다(`missing_edif_verify_note`)."""
        for child in self.verify_frame.winfo_children():
            child.destroy()
        self.verify_trees = {}
        note = missing_edif_verify_note(getattr(result, 'input_mode', 'edif'))
        if note:
            ttk.Label(self.verify_frame, text=note, foreground='#606060').pack(
                padx=10, pady=(10, 0), anchor='w')
        present = [k for k in VERIFY_ORDER if k in (result.verifications or {})]
        if not present:
            ttk.Label(self.verify_frame, text=t('verify_no_result_hint')
                      ).pack(padx=10, pady=10, anchor='w')
            return
        titles = VERIFY_TITLES()
        for key in present:
            cmp = result.verifications[key]
            box = ttk.LabelFrame(self.verify_frame, text=titles.get(key, key))
            box.pack(fill='both', expand=True, padx=4, pady=3)
            ttk.Label(box, text=verification_summary(cmp)).pack(anchor='w', padx=6, pady=(2, 0))
            # 검증이 셋 다 있으면 한 화면에 들어가도록 표 높이를 줄인다(창을 키우면 같이 늘어난다).
            tree = ttk.Treeview(box, columns=('net', 'status', 'missing', 'extra'),
                                show='headings', height=4 if len(present) >= 3 else 8)
            for col, text, width in (('net', t('col_net'), 240), ('status', t('col_status'), 90),
                                     ('missing', t('col_missing'), 380),
                                     ('extra', t('col_extra'), 380)):
                tree.heading(col, text=text)
                tree.column(col, width=width, anchor='w')
            sb = ttk.Scrollbar(box, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side='left', fill='both', expand=True, padx=(4, 0), pady=4)
            sb.pack(side='right', fill='y', pady=4)
            for r in verification_rows(cmp, result.ref_nets):
                tree.insert('', 'end', values=(r['net'], r['status'], r['missing'], r['extra']))
            self.verify_trees[key] = tree

    def refresh_issues(self):
        """이슈 탭: 필터(부분 문자열, 대소문자 무시)에 맞는 것만 보여준다."""
        pat = self.var_issue_filter.get().strip().lower()
        self.issue_list.delete(0, 'end')
        shown = 0
        for i in self.issues:
            text = str(i)
            if pat and pat not in text.lower():
                continue
            self.issue_list.insert('end', text)
            shown += 1
        self.var_issue_count.set(t('issue_count', shown=shown, total=len(self.issues)))
        self._set_tab_count('tab_issues', len(self.issues))

    # ----- 보드 차이 -----
    def set_diff_rows(self, rows):
        self.diff_data = list(rows or ())
        kind_labels = KIND_LABELS()
        self.diff_tree.delete(*self.diff_tree.get_children())
        for r in self.diff_data:
            self.diff_tree.insert('', 'end', values=(kind_labels.get(r['kind'], r['kind']),
                                                     r['target'], r['detail'], r['current']))
        self.var_diff_choice.set('')
        self.diff_combo.configure(values=[IGNORE()])
        self._set_diff_detail('')
        self._set_tab_count('tab_diff', len(self.diff_data))

    def _selected_diff_index(self):
        sel = self.diff_tree.selection()
        if not sel:
            return None
        items = self.diff_tree.get_children()
        try:
            return items.index(sel[0])
        except ValueError:
            return None

    def on_diff_select(self):
        idx = self._selected_diff_index()
        if idx is None or idx >= len(self.diff_data):
            return
        row = self.diff_data[idx]
        self.diff_combo.configure(values=list(row['choices']))
        self.var_diff_choice.set(row['current'])
        self._set_diff_detail(f"{row['target']}: {row['detail']}")

    def _set_diff_detail(self, text):
        box = getattr(self, 'diff_detail', None)
        if box is None:
            return
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', text or '')
        box.configure(state='disabled')

    def on_diff_choice(self):
        idx = self._selected_diff_index()
        if idx is None or idx >= len(self.diff_data):
            return
        row = self.diff_data[idx]
        choice = self.var_diff_choice.get()
        if choice not in row['choices']:
            return
        row['current'] = choice
        self.diff_tree.set(self.diff_tree.get_children()[idx], 'choice', choice)

    def current_resolutions(self):
        """표에서 고른 처리 + 기존 핀 타입 오버라이드."""
        return resolutions_from_rows(self.diff_data, base=self.resolutions)

    def apply_resolutions_and_rerun(self):
        self.resolutions = self.current_resolutions()
        self.start_run()

    # ----- 에이전트 제안 -----
    def set_suggestions(self, suggestions):
        """새 제안 목록을 받는다. 핀 타입/note 는 기본 미선택(사용자가 직접 고른다)."""
        self.suggestions = list(suggestions or ())
        for s in self.suggestions:
            s.selected = False if s.kind in ('pin_type', 'note') else True
        self._render_suggestions()
        self._set_tab_count('tab_suggestions', len(self.suggestions))
        if not self.suggestions:
            self.append_log('agents: no suggestions')

    def _render_suggestions(self):
        """`self.suggestions`(선택 상태 포함)를 표에 그린다 — 언어 전환 시 재사용해도
        사용자가 고른 선택(`s.selected`)을 그대로 유지한다(기본 선택으로 되돌리지 않는다)."""
        self.sugg_tree.delete(*self.sugg_tree.get_children())
        review = []
        for s in self.suggestions:
            self.sugg_tree.insert('', 'end', values=self._sugg_values(s))
            if s.agent == 'review' and s.payload.get('markdown'):
                review.append(s.payload['markdown'])
        self.review_text.delete('1.0', 'end')
        self.review_text.insert('end', '\n\n'.join(review) if review else t('no_review_summary'))

    @staticmethod
    def _sugg_values(s):
        payload = s.payload or {}
        text = payload.get('type') or payload.get('choice') or payload.get('error') or ''
        if not text and payload:
            text = json.dumps(payload, ensure_ascii=False)[:120]
        return ('[v]' if s.selected else '[ ]', s.agent, s.kind, s.target, text,
                (s.reason or '')[:200], f'{s.confidence:.2f}')

    def _on_sugg_click(self, event):
        if self.sugg_tree.identify_region(event.x, event.y) != 'cell':
            return
        if self.sugg_tree.identify_column(event.x) != '#1':
            return
        item = self.sugg_tree.identify_row(event.y)
        if item:
            self.toggle_suggestion(item)

    def toggle_suggestion(self, item):
        items = self.sugg_tree.get_children()
        try:
            idx = items.index(item)
        except ValueError:
            return
        if idx >= len(self.suggestions):
            return
        s = self.suggestions[idx]
        s.selected = not s.selected
        self.sugg_tree.set(item, 'sel', '[v]' if s.selected else '[ ]')

    def apply_selected_suggestions(self):
        """체크한 제안만 옵션(`Resolutions`)에 반영하고 다시 실행한다."""
        if not self.suggestions:
            messagebox.showinfo(t('dlg_no_suggestions_title'), t('dlg_no_suggestions_msg'))
            return
        if not any(s.selected for s in self.suggestions):
            messagebox.showinfo(t('dlg_no_selection_title'), t('dlg_no_selection_msg'))
            return
        from .agents import apply_suggestions
        opts = apply_suggestions(self.build_options(), self.suggestions)
        self.apply_options(opts)
        self.start_run()

    # ----- 결과 파일 -----
    def set_files(self, files, input_mode='edif'):
        """결과 파일 표를 채운다. `input_mode` 가 kicad-project/dsn 이면 [1][2] 안내를 함께 띄운다."""
        self.files = dict(files or {})
        note = missing_edif_verify_note(input_mode)
        self.files_note.config(text=note or '')
        if note:
            self.files_note.pack(padx=10, pady=(8, 0), anchor='w', before=self.file_tree)
        else:
            self.files_note.pack_forget()
        self.file_tree.delete(*self.file_tree.get_children())
        labels = FILE_LABELS()
        for key in ('root_sch', 'pro', 'lib', 'board', 'pdf', 'erc_json', 'netlist_txt'):
            if key in self.files:
                self.file_tree.insert('', 'end', values=(labels.get(key, key), self.files[key]))
        for key, path in sorted(self.files.items()):
            if key not in labels:
                self.file_tree.insert('', 'end', values=(key, path))

    def open_outdir(self):
        path = self.var_outdir.get().strip()
        if not path and getattr(self, 'files', None):
            path = os.path.dirname(next(iter(self.files.values())))
        if not open_path(path):
            messagebox.showinfo(t('dlg_cannot_open_title'), t('dlg_cannot_open_outdir_msg'))

    def open_project(self):
        """프로젝트를 KiCad 로 연다. 회로도 포맷이 정식판 상한(20260306)보다 높으면 파일 연결
        프로그램(설치된 KiCad 10)은 열지 못하므로, 그 회로도를 읽은 kicad-cli 옆의 포터블
        `kicad.exe`(나이틀리, 설치 없음)로 직접 띄운다."""
        path = (getattr(self, 'files', None) or {}).get('pro')
        if not path or not os.path.exists(path):
            messagebox.showinfo(t('dlg_cannot_open_title'), t('dlg_cannot_open_project_msg'))
            return
        from .explain import STABLE_SCH_CEILING, kicad_gui_next_to
        result = getattr(self, 'result', None)
        version = getattr(getattr(result, 'view', None), 'version', 0) or 0
        if version > STABLE_SCH_CEILING:
            gui = kicad_gui_next_to(getattr(result, 'kicad_cli', None)) or \
                kicad_gui_next_to(self.var_kicad_cli.get().strip())
            if gui:
                try:
                    subprocess.Popen([gui, path])
                    self.append_log(f'open: {gui} {path}')
                    return
                except OSError as e:
                    self.append_log(f'open failed: {e}')
            messagebox.showwarning(t('dlg_need_nightly_title'),
                                   t('dlg_need_nightly_msg', version=version))
            return
        if not open_path(path):
            messagebox.showinfo(t('dlg_cannot_open_title'), t('dlg_cannot_open_project_msg'))

    # ----- 종료 -----
    def on_close(self):
        save_settings(self.settings_dict())
        self.destroy()

    def destroy(self):
        """창을 닫기 전에 폴링 예약을 취소한다(닫은 뒤 콜백이 돌아 Tk 오류가 나지 않도록)."""
        poll_id, self._poll_id = getattr(self, '_poll_id', None), None
        if poll_id is not None:
            try:
                self.after_cancel(poll_id)
            except Exception:
                pass
        super().destroy()


def main(argv=None):
    """GUI 진입점. 콘솔로 나가는 오류 메시지는 ASCII 영문."""
    if tk is None:
        print(f'error: tkinter is not available ({_TK_ERROR})', file=sys.stderr)
        return 2
    try:
        app = App()
    except Exception as e:
        print(f'error: cannot start GUI: {e}', file=sys.stderr)
        return 2
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
