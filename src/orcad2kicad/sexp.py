"""Minimal S-expression tokenizer/parser for EDIF 2.0.0 and KiCad files."""
import re

# EDIF 2.0.0 원문은 백슬래시 이스케이프 규약이 없다(경로 문자열 등에 순수 리터럴 백슬래시가 그대로 나오고,
# 심지어 닫는 따옴표 바로 앞에 오기도 한다 - 예: 샘플 EDIF의 `(rename R_E_ "R\E\")`).
# 반면 KiCad가 쓰는 파일은 '\"'/'\\' 를 정식 이스케이프로 쓴다. 두 규약을 하나의 정규식으로 동시에
# 올바르게 처리할 수는 없으므로(따옴표 직전의 단일 백슬래시가 "이스케이프된 따옴표"인지 "종료 따옴표 앞의
# 리터럴 백슬래시"인지는 원문 규약을 모르면 구분 불가) parse()에 escape 옵션을 두어 호출부가 형식을 명시한다.
_tok_raw = re.compile(r'\s*(?:(\()|(\))|("[^"]*")|([^\s()"]+))', re.S)
_tok_esc = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))', re.S)
_esc = re.compile(r'\\(["\\])')

def _unescape(s):
    """따옴표 안 문자열의 이스케이프 해제. '\\"' -> '"', '\\\\' -> '\\' 만 처리한다(그 외 백슬래시는 그대로 둔다)."""
    return _esc.sub(r'\1', s)

def parse(text, escape=True):
    """S-식 텍스트 -> 트리(들의 리스트). escape=True(기본, KiCad 파일용)면 문자열 안의 '\\"'/'\\\\' 를
    이스케이프로 해석한다. escape=False(EDIF용)면 따옴표 사이 내용을 그대로 받아들인다(리터럴 백슬래시 허용)."""
    tok = _tok_esc if escape else _tok_raw
    stack = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = tok.match(text, pos)
        if not m:
            break
        pos = m.end()
        lp, rp, st, at = m.groups()
        if lp:
            stack.append([])
        elif rp:
            node = stack.pop()
            stack[-1].append(node)
        elif st is not None:
            content = st[1:-1]
            stack[-1].append(_unescape(content) if escape else content)
        elif at is not None:
            try:
                stack[-1].append(int(at))
            except ValueError:
                try:
                    stack[-1].append(float(at))
                except ValueError:
                    stack[-1].append(Sym(at))
    while len(stack) > 1:  # tolerate unbalanced input
        node = stack.pop()
        stack[-1].append(node)
    return stack[0]

class Sym(str):
    """Bare symbol (distinguished from quoted strings)."""
    __slots__ = ()
    def __repr__(self):
        return f"Sym({str.__str__(self)})"

def head(node):
    return node[0] if isinstance(node, list) and node and isinstance(node[0], Sym) else None

def children(node, name):
    return [c for c in node[1:] if isinstance(c, list) and head(c) == name]

def child(node, name):
    for c in node[1:]:
        if isinstance(c, list) and head(c) == name:
            return c
    return None

def walk(node, name):
    if isinstance(node, list):
        if head(node) == name:
            yield node
        for c in node:
            yield from walk(c, name)

def edif_name(x):
    """Resolve an EDIF nameDef: bare symbol, (rename SYM "orig") or (name SYM ...)."""
    if isinstance(x, list):
        h = head(x)
        if h == 'rename':
            return str(x[2]) if len(x) > 2 else str(x[1])
        if h == 'name':
            return edif_name(x[1])
        return str(x)
    s = str(x)
    return s

def edif_id(x):
    """EDIF internal identifier (the symbol used in refs)."""
    if isinstance(x, list):
        h = head(x)
        if h in ('rename', 'name'):
            return str(x[1])
    return str(x)

def _fmt_atom(a):
    """단일 아톰을 KiCad 스타일 텍스트로. Sym은 그대로, 실수는 고정소수점(최대 6자리, 뒤 0 제거),
    문자열은 백슬래시/따옴표를 이스케이프해 큰따옴표로 감싼다."""
    if isinstance(a, Sym):
        return str(a)
    if isinstance(a, bool):          # bool은 int의 서브클래스이므로 int보다 먼저 검사
        return 'yes' if a else 'no'
    if isinstance(a, int):
        return str(a)
    if isinstance(a, float):
        s = f'{a:.6f}'.rstrip('0').rstrip('.')
        return s if s not in ('', '-0') else '0'
    return '"' + str(a).replace('\\', '\\\\').replace('"', '\\"') + '"'

def to_text(node, indent=0):
    """S-식 트리 -> 텍스트. 한 노드의 아톰(맨 앞의 head 포함)은 한 줄에 쓰고, 그 뒤에 나오는
    자식 리스트는 다음 줄부터 탭으로 들여써서 붙인다 (KiCad 파일 스타일).

    들여쓰기는 가독성용일 뿐 구문에 영향은 없다: parse(to_text(t))[0] == t 만 보장하면 된다."""
    if not isinstance(node, list):
        return _fmt_atom(node)
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        i += 1
    head_line = '(' + ' '.join(_fmt_atom(a) for a in node[:i])
    rest = node[i:]
    if not rest:
        return head_line + ')'
    child_pad = '\t' * (indent + 1)
    lines = [head_line]
    for c in rest:
        lines.append(to_text(c, indent + 1) if isinstance(c, list) else child_pad + _fmt_atom(c))
    return '\n'.join(lines) + '\n' + '\t' * indent + ')'
