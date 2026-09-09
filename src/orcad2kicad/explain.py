"""변환·검증 결과를 사람 말로 풀어 쓰는 "요약 설명" — 문제가 없으면 왜 없는지, 있으면 무엇을
봐야 하는지를 항목별로 적는다(사용자 요청 2026-09-09: "결과가 너무 단순한데, 문제없으면 문제없다고
자세히 설명을 해주면 좋겠다").

`explain_result(result, lang)` 가 줄 목록을 돌려준다. GUI 는 한국어(`ko`)로 결과 탭·로그에 보여 주고,
CLI(`--explain`)와 MCP JSON 은 영문(`en`, 콘솔 규칙: ASCII)을 쓴다. 판단 자체는 하지 않고
`PipelineResult` 에 이미 있는 검증 결과·ERC 집계·파일 목록만 해석한다.
"""
from __future__ import annotations
import os

from .branding import CONTACT_KO, CONTACT_EN

# ERC 위반 유형별 설명. level: 'info'(연결과 무관, 조치 불필요) | 'check'(확인 권장) | 'fix'(조치 필요).
ERC_TYPES = {
    'endpoint_off_grid': ('info',
        '배선/핀 끝점이 KiCad 격자에서 벗어남. OrCAD 좌표 단위(0.254 mm) 차이로 생기는 임포터 특성이며 전기적 연결에는 영향 없음.',
        'wire/pin endpoint off the KiCad grid; caused by the OrCAD unit (0.254 mm); no effect on connectivity.'),
    'unconnected_wire_endpoint': ('info',
        '배선 끝이 아무 것에도 붙지 않음(OrCAD의 열린 배선 끝·버스 진입선 등). [3]이 PASS면 연결 문제가 아니라 그림 정리 문제.',
        'wire end attached to nothing (open wire ends / bus entries in OrCAD); cosmetic when [3] passes.'),
    'pin_not_connected': ('info',
        '연결이 없는 핀에 미접속(X) 표시가 없음. OrCAD의 No-Connect 표시가 옮겨지지 않은 것. 의도된 미사용 핀이면 무시.',
        'unused pin without a no-connect marker (OrCAD no-connect flags are not imported); ignore for intentionally unused pins.'),
    'power_pin_not_driven': ('info',
        '전원 입력 핀의 넷에 전원 공급원 표시(PWR_FLAG)가 없음. KiCad 관례 차이. 전원 넷마다 PWR_FLAG를 하나 붙이면 사라짐.',
        'power input pin without a driver flag (PWR_FLAG); a KiCad convention, add one PWR_FLAG per power net.'),
    'pin_not_driven': ('check',
        '입력 핀만 있고 구동원이 없는 넷. 실제 연결 누락일 수 있으니 해당 넷을 확인.',
        'net with only input pins and no driver; check the net for a missing connection.'),
    'same_local_global_label': ('info',
        '같은 이름의 로컬 라벨과 전역 라벨이 함께 있음. PADS 넷 이름을 그대로 쓰려고 전역 라벨을 쓰면서 생김. 같은 넷으로 이어짐.',
        'a local and a global label share a name; comes from keeping PADS net names as global labels; same net.'),
    'multiple_net_names': ('info',
        '한 넷에 라벨 이름이 여럿(OrCAD에서 별칭으로 이어 둔 넷, 예: VCC/VCC_3.3V). 어느 이름이 쓰였는지는 [3] 결과가 보여 줌.',
        'one net carries several label names (OrCAD aliases such as VCC/VCC_3.3V); [3] shows which name was used.'),
    'lib_symbol_issues': ('fix',
        '심볼을 라이브러리 테이블에서 찾지 못함. 우리가 만든 sym-lib-table이 적용되면 0이어야 함. 프로젝트를 KiCad에서 열어 라이브러리 경로 확인.',
        'symbol not found in the library table; should be 0 with the generated sym-lib-table; check library paths in KiCad.'),
    'footprint_link_issues': ('check',
        '풋프린트 필드의 PADS:<이름>을 라이브러리에서 못 찾음. PADS 보드를 지정하지 않아 PADS.pretty가 없을 때 생김(필드는 넷리스트 이름으로만 채워짐). 배선된 PADS ASCII를 "보드" 칸에 넣으면 해결.',
        'footprint field PADS:<name> not found in a library; happens without a PADS board (no PADS.pretty, fields filled from the netlist only). Give the routed PADS ASCII as --board.'),
    'pin_to_pin': ('check',
        '핀 타입 충돌(출력끼리 연결 등). OrCAD 심볼의 핀 타입이 대략적으로 옮겨져 생기는 경우가 대부분. 실제 회로가 맞으면 무시.',
        'pin type conflict (e.g. output to output); usually from approximate OrCAD pin types; ignore if the circuit is right.'),
    'isolated_pin_label': ('check',
        '라벨이 핀에만 붙어 있고 배선이 없음. [3]이 PASS면 연결에는 문제 없으나 위치 확인 권장.',
        'label attached to a pin without a wire; connectivity is fine when [3] passes, but worth a look.'),
    'label_dangling': ('check',
        '어디에도 붙지 않은 라벨. [3]이 PASS면 연결에는 영향 없음. 위치 확인 권장.',
        'label attached to nothing; no effect on connectivity when [3] passes; worth a look.'),
    'no_connect_dangling': ('info',
        '미접속 표시가 아무 핀에도 붙지 않음. 지워도 됨.',
        'no-connect marker attached to nothing; can be deleted.'),
    'duplicate_reference': ('fix',
        '레퍼런스가 중복됨. 원본 OrCAD에서도 중복이었는지 확인 필요.',
        'duplicate reference designator; check whether the OrCAD source also had it.'),
}
_LEVEL_KO = {'info': '정보(조치 불필요)', 'check': '확인 권장', 'fix': '조치 필요'}
_LEVEL_EN = {'info': 'info, no action', 'check': 'check', 'fix': 'needs fixing'}

