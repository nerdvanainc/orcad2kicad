"""`i18n.py`(GUI 두 언어 문자열 표) 회귀 테스트.

tkinter 는 전혀 쓰지 않으므로(디스플레이 없는 CI 러너에서도) 이 테스트 전부가 항상 돈다.
"""
import os
import unittest
from unittest import mock

from orcad2kicad import i18n


class StringTableTest(unittest.TestCase):
    """ko/en 두 표는 키 집합이 같고, 빈 값이 없고, en 은 전부 ASCII 여야 한다."""

    def test_key_sets_match(self):
        self.assertEqual(set(i18n.STRINGS['ko']), set(i18n.STRINGS['en']))

    def test_no_empty_values(self):
        for lang in ('ko', 'en'):
            for key, value in i18n.STRINGS[lang].items():
                self.assertTrue(value, f'{lang}.{key} is empty')

    def test_en_values_are_ascii(self):
        for key, value in i18n.STRINGS['en'].items():
            self.assertTrue(value.isascii(), f'en.{key} is not ASCII: {value!r}')

    def test_ko_and_en_differ_for_translated_keys(self):
        # backend_api/claude-cli/codex-cli 처럼 고유명사라 두 언어가 같은 값(둘 다 ASCII)인
        # 키를 빼고, 나머지 표본 키는 ko != en 이어야 한다(실제로 번역됐는지 확인).
        sample = ('status_idle', 'choice_ignore', 'window_title', 'btn_run', 'tab_log',
                 'verify_no_result', 'dlg_error_title', 'note_missing_edif')
        for key in sample:
            self.assertNotEqual(i18n.STRINGS['ko'][key], i18n.STRINGS['en'][key], key)


class TFunctionTest(unittest.TestCase):
    def setUp(self):
        self._old_lang = i18n.get_language()
        self.addCleanup(i18n.set_language, self._old_lang)

    def test_t_follows_current_language(self):
        i18n.set_language('ko')
        self.assertEqual(i18n.t('status_idle'), '대기 중')
        i18n.set_language('en')
        self.assertEqual(i18n.t('status_idle'), 'Idle')

    def test_t_formats_placeholders(self):
        i18n.set_language('en')
        text = i18n.t('status_done_exit', code=3)
        self.assertIn('3', text)

    def test_t_falls_back_to_key_for_unknown_key(self):
        i18n.set_language('en')
        self.assertEqual(i18n.t('no_such_key_xyz'), 'no_such_key_xyz')

    def test_t_does_not_raise_on_missing_format_args(self):
        i18n.set_language('en')
        # 'status_done_exit' expects {code}; omitting it must not raise.
        text = i18n.t('status_done_exit')
        self.assertIsInstance(text, str)

    def test_set_language_unknown_falls_back_to_default(self):
        i18n.set_language('fr')
        self.assertEqual(i18n.get_language(), i18n.DEFAULT_LANG)

    def test_get_language_default(self):
        i18n.set_language('ko')
        self.assertEqual(i18n.get_language(), 'ko')


class DetectDefaultLanguageTest(unittest.TestCase):
    """환경변수/로캘을 흉내 내 `detect_default_language()` 를 확인한다."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ('LC_ALL', 'LANG')}
        for k in ('LC_ALL', 'LANG'):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _detect(self, locale_value=None, windows_candidate=None):
        with mock.patch.object(i18n.locale, 'getlocale', return_value=(locale_value, 'UTF-8')):
            with mock.patch.object(i18n, '_windows_ui_lang_candidate', return_value=windows_candidate):
                return i18n.detect_default_language()

    def test_lang_env_korean(self):
        os.environ['LANG'] = 'ko_KR.UTF-8'
        self.assertEqual(self._detect(), 'ko')

    def test_lc_all_env_korean(self):
        os.environ['LC_ALL'] = 'ko_KR.UTF-8'
        self.assertEqual(self._detect(), 'ko')

    def test_lang_env_english(self):
        os.environ['LANG'] = 'en_US.UTF-8'
        self.assertEqual(self._detect(), 'en')

    def test_no_env_no_locale_defaults_english(self):
        self.assertEqual(self._detect(locale_value=None), 'en')

    def test_locale_getlocale_korean(self):
        self.assertEqual(self._detect(locale_value='ko_KR'), 'ko')

    def test_windows_ui_lang_korean(self):
        self.assertEqual(self._detect(windows_candidate='ko_KR'), 'ko')

    def test_windows_ui_lang_not_korean_does_not_force_english_over_env(self):
        os.environ['LANG'] = 'ko_KR.UTF-8'
        self.assertEqual(self._detect(windows_candidate=None), 'ko')

    def test_case_insensitive(self):
        os.environ['LANG'] = 'KO_KR.UTF-8'
        self.assertEqual(self._detect(), 'ko')


if __name__ == '__main__':
    unittest.main()
