"""PADS 보드 임포트 결과의 솔더마스크 정리(합성 데이터): CAM 문서 읽기, 패드 마스크 개구 복원, 임포터 고정 여유값.

배경(2026-10-29 실측): KiCad PADS 임포터는 CAM 의 "솔더마스크에 패드 포함" 설정을 읽지 않아 스루홀 패드의 개구가
빠지거나 한쪽만 남고, 보드 여유값에는 숫자 3 을 파일 단위 그대로 넣는다(METRIC 3 mm, BASIC 0.000002 mm).
"""
import unittest

from orcad2kicad.kicad_board import (fix_importer_mask_clearance, mask_clearance_from_cam, move_spurious_mask_graphics,
                                     pads_mask_cam, pads_missing_mask, pads_spurious_mask_layers,
                                     restore_pad_mask_openings)


def _cam_doc(name, fab, layers, pads, vias, oversize):
    return (f'CAM_DOC_NAME {name}\n{{\n'
            'DOC_PLOT_TYPE Solder_Mask\n'
            f'DOC_LAYER_NUMBERS {layers}\n'
            f'DOC_FABRICATION_LAYER_NUMBER {fab}\n'
            f'DOC_PADS {pads}\n'
            f'DOC_VIAS {vias}\n'
            f'DOC_PAD_OVERSIZE {oversize}\n'
            '}\n')


def _asc(unit, top_vias='None None', bottom_vias='None Black', oversize='381000'):
    return (f'!PADS-POWERPCB-V10.0-{unit}! DESIGN DATABASE ASCII FILE 1.0\n'
            'LAYER 21\n{\nLAYER_NAME Solder Mask Top\nLAYER_TYPE SOLDER_MASK\n}\n'
            'LAYER 28\n{\nLAYER_NAME Solder Mask Bottom\nLAYER_TYPE SOLDER_MASK\n}\n'
            + _cam_doc('SOLDERMASK_TOP', 21, '1 21 0 0', 'Black None None None', top_vias + ' None None', oversize)
            + _cam_doc('SOLDERMASK_BOTTOM', 28, '4 28 0 0', 'Black Black None None', bottom_vias + ' None None',
                       oversize)
            + _cam_doc('TOP', 1, '1 0 0', 'Black None None', 'Black None None', '0').replace(
                'Solder_Mask', 'Routing'))


_PCB = '''(kicad_pcb
	(setup
		(pad_to_mask_clearance 3)
	)
	(footprint "PADS:CONN"
		(layer "F.Cu")
		(pad "1" thru_hole circle
			(at 0 0)
			(size 1.5 1.5)
			(drill 1)
			(layers "*.Cu")
			(net "A")
		)
		(pad "2" thru_hole circle
			(at 2.54 0)
			(size 1.5 1.5)
			(drill 1)
			(layers "*.Cu" "F.Mask")
		)
		(pad "3" smd rect
			(at 5 0)
			(size 1 1)
			(layers "F.Cu" "F.Paste")
		)
		(pad "4" smd rect
			(at 7 0)
			(size 1 1)
			(layers "B.Cu" "B.Mask" "B.Paste")
		)
		(pad "" np_thru_hole circle
			(at 9 0)
			(size 3 3)
			(drill 3)
			(layers "*.Cu")
		)
		(pad "5" thru_hole circle
			(at 11 0)
			(size 1.5 1.5)
			(drill 1)
			(layers "*.Cu" "*.Mask")
		)
	)
	(via
		(at 1 1)
		(size 0.6)
		(drill 0.3)
		(layers "F.Cu" "B.Cu")
		(tenting
			(front yes)
			(back yes)
		)
		(net "A")
	)
)
'''


class PadsMaskCamTest(unittest.TestCase):
    def test_sides_pads_vias_and_oversize(self):
        cam = pads_mask_cam(_asc('BASIC'))
        self.assertEqual(cam, {'top': {'pads': True, 'vias': False, 'oversize_mm': 0.254},
                               'bottom': {'pads': True, 'vias': False, 'oversize_mm': 0.254}})

    def test_via_column_is_the_copper_layer_only(self):
        """`DOC_VIAS None Black` = 마스크 레이어 자체 칸만 — 구리 비아는 안 그림(실측 PADS 거버에도 비아 없음)."""
        cam = pads_mask_cam(_asc('METRIC', top_vias='Black None', bottom_vias='None Black', oversize='0'))
        self.assertTrue(cam['top']['vias'])
        self.assertFalse(cam['bottom']['vias'])
        self.assertEqual(cam['top']['oversize_mm'], 0.0)

    def test_no_solder_mask_doc(self):
        self.assertEqual(pads_mask_cam('!PADS-POWERPCB-V10.0-BASIC!\nCAM_DOC_NAME X\n{\nDOC_PLOT_TYPE Routing\n}\n'), {})


