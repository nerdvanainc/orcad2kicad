"""한글 문자열의 글꼴 지정(apply_korean_font).

KiCad 기본 스트로크 글꼴은 한글을 그리지 않고(EDIF 경로: 'TVS Diode는 …' 가 'TVS Diode' 로만 보임),
'Arial' 같은 영문 글꼴은 한글을 네모로 그린다(.DSN 경로). 한글이 든 문자열의 effects/font 에 한글
글꼴(기본 'Malgun Gothic')을 지정해야 보인다(2026-10-27 사용자 보고·렌더링 실측)."""
import unittest

from orcad2kicad.kicad_fonts import apply_korean_font, DEFAULT_KOREAN_FONT


class ApplyKoreanFontTest(unittest.TestCase):
    def test_replaces_and_inserts_face_only_for_hangul(self):
        src = ('(kicad_sch\n'
               '\t(text "TVS Diode는 커넥터 근처로 위치"\n'
               '\t\t(at 1 2 0)\n'
               '\t\t(effects\n\t\t\t(font\n\t\t\t\t(face "Arial")\n\t\t\t\t(size 1.7 1.51)\n\t\t\t)\n\t\t)\n'
               '\t)\n'
               '\t(text "모듈 CS" (at 1 2 0) (effects (font (size 1.98 1.98)) (justify left top)))\n'
               '\t(text "ASCII only" (at 1 2 0) (effects (font (face "Arial") (size 1 1))))\n'
               '\t(symbol (lib_id "x:R")\n'
               '\t\t(property "Value" "저항" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
               '\t\t(property "Reference" "R1" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
               '\t)\n'
               '\t(lib_symbols (symbol "x:R" (symbol "R_0_1" (pin passive line (at 0 0 0) (length 2)\n'
               '\t\t(name "입력" (effects (font (size 1.27 1.27))))\n'
               '\t\t(number "1" (effects (font (size 1.27 1.27))))))))\n'
               ')\n')
        out, n = apply_korean_font(src)
        self.assertEqual(n, 4)
        face = f'(face "{DEFAULT_KOREAN_FONT}")'
        self.assertIn(f'\t\t\t\t{face}\n\t\t\t\t(size 1.7 1.51)', out)           # Arial -> 한글 글꼴
        self.assertNotIn('(face "Arial")\n\t\t\t\t(size 1.7 1.51)', out)
        self.assertIn(f'(text "모듈 CS" (at 1 2 0) (effects (font {face} (size 1.98 1.98))', out)
        self.assertIn('(text "ASCII only" (at 1 2 0) (effects (font (face "Arial") (size 1 1))))', out)
        self.assertIn(f'(property "Value" "저항" (at 0 0 0) (effects (font {face} (size 1.27 1.27))))', out)
        self.assertIn('(property "Reference" "R1" (at 0 0 0) (effects (font (size 1.27 1.27))))', out)
        self.assertIn(f'(name "입력" (effects (font {face} (size 1.27 1.27))))', out)
        self.assertIn('(number "1" (effects (font (size 1.27 1.27))))', out)
        self.assertEqual(apply_korean_font(out), (out, 0))                    # 멱등

    def test_custom_face_and_disabled(self):
        src = '(kicad_sch (text "한글" (at 0 0 0) (effects (font (size 1 1)))))'
        out, n = apply_korean_font(src, 'NanumGothic')
        self.assertEqual(n, 1)
        self.assertIn('(font (face "NanumGothic") (size 1 1))', out)
        self.assertEqual(apply_korean_font(src, ''), (src, 0))                # 빈 이름 = 끔

    def test_block_without_effects_is_left_alone(self):
        src = '(kicad_sch (text "한글" (at 0 0 0)))'
        self.assertEqual(apply_korean_font(src), (src, 0))


if __name__ == '__main__':
    unittest.main()
