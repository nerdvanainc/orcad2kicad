"""PADS 바이너리 `.pcb` 보드는 읽지 않고 안내 메시지로 깔끔히 끝난다(샘플 파일 불필요)."""
import os
import tempfile
import unittest

from orcad2kicad.mcp_server import Server, ToolError
from orcad2kicad.pipeline import PipelineOptions, classify_dropped, run_pipeline

EXPECT = ('cannot read board: x.pcb is a PADS binary file. Export ASCII in PADS Layout '
          '(File > Export > ASCII) and give the .asc file.')


class PcbRejectedTest(unittest.TestCase):
    def test_pipeline_rejects_pcb_any_case(self):
        for name in ('x.pcb', 'x.PCB'):
            res = run_pipeline(PipelineOptions(edf='in.edf', board='C:/in/' + name))
            self.assertEqual(res.exit_code, 2)
            self.assertEqual(res.error, EXPECT.replace('x.pcb', name))

    def test_mcp_board_diff_raises_tool_error(self):
        srv = Server()
        with self.assertRaises(ToolError) as cm:
            srv.call_tool('board_diff', {'edf': 'in.edf', 'board': 'C:/in/x.pcb'})
        self.assertIn(EXPECT, str(cm.exception))

    def test_dropped_pcb_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            pcb = os.path.join(tmp, 'b.pcb')
            with open(pcb, 'wb') as f:
                f.write(b'\x00\x01binary')
            out = classify_dropped([pcb])
        self.assertIsNone(out['board'])
        self.assertTrue(any(p.endswith('b.pcb') for p in out['ignored']))


if __name__ == '__main__':
    unittest.main()
