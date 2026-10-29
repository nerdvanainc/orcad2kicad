"""변환·검증 결과를 사람 말로 풀어 쓰는 "요약 설명" — 문제가 없으면 왜 없는지, 있으면 무엇을
봐야 하는지를 항목별로 적는다(사용자 요청 2026-09-09: "결과가 너무 단순한데, 문제없으면 문제없다고
자세히 설명을 해주면 좋겠다").

`explain_result(result, lang)` 가 줄 목록을 돌려준다. GUI 는 한국어(`ko`)로 결과 탭·로그에 보여 주고,
CLI(`--explain`)와 MCP JSON 은 영문(`en`, 콘솔 규칙: ASCII)을 쓴다. 판단 자체는 하지 않고
`PipelineResult` 에 이미 있는 검증 결과·ERC 집계·파일 목록만 해석한다.
"""
from __future__ import annotations
import os



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


def _examples(mapping, n=3):
    """{old: new} -> 'a -> b, c -> d' (앞 n개)."""
    return ', '.join(f'{k} -> {v}' for k, v in list(mapping.items())[:n])


def _net_name_lines(result, lang):
    """넷 이름 모드(`--net-names`)와 보드 넷 이름 동기화(`--board-net-names`) 결과 설명 줄."""
    ko = lang == 'ko'
    L = []
    nn = getattr(result, 'net_names', None) or 'kicad'
    if nn == 'keep':
        L.append('넷 이름: PADS·OrCAD 이름 유지(keep) - 별칭·이름 없는 넷은 전역 라벨(사각 깃발)로 만들어 이름을 지킴. '
                 '같은 이름 별칭이 여러 페이지에 있으면 합쳐질 수 있음.' if ko
                 else 'net names: keep PADS/OrCAD names (keep) - aliases and unnamed nets become global labels '
                      '(square flags) so names survive; same-named aliases on several pages may merge.')
    else:
        L.append('넷 이름: KiCad 방식(kicad) - 인위적 전역 라벨 없음. 별칭은 지역 라벨(예: /USB/DTR#), '
                 '이름 없는 넷은 KiCad 자동 이름(예: Net-(C7-Pad2)).' if ko
                 else 'net names: KiCad style (kicad) - no artificial global labels. Aliases are local labels '
                      '(e.g. /USB/DTR#), unnamed nets get KiCad auto names (e.g. Net-(C7-Pad2)).')
    loc = getattr(result, 'localize', None)
    if loc:
        if loc.get('reverted'):
            L.append('    숨은 전역 라벨 제거는 연결이 달라져 원복함(keep 상태로 남음).' if ko
                     else '    removing hidden global labels changed connectivity and was reverted (stays keep).')
        else:
            ren = loc.get('renamed') or {}
            ex = _examples(ren)
            L.append((f'    임포터가 붙인 숨은 전역 라벨 {loc.get("removed", 0)}개 제거, 이름이 바뀐 넷 {len(ren)}개'
                      + (f'(예: {ex})' if ex else '') + ' - 핀 연결은 kicad-cli 넷리스트로 동일함을 확인.') if ko
                     else (f'    removed {loc.get("removed", 0)} hidden importer global labels, {len(ren)} nets renamed'
                           + (f' (e.g. {ex})' if ex else '') + ' - pin connectivity verified identical with kicad-cli.'))
    bs = getattr(result, 'board_sync', None)
    if bs:
        mode = bs.get('mode')
        if mode == 'pads':
            L.append('보드 넷 이름: PADS 그대로 유지. 회로도 이름과 다른 넷은 Update PCB from Schematic 때 바뀔 수 있음'
                     '(이 모드에서는 차이를 계산하지 않음).' if ko
                     else 'board net names: kept as PADS. Nets whose schematic name differs may be renamed by '
                          'Update PCB from Schematic (the difference is not computed in this mode).')
        elif bs.get('reverted'):
            L.append('보드 넷 이름: 회로도에 맞추는 중 패드 연결이 달라져 원복함(PADS 이름 그대로).' if ko
                     else 'board net names: sync reverted because pad connectivity changed (PADS names kept).')
        elif bs.get('skipped'):
            L.append('보드 넷 이름: 맞추지 못함 - 회로도 넷리스트를 읽을 kicad-cli가 없음(PADS 이름 그대로).' if ko
                     else 'board net names: not synced - no kicad-cli to read the schematic netlist (PADS names kept).')
        else:
            ren = bs.get('renamed') or {}
            ex = _examples(ren)
            total, cons = bs.get('total', 0), bs.get('consistent', 0)
            differ = bs.get('differ') or []
            head = (f'보드 넷 이름: 회로도에 맞춤 - {len(ren)}개 넷 이름 변경' + (f'(예: {ex})' if ex else '')
                    + f', 회로도와 이름이 같은 보드 넷 {cons}/{total}.') if ko else \
                (f'board net names: matched to the schematic - {len(ren)} nets renamed'
                 + (f' (e.g. {ex})' if ex else '') + f', {cons}/{total} board nets have the schematic name.')
            L.append(head)
            if not differ:
                L.append('    Update PCB from Schematic 에서 넷 이름이 바뀌지 않습니다.' if ko
                         else '    Update PCB from Schematic will not change any net name.')
            else:
                names = ', '.join(differ[:8]) + ('...' if len(differ) > 8 else '')
                L.append(f'    이름이 다른 넷 {len(differ)}개: {names} - Update PCB 에서 바뀔 수 있음.' if ko
                         else f'    {len(differ)} nets differ: {names} - Update PCB may rename them.')
    return L


