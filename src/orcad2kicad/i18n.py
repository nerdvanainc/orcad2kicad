"""GUI 두 언어(한국어/영어) 문자열 표.

`t(key, **fmt)` 가 현재 언어(`set_language`/`get_language`)로 문자열을 찾아 돌려준다. 키가
없어도 GUI 가 죽지 않도록 키 텍스트 자체를 돌려준다(번역을 깜빡해도 화면에 어색한 라벨이
나올 뿐 예외는 나지 않는다).

CLI/파이프라인/로그에 흐르는 메시지는 이미 ASCII 영문이라 이 표를 쓰지 않는다(gui.py 문서
문자열 참조). `en` 값은 콘솔·redirect 로 갈 일이 없어도(GUI 창 문자열이라) 테스트에서 ASCII
인지 확인한다 — 굳이 비ASCII 를 쓸 이유가 없고, 나중에 로그/CLI 로 재사용하기 쉬워진다.
"""
from __future__ import annotations
import ctypes
import locale
import os

DEFAULT_LANG = 'ko'
_current_lang = DEFAULT_LANG

STRINGS = {
    'ko': {
        # ---- 표 선택지 ----
        'choice_ignore': '무시',
        'choice_add_to_sch': '회로도에 추가',
        'choice_fp_board': '보드',
        'choice_fp_orcad': 'OrCAD',
        'choice_hide': '리포트에서 숨김',
        # ---- 보드 차이 종류 ----
        'kind_board_only_part': '보드 전용 부품',
        'kind_sch_only_net': '회로도 전용 레퍼런스',
        'kind_footprint_diff': '풋프린트 이름 차이',
        'kind_missing_pins': '보드 패드에 없는 핀',
        'kind_net_diff': '넷 차이',
        # ---- 검증 탭 ----
        'verify_title_edif': '[1] EDIF 조인 vs PADS',
        'verify_title_geometry': '[2] 지오메트리 vs PADS',
        'verify_title_kicad': '[3] kicad-cli 넷리스트 vs PADS',
        'verify_tab_placeholder': '아직 검증 결과가 없습니다. PADS 넷리스트를 지정하고 실행하세요.',
        'verify_no_result_hint': '검증 결과가 없습니다(PADS 넷리스트를 지정하면 [1][2] 검증이 돕니다).',
        'verify_no_result': '검증 결과 없음',
        'verify_summary': '일치 {matched} / 불일치 {mismatches} / 우리만 {only_ours} / 기준만 {only_ref}  ->  {status}',
        'note_missing_edif': '[1][2] EDIF 없음 — 건너뜀',
        # ---- AI 백엔드/에이전트 ----
        'backend_none': '없음 (결정적 변환만)',
        'agent_pin_type': '핀 타입',
        'agent_net_diff': '넷/보드 차이',
        'agent_review': '리뷰 요약',
        # ---- 결과 파일 종류 ----
        'file_root_sch': '루트 회로도',
        'file_lib': '심볼 라이브러리',
        'file_pro': 'KiCad 프로젝트',
        'file_board': '보드(.kicad_pcb)',
        'file_erc_json': 'ERC 결과(JSON)',
        'file_netlist_txt': 'kicad-cli 넷리스트',
        # ---- 보드 차이 표 상세 ----
        'btn_apply_rerun': '적용 후 재출력',
        'diff_detail_board_only_part': '보드에만 있고 회로도에 없는 부품',
        'diff_detail_board_only_added': '보드 전용 부품 — 이미 회로도에 추가함(되돌리려면 무시 선택)',
        'diff_detail_sch_only_net': '회로도에만 있고 보드에 없는 레퍼런스',
        'none_value': '없음',
        'diff_detail_footprint_diff': 'OrCAD={sch_fp} / 보드={board_fp}',
        'diff_detail_missing_pins': '보드 패드에 없는 회로도 핀: {pins}',
        'diff_detail_net_mismatch': '기준에만={missing} 보드에만={extra}',
        'diff_detail_net_only_ours': '보드에만 있는 넷',
        'diff_detail_net_only_ref': '기준 넷리스트에만 있는 넷',
        # ---- 창/상태 ----
        'window_title': '{product} {version} — OrCAD → KiCad 변환 · {company}',
        'status_idle': '대기 중',
        'status_running': '변환 중...',
        'status_done_pass': '완료 (모든 검증 PASS)',
        'status_done_fail': '완료 (검증 FAIL 있음)',
        'status_done_error': '오류 (입출력/kicad-cli)',
        'status_done_exit': '완료 (exit {code})',
        'status_portable_running': '나이틀리 KiCad 준비 중...',
        'status_portable_done': '나이틀리 KiCad 준비 완료',
        'status_suggest_running': '제안 생성 중...',
        # ---- 언어 선택 ----
        'label_language': 'Language / 언어',   # 한국어를 몰라도 전환할 수 있도록 영문 병기
        # ---- 입력 프레임 ----
        'frame_input': '입력',
        'label_input_mode': '입력 종류:',
        'input_mode_dsn': 'OrCAD .DSN (KiCad 나이틀리 필요)',
        'input_mode_kicad_project': 'KiCad 프로젝트(.kicad_pro)',
        'btn_browse': '찾기...',
        'label_edf_file': 'EDIF 파일',
        'label_dsn_file': 'OrCAD .DSN 파일',
        'label_kicad_project_file': 'KiCad 프로젝트(.kicad_pro)',
        'label_netlist_file': 'PADS 넷리스트(.asc)',
        'label_board_file': 'PADS 레이아웃 ASCII 또는 KiCad PCB',
        'label_outdir': '출력 폴더',
        'label_project': '프로젝트 이름',
        'label_kicad_cli': 'kicad-cli 경로',
        'filetype_all': '모든 파일',
        'filetype_netlist': 'PADS 넷리스트',
        'filetype_board': 'PADS 레이아웃 ASCII / KiCad 보드',
        'filetype_kicad_project': 'KiCad 프로젝트',
        'filetype_exe': '실행 파일',
        'btn_portable_kicad': '나이틀리 KiCad 포터블 준비(설치 없음)',
        'note_portable_kicad': '나이틀리(10.99+)는 .DSN 임포트에만 필요. 결과는 정식 KiCad 10.0 포맷으로 저장됨',
        'label_net_names': '넷 이름:',
        'net_names_auto': '자동',
        'net_names_keep': '보드 이름 유지(keep)',
        'net_names_kicad': 'KiCad 방식',
        'chk_pdf': 'PDF 출력',
        'chk_strict': '보드 차이 엄격(strict)',
        'label_input_file_generic': '입력 파일',
        # ---- AI 프레임 ----
        'frame_ai': 'AI 에이전트 (선택 — 없어도 변환·검증은 전부 동작)',
        'label_backend': '백엔드',
        'label_api_key': 'API 키',
        'label_model': '모델',
        'label_agents': '에이전트:',
        'btn_suggest': '제안 생성',
        'backend_available_prefix': '사용 가능: ',
        'backend_unavailable_prefix': '사용 불가: ',
        # ---- 실행/정보 ----
        'btn_run': '변환 실행',
        'btn_about': '정보',
        'btn_site': '사이트 열기',
        'btn_github': 'GitHub 열기',
        'btn_close': '닫기',
        # ---- 탭 이름 ----
        'tab_log': '로그',
        'tab_verify': '검증',
        'tab_issues': '이슈',
        'tab_diff': '보드 차이',
        'tab_suggestions': '에이전트 제안',
        'tab_results': '결과',
        # ---- 이슈 탭 ----
        'label_filter': '필터:',
        'issue_count': '{shown}건 / 전체 {total}건',
        # ---- 보드 차이 탭 ----
        'col_kind': '종류',
        'col_target': '대상',
        'col_detail': '상세',
        'col_choice': '처리',
        'label_row_choice': '선택한 행의 처리:',
        'note_placeholder_page': '(보드 전용 부품은 99-PCB-ONLY 페이지에 자리표시 심볼로 추가됩니다)',
        # ---- 에이전트 제안 탭 ----
        'col_sel': '선택',
        'col_agent': '에이전트',
        'col_payload': '내용',
        'col_reason': '근거',
        'col_conf': '확신',
        'btn_apply_selected': '선택 적용 후 재출력',
        'note_suggestion_toggle': '(선택 칸을 클릭하면 체크가 바뀝니다. 핀 타입 제안은 기본 미선택)',
        'no_review_summary': '(리뷰 요약 없음)',
        # ---- 결과 탭 ----
        'label_explain_summary': '요약 설명 (각 검증이 무엇을 확인했고 무엇을 봐야 하는지)',
        'col_path': '경로',
        'btn_open_outdir': '결과 폴더 열기',
        'btn_open_project': 'KiCad 프로젝트 열기',
        # ---- 대화상자 ----
        'dlg_input_required_title': '입력 필요',
        'dlg_input_required_msg': '{label}을 선택하세요.',
        'dlg_input_error_title': '입력 오류',
        'dlg_input_error_msg': '{label}을 찾을 수 없습니다:\n{path}',
        'dlg_portable_title': '나이틀리 KiCad 포터블 준비',
        'note_no_extractor': ('이 PC 에는 압축 도구가 없어 7-Zip 콘솔판(약 6 MB)도 공식 사이트에서\n'
                              '함께 받습니다. 역시 압축만 풀며 설치하지 않습니다.\n\n'),
        'dlg_portable_confirm': ('최신 KiCad 나이틀리 설치본(약 234 MB)을 내려받아\n'
                                 '설치하지 않고 압축만 풉니다(정리 후 약 300 MB).\n\n'
                                 '{extra}위치: {root}\n\n'
                                 '계속할까요?'),
        'dlg_need_run_title': '먼저 변환',
        'dlg_need_run_msg': '먼저 변환을 실행한 뒤 제안을 생성하세요.',
        'dlg_no_backend_title': '백엔드 없음',
        'dlg_no_backend_msg': 'AI 백엔드를 선택하세요(없음 상태에서는 제안이 생기지 않습니다).',
        'dlg_no_agent_title': '에이전트 없음',
        'dlg_no_agent_msg': '에이전트를 하나 이상 선택하세요.',
        'dlg_error_title': '오류',
        'dlg_no_suggestions_title': '제안 없음',
        'dlg_no_suggestions_msg': '먼저 제안을 생성하세요.',
        'dlg_no_selection_title': '선택 없음',
        'dlg_no_selection_msg': '적용할 제안을 선택하세요(선택 칸 클릭).',
        'dlg_cannot_open_title': '열 수 없음',
        'dlg_cannot_open_outdir_msg': '결과 폴더가 없습니다(먼저 출력 폴더를 지정하고 실행하세요).',
        'dlg_cannot_open_project_msg': 'KiCad 프로젝트 파일(.kicad_pro)이 없습니다.',
        'dlg_need_nightly_title': '나이틀리 KiCad 필요',
        'dlg_need_nightly_msg': ('이 회로도의 포맷({version})은 정식 KiCad 10에서 열리지 않습니다.\n'
                                 '포터블 나이틀리(설치 없음)의 kicad.exe 가 필요합니다 - 입력 프레임의 '
                                 '[나이틀리 KiCad 포터블 준비] 버튼으로 준비한 뒤 다시 누르세요.'),
        'explain_failed': '요약 설명을 만들지 못했습니다: {error}',
        # ---- 검증 표 헤더 ----
        'col_net': '넷',
        'col_status': '상태',
        'col_missing': '기준에만(missing)',
        'col_extra': '우리에만(extra)',
    },
    'en': {
        'choice_ignore': 'Ignore',
        'choice_add_to_sch': 'Add to schematic',
        'choice_fp_board': 'Board',
        'choice_fp_orcad': 'OrCAD',
        'choice_hide': 'Hide from report',
        'kind_board_only_part': 'Board-only part',
        'kind_sch_only_net': 'Schematic-only reference',
        'kind_footprint_diff': 'Footprint name difference',
        'kind_missing_pins': 'Pins missing from board pads',
        'kind_net_diff': 'Net difference',
        'verify_title_edif': '[1] EDIF join vs PADS',
        'verify_title_geometry': '[2] Geometry vs PADS',
        'verify_title_kicad': '[3] kicad-cli netlist vs PADS',
        'verify_tab_placeholder': 'No verification results yet. Specify a PADS netlist and run.',
        'verify_no_result_hint': 'No verification results (specify a PADS netlist to run [1][2]).',
        'verify_no_result': 'No verification result',
        'verify_summary': 'matched {matched} / mismatched {mismatches} / ours only {only_ours} / ref only {only_ref}  ->  {status}',
        'note_missing_edif': '[1][2] no EDIF - skipped',
        'backend_none': 'None (deterministic conversion only)',
        'agent_pin_type': 'Pin type',
        'agent_net_diff': 'Net/board diff',
        'agent_review': 'Review summary',
        'file_root_sch': 'Root schematic',
        'file_lib': 'Symbol library',
        'file_pro': 'KiCad project',
        'file_board': 'Board (.kicad_pcb)',
        'file_erc_json': 'ERC result (JSON)',
        'file_netlist_txt': 'kicad-cli netlist',
        'btn_apply_rerun': 'Apply and re-run',
        'diff_detail_board_only_part': 'Part on the board only, not in the schematic',
        'diff_detail_board_only_added': 'Board-only part - already added to the schematic (choose Ignore to revert)',
        'diff_detail_sch_only_net': 'Reference in the schematic only, not on the board',
        'none_value': 'none',
        'diff_detail_footprint_diff': 'OrCAD={sch_fp} / Board={board_fp}',
        'diff_detail_missing_pins': 'Schematic pins missing from board pads: {pins}',
        'diff_detail_net_mismatch': 'reference only={missing} board only={extra}',
        'diff_detail_net_only_ours': 'Net found on the board only',
        'diff_detail_net_only_ref': 'Net found in the reference netlist only',
        'window_title': '{product} {version} - OrCAD to KiCad Converter - {company}',
        'status_idle': 'Idle',
        'status_running': 'Converting...',
        'status_done_pass': 'Done (all verifications PASS)',
        'status_done_fail': 'Done (some verifications FAIL)',
        'status_done_error': 'Error (I/O or kicad-cli)',
        'status_done_exit': 'Done (exit {code})',
        'status_portable_running': 'Preparing KiCad nightly...',
        'status_portable_done': 'KiCad nightly ready',
        'status_suggest_running': 'Generating suggestions...',
        'label_language': 'Language',
        'frame_input': 'Input',
        'label_input_mode': 'Input type:',
        'input_mode_dsn': 'OrCAD .DSN (requires KiCad nightly)',
        'input_mode_kicad_project': 'KiCad project (.kicad_pro)',
        'btn_browse': 'Browse...',
        'label_edf_file': 'EDIF file',
        'label_dsn_file': 'OrCAD .DSN file',
        'label_kicad_project_file': 'KiCad project (.kicad_pro)',
        'label_netlist_file': 'PADS netlist (.asc)',
        'label_board_file': 'PADS layout ASCII or KiCad PCB',
        'label_outdir': 'Output folder',
        'label_project': 'Project name',
        'label_kicad_cli': 'kicad-cli path',
        'filetype_all': 'All files',
        'filetype_netlist': 'PADS netlist',
        'filetype_board': 'PADS layout ASCII / KiCad board',
        'filetype_kicad_project': 'KiCad project',
        'filetype_exe': 'Executable',
        'btn_portable_kicad': 'Prepare portable KiCad nightly (no install)',
        'note_portable_kicad': 'Nightly (10.99+) is only needed to import .DSN; results are saved in the stable KiCad 10.0 format',
        'label_net_names': 'Net names:',
        'net_names_auto': 'Auto',
        'net_names_keep': 'Keep board names (keep)',
        'net_names_kicad': 'KiCad style',
        'chk_pdf': 'PDF output',
        'chk_strict': 'Strict board diff (strict)',
        'label_input_file_generic': 'input file',
        'frame_ai': 'AI agents (optional - conversion and verification fully work without them)',
        'label_backend': 'Backend',
        'label_api_key': 'API key',
        'label_model': 'Model',
        'label_agents': 'Agents:',
        'btn_suggest': 'Generate suggestions',
        'backend_available_prefix': 'available: ',
        'backend_unavailable_prefix': 'unavailable: ',
        'btn_run': 'Run conversion',
        'btn_about': 'About',
        'btn_site': 'Open site',
        'btn_github': 'Open GitHub',
        'btn_close': 'Close',
        'tab_log': 'Log',
        'tab_verify': 'Verify',
        'tab_issues': 'Issues',
        'tab_diff': 'Board diff',
        'tab_suggestions': 'Agent suggestions',
        'tab_results': 'Results',
        'label_filter': 'Filter:',
        'issue_count': '{shown} shown / {total} total',
        'col_kind': 'Kind',
        'col_target': 'Target',
        'col_detail': 'Detail',
        'col_choice': 'Choice',
        'label_row_choice': 'Choice for selected row:',
        'note_placeholder_page': '(Board-only parts are added as placeholder symbols on the 99-PCB-ONLY page)',
        'col_sel': 'Sel',
        'col_agent': 'Agent',
        'col_payload': 'Payload',
        'col_reason': 'Reason',
        'col_conf': 'Conf',
        'btn_apply_selected': 'Apply selected and re-run',
        'note_suggestion_toggle': '(Click the Sel cell to toggle it. Pin-type suggestions are unselected by default)',
        'no_review_summary': '(no review summary)',
        'label_explain_summary': 'Summary explanation (what each verification checked and what to look at)',
        'col_path': 'Path',
        'btn_open_outdir': 'Open results folder',
        'btn_open_project': 'Open KiCad project',
        'dlg_input_required_title': 'Input required',
        'dlg_input_required_msg': 'Select {label}.',
        'dlg_input_error_title': 'Input error',
        'dlg_input_error_msg': '{label} not found:\n{path}',
        'dlg_portable_title': 'Prepare portable KiCad nightly',
        'note_no_extractor': ('This PC has no archive tool, so the 7-Zip console edition (about 6 MB)\n'
                              'will also be downloaded from the official site. It is only extracted,\n'
                              'never installed.\n\n'),
        'dlg_portable_confirm': ('Download the latest KiCad nightly installer (about 234 MB)\n'
                                 'and only extract it, no install (about 300 MB after cleanup).\n\n'
                                 '{extra}Location: {root}\n\n'
                                 'Continue?'),
        'dlg_need_run_title': 'Run conversion first',
        'dlg_need_run_msg': 'Run the conversion first, then generate suggestions.',
        'dlg_no_backend_title': 'No backend',
        'dlg_no_backend_msg': 'Select an AI backend (no suggestions are made while set to None).',
        'dlg_no_agent_title': 'No agents',
        'dlg_no_agent_msg': 'Select at least one agent.',
        'dlg_error_title': 'Error',
        'dlg_no_suggestions_title': 'No suggestions',
        'dlg_no_suggestions_msg': 'Generate suggestions first.',
        'dlg_no_selection_title': 'No selection',
        'dlg_no_selection_msg': 'Select suggestions to apply (click the Sel cell).',
        'dlg_cannot_open_title': 'Cannot open',
        'dlg_cannot_open_outdir_msg': 'No results folder (specify an output folder and run first).',
        'dlg_cannot_open_project_msg': 'No KiCad project file (.kicad_pro).',
        'dlg_need_nightly_title': 'KiCad nightly required',
        'dlg_need_nightly_msg': ('This schematic format ({version}) cannot be opened by stable KiCad 10.\n'
                                 'The portable nightly kicad.exe (no install) is required - use the\n'
                                 '[Prepare portable KiCad nightly] button in the Input frame, then try again.'),
        'explain_failed': 'Could not build the summary explanation: {error}',
        'col_net': 'Net',
        'col_status': 'Status',
        'col_missing': 'reference only (missing)',
        'col_extra': 'ours only (extra)',
    },
}

