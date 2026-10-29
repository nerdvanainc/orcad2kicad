"""explain.explain_result — 결과를 사람 말로 푸는 요약 설명(합성 결과로 확인)."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from orcad2kicad.pipeline import PipelineOptions, PipelineResult, result_to_json
from orcad2kicad.verify import NetCompare, NetDiff
from orcad2kicad.kicad_board import BoardDiff
from orcad2kicad.explain import (explain_result, format_explanation, kicad_gui_next_to, STABLE_SCH_CEILING,
                                 attention_items, _hidden_label_lines)


def _view(version=20260830):
    return SimpleNamespace(project='P', sheet_files=['a', 'b'], symbols=[1, 2, 3], version=version,
                           references=lambda: {'R1', 'U1'}, issues=[])


class ExplainDsnTest(unittest.TestCase):
    def _result(self, **kw):
        opts = PipelineOptions(dsn='X/BOARD.DSN', netlist='X/BOARD.asc', outdir='X/BOARD_kicad')
        r = PipelineResult(options=opts, input_mode='dsn', view=_view())
        r.verifications = {'kicad': NetCompare(matched=120)}
        r.footprint_sources = {'R1': 'netlist', 'U1': 'netlist'}
        r.erc = {'count': 3, 'by_type': {'endpoint_off_grid': 2, 'footprint_link_issues': 1}, 'items': []}
        r.files = {'pro': 'X/BOARD_kicad/BOARD.kicad_pro'}
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def test_all_pass_korean(self):
        lines = explain_result(self._result(), 'ko')
        text = '\n'.join(lines)
        self.assertIn('[3]', text)
        self.assertIn('PASS - 120개 넷', text)
        self.assertIn('[1][2] 건너뜀', text)
        self.assertIn('[4] 건너뜀', text)                     # 보드 미지정
        self.assertIn('footprint_link_issues 1', text)
        self.assertIn('PADS 보드를 지정하지 않아', text)
        self.assertIn(f'회로도 포맷 20260830', text)          # 정식판에서 안 열림 안내
        self.assertTrue(lines[-1].startswith('결론: 문제 없음'), lines[-1])

    def test_english_and_json(self):
        r = self._result()
        text = format_explanation(r, 'en')
        self.assertTrue(text.isascii(), text)
        self.assertIn('conclusion: no problems', text)
        self.assertEqual(result_to_json(r)['explanation'], explain_result(r, 'en'))

    def test_attention_items_helper(self):
        """GUI 상태 문구가 쓰는 `attention_items` — 전부 통과면 비고, FAIL 이 있으면 항목이 있다."""
        self.assertEqual(attention_items(self._result(), 'ko'), [])
        cmp = NetCompare(matched=5, mismatches=[NetDiff(net='X', missing={'R1.1'}, extra=set())])
        r = self._result(verifications={'kicad': cmp}, exit_code=1)
        self.assertIn('[3] FAIL', attention_items(r, 'en'))

    def test_fail_lists_attention(self):
        cmp = NetCompare(matched=5, mismatches=[NetDiff(net='X', missing={'R1.1'}, extra=set())])
        r = self._result(verifications={'kicad': cmp}, exit_code=1)
        r.erc['by_type']['pin_to_pin'] = 2
        lines = explain_result(r, 'ko')
        self.assertIn('FAIL - 일치 5개', '\n'.join(lines))
        self.assertTrue(lines[-1].startswith('결론: 확인할 항목 있음'), lines[-1])
        self.assertIn('[3] FAIL', lines[-1])
        self.assertIn('ERC pin_to_pin 2', lines[-1])

    def test_board_diff(self):
        diff = BoardDiff(only_board_refs=['J19', 'J20'], net_compare=NetCompare(matched=130),
                         net_reference='schematic netlist')
        r = self._result(board_diff=diff, board=object())
        r.options.board = 'X/board.asc'
        text = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('보드에만 있는 부품 2개(J19, J20)', text)
        self.assertIn('회로도 넷리스트(정답 .asc 없음)', text)
        self.assertIn('보드 전용 부품', text.splitlines()[-1])

    def test_error(self):
        r = PipelineResult(options=PipelineOptions(dsn='a.DSN'), error='boom')
        self.assertEqual(explain_result(r, 'en'), ['stopped with an error: boom'])

    def test_kicad_gui_next_to(self):
        d = tempfile.mkdtemp(prefix='o2k-gui-')
        try:
            cli = os.path.join(d, 'kicad-cli.exe')
            open(cli, 'wb').close()
            self.assertIsNone(kicad_gui_next_to(cli))
            open(os.path.join(d, 'kicad.exe'), 'wb').close()
            self.assertEqual(kicad_gui_next_to(cli), os.path.join(d, 'kicad.exe'))
            self.assertIsNone(kicad_gui_next_to(None))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class ExplainEdifTest(unittest.TestCase):
    def test_edif_without_netlist(self):
        design = SimpleNamespace(pages=[SimpleNamespace(instances=[SimpleNamespace(reference='R1')])])
        r = PipelineResult(options=PipelineOptions(edf='a.EDF'), input_mode='edif', design=design)
        lines = explain_result(r, 'ko')
        self.assertIn('[1][2] 건너뜀', lines[1])
        self.assertIn('출력: 없음', '\n'.join(lines))
        self.assertTrue(lines[-1].startswith('결론: 문제 없음'))
        self.assertLessEqual(STABLE_SCH_CEILING, 20260830)


class ExplainNetNamesTest(unittest.TestCase):
    """넷 이름 방침(localize)과 보드 넷 이름 동기화(board_sync) 요약 줄."""

    def _r(self, **kw):
        r = PipelineResult(options=PipelineOptions(dsn='X/B.DSN', outdir='X/o'), input_mode='dsn')
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def test_kicad_with_localize_and_sync_ko(self):
        r = self._r(net_names='kicad',
                    localize={'removed': 12, 'kept_names': [], 'renamed': {'DTR#': '/USB/DTR#', 'N1': 'Net-(C7-Pad2)',
                                                                           'A': 'B', 'C': 'D'},
                              'verified': True, 'reverted': False},
                    board_sync={'mode': 'schematic', 'renamed': {'DTR#': '/USB/DTR#'}, 'references': 3,
                                'unmatched': [], 'consistent': 40, 'total': 40, 'differ': []})
        text = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('넷 이름: KiCad 방식', text)
        self.assertIn('숨은 전역 라벨 12개 제거, 이름이 바뀐 넷 4개', text)
        self.assertIn('DTR# -> /USB/DTR#', text)
        self.assertNotIn('C -> D', text)                       # 예시는 3개까지
        self.assertIn('회로도에 맞춤 - 1개 넷 이름 변경', text)
        self.assertIn('40/40', text)
        self.assertIn('Update PCB from Schematic 에서 넷 이름이 바뀌지 않습니다', text)

    def test_differ_listed_and_english_ascii(self):
        r = self._r(net_names='keep',
                    board_sync={'mode': 'schematic', 'renamed': {}, 'references': 0, 'unmatched': ['X'],
                                'consistent': 9, 'total': 11, 'differ': ['GND2', 'VX']})
        ko = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('PADS·OrCAD 이름 유지', ko)
        self.assertIn('이름이 다른 넷 2개: GND2, VX', ko)
        en = '\n'.join(explain_result(r, 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('2 nets differ: GND2, VX', en)

    def _hidden(self, mode, **kw):
        rem = [{'name': f'SPI_MOSI{i}', 'at': (1.0, 2.0), 'kind': k, 'alias': 'ALT' if k == 'renamed' else None,
                'sheet': 'P01_MAIN'} for i, k in enumerate(['alias', 'alias', 'wire', 'renamed', 'free', 'alias'])]
        info = {'mode': mode, 'remaining': rem, 'applied': 0, 'verified': None, 'reverted': False}
        info.update(kw)
        return self._r(net_names='kicad', hidden_labels=info)

    def test_hidden_labels_none_in_edif_mode(self):
        self.assertEqual(_hidden_label_lines(self._r(), 'ko'), [])
        self.assertNotIn('숨은 라벨', '\n'.join(explain_result(self._r(), 'ko')))

    def test_hidden_labels_nothing_left(self):
        r = self._r(hidden_labels={'mode': 'keep', 'remaining': [], 'applied': 0, 'verified': None,
                                   'reverted': False})
        self.assertIn('남은 것 없음', '\n'.join(explain_result(r, 'ko')))
        self.assertIn('none left', '\n'.join(explain_result(r, 'en')))

    def test_hidden_labels_keep_hints_alternatives(self):
        r = self._hidden('keep')
        ko = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('숨은 라벨: 6개 남음', ko)
        self.assertIn('실제 연결', ko)
        self.assertIn('SPI_MOSI0 (P01_MAIN)', ko)
        self.assertNotIn('SPI_MOSI5', ko)                 # 5개까지만
        self.assertIn('외 1개', ko)
        self.assertIn("('ALT')", ko)
        self.assertIn('show', ko)
        self.assertIn('shrink', ko)
        en = '\n'.join(explain_result(r, 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('hidden labels: 6 left', en)
        self.assertIn('--hidden-labels show', en)
        self.assertIn('and 1 more', en)

    def test_hidden_labels_show_shrink_reverted(self):
        ko = '\n'.join(explain_result(self._hidden('show', applied=6, verified=True), 'ko'))
        self.assertIn('6개를 보이는 전역 라벨로 바꿈', ko)
        self.assertNotIn('원복', ko)
        ko = '\n'.join(explain_result(self._hidden('shrink', applied=6, verified=True), 'ko'))
        self.assertIn('0.01 mm', ko)
        self.assertIn('보이는 라벨이 없게', ko)
        en = '\n'.join(explain_result(self._hidden('shrink', applied=6, verified=True), 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('shrunk to 0.01 mm', en)
        r = self._hidden('show', verified=False, reverted=True)
        self.assertIn('원복함', '\n'.join(explain_result(r, 'ko')))
        self.assertIn('reverted', '\n'.join(explain_result(r, 'en')))

    def test_hidden_labels_kept_by_net_names_keep(self):
        r = self._hidden('keep')
        r.net_names = 'keep'
        self.assertIn('일부러 남김', '\n'.join(explain_result(r, 'ko')))

    def test_pads_mode_and_reverted(self):
        ko = '\n'.join(explain_result(self._r(board_sync={'mode': 'pads'}), 'ko'))
        self.assertIn('보드 넷 이름: PADS 그대로 유지', ko)
        r = self._r(net_names='keep', localize={'removed': 0, 'renamed': {}, 'reverted': True})
        self.assertIn('원복함', '\n'.join(explain_result(r, 'ko')))


class ExplainCleanupExtrasTest(unittest.TestCase):
    """숨은 라벨 이름 충돌 경고 / 전원 심볼 배치 / 정리 guard 후퇴 / 중복 별칭 라벨 제거."""

    def _r(self, **kw):
        r = PipelineResult(options=PipelineOptions(dsn='X/B.DSN', outdir='X/o'), input_mode='dsn')
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def _info(self, **kw):
        info = {'mode': 'keep', 'remaining': [], 'applied': 0, 'verified': None, 'reverted': False,
                'conflicts': [], 'power_symbols_placed': []}
        info.update(kw)
        return info

    def _conflicts(self, n):
        return [{'name': f'AVCC_5V{i}', 'others': ['AVCC_3.3V'], 'at': (1.0, 2.0), 'with': 'power',
                 'sheet': 'P01'} for i in range(n)]

    def test_conflict_warning_ko_en_and_attention(self):
        r = self._r(hidden_labels=self._info(conflicts=self._conflicts(6)))
        ko = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('숨은 라벨 이름 충돌 6건', ko)
        self.assertIn('AVCC_5V0 = AVCC_3.3V (P01)', ko)
        self.assertNotIn('AVCC_5V5 =', ko)              # 5건까지만
        self.assertIn('외 1건', ko)
        self.assertIn('Edit > Find', ko)
        self.assertIn('--hidden-labels show', ko)
        self.assertIn('이미 이름을 둘', ko)
        self.assertIn('결론: 확인할 항목 있음', ko)
        self.assertTrue(attention_items(r, 'ko'))
        en = '\n'.join(explain_result(r, 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('ONE net', en)
        self.assertIn('AVCC_5V0 = AVCC_3.3V (P01)', en)
        self.assertIn('items to check', en)

    def test_conflict_shown_even_without_remaining_and_none_is_quiet(self):
        quiet = '\n'.join(explain_result(self._r(hidden_labels=self._info()), 'ko'))
        self.assertNotIn('충돌', quiet)
        self.assertEqual(attention_items(self._r(hidden_labels=self._info()), 'ko'), [])

    def test_power_symbols_placed(self):
        placed = [{'sheet': 'P01', 'name': 'GND', 'at': (1, 2), 'ref': 'U1'}]
        r = self._r(hidden_labels=self._info(power_symbols_placed=placed))
        ko = '\n'.join(explain_result(r, 'ko'))
        self.assertIn('전원 심볼 배치', ko)
        self.assertIn('GND@P01', ko)
        en = '\n'.join(explain_result(r, 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('power symbols placed: 1', en)

    def test_guard_fallback_and_revert(self):
        fb = self._r(import_cleanup={'guard': {'level': 'no label snapping', 'verified': True, 'tried': ['full', 'no label snapping']}})
        ko = '\n'.join(explain_result(fb, 'ko'))
        self.assertIn('물러나 적용', ko)
        self.assertNotIn('전부 되돌림', ko)
        self.assertEqual(attention_items(fb, 'ko'), [])
        self.assertIn('fell back', '\n'.join(explain_result(fb, 'en')))
        rv = self._r(import_cleanup={'guard': {'level': None, 'verified': False, 'tried': ['full']}})
        self.assertIn('전부 되돌림', '\n'.join(explain_result(rv, 'ko')))
        self.assertTrue(attention_items(rv, 'ko'))
        full = self._r(import_cleanup={'guard': {'level': 'full', 'verified': True, 'tried': ['full']}})
        self.assertNotIn('임포트 정리', '\n'.join(explain_result(full, 'ko')))
        self.assertNotIn('임포트 정리', '\n'.join(explain_result(self._r(import_cleanup={'guard': None}), 'ko')))

    def test_duplicate_alias_removed(self):
        self.assertNotIn('중복 별칭', '\n'.join(explain_result(self._r(), 'ko')))
        r = self._r(duplicate_alias_removed=4)
        self.assertIn('중복 별칭 라벨: 같은 배선', '\n'.join(explain_result(r, 'ko')))
        self.assertIn('4 local alias labels', '\n'.join(explain_result(r, 'en')))
        self.assertIn('중복 별칭 라벨', '\n'.join(explain_result(self._r(duplicate_alias_removed=0), 'ko')))

    def test_local_kind_text(self):
        rem = [{'name': 'X', 'at': (0, 0), 'kind': 'local', 'alias': None, 'sheet': 'S'}]
        r = self._r(hidden_labels=self._info(remaining=rem))
        self.assertIn('숨은 지역 라벨', '\n'.join(explain_result(r, 'ko')))


class AutoChangeNoticeTest(unittest.TestCase):
    """자동 변경은 사용자가 확실히 알도록: 목록(_auto_change_items) → 요약 위쪽 + 결론 꼬리 + auto_changes.txt."""

    def _r(self, cleanup, **kw):
        r = PipelineResult(options=PipelineOptions(dsn='X/B.DSN', outdir='X/o'), input_mode='dsn')
        r.import_cleanup = cleanup
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def test_items_and_explain(self):
        from orcad2kicad.pipeline import _auto_change_items
        cl = {'offpage_converted': 3, 'gaps_merged': 0, 'pin_numbers_fitted': 5, 'worksheet': 'w.kicad_wks',
              'dedupe': {'removed': 2}, 'offpage_unconverted': [
                  {'text': 'MOTOR_DRV_CS1', 'net': 'AIN1', 'at': (1.0, 2.0), 'sheet': 'P06'}]}
        r = self._r(cl, localize={'removed': 4, 'renamed': {'A': '/S/A'}, 'reverted': False})
        items = _auto_change_items(r)
        keys = [i['key'] for i in items]
        self.assertEqual(keys, ['offpage', 'pin_numbers', 'dedupe', 'worksheet', 'localize', 'nets_renamed'])
        self.assertNotIn('hop_gaps', keys)                    # 0 개는 뺀다
        r.auto_changes = items
        ko = explain_result(r, 'ko')
        self.assertIn('자동 변경: 도구가 회로도·보드를 6가지로 직접 고침', ko[1])   # 입력 바로 아래
        text = '\n'.join(ko)
        self.assertIn("'MOTOR_DRV_CS1'=AIN1", text)
        self.assertIn('위 "자동 변경" 목록을 확인할 것', ko[-1])
        self.assertTrue(attention_items(r, 'ko'))            # 그대로 둔 커넥터는 확인 항목
        en = '\n'.join(explain_result(r, 'en'))
        self.assertTrue(en.isascii(), en)
        self.assertIn('automatic changes', en)

    def test_report_file(self):
        from orcad2kicad.pipeline import _report_auto_changes
        with tempfile.TemporaryDirectory() as d:
            r = PipelineResult(options=PipelineOptions(dsn='X/B.DSN', outdir=d), input_mode='dsn')
            r.import_cleanup = {'offpage_converted': 1, 'offpage_unconverted': []}
            logs = []
            _report_auto_changes(r.options, r, logs.append)
            self.assertTrue(logs[0].startswith('AUTO CHANGES:'))
            self.assertTrue(all(l.isascii() for l in logs))
            with open(os.path.join(d, 'auto_changes.txt'), encoding='utf-8-sig') as fh:
                body = fh.read()
            self.assertIn('오프페이지 커넥터 그림', body)
            self.assertEqual(r.files['auto_changes'], os.path.join(d, 'auto_changes.txt'))

    def test_bus_split_is_announced_first(self):
        """버스 접속점 삭제는 연결을 바꾸므로 목록 맨 앞 + 나뉜 넷을 경고·이슈·파일·요약에 모두 적는다."""
        from orcad2kicad.pipeline import _auto_change_items, _report_auto_changes
        with tempfile.TemporaryDirectory() as d:
            r = PipelineResult(options=PipelineOptions(dsn='X/B.DSN', outdir=d), input_mode='dsn')
            r.import_cleanup = {'offpage_converted': 1, 'offpage_unconverted': [], 'bus_split': {
                'removed': 7, 'reverted': False, 'verified': True,
                'splits': [{'net': 'D1', 'into': ['D1', 'D2', 'D3']}]}}
            self.assertEqual([i['key'] for i in _auto_change_items(r)], ['bus_junctions', 'offpage'])
            logs = []
            _report_auto_changes(r.options, r, logs.append)
            self.assertTrue(all(l.isascii() for l in logs))
            self.assertIn('SPLIT', logs[0])
            self.assertTrue(any(i.startswith("WARNING net 'D1' split into D1, D2, D3") for i in r.issues), r.issues)
            with open(os.path.join(d, 'auto_changes.txt'), encoding='utf-8-sig') as fh:
                body = fh.read()
            self.assertIn('[나뉜 넷 - 연결이 바뀜] 1개', body)
            self.assertIn('- D1 -> D1, D2, D3', body)
            self.assertIn('연결이 바뀐 넷 1개(D1->3)', '\n'.join(explain_result(r, 'ko')))
            self.assertTrue('\n'.join(explain_result(r, 'en')).isascii())

    def test_reverted_bus_split_is_not_listed(self):
        from orcad2kicad.pipeline import _auto_change_items
        r = self._r({'bus_split': {'removed': 0, 'reverted': True, 'verified': False, 'splits': []}})
        self.assertEqual(_auto_change_items(r), [])

    def test_nothing_changed_is_quiet(self):
        from orcad2kicad.pipeline import _report_auto_changes
        r = self._r({})
        logs = []
        _report_auto_changes(r.options, r, logs.append)
        self.assertEqual(logs, [])
        self.assertEqual(r.auto_changes, [])