_HIDDEN_KIND_KO = {
    'alias': 'OrCAD 별칭을 같은 이름의 전역 넷에 이어 주는 라벨',
    'renamed': '이 넷이 다른 별칭 이름으로 표시되도록 하는 라벨',
    'wire': '이 배선에 이름을 주는 유일한 라벨',
    'free': '배선에 붙지 않은 라벨',
    'local': '이 배선에 시트 안 이름을 주는 유일한 숨은 지역 라벨',
}
_HIDDEN_KIND_EN = {
    'alias': 'joins an OrCAD alias to the global net of the same name',
    'renamed': 'makes the net show up under another alias name',
    'wire': 'the only label naming this wire',
    'free': 'a label not attached to a wire',
    'local': 'the only hidden local label naming this wire',
}


def _conflict_lines(info, lang):
    """숨은 라벨 이름 충돌(같은 배선에 다른 이름 둘 = 한 넷으로 합쳐짐) 경고 줄. 없으면 빈 목록."""
    conf = info.get('conflicts') or []
    if not conf:
        return []
    ko = lang == 'ko'
    pairs = []
    seen = set()
    for c in conf:
        key = (c.get('name'), '/'.join(c.get('others') or ()), c.get('sheet'))
        if key not in seen:
            seen.add(key)
            pairs.append(key)
    if ko:
        L = [f'!! 경고 - 숨은 라벨 이름 충돌 {len(pairs)}건: 같은 배선에 이름이 서로 다른 라벨/전원 심볼이 함께 있음. '
             'KiCad 에서 한 배선의 이름 둘은 두 넷이 아니라 하나의 넷이므로 두 넷이 합쳐져 있음(ERC 에는 '
             'multiple_net_names 경고로만 나옴).']
    else:
        L = [f'!! WARNING - {len(pairs)} hidden label name conflicts: a hidden label and a power symbol or label with '
             'ANOTHER name sit on the same wire. In KiCad two names on one wire are ONE net, so two nets are merged '
             '(ERC only shows a multiple_net_names warning).']
    for name, others, sheet in pairs[:5]:
        L.append(f'    {name} = {others} ({sheet})')
    if len(pairs) > 5:
        L.append(f'    ... 외 {len(pairs) - 5}건' if ko else f'    ... and {len(pairs) - 5} more')
    if ko:
        L.append('    고치는 법: KiCad 에서 편집 > 찾기(Edit > Find)로 다른 쪽 이름을 찾아, 의도하지 않은 숨은 라벨을 지우거나 '
                 '의도한 전원 심볼을 되돌릴 것. 숨은 라벨이 안 보이면 --hidden-labels show (GUI: "보이게 바꾸기")로 다시 변환하면 보임.')
        L.append('    참고: 새로 변환한 결과에서도 같은 쌍이 나오면 OrCAD 원본의 그 넷이 이미 이름을 둘 가진 것일 수 있음 '
                 '(원본 회로도와 대조할 것).')
    else:
        L.append('    How to fix: in KiCad use Edit > Find to locate the other name, then delete the unintended hidden '
                 'label or put the intended power symbol back. --hidden-labels show makes hidden labels visible.')
        L.append('    Note: if the same pair appears in a fresh conversion, the original OrCAD net may already have '
                 'carried two names (compare with the source schematic).')
    return L