# 정식 KiCad 10.0 이 읽을 수 있는 회로도 포맷 상한(kicad_netlist.SCH_FORMAT_CEILING 과 같은 값).
STABLE_SCH_CEILING = 20260306

_MODE_KO = {'edif': 'OrCAD EDIF 익스포트', 'dsn': 'OrCAD .DSN (KiCad 나이틀리 임포터로 변환)',
            'kicad-project': 'KiCad로 이미 임포트한 프로젝트'}
_MODE_EN = {'edif': 'OrCAD EDIF export', 'dsn': 'OrCAD .DSN (converted by the KiCad nightly importer)',
            'kicad-project': 'existing KiCad-imported project'}


def _cmp_text(cmp, what_ko, what_en, lang):
    """NetCompare -> 한 줄 설명."""
    if cmp.ok:
        if lang == 'ko':
            return (f'PASS - {cmp.matched}개 넷 모두 핀 집합이 같음(누락 0, 추가 0). '
                    f'{what_ko}')
        return f'PASS - all {cmp.matched} nets have identical pin sets (0 missing, 0 extra). {what_en}'
    n_mis, n_ours, n_ref = len(cmp.mismatches), len(cmp.only_ours), len(cmp.only_ref)
    if lang == 'ko':
        return (f'FAIL - 일치 {cmp.matched}개, 핀 집합이 다른 넷 {n_mis}개, 우리 쪽에만 있는 넷 {n_ours}개, '
                f'기준에만 있는 넷 {n_ref}개. 검증 탭의 넷 표에서 해당 넷을 확인할 것.')
    return (f'FAIL - {cmp.matched} matched, {n_mis} nets with different pin sets, {n_ours} only ours, '
            f'{n_ref} only in the reference. See the net table.')