class RestorePadMaskTest(unittest.TestCase):
    def test_restore(self):
        out, st = restore_pad_mask_openings(_PCB, {'top': {'pads': True}, 'bottom': {'pads': True}})
        self.assertEqual(st, {'pads_front': 2, 'pads_back': 2, 'vias_front': 0, 'vias_back': 0, 'npth': 1})
        self.assertIn('(layers "*.Cu" "F.Mask" "B.Mask")\n\t\t\t(net "A")', out)     # 1: 둘 다 없던 것
        self.assertIn('(layers "*.Cu" "F.Mask" "B.Mask")\n\t\t)', out)               # 2: 뒷면만 없던 것
        self.assertIn('(layers "F.Cu" "F.Paste" "F.Mask")', out)                      # 3: SMD 는 자기 면만
        self.assertIn('(layers "B.Cu" "B.Mask" "B.Paste")', out)                      # 4: 이미 있음
        self.assertIn('(layers "*.Cu" "*.Mask")', out)                                # 5: 와일드카드 그대로
        self.assertEqual(out.count('(layers "*.Cu")'), 1)                             # 무도금 홀은 그대로
        self.assertIn('(front yes)', out)                                             # 비아는 CAM 이 안 그리면 그대로
        # 바뀐 것은 layers 줄뿐
        changed = [(a, b) for a, b in zip(_PCB.split('\n'), out.split('\n')) if a != b]
        self.assertEqual(len(changed), 3)
        self.assertTrue(all('(layers' in a for a, _ in changed))
        self.assertEqual(restore_pad_mask_openings(out, {'top': {'pads': True}, 'bottom': {'pads': True}})[1]
                         ['pads_front'], 0)                                           # 멱등

    def test_side_without_pads_untouched_and_vias_opened(self):
        out, st = restore_pad_mask_openings(_PCB, {'top': {'pads': False, 'vias': True}})
        self.assertEqual((st['pads_front'], st['pads_back'], st['vias_front'], st['vias_back']), (0, 0, 1, 0))
        self.assertIn('(front no)', out)
        self.assertIn('(back yes)', out)

    def test_missing_report(self):
        self.assertEqual(pads_missing_mask(_PCB), {'front': 2, 'back': 2})


class ImporterClearanceTest(unittest.TestCase):
    def test_metric_constant_no_oversize(self):
        asc = _asc('METRIC', oversize='0')
        out, old, new = fix_importer_mask_clearance(_PCB, asc, pads_mask_cam(asc))
        self.assertEqual((old, new), (3.0, 0.0))
        self.assertIn('(pad_to_mask_clearance 0)', out)

    def test_basic_constant_zero_and_cam_value_for_notice(self):
        """오버사이즈가 있어도 0(사용자 결정 — 미세 피치 웹 보존). CAM 값의 쪽당 환산(0.254 -> 0.127)은 고지용."""
        pcb = _PCB.replace('(pad_to_mask_clearance 3)', '(pad_to_mask_clearance 0.000002)')
        asc = _asc('BASIC')
        cam = pads_mask_cam(asc)
        out, old, new = fix_importer_mask_clearance(pcb, asc, cam)
        self.assertAlmostEqual(old, 2e-6)
        self.assertEqual(new, 0.0)
        self.assertIn('(pad_to_mask_clearance 0)', out)
        self.assertEqual(mask_clearance_from_cam(cam), 0.127)

    def test_without_cam_resets_to_zero(self):
        out, old, new = fix_importer_mask_clearance(_PCB, '!PADS-POWERPCB-V10.0-METRIC!')
        self.assertEqual((old, new), (3.0, 0.0))

    def test_other_values_kept(self):
        pcb = _PCB.replace('(pad_to_mask_clearance 3)', '(pad_to_mask_clearance 0.05)')
        self.assertEqual(fix_importer_mask_clearance(pcb, '!PADS-POWERPCB-V10.0-METRIC!'), (pcb, None, None))
        self.assertEqual(fix_importer_mask_clearance(_PCB, 'not a pads header'), (_PCB, None, None))