def _guard_lines(result, lang):
    """정리 단계의 넷리스트 대조(guard)가 후퇴하거나 되돌렸을 때의 쉬운 설명. 정상(full)이면 빈 목록."""
    cl = getattr(result, 'import_cleanup', None)
    g = cl.get('guard') if cl else None
    if not g:
        return []
    ko = lang == 'ko'
    if not g.get('verified'):
        return [('임포트 정리: 모든 시도에서 넷리스트가 달라져 정리를 전부 되돌림 - 임포터가 만든 그대로임. '
                 '배선 모양은 덜 깔끔하지만 연결은 원본과 같음.') if ko
                else ('import cleanup: every attempt changed the netlist, so all cleanup was reverted - the output is '
                      'as the importer wrote it. The drawing is less tidy but the connectivity is the original.')]
    lv = g.get('level')
    if lv == 'full':
        return []
    if ko:
        what = {'no label snapping': '라벨 위치 맞추기를 뺀', 'safe steps only': '안전한 단계만 남긴'}.get(lv, str(lv))
        return [f'임포트 정리: 전체 정리를 하면 넷리스트가 달라져서 {what} 정리로 물러나 적용함(넷리스트 동일 확인). '
                '일부 라벨/배선 모양이 덜 다듬어졌을 수 있음.']
    return [f"import cleanup: the full cleanup changed the netlist, so it fell back to '{lv}' (netlist verified identical). "
            'Some labels/wires may be less tidy.']


def _dup_alias_lines(result, lang):
    n = getattr(result, 'duplicate_alias_removed', None)
    if n is None:
        return []
    if lang == 'ko':
        return [f'중복 별칭 라벨: 같은 배선의 보이는 전역 라벨과 이름이 같은 지역 별칭 라벨 {n}개를 지움'
                '(넷리스트 동일 확인). ERC same_local_global_label 경고가 줄지만 그림에서 글자가 하나씩 사라짐.']
    return [f'duplicate alias labels: {n} local alias labels sitting on the same wire as a same-name visible global label '
            'were removed (netlist verified identical). This removes ERC same_local_global_label warnings, but the '
            'drawing loses one text each.']


def _auto_change_lines(result, lang):
    """도구가 회로도를 **자동으로** 고친 내용(pipeline._auto_change_items) — 사용자가 놓치지 않게 요약 위쪽에 둔다.

    그대로 둔 오프페이지 커넥터(화면 글자 ≠ 실제 넷 이름)도 여기서 알린다."""
    items = getattr(result, 'auto_changes', None) or []
    unconv = ((getattr(result, 'import_cleanup', None) or {}).get('offpage_unconverted')) or []
    if not items and not unconv:
        return []
    ko = lang == 'ko'
    f = os.path.basename(result.files.get('auto_changes') or 'auto_changes.txt')
    L = []
    if items:
        L.append((f'자동 변경: 도구가 회로도를 {len(items)}가지로 직접 고침(핀 연결은 단계마다 넷리스트로 대조) - '
                  f'전체 목록은 출력 폴더의 {f}' if ko
                  else f'automatic changes: the tool edited the schematic in {len(items)} ways (connections verified '
                       f'with the netlist at each step) - full list in {f} in the output folder'))
        for it in items:
            L.append(f"    - {it['ko'] if ko else it['en']}: {it['count']}"
                     + (f"  (확인: {it['check']})" if ko and it.get('check') else ''))
    splits = ((getattr(result, 'import_cleanup', None) or {}).get('bus_split') or {}).get('splits') or []
    if splits:
        ex = ', '.join(f"{sp['net']}->{len(sp['into'])}" for sp in splits[:4]) + (' ...' if len(splits) > 4 else '')
        L.append((f'연결이 바뀐 넷 {len(splits)}개({ex}): 임포터가 버스 위 접속점으로 버스 멤버들을 한 넷으로 합쳐 둔 것을 '
                  '나눔. PCB 와 맞는지 확인할 것.' if ko
                  else f'{len(splits)} nets split ({ex}): the importer had joined bus members into one net through '
                       'junctions on the bus. Check against the PCB.'))
    if unconv:
        ex = ', '.join(f"'{u['text']}'={u['net']}" for u in unconv[:4]) + (' ...' if len(unconv) > 4 else '')
        L.append((f'그대로 둔 오프페이지 커넥터 {len(unconv)}개: 화면 글자와 실제 넷 이름이 다름({ex}). OrCAD 원본에서 '
                  '한 넷에 커넥터 이름이 둘이라, 라벨 이름을 바꾸면 넷이 갈라져 그림으로 둠.' if ko
                  else f'{len(unconv)} off-page connectors left as drawings: the shown text differs from the net name '
                       f'({ex}). OrCAD gave the net two connector names; renaming would split the net.'))
    return L