assert set(STRINGS['ko']) == set(STRINGS['en']), 'ko/en key sets must match'


def set_language(lang):
    """현재 언어를 바꾼다. 모르는 값이면 기본 언어(`ko`)로 되돌린다."""
    global _current_lang
    _current_lang = lang if lang in STRINGS else DEFAULT_LANG


def get_language():
    return _current_lang


def t(key, **fmt):
    """현재 언어로 `key` 문자열을 찾아 돌려준다(없으면 기본 언어, 그래도 없으면 키 자체).

    `**fmt` 를 주면 `str.format` 로 채운다 — 자리표시자가 안 맞아도(호출부 실수) 예외 대신
    포맷 전 문자열을 돌려준다(GUI 가 멈추면 안 되므로)."""
    table = STRINGS.get(_current_lang, STRINGS[DEFAULT_LANG])
    text = table.get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANG].get(key, key)
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def _windows_ui_lang_candidate():
    """윈도우 UI 언어(GetUserDefaultUILanguage)를 대략적인 로캘 문자열로.

    한국어면 'ko_KR', 그 외(또는 조회 실패)는 None(모른다는 뜻 — 강제로 'en' 후보를 넣지
    않는다. 다른 소스가 'ko' 를 말하면 그쪽을 따른다)."""
    if os.name != 'nt':
        return None
    try:
        get_ui_lang = ctypes.windll.kernel32.GetUserDefaultUILanguage    # noqa: SLF001
        lang_id = get_ui_lang()
    except (AttributeError, OSError, ValueError):
        return None
    if not lang_id:
        return None
    primary = lang_id & 0x3FF          # LANG_KOREAN = 0x12
    return 'ko_KR' if primary == 0x12 else None


def detect_default_language():
    """OS 로캘이 한국어(`ko*`)로 보이면 'ko', 아니면 'en'.

    `locale.getlocale()`, `LC_ALL`/`LANG` 환경변수, (윈도우면) `GetUserDefaultUILanguage`
    중 하나라도 'ko' 로 시작하면 'ko' 다."""
    candidates = [os.environ.get('LC_ALL'), os.environ.get('LANG')]
    try:
        candidates.append(locale.getlocale()[0])
    except (ValueError, TypeError):
        pass
    candidates.append(_windows_ui_lang_candidate())
    for c in candidates:
        if c and str(c).lower().startswith('ko'):
            return 'ko'
    return 'en'
