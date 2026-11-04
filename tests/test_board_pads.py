"""PADS 보드 임포트 결과의 바닥층 전용 패드 복원(합성 데이터).

배경(2026-11-04 실측): 패드 스택이 윗면 크기 0, 바닥면 크기 > 0 인 SMD 패드(핫스왑 소켓처럼 윗면 부품의 패드가
뒷면에만 있는 경우)를 KiCad PADS 임포터가 윗면 크기만 읽어 F.Cu 위 0.001 mm 패드로 만든다. 구리가 사라져 그 패드로
오는 배선이 끊겨 보이는데, 넷 연결표에는 이상이 없어서 [4] 는 PASS 다. 같은 0.001 크기로 나오는 무도금 홀과
구멍만 있는 스루홀 패드는 정상이므로 건드리면 안 된다.
"""
import unittest

from orcad2kicad.kicad_board import fix_bottom_only_pads, pads_bottom_only_pins


def _asc(unit='METRIC', size='2.55', extra=''):
    return (f'!PADS-POWERPCB-V10.0-{unit}! DESIGN DATABASE ASCII FILE 1.0\n'
            '\n*PARTDECAL*  ITEMS\n\n'
            'SOCKET M 0 0 1 5 3 0 1\n'
            'OPEN 2 0.1 0 26\n'
            '0 0\n'
            '1 1\n'
            'T-7 2 -7 2 1\n'
            'T6 5 6 5 2\n'
            'T-3 2 -3 2 3\n'
            'T-5 0 -5 0 4\n'
            'T5 0 5 0 A5\n'
            'PAD 0 3\n'
            f'-2 0 S 0 0\n'
            '-1 0 S 0\n'
            f'0 {size} S 0\n'
            'PAD 3 3\n'
            '-2 2.54 R 3\n'
            '-1 2.54 R\n'
            '0 2.54 R\n'
            'PAD 4 3\n'
            '-2 0 R 1.8 N\n'
            '-1 0 R\n'
            '0 0 R\n'
            + extra +
            '\nNORMAL M 0 0 0 1 1 0 0\n'
            'T0 0 0 0 1\n'
            'PAD 0 3\n'
            '-2 1 R 0\n'
            '-1 0 R\n'
            '0 0 R\n'
            '\n*PARTTYPE*   ITEMS\n')


def _pad(name, kind, shape, size='0.001 0.001', layers='"F.Cu" "F.Mask" "F.Paste"', drill=None, net=None):
    lines = [f'\t\t(pad "{name}" {kind} {shape}', '\t\t\t(at 1 2 270)', f'\t\t\t(size {size})']
    if drill:
        lines.append(f'\t\t\t(drill {drill})')
    lines.append(f'\t\t\t(layers {layers})')
    if net:
        lines.append(f'\t\t\t(net "{net}")')
    lines.append('\t\t\t(uuid "00000000-0000-0000-0000-000000000000")')
    lines.append('\t\t)')
    return '\n'.join(lines)


def _fp(name, side, pads):
    return '\n'.join([f'\t(footprint "{name}"', f'\t\t(layer "{side}")'] + pads + ['\t)'])


def _pcb(*footprints):
    return '(kicad_pcb\n' + '\n'.join(footprints) + '\n)\n'


class BottomOnlyPinsTest(unittest.TestCase):
    def test_default_stack_applies_only_to_pins_without_their_own(self):
        pins, _ = pads_bottom_only_pins(_asc())
        # 1, 2, A5 는 PAD 0 을 따른다. 3(스루홀)과 4(무도금 홀)는 자기 정의가 있다.
        self.assertEqual(sorted(pins['SOCKET']), ['1', '2', 'A5'])
        self.assertEqual(pins['SOCKET']['1'], ('rect', 2.55))

    def test_decal_with_copper_on_top_is_not_listed(self):
        pins, _ = pads_bottom_only_pins(_asc())
        self.assertNotIn('NORMAL', pins)

    def test_size_uses_file_unit(self):
        pins, _ = pads_bottom_only_pins(_asc('BASIC', size='1905000'))
        self.assertAlmostEqual(pins['SOCKET']['1'][1], 1.27, places=6)   # 1905000 / 38100 mil

    def test_decal_unit_letter_wins_outside_basic_files(self):
        # PADS 는 BASIC 이 아닌 파일에서 데칼 값을 데칼 자기 단위로 쓴다. 실측: 같은 보드를 MILS·INCHES 로
        # 내보내도 M 데칼의 패드는 2.55 그대로였다.
        for unit in ('MILS', 'INCHES', 'METRIC'):
            pins, _ = pads_bottom_only_pins(_asc(unit))
            self.assertAlmostEqual(pins['SOCKET']['1'][1], 2.55, places=6, msg=unit)
        pins, _ = pads_bottom_only_pins(_asc('METRIC', size='100').replace('SOCKET M ', 'SOCKET I '))
        self.assertAlmostEqual(pins['SOCKET']['1'][1], 2.54, places=6)

    def test_round_pad_becomes_circle(self):
        asc = _asc().replace('0 2.55 S 0\n', '0 2.55 R\n').replace('-2 0 S 0 0\n', '-2 0 R 0\n')
        pins, _ = pads_bottom_only_pins(asc)
        self.assertEqual(pins['SOCKET']['1'], ('circle', 2.55))

    def test_drilled_stack_is_not_bottom_only(self):
        pins, _ = pads_bottom_only_pins(_asc().replace('-2 0 S 0 0\n', '-2 0 S 0 1.2\n'))
        self.assertNotIn('SOCKET', pins)

    def test_inner_copper_means_not_bottom_only(self):
        pins, _ = pads_bottom_only_pins(_asc().replace('-1 0 S 0\n', '-1 1 S 0\n'))
        self.assertNotIn('SOCKET', pins)

    def test_explicit_extra_layer_means_not_bottom_only(self):
        asc = _asc().replace('PAD 0 3\n-2 0 S', 'PAD 0 4\n-2 0 S').replace('0 2.55 S 0\n', '0 2.55 S 0\n2 1 S 0\n')
        pins, _ = pads_bottom_only_pins(asc)
        self.assertNotIn('SOCKET', pins)

    def test_unsupported_shape_is_reported_not_guessed(self):
        asc = _asc().replace('0 2.55 S 0\n', '0 2.55 RF 0.000 3 0 0 0\n')
        pins, unsupported = pads_bottom_only_pins(asc)
        self.assertNotIn('SOCKET', pins)
        self.assertEqual(unsupported, {'SOCKET': 'RF'})

    def test_no_decal_section(self):
        self.assertEqual(pads_bottom_only_pins('!PADS-POWERPCB-V10.0-METRIC!\n'), ({}, {}))

    def test_unknown_unit_gives_nothing(self):
        self.assertEqual(pads_bottom_only_pins(_asc('UNKNOWN')), ({}, {}))