def _hidden_label_lines(result, lang):
    """.DSN/KiCad 프로젝트 입력에서 정리 뒤 남은 숨은(투명) 전역 라벨과 `--hidden-labels` 처리 결과 설명 줄.

    EDIF 입력(`hidden_labels` 가 None)이면 아무 것도 내지 않는다."""
    info = getattr(result, 'hidden_labels', None)
    if not info:
        return []
    ko = lang == 'ko'
    rem = info.get('remaining') or []
    mode = info.get('mode') or 'keep'
    L = _conflict_lines(info, lang)
    placed = info.get('power_symbols_placed') or []
    if placed:
        names = ', '.join(f"{d.get('name')}@{d.get('sheet')}" for d in placed[:6]) + (' ...' if len(placed) > 6 else '')
        L.append((f'전원 심볼 배치: 부품 핀에 바로 얹힌 숨은 전원 라벨 {len(placed)}개를 같은 이름의 전원 심볼로 바꿈({names}). '
                  '넷 이름·핀 연결이 같음을 확인했고, 이제 화면에 보이는 연결임.') if ko
                 else (f'power symbols placed: {len(placed)} hidden power labels sitting directly on part pins were replaced '
                       f'by power symbols of the same name ({names}). Net names and pins verified identical; '
                       'the connection is now visible.'))
    if not rem:
        L.append('숨은 라벨: 정리 뒤 남은 것 없음 - 임포터가 붙인 숨은 라벨은 모두 중복이거나 지역 라벨로 바뀜.' if ko
                 else 'hidden labels: none left - every hidden importer label was a duplicate or was localized.')
        return L
    n = len(rem)
    keep_names = getattr(result, 'net_names', None) == 'keep'
    if ko:
        why = ('넷 이름 유지(keep) 때문에 일부러 남김' if keep_names
               else '실제 연결을 실어 나르므로 지우면 넷이 끊기거나 이름이 바뀜')
        L.append(f'숨은 라벨: {n}개 남음 - {why}. KiCad 화면에서는 보이지 않지만 PDF/SVG 에는 검게 찍힘.')
    else:
        why = ('kept on purpose by net names keep' if keep_names
               else 'they carry real connections, so removing one would cut or rename a net')
        L.append(f'hidden labels: {n} left - {why}. Invisible in KiCad, but printed black in PDF/SVG.')
    for d in rem[:5]:
        kind = d.get('kind')
        alias = d.get('alias')
        if ko:
            reason = _HIDDEN_KIND_KO.get(kind, kind or '')
            if kind == 'renamed' and alias:
                reason += f"('{alias}')"
        else:
            reason = _HIDDEN_KIND_EN.get(kind, kind or '')
            if kind == 'renamed' and alias:
                reason += f" ('{alias}')"
        L.append(f"    {d.get('name')} ({d.get('sheet')}): {reason}")
    if n > 5:
        L.append(f'    ... 외 {n - 5}개' if ko else f'    ... and {n - 5} more')
    applied = info.get('applied', 0)
    if info.get('reverted'):
        L.append(('    처리: 시도했으나 넷리스트가 달라져 원복함 - 임포트된 그대로 둠.' if ko
                  else '    done: tried, but the netlist changed, so it was reverted - kept as imported.'))
    elif mode == 'show' and applied:
        L.append((f'    처리: {applied}개를 보이는 전역 라벨로 바꿈(넷 이름·핀 연결이 같음을 확인). '
                  '그림에는 OrCAD 페이지에 없던 라벨이 늘어남.') if ko
                 else (f'    done: {applied} made visible as global labels (net names and pins verified identical). '
                       'The drawing gains labels the OrCAD page did not show.'))
    elif mode == 'shrink' and applied:
        L.append((f'    처리: {applied}개의 글자를 0.01 mm 로 줄여 PDF/SVG 에서도 감춤(넷리스트 동일 확인). '
                  '이 연결은 어디에도 보이는 라벨이 없게 됨.') if ko
                 else (f'    done: {applied} shrunk to 0.01 mm so PDF/SVG hide them too (netlist verified identical). '
                       'These connections then have no visible label anywhere.'))
    elif mode == 'keep':
        L.append(('    처리: 임포트된 그대로 둠. 바꾸려면 "보이게 바꾸기"(show: PDF 가 화면과 같아지는 대신 '
                  '그림에 라벨이 늘어남) 또는 "PDF에서도 감추기"(shrink: PDF 에서 사라지는 대신 이 연결에 '
                  '보이는 라벨이 없어짐)를 고를 것.') if ko
                 else ('    done: kept as imported. Alternatives: --hidden-labels show (PDF matches the screen, '
                       'but the drawing gains labels) or --hidden-labels shrink (gone from PDF, but the connection '
                       'then has no visible label anywhere).'))
    elif info.get('verified') is None:
        L.append(('    처리: 적용하지 못함(검증할 kicad-cli 없음) - 임포트된 그대로 둠.' if ko
                  else '    done: not applied (no kicad-cli to verify) - kept as imported.'))
    return L