def _asc_l25(l25_type='UNASSIGNED', cam_l25=False):
    """BASIC 단위(1 mil = 38100) — 디칼 QFP 의 레이어 25 외곽선(폭 1500 = 0.001 mm, ±10.1 mm 사각)과 레이어 26 실크."""
    cam_layers = '1 21 25' if cam_l25 else '1 21'
    return ('!PADS-POWERPCB-V10.0-BASIC! DESIGN DATABASE ASCII FILE 1.0\n'
            'LAYER 21\n{\nLAYER_NAME Solder Mask Top\nLAYER_TYPE SOLDER_MASK\n}\n'
            f'LAYER 25\n{{\nLAYER_NAME Layer_25\nLAYER_TYPE {l25_type}\n}}\n'
            'LAYER 28\n{\nLAYER_NAME Solder Mask Bottom\nLAYER_TYPE SOLDER_MASK\n}\n'
            '*PARTDECAL*  ITEMS\n\n*REMARK* NAME UNITS ORIX ORIY PIECES TERMINALS STACKS TEXT LABELS\n\n'
            'QFP M 0     0     2 0 0 0 0\n'
            'CLOSED 5   1500 0   25  \n'
            '-15150000 -15150000\n-15150000 15150000\n15150000 15150000\n15150000 -15150000\n'
            '-15150000 -15150000\n'
            'OPEN   2   75000 0   26  \n0 0\n0 750000\n'
            '\n*PARTTYPE*   ITEMS\n'
            + _cam_doc('SOLDERMASK_TOP', 21, cam_layers + ' 0', 'Black None None', 'None None None', '0'))


def _fp_line(x1, y1, x2, y2, layer, width='0.001'):
    return (f'\t\t(fp_line\n\t\t\t(start {x1} {y1})\n\t\t\t(end {x2} {y2})\n'
            f'\t\t\t(stroke\n\t\t\t\t(width {width})\n\t\t\t\t(type solid)\n\t\t\t)\n'
            f'\t\t\t(layer "{layer}")\n\t\t)\n')


_PCB_L25 = ('(kicad_pcb\n\t(footprint "QFP"\n\t\t(layer "F.Cu")\n'
            + _fp_line(-10.1, -10.1, 10.1, -10.1, 'F.Mask')
            + _fp_line(10.1, -10.1, 10.1, 10.1, 'F.Mask')
            + _fp_line(-3, -3, 3, -3, 'F.Mask')                     # 디칼에 없는 마스크 그림 — 그대로
            + _fp_line(-10.1, -10.1, 10.1, -10.1, 'F.Mask', '0.2')  # 좌표는 같지만 폭이 다름 — 그대로
            + _fp_line(0, 0, 0, -0.75, 'F.SilkS', '0.05')
            + '\t)\n\t(footprint "QFP"\n\t\t(layer "B.Cu")\n'      # 뒷면 배치 — 마스크도 뒷면, 좌표는 뒤집힘
            + _fp_line(-10.1, 10.1, 10.1, 10.1, 'B.Mask')
            + '\t)\n\t(footprint "OTHER"\n\t\t(layer "F.Cu")\n'
            + _fp_line(-10.1, -10.1, 10.1, -10.1, 'F.Mask')        # 다른 디칼 — 그대로
            + '\t)\n)\n')


class MaskLayerGraphicsTest(unittest.TestCase):
    """KiCad PADS 임포터는 종류가 UNASSIGNED 인 레이어 25/28 을 F.Mask/B.Mask 로 보낸다(실측 2026-10-29) —
    PADS 에서는 마스크와 무관한 문서용 그림이 KiCad 에서는 마스크 개구가 된다."""

    def test_spurious_layers(self):
        self.assertEqual(pads_spurious_mask_layers(_asc_l25()), {25: 'F.Mask'})
        self.assertEqual(pads_spurious_mask_layers(_asc_l25('SOLDER_MASK')), {})   # 진짜 마스크 레이어
        self.assertEqual(pads_spurious_mask_layers(_asc_l25(cam_l25=True)), {})    # CAM 이 25 를 마스크에 그림

    def test_moves_matching_decal_graphics_to_comments(self):
        out, st = move_spurious_mask_graphics(_PCB_L25, _asc_l25())
        self.assertEqual(st['moved'], 3)
        self.assertEqual(st['footprints'], 2)
        self.assertEqual(st['layers'], [25])
        self.assertEqual(out.count('(layer "Cmts.User")'), 3)
        self.assertIn('(start -3 -3)\n\t\t\t(end 3 -3)\n\t\t\t(stroke\n\t\t\t\t(width 0.001)\n\t\t\t\t(type solid)\n'
                      '\t\t\t)\n\t\t\t(layer "F.Mask")', out)
        self.assertIn('(width 0.2)\n\t\t\t\t(type solid)\n\t\t\t)\n\t\t\t(layer "F.Mask")', out)
        other = out[out.index('(footprint "OTHER"'):]
        self.assertIn('(layer "F.Mask")', other)
        self.assertEqual(len(out), len(_PCB_L25) + 3 * (len('Cmts.User') - len('F.Mask')))  # layer 토큰만 바뀜
        self.assertEqual(move_spurious_mask_graphics(out, _asc_l25())[1]['moved'], 0)          # 멱등

    def test_no_change_when_layer_is_real_mask(self):
        for asc in (_asc_l25('SOLDER_MASK'), _asc_l25(cam_l25=True)):
            out, st = move_spurious_mask_graphics(_PCB_L25, asc)
            self.assertEqual((out, st['moved']), (_PCB_L25, 0))


if __name__ == '__main__':
    unittest.main()
