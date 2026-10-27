"""보드 넷 이름을 회로도 넷리스트에 맞추기(--board-net-names schematic).

회로도가 KiCad 방식 이름(`/05-USB/RTS#`, `Net-(R36-Pad2)`)이면 PADS 이름 보드는 Update PCB 때 넷 이름이
바뀐다. 출력 보드의 넷 이름을 핀 집합이 같은 회로도 넷 이름으로 바꿔 두면 변경이 0 이 된다."""
import os
import shutil
import tempfile
import unittest

from orcad2kicad.kicad_board import board_net_mapping, rename_board_nets


class BoardNetMappingTest(unittest.TestCase):
    def test_exact_superset_and_ambiguous(self):
        board = {'RTS#': {'U6.14', 'Q5.2'},            # 핀 집합 일치 → 이름 바꿈
                 'GND': {'U6.1', 'C1.2'},               # 같은 이름 → 그대로
                 'N1': {'R1.1', 'H1.'},                # 회로도에 없는 패드(H1.) 하나 더 → 겹치는 넷 하나 → 바꿈
                 'X1': {'A.1', 'B.1'},                  # 겹치는 회로도 넷이 둘 → 애매 → 그대로
                 'Y': {'Z.9'}}                          # 회로도에 없음 → 그대로
        sch = {'/05-USB/RTS#': frozenset({'U6.14', 'Q5.2'}),
               'GND': frozenset({'U6.1', 'C1.2'}),
               'Net-(R1-Pad1)': frozenset({'R1.1', 'R2.1'}),
               'S1': frozenset({'A.1'}), 'S2': frozenset({'B.1'})}
        mapping, unmatched = board_net_mapping(board, sch)
        self.assertEqual(mapping, {'RTS#': '/05-USB/RTS#', 'N1': 'Net-(R1-Pad1)'})
        self.assertEqual(sorted(unmatched), ['X1', 'Y'])

    def test_two_board_nets_to_one_name_are_dropped(self):
        board = {'A': {'U1.1'}, 'B': {'U1.2'}}
        sch = {'S': frozenset({'U1.1', 'U1.2'})}
        mapping, unmatched = board_net_mapping(board, sch)
        self.assertEqual(mapping, {})
        self.assertEqual(sorted(unmatched), ['A', 'B'])


class RenameBoardNetsTest(unittest.TestCase):
    def test_renames_every_net_reference(self):
        d = tempfile.mkdtemp(prefix='o2k-bsync-')
        self.addCleanup(shutil.rmtree, d, True)
        pcb = os.path.join(d, 'b.kicad_pcb')
        with open(pcb, 'w', encoding='utf-8') as fh:
            fh.write('(kicad_pcb (version 20260206)\n'
                     '  (footprint "PADS:R" (pad "1" smd rect (net "RTS#")) (pad "2" smd rect (net "GND")))\n'
                     '  (segment (start 0 0) (end 1 0) (net "RTS#"))\n'
                     '  (via (at 1 0) (net "RTS#"))\n'
                     '  (zone (net "GND") (net_name "GND"))\n'
                     '  (gr_text "RTS#" (at 0 0))\n'
                     ')\n')
        n = rename_board_nets(pcb, {'RTS#': '/05-USB/RTS#', 'GND': 'GND'})
        self.assertEqual(n, 3)
        text = open(pcb, encoding='utf-8').read()
        self.assertEqual(text.count('(net "/05-USB/RTS#")'), 3)
        self.assertIn('(net "GND")', text)
        self.assertIn('(gr_text "RTS#"', text)                 # 넷이 아닌 글자는 그대로


if __name__ == '__main__':
    unittest.main()