def _explain(result, lang='ko'):
    """PipelineResult -> (설명 줄 목록, 확인할 항목 목록). 판정은 결과에 있는 값만 해석한다."""
    ko = lang == 'ko'
    L = []
    if result.error:
        L.append(('오류로 중단됨: ' if ko else 'stopped with an error: ') + str(result.error))
        return L, []
    opts = result.options
    mode = getattr(result, 'input_mode', 'edif')
    attention = []          # 결론에 모을 "봐야 할 것"
    rf = getattr(result, 'ref_format', None) or 'PADS'      # 기준 넷리스트 형식 이름

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
    # 1b) 자동 변경 — 사용자가 확실히 알도록 입력 바로 아래
    L.extend(_auto_change_lines(result, lang))
    if (getattr(result, 'import_cleanup', None) or {}).get('offpage_unconverted'):
        attention.append('오프페이지 글자 ≠ 넷 이름' if ko else 'off-page text != net name')

    # 2) [1][2]
    ver = result.verifications or {}
    if mode == 'edif':
        if 'edif' in ver:
            L.append((f'[1] EDIF 연결 정보 vs 기준 넷리스트({rf}): ' if ko else f'[1] EDIF connectivity vs reference netlist ({rf}): ')
                     + _cmp_text(ver['edif'], 'OrCAD가 저장한 연결 정보가 정답과 같음.',
                                 'the connectivity stored by OrCAD equals the reference.', lang))
            L.append((f'[2] 배선 기하 vs 기준 넷리스트({rf}): ' if ko else f'[2] wire geometry vs reference netlist ({rf}): ')
                     + _cmp_text(ver['geometry'], '그림(배선·핀 위치)만으로 추적한 연결도 정답과 같음.',
                                 'connectivity traced from wire/pin positions alone also equals the reference.', lang))
            for k in ('edif', 'geometry'):
                if not ver[k].ok:
                    attention.append(f'[{1 if k == "edif" else 2}] FAIL')
        else:
            L.append('[1][2] ' + ('건너뜀 - 기준 넷리스트(PADS .asc 또는 IPC-D-356)를 지정하지 않아 정답이 없음. 넷리스트를 주면 OrCAD 연결 정보와 배선 기하를 각각 정답과 대조함.' if ko
                                  else 'skipped - no reference netlist (PADS .asc or IPC-D-356) given, so there is nothing to compare against.'))
    else:
        L.append('[1][2] ' + ('건너뜀 - EDIF 입력이 아님(이 경로에서는 [3]이 같은 역할을 함).' if ko
                              else 'skipped - not an EDIF input ([3] plays that role on this path).'))

    # 3) [3]
    if 'kicad' in ver:
        L.append((f'[3] KiCad가 뽑은 넷리스트 vs 기준 넷리스트({rf}): ' if ko else f'[3] kicad-cli netlist vs reference netlist ({rf}): ')
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
        L.append('[3] ' + ('건너뜀 - 기준 넷리스트가 없음. OrCAD가 내보낸 PADS2000 넷리스트(.asc)나 보드 툴의 IPC-D-356 넷리스트를 주면 KiCad 회로도의 연결을 정답과 대조함.' if ko
                           else 'skipped - no reference netlist. Give the OrCAD-exported PADS2000 netlist (.asc) or an IPC-D-356 netlist from the board tool to compare the KiCad connectivity.'))

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
                    if n_n and not result.board else '')
                 + ((f' 보드가 없어 넷리스트의 풋프린트 이름 {result.footprint_names_unset}개는 채우지 않음(가리킬 '
                     '라이브러리가 없어 KiCad 경고만 생김) - --board 로 PADS 보드를 주면 채움.' if ko
                     else f' No board, so {result.footprint_names_unset} footprint names from the netlist were left '
                          'unset (no library to point to, KiCad would only warn) - give --board to fill them.')
                    if getattr(result, 'footprint_names_unset', 0) else ''))
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

    # 4b) 넷 이름 방침과 보드 넷 이름 동기화(옵션의 결과를 사람이 알아보도록)
    L.extend(_net_name_lines(result, lang))
    L.extend(_guard_lines(result, lang))
    L.extend(_dup_alias_lines(result, lang))
    L.extend(_hidden_label_lines(result, lang))
    hl = getattr(result, 'hidden_labels', None)
    if hl and hl.get('conflicts'):
        attention.append('숨은 라벨 이름 충돌(넷 합쳐짐)' if ko else 'hidden label name conflicts (nets merged)')
    g = (getattr(result, 'import_cleanup', None) or {}).get('guard')
    if g and not g.get('verified'):
        attention.append('임포트 정리 전부 되돌림' if ko else 'import cleanup reverted')

    # 5) ERC
    erc = result.erc
    if erc:
        by = erc.get('by_type') or {}
        def _level(t):
            level = ERC_TYPES.get(t, ('check',))[0]
            return 'fix' if (t == 'footprint_link_issues' and result.board is not None) else level
        all_info = bool(by) and all(_level(t) == 'info' for t in by)
        if all_info:
            # 전부 정보성이면 한 줄로 — 결과 탭에서 결론 줄이 밀려 안 보이던 문제(2026-09-11), 읽는 사람도 유형별 설명이 필요 없다
            kinds = ', '.join(f'{t} {n}' for t, n in sorted(by.items(), key=lambda kv: -kv[1]))
            L.append((f'ERC {erc.get("count", 0)}건 - 전부 정보성(조치 불필요): {kinds}. 임포터 특성·KiCad 관례 차이이며 연결과 무관함.' if ko
                      else f'ERC: {erc.get("count", 0)} violations, all informational (no action): {kinds} - importer artefacts / KiCad conventions, unrelated to connectivity.'))
            by = {}
        else:
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
    n_auto = len(getattr(result, 'auto_changes', None) or [])
    if n_auto:
        L[-1] += (f' 단, 도구가 회로도를 {n_auto}가지로 자동 수정했으니 위 "자동 변경" 목록을 확인할 것.' if ko
                  else f' Note: the tool edited the schematic automatically in {n_auto} ways - review the '
                       '"automatic changes" list above.')
    return L, attention


def explain_result(result, lang='ko'):
    """PipelineResult -> 설명 줄 목록(`lang`: 'ko' | 'en')."""
    return _explain(result, lang)[0]


def attention_items(result, lang='ko'):
    """결론 줄에 모이는 "확인할 항목" 목록(비어 있으면 문제 없음). 종료 코드가 0 이어도 [4] 넷 차이·
    회로도 전용 부품·ERC check 항목이 있으면 비어 있지 않다 — GUI 상태 표시가 이걸로 "모든 검증
    PASS" 와 "확인할 항목 있음" 을 가른다."""
    return _explain(result, lang)[1]


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