class FixBottomOnlyPadsTest(unittest.TestCase):
    def test_moves_pad_to_back_with_real_size(self):
        pcb = _pcb(_fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect', net='KEY')]))
        new, st = fix_bottom_only_pads(pcb, _asc())
        self.assertIn('(size 2.55 2.55)', new)
        self.assertIn('(layers "B.Cu" "B.Mask" "B.Paste")', new)
        self.assertNotIn('F.Cu" "F.Mask', new)
        self.assertIn('(at 1 2 270)', new)
        self.assertIn('(net "KEY")', new)
        self.assertEqual(st['moved'], 1)

    def test_round_pad_shape_is_rewritten(self):
        asc = _asc().replace('0 2.55 S 0\n', '0 2.55 R\n').replace('-2 0 S 0 0\n', '-2 0 R 0\n')
        new, _ = fix_bottom_only_pads(_pcb(_fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect')])), asc)
        self.assertIn('(pad "1" smd circle', new)

    def test_part_on_bottom_side_goes_to_front(self):
        pcb = _pcb(_fp('SOCKET', 'B.Cu', [_pad('2', 'smd', 'rect', layers='"B.Cu" "B.Mask" "B.Paste"')]))
        new, st = fix_bottom_only_pads(pcb, _asc())
        self.assertIn('(layers "F.Cu" "F.Mask" "F.Paste")', new)
        self.assertEqual(st['moved'], 1)

    def test_npth_and_drilled_pads_of_same_size_stay(self):
        pads = [_pad('', 'np_thru_hole', 'circle', layers='"*.Cu"', drill='1.8'),
                _pad('4', 'np_thru_hole', 'circle', layers='"*.Cu"', drill='1.8'),
                _pad('3', 'thru_hole', 'circle', layers='"*.Cu"', drill='1')]
        pcb = _pcb(_fp('SOCKET', 'F.Cu', pads))
        new, st = fix_bottom_only_pads(pcb, _asc())
        self.assertEqual(new, pcb)
        self.assertEqual(st['moved'], 0)

    def test_pad_with_real_size_stays(self):
        pcb = _pcb(_fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect', size='2 2')]))
        self.assertEqual(fix_bottom_only_pads(pcb, _asc())[0], pcb)

    def test_other_decal_with_same_pin_stays(self):
        pcb = _pcb(_fp('NORMAL', 'F.Cu', [_pad('1', 'smd', 'rect')]))
        self.assertEqual(fix_bottom_only_pads(pcb, _asc())[0], pcb)

    def test_library_prefix_is_ignored(self):
        pcb = _pcb(_fp('lib:SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect')]))
        self.assertEqual(fix_bottom_only_pads(pcb, _asc())[1]['moved'], 1)

    def test_pin_name_from_terminal_line(self):
        pcb = _pcb(_fp('SOCKET', 'F.Cu', [_pad('A5', 'smd', 'rect')]))
        self.assertEqual(fix_bottom_only_pads(pcb, _asc())[1]['moved'], 1)

    def test_second_run_changes_nothing(self):
        pcb = _pcb(_fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect'), _pad('2', 'smd', 'rect')]))
        once, st = fix_bottom_only_pads(pcb, _asc())
        self.assertEqual(st['moved'], 2)
        twice, st2 = fix_bottom_only_pads(once, _asc())
        self.assertEqual(twice, once)
        self.assertEqual(st2['moved'], 0)

    def test_text_outside_the_pad_is_untouched(self):
        keep = '\t(gr_text "(size 0.001 0.001)"\n\t)'
        pcb = _pcb(keep, _fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect')]), keep)
        new, _ = fix_bottom_only_pads(pcb, _asc())
        self.assertEqual(new.count(keep), 2)

    def test_unsupported_shape_is_counted(self):
        asc = _asc().replace('0 2.55 S 0\n', '0 2.55 RF 0.000 3 0 0 0\n')
        pcb = _pcb(_fp('SOCKET', 'F.Cu', [_pad('1', 'smd', 'rect')]))
        new, st = fix_bottom_only_pads(pcb, asc)
        self.assertEqual(new, pcb)
        self.assertEqual(st['unsupported'], {'SOCKET': 'RF'})


if __name__ == '__main__':
    unittest.main()
