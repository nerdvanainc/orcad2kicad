"""explain.explain_result — 결과를 사람 말로 푸는 요약 설명(합성 결과로 확인)."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from orcad2kicad.pipeline import PipelineOptions, PipelineResult, result_to_json
from orcad2kicad.verify import NetCompare, NetDiff
from orcad2kicad.kicad_board import BoardDiff
from orcad2kicad.explain import (explain_result, format_explanation, kicad_gui_next_to, STABLE_SCH_CEILING,
                                 attention_items)


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
