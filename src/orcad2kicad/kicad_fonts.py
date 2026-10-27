"""한글 문자열에 한글 글꼴 지정 — KiCad 파일 텍스트를 문자열 수준 최소 수정으로 고친다.

KiCad 기본 스트로크 글꼴(face 없음)은 한글 글리프가 없어 한글을 **그리지 않고**(EDIF 경로에서
'TVS Diode는 커넥터 근처로 위치' 가 'TVS Diode' 로만 보임), 'Arial' 같은 영문 외곽선 글꼴은 한글을
**네모(□)** 로 그린다(.DSN 경로, 임포터가 OrCAD 글꼴 이름을 그대로 옮김). 한글이 든 문자열의
`(effects (font …))` 에 한글 글꼴(기본 'Malgun Gothic' = 맑은 고딕, Windows 기본 탑재)을 지정하면
보인다(2026-10-27 사용자 보고, kicad-cli SVG 렌더링으로 확인).

대상 블록: text·label·global_label·hierarchical_label·text_box·property·pin 의 name/number — 머리
문자열(첫 자식 블록 앞의 문자열들, property 는 이름과 값 둘 다)에 한글이 있고 직계 `effects` →
직계 `font` 가 있으면, `face` 가 있으면 그 이름을 바꾸고 없으면 `(font` 바로 뒤에 넣는다. 그 밖의
바이트는 건드리지 않는다. 의존성 없는 모듈(kicad_writer·kicad_cleanup 양쪽에서 쓴다).
"""
from __future__ import annotations

import re

__all__ = ['DEFAULT_KOREAN_FONT', 'apply_korean_font', 'has_hangul']

DEFAULT_KOREAN_FONT = 'Malgun Gothic'

_HEAD_RE = re.compile(r'\((text|label|global_label|hierarchical_label|text_box|property|name|number)\s')
_HANGUL_RE = re.compile('[ᄀ-ᇿ㄰-㆏가-힣]')
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def has_hangul(s: str) -> bool:
    return bool(_HANGUL_RE.search(s))


def _skip_string(text, i):
    """text[i] == '"' 일 때 닫는 따옴표 다음 위치."""
    i += 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    return n


def _block_end(text, i):
    """text[i] == '(' 인 블록의 끝(닫는 괄호 다음) 위치. 문자열 안 괄호는 세지 않는다."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _direct_children(text, s, e):
    """블록 [s, e) 의 직계 자식 블록 (이름, 시작, 끝) 목록."""
    out = []
    i = s + 1
    while i < e - 1:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == '(':
            end = _block_end(text, i)
            m = re.match(r'\(([A-Za-z_][A-Za-z0-9_]*)', text[i:i + 64])
            out.append((m.group(1) if m else '', i, end))
            i = end
            continue
        i += 1
    return out


def _head_strings(text, s, e):
    """블록의 첫 자식 블록 앞에 있는 문자열들."""
    kids = _direct_children(text, s, e)
    head_end = kids[0][1] if kids else e
    return [m.group(1) for m in _STRING_RE.finditer(text, s, head_end)]


def apply_korean_font(text: str, face: str = DEFAULT_KOREAN_FONT) -> tuple[str, int]:
    """한글이 든 문자열 블록의 글꼴을 `face` 로 지정한다. 반환값: (새 텍스트, 바꾼 블록 수).

    `face` 가 비어 있으면 아무것도 하지 않는다. 이미 `face` 이면 건드리지 않으므로 멱등이다."""
    if not face or not _HANGUL_RE.search(text):
        return text, 0
    qface = '"' + face.replace('\\', '\\\\').replace('"', '\\"') + '"'
    edits = []                         # (시작, 끝, 대체 문자열)
    for m in _HEAD_RE.finditer(text):
        s = m.start()
        # 문자열 안에서 우연히 일치한 경우를 거른다: 앞 글자가 공백/여는 괄호/줄 시작이어야 한다
        if s > 0 and text[s - 1] not in ' \t\r\n(':
            continue
        e = _block_end(text, s)
        if not any(_HANGUL_RE.search(h) for h in _head_strings(text, s, e)):
            continue
        eff = next(((cs, ce) for name, cs, ce in _direct_children(text, s, e) if name == 'effects'), None)
        if eff is None:
            continue
        font = next(((cs, ce) for name, cs, ce in _direct_children(text, *eff) if name == 'font'), None)
        if font is None:
            continue
        face_blk = next(((cs, ce) for name, cs, ce in _direct_children(text, *font) if name == 'face'), None)
        if face_blk is not None:
            cur = _STRING_RE.search(text, *face_blk)
            if cur is None or cur.group(1) == face:
                continue
            edits.append((cur.start(), cur.end(), qface))
        else:
            pos = font[0] + len('(font')
            edits.append((pos, pos, f' (face {qface})'))
    if not edits:
        return text, 0
    edits.sort()
    out = []
    pos = 0
    for a, b, rep in edits:
        out.append(text[pos:a])
        out.append(rep)
        pos = b
    out.append(text[pos:])
    return ''.join(out), len(edits)
