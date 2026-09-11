"""제품·회사 표기 문구 — 딱 이 파일 한 곳에서만 관리한다(CLI/GUI/MCP/문서 생성기가 공유).

콘솔에 찍히는 문자열(`CLI_BANNER`)은 ASCII만 쓴다(cp949 콘솔에서 깨지지 않도록).
"""
from __future__ import annotations

VERSION = '1.0.1'
COMPANY = 'Nerdvana Inc.'
SITE_URL = 'https://www.nerdvana.co.kr'
SITE_SHORT = 'www.nerdvana.co.kr'
GITHUB_URL = 'https://github.com/nerdvanainc/orcad2kicad'
PRODUCT = 'orcad2kicad'
TAGLINE_KO = 'OrCAD 회로도를 KiCad 프로젝트로 변환하고 검증하는 도구'
TAGLINE_EN = 'OrCAD schematic to KiCad converter with netlist verification'
CONTACT_KO = f'개발 의뢰·문의: {COMPANY} ({SITE_SHORT})'
CONTACT_EN = f'Contact / development inquiries: {COMPANY} ({SITE_URL})'
CLI_BANNER = f'{PRODUCT} {VERSION} - OrCAD to KiCad converter by {COMPANY} ({SITE_URL})'   # ASCII


def about_text(lang: str = 'ko') -> str:
    """GUI '정보'/'About' 창에 보여줄 여러 줄 텍스트(제품/버전/태그라인/회사/사이트/GitHub/
    라이선스 요약). `lang` 이 'en' 이 아니면(즉 기본값 포함 그 외 전부) 한국어다."""
    if lang == 'en':
        return '\n'.join([
            f'{PRODUCT} {VERSION}',
            TAGLINE_EN,
            '',
            f'Developed by: {COMPANY}',
            f'Site: {SITE_URL}',
            f'GitHub: {GITHUB_URL}',
            '',
            'License: MIT (open source, free for personal and commercial use). '
            'Source and releases are on GitHub.',
            'See the LICENSE file for details.',
        ])
    return '\n'.join([
        f'{PRODUCT} {VERSION}',
        TAGLINE_KO,
        '',
        f'개발: {COMPANY}',
        f'사이트: {SITE_URL}',
        f'GitHub: {GITHUB_URL}',
        '',
        '라이선스: MIT (오픈소스, 개인·상업 사용 무료). 소스와 릴리스는 GitHub 에 있습니다.',
        '자세한 내용은 LICENSE 파일 참조.',
    ])


def about_text_ko() -> str:
    """`about_text('ko')` 의 얇은 래퍼(기존 호출부 호환용)."""
    return about_text('ko')