def explain_result(result, lang='ko'):
    """PipelineResult -> 설명 줄 목록(`lang`: 'ko' | 'en'). 판정은 결과에 있는 값만 해석한다."""
    ko = lang == 'ko'
    L = []
    if result.error:
        L.append(('오류로 중단됨: ' if ko else 'stopped with an error: ') + str(result.error))
        return L
    opts = result.options
    mode = getattr(result, 'input_mode', 'edif')
    attention = []          # 결론에 모을 "봐야 할 것"

    # 1) 입력
    src = opts.dsn or opts.kicad_project or opts.edf or ''
    if result.view is not None:
        v = result.view
        counts = (f'시트 {len(v.sheet_files)}, 심볼 {len(v.symbols)}, 레퍼런스 {len(v.references())}' if ko
                  else f'{len(v.sheet_files)} sheets, {len(v.symbols)} symbols, {len(v.references())} references')
    elif result.design is not None:
        d = result.design
        n_inst = sum(len(p.instances) for p in d.pages)
        refs = {i.reference for p in d.pages for i in p.instances}
        counts = (f'페이지 {len(d.pages)}, 인스턴스 {n_inst}, 레퍼런스 {len(refs)}' if ko
                  else f'{len(d.pages)} pages, {n_inst} instances, {len(refs)} references')
    else:
        counts = ''
    L.append((f'입력: {_MODE_KO.get(mode, mode)} - {os.path.basename(src)}' if ko
              else f'input: {_MODE_EN.get(mode, mode)} - {os.path.basename(src)}') + (f' ({counts})' if counts else ''))

    # 2) [1][2]
    ver = result.verifications or {}
    if mode == 'edif':
        if 'edif' in ver:
            L.append(('[1] EDIF 연결 정보 vs PADS 넷리스트: ' if ko else '[1] EDIF connectivity vs PADS netlist: ')
                     + _cmp_text(ver['edif'], 'OrCAD가 저장한 연결 정보가 정답과 같음.',
                                 'the connectivity stored by OrCAD equals the reference.', lang))
            L.append(('[2] 배선 기하 vs PADS 넷리스트: ' if ko else '[2] wire geometry vs PADS netlist: ')
                     + _cmp_text(ver['geometry'], '그림(배선·핀 위치)만으로 추적한 연결도 정답과 같음.',
                                 'connectivity traced from wire/pin positions alone also equals the reference.', lang))
            for k in ('edif', 'geometry'):
                if not ver[k].ok:
                    attention.append(f'[{1 if k == "edif" else 2}] FAIL')
        else:
            L.append('[1][2] ' + ('건너뜀 - PADS 넷리스트(.asc)를 지정하지 않아 정답이 없음. 넷리스트를 주면 OrCAD 연결 정보와 배선 기하를 각각 정답과 대조함.' if ko
                                  else 'skipped - no PADS netlist (.asc) given, so there is no reference to compare against.'))
    else:
        L.append('[1][2] ' + ('건너뜀 - EDIF 입력이 아님(이 경로에서는 [3]이 같은 역할을 함).' if ko
                              else 'skipped - not an EDIF input ([3] plays that role on this path).'))

    # 3) [3]
    if 'kicad' in ver:
        L.append(('[3] KiCad가 뽑은 넷리스트 vs PADS 넷리스트: ' if ko else '[3] kicad-cli netlist vs PADS netlist: ')
                 + _cmp_text(ver['kicad'],
                             '변환된 KiCad 회로도의 전기적 연결이 OrCAD 원본(PADS로 내보낸 정답)과 완전히 같음.',
                             'the electrical connectivity of the converted KiCad schematic equals the OrCAD original.', lang))
        if not ver['kicad'].ok:
            attention.append('[3] FAIL')
        renamed = [i for i in (result.issues or ()) if 'renamed to' in i and 'identical pins' in i]
        if renamed:
            L.append(('    표기만 맞춘 넷 ' if ko else '    nets matched by spelling only: ') + f'{len(renamed)}'
                     + (' 건(오버바 /이름, 대소문자, 중복 라벨 접미 번호) - 연결은 같고 이름 표기만 다른 것.' if ko
                        else ' (overbar /NAME, letter case, duplicate-label suffix) - same connections, different spelling.'))
    elif opts.netlist:
        L.append('[3] ' + ('건너뜀 - 회로도를 읽을 수 있는 kicad-cli가 없거나 출력 폴더가 없음.' if ko
                           else 'skipped - no kicad-cli able to read the schematic, or no output directory.'))
    else:
        L.append('[3] ' + ('건너뜀 - PADS 넷리스트(.asc)가 없음. OrCAD가 내보낸 PADS2000 넷리스트를 주면 KiCad 회로도의 연결을 정답과 대조함.' if ko
                           else 'skipped - no PADS netlist (.asc). Give the OrCAD-exported PADS2000 netlist to compare the KiCad connectivity.'))

    # 4) 풋프린트 필드 / [4]
    fs = result.footprint_sources or {}
    if fs:
        n_b = sum(1 for s in fs.values() if s == 'board')
        n_n = sum(1 for s in fs.values() if s == 'netlist')
        n_e = sum(1 for s in fs.values() if not s)
        L.append((f'풋프린트 필드: 보드에서 {n_b}, 넷리스트 이름으로 {n_n}, 비어 있음 {n_e}.' if ko
                  else f'footprint fields: {n_b} from the board, {n_n} from netlist names, {n_e} empty.')
                 + ((' 보드가 없으면 PADS.pretty 라이브러리가 만들어지지 않아 필드 이름이 라이브러리와 연결되지 않음.' if ko
                     else ' Without a board no PADS.pretty library is written, so the names do not resolve to a library.')
                    if n_n and not result.board else ''))
    diff = result.board_diff
    if diff is not None:
        parts = []
        if diff.only_board_refs:
            parts.append((f'보드에만 있는 부품 {len(diff.only_board_refs)}개({", ".join(diff.only_board_refs[:6])}{"..." if len(diff.only_board_refs) > 6 else ""})' if ko
                          else f'{len(diff.only_board_refs)} parts only on the board ({", ".join(diff.only_board_refs[:6])}{"..." if len(diff.only_board_refs) > 6 else ""})'))
            attention.append('보드 전용 부품' if ko else 'board-only parts')
        if diff.only_sch_refs:
            parts.append((f'회로도에만 있는 부품 {len(diff.only_sch_refs)}개' if ko
                          else f'{len(diff.only_sch_refs)} parts only in the schematic'))
            attention.append('회로도 전용 부품' if ko else 'schematic-only parts')
        if diff.footprint_diff:
            parts.append((f'풋프린트 이름이 다른 부품 {len(diff.footprint_diff)}개' if ko
                          else f'{len(diff.footprint_diff)} parts with a different footprint name'))
        if diff.missing_pins:
            parts.append((f'보드 패드 번호와 안 맞는 핀이 있는 부품 {len(diff.missing_pins)}개(나사 구멍류는 정상)' if ko
                          else f'{len(diff.missing_pins)} parts whose pins do not match pad numbers (mounting holes are normal)'))
        head = ('[4] 보드 vs 회로도: ' if ko else '[4] board vs schematic: ')
        if not parts:
            L.append(head + ('부품 목록·풋프린트 이름이 모두 일치(보드에만/회로도에만 있는 부품 없음).' if ko
                             else 'part lists and footprint names all match (no board-only or schematic-only parts).'))
        else:
            L.append(head + ('; '.join(parts)) + ('. "보드 차이" 탭에서 항목별로 처리.' if ko else '. Handle each in the board-diff tab.'))
        if diff.net_compare is not None:
            basis = ('PADS 정답 넷리스트' if diff.net_reference == 'reference netlist' else '회로도 넷리스트(정답 .asc 없음)') if ko \
                else diff.net_reference
            L.append(('    보드 배선 넷 vs ' if ko else '    board nets vs ') + basis + ': '
                     + _cmp_text(diff.net_compare,
                                 '실제 배선된 보드의 연결이 회로도와 같음 - Update PCB from Schematic 을 해도 넷이 바뀌지 않음.',
                                 'the routed board matches the schematic - Update PCB from Schematic will not change nets.', lang))
            if not diff.net_compare.ok:
                attention.append('[4] 보드 넷 차이' if ko else '[4] board net differences')
            if getattr(diff, 'net_renames', None):
                L.append((f'    보드 넷 이름 {len(diff.net_renames)}개는 표기만 달라 기준 이름으로 맞춰 비교함.' if ko
                          else f'    {len(diff.net_renames)} board net names differed only in spelling and were matched.'))
    elif opts.board is None:
        L.append('[4] ' + ('건너뜀 - PADS 보드를 지정하지 않음. 배선된 PADS Layout ASCII(.asc)를 "보드" 칸에 주면 풋프린트 라이브러리(PADS.pretty)와 회로도-보드 연결(path)을 만들고 부품·넷 차이를 대조함.' if ko
                           else 'skipped - no PADS board given. Give the routed PADS Layout ASCII (.asc) as the board to build PADS.pretty, link schematic and board, and compare parts/nets.'))

    # 5) ERC
    erc = result.erc
    if erc:
        by = erc.get('by_type') or {}
        L.append((f'ERC {erc.get("count", 0)}건 - 유형별:' if ko else f'ERC: {erc.get("count", 0)} violations by type:'))
        for t, n in sorted(by.items(), key=lambda kv: -kv[1]):
            level, ko_txt, en_txt = ERC_TYPES.get(t, ('check', '설명 없는 유형 - KiCad ERC 창에서 확인.',
                                                       'no description - check in the KiCad ERC dialog.'))
            if t == 'footprint_link_issues' and result.board is not None:
                level = 'fix'
            if t == 'pin_not_connected' and n > 0:
                # 임포터 결과 대부분 - 정보로 둔다
                pass
            L.append(f'    - {t} {n}: [{(_LEVEL_KO if ko else _LEVEL_EN)[level]}] ' + (ko_txt if ko else en_txt))
            if level == 'fix' or (level == 'check' and t in ('pin_not_driven', 'pin_to_pin', 'duplicate_reference')):
                attention.append(f'ERC {t} {n}')
    elif result.files.get('root_sch') or result.files.get('pro'):
        L.append('ERC: ' + ('실행 안 함 - 회로도를 읽을 수 있는 kicad-cli가 없음.' if ko
                            else 'not run - no kicad-cli able to read the schematic.'))

    # 6) 출력과 여는 방법
    pro = result.files.get('pro')
    outdir = opts.outdir
    if pro or outdir:
        L.append((f'출력: {outdir or os.path.dirname(pro)}' if ko else f'output: {outdir or os.path.dirname(pro)}')
                 + (f' ({os.path.basename(pro)})' if pro else ''))
        version = getattr(result.view, 'version', 0) if result.view is not None else 0
        if version and version > STABLE_SCH_CEILING:
            gui = kicad_gui_next_to(result.kicad_cli)
            L.append((f'    회로도 포맷 {version}은 정식 KiCad 10에서 열리지 않음(상한 {STABLE_SCH_CEILING}). ' if ko
                      else f'    schematic format {version} cannot be opened by stable KiCad 10 (limit {STABLE_SCH_CEILING}). ')
                     + ((f'포터블 나이틀리로 열 것(설치 불필요): {gui}  - GUI의 "KiCad 프로젝트 열기" 버튼이 이것을 사용함.' if ko
                         else f'open it with the portable nightly (no install): {gui}')
                        if gui else ('나이틀리 kicad.exe 를 찾지 못함 - --fetch-kicad-nightly 로 포터블을 준비할 것.' if ko
                                     else 'nightly kicad.exe not found - prepare a portable nightly with --fetch-kicad-nightly.')))
        elif version and version <= STABLE_SCH_CEILING and mode != 'edif':
            n_sub = max(0, len(result.view.sheet_files) - 1) if result.view is not None else 0
            L.append((f'    회로도 포맷 {version}(정식 KiCad 10.0) - 루트 시트 + 하위 시트 {n_sub}개 구조. '
                      f'정식판으로 열고 편집 가능.' if ko
                      else f'    schematic format {version} (stable KiCad 10.0) - root sheet + {n_sub} '
                           f'sub-sheets; open and edit with the stable release.'))
    elif mode == 'edif':
        L.append('출력: ' + ('없음(출력 폴더 미지정 - 검증만 함).' if ko else 'none (no output directory - verification only).'))

    # 7) 결론
    if attention:
        L.append(('결론: 확인할 항목 있음 - ' if ko else 'conclusion: items to check - ') + ', '.join(attention))
    else:
        if ko:
            L.append('결론: 문제 없음. 연결 검증은 전부 통과했고 남은 ERC 항목은 연결과 무관한 임포터 특성·KiCad 관례 차이뿐이다.'
                     if erc else '결론: 문제 없음. 실행한 검증은 전부 통과했다.')
        else:
            L.append('conclusion: no problems. All connectivity checks passed; remaining ERC items are importer artefacts or KiCad conventions unrelated to connectivity.'
                     if erc else 'conclusion: no problems. All checks that ran passed.')
    L.append(CONTACT_KO if ko else CONTACT_EN)
    return L


def kicad_gui_next_to(cli_path):
    """kicad-cli 옆의 KiCad GUI 실행 파일(kicad.exe) 경로. 없으면 None."""
    if not cli_path:
        return None
    d = os.path.dirname(cli_path)
    for name in ('kicad.exe', 'kicad'):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def format_explanation(result, lang='ko'):
    title = '=== 요약 설명 ===' if lang == 'ko' else '=== summary ==='
    return '\n'.join([title] + explain_result(result, lang))
