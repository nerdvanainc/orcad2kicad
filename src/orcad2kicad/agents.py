"""AI 에이전트: 백엔드 추상화 + 제안 생성. **AI 는 제안만 하고, 적용은 사용자가 결정한다.**

파이프라인 결과(`PipelineResult`)를 입력으로 `Suggestion` 목록을 만들고,
사용자가 GUI/CLI 에서 고른 것(`selected=True`)만 `apply_suggestions` 로 새 `PipelineOptions`
(=`Resolutions`)에 반영한 뒤 파이프라인을 다시 돌리는 구조다. 결정적 변환·검증은 백엔드가
하나도 없어도 그대로 동작해야 하므로, 이 모듈은 어디서도 예외로 파이프라인을 막지 않는다.

백엔드 종류(`make_backend(kind)`):
  'none'       -> None (에이전트 사용 안 함)
  'api'        -> Anthropic Messages API 직접 호출(표준 라이브러리 urllib). 키는 인자 또는
                  환경변수 ANTHROPIC_API_KEY.
  'claude-cli' -> 로컬에 설치된 Claude Code CLI(`claude -p ... --output-format json`).
                  API 키 없이 기존 구독 인증을 그대로 쓸 수 있는 경로.
  'codex-cli'  -> `codex exec ...` (실험적. 설치돼 있을 때만).

프로젝트 규칙상 런타임 의존성은 표준 라이브러리뿐이라 anthropic SDK 대신 urllib 로 HTTP 를
직접 호출한다. 콘솔/로그로 나가는 문자열은 전부 ASCII 영문이며, API 키는 어디에도 찍지 않는다.
"""
from __future__ import annotations
import copy
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .pipeline import PipelineOptions, Resolutions, result_to_json, summary

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_API_MODEL = 'claude-sonnet-5'
MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 120

# KiCad 심볼 핀의 전기 타입(그대로 .kicad_sym 에 들어간다). 이 목록 밖의 제안은 버린다.
KICAD_PIN_TYPES = ('input', 'output', 'bidirectional', 'tri_state', 'passive', 'free',
                   'unspecified', 'power_in', 'power_out', 'open_collector', 'open_emitter',
                   'no_connect')
# API 오류 본문에 되비칠 수 있는 키 형태 토큰(방어적 마스킹용)
_SK_TOKEN = re.compile(r'sk-ant-[A-Za-z0-9_\-]*')
PLACEHOLDER_PREFIX = 'PCB_ONLY_'    # 보드 전용 자리표시 심볼(원래 전부 passive 라 후보에서 뺀다)
MIN_IC_PINS = 8                       # 이 정도 핀부터 IC 로 보고 핀 타입을 묻는다


class AgentError(Exception):
    """백엔드 호출/응답 파싱 실패. run_agents 가 잡아 note 제안으로 기록한다."""


# ---------- 응답 텍스트에서 JSON 뽑기 ----------

def extract_json(text) -> dict:
    """모델 응답 텍스트에서 첫 번째 최상위 JSON 객체를 찾아 dict 로 돌려준다.

    코드펜스(```json ... ```)나 앞뒤 설명 문장이 섞여 있어도 되게 문자열 안의 중괄호를
    건너뛰며 균형을 맞춘다. 객체를 못 찾으면 AgentError."""
    if not isinstance(text, str):
        raise AgentError(f'not a text response: {type(text).__name__}')
    for start, ch in enumerate(text):
        if ch != '{':
            continue
        depth, in_str, esc = 0, False, False
        for end in range(start, len(text)):
            c = text[end]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:end + 1])
                    except ValueError:
                        break            # 이 시작 위치는 실패 - 다음 '{' 부터 다시
                    if isinstance(obj, dict):
                        return obj
                    break
    raise AgentError('no JSON object in response: ' + text.strip()[:200])


# ---------- 백엔드 ----------

class Backend:
    """백엔드 공통 인터페이스. complete 는 항상 dict 를 돌려주거나 AgentError 를 올린다."""
    name = 'backend'

    def complete(self, system: str, user: str, schema: dict = None,
                 timeout: int = DEFAULT_TIMEOUT) -> dict:
        raise NotImplementedError


class ApiBackend(Backend):
    """Anthropic Messages API 직접 호출(urllib). schema 는 프롬프트에 이미 적혀 있어 쓰지 않는다."""
    name = 'api'

    def __init__(self, api_key=None, model=None, url=API_URL, max_tokens=MAX_TOKENS):
        key = api_key or os.environ.get('ANTHROPIC_API_KEY') or ''
        if not key:
            raise AgentError('ANTHROPIC_API_KEY not set '
                             '(set the environment variable or fill the GUI API key field)')
        self._key = key
        self.model = model or DEFAULT_API_MODEL
        self.url = url
        self.max_tokens = max_tokens

    def _redact(self, text):
        """오류 메시지에 실릴 문자열에서 API 키를 지운다(키 자체 + sk-ant-... 형태 토큰)."""
        if not text:
            return text
        if self._key:
            text = text.replace(self._key, '***')
        return _SK_TOKEN.sub('***', text)

    def complete(self, system, user, schema=None, timeout=DEFAULT_TIMEOUT):
        body = {'model': self.model, 'max_tokens': self.max_tokens, 'system': system,
                'messages': [{'role': 'user', 'content': user}]}
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode('utf-8'), method='POST',
            headers={'x-api-key': self._key, 'anthropic-version': API_VERSION,
                     'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                pass
            # 오류 본문에 키가 그대로 되비쳐 오는 경우가 있어 지운 뒤에 올린다
            raise AgentError(f'api http {e.code}: {self._redact(detail)}')
        except (urllib.error.URLError, OSError) as e:
            raise AgentError(f'api request failed: {e}')
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AgentError('api response is not JSON: ' + raw[:200])
        for block in payload.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'text':
                return extract_json(block.get('text') or '')
        raise AgentError('api response has no text block: ' + raw[:200])


def find_claude_cli(exe=None):
    """Claude Code CLI 경로. 인자로 준 값이 우선(검증하지 않는다) -> PATH 탐색. 없으면 None.

    Windows 는 래퍼가 claude.cmd / claude.exe 로 설치될 수 있어 함께 찾는다."""
    if exe:
        return exe
    for name in ('claude', 'claude.cmd', 'claude.exe'):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_codex_cli(exe=None):
    if exe:
        return exe
    for name in ('codex', 'codex.cmd', 'codex.exe'):
        found = shutil.which(name)
        if found:
            return found
    return None


class ClaudeCliBackend(Backend):
    """로컬 `claude -p <prompt> --output-format json --max-turns 1` 호출.

    자식 프로세스 환경에서 CLAUDECODE / CLAUDE_CODE_ENTRYPOINT 를 지운다 — 이 도구가
    Claude Code 안에서 실행될 때 그 변수가 남아 있으면 자식이 중첩 세션으로 오해한다."""
    name = 'claude-cli'

    def __init__(self, exe=None, model=None):
        path = find_claude_cli(exe)
        if not path:
            raise AgentError('claude CLI not found in PATH')
        self.exe = path
        self.model = model

    def _args(self, prompt, schema):
        args = [self.exe, '-p', prompt, '--output-format', 'json', '--max-turns', '1']
        if schema:
            args += ['--json-schema', json.dumps(schema)]
        if self.model:
            args += ['--model', self.model]
        return args

    def complete(self, system, user, schema=None, timeout=DEFAULT_TIMEOUT):
        prompt = f'{system}\n\n{user}' if system else user
        env = {k: v for k, v in os.environ.items()
               if k not in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT')}
        try:
            r = subprocess.run(self._args(prompt, schema), capture_output=True, text=True,
                               encoding='utf-8', errors='replace', env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AgentError(f'claude CLI timeout after {timeout}s')
        except OSError as e:
            raise AgentError(f'claude CLI cannot run: {e}')
        if r.returncode != 0:
            raise AgentError(f'claude CLI failed ({r.returncode}): '
                             + (r.stderr or r.stdout or '').strip()[:300])
        try:
            payload = json.loads(r.stdout or '')
        except ValueError:
            raise AgentError('claude CLI stdout is not JSON: ' + (r.stdout or '').strip()[:200])
        if isinstance(payload, list):        # stream-json 형태로 오면 마지막 항목이 결과
            payload = payload[-1] if payload else {}
        if isinstance(payload, dict):
            structured = payload.get('structured_output')
            if isinstance(structured, dict):
                return structured            # --json-schema 를 준 경우 이쪽이 정확하다
            if payload.get('is_error'):
                raise AgentError('claude CLI reported an error: '
                                 + str(payload.get('result'))[:200])
            if isinstance(payload.get('result'), str):
                return extract_json(payload['result'])
        raise AgentError('claude CLI output has no result field')


class CodexCliBackend(Backend):
    """`codex exec <prompt>` (실험적). stdout 에서 첫 JSON 객체를 뽑는다."""
    name = 'codex-cli'

    def __init__(self, exe=None, model=None):
        path = find_codex_cli(exe)
        if not path:
            raise AgentError('codex CLI not found in PATH')
        self.exe = path
        self.model = model

    def complete(self, system, user, schema=None, timeout=DEFAULT_TIMEOUT):
        prompt = f'{system}\n\n{user}' if system else user
        args = [self.exe, 'exec', prompt]
        if self.model:
            args += ['--model', self.model]
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AgentError(f'codex CLI timeout after {timeout}s')
        except OSError as e:
            raise AgentError(f'codex CLI cannot run: {e}')
        if r.returncode != 0:
            raise AgentError(f'codex CLI failed ({r.returncode}): '
                             + (r.stderr or r.stdout or '').strip()[:300])
        return extract_json(r.stdout or '')


class FakeBackend(Backend):
    """테스트용. 미리 준 응답을 순서대로 돌려주고(Exception 이면 raise), 호출 기록을 남긴다."""
    name = 'fake'

    def __init__(self, responses=(), default=None):
        self.responses = list(responses)
        self.default = default
        self.calls = []                      # [(system, user, schema)]

    def complete(self, system, user, schema=None, timeout=DEFAULT_TIMEOUT):
        self.calls.append((system, user, schema))
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return copy.deepcopy(item)
        if self.default is not None:
            return copy.deepcopy(self.default)
        raise AgentError('FakeBackend: no more responses')


BACKEND_KINDS = ('none', 'api', 'claude-cli', 'codex-cli')


def make_backend(kind, api_key=None, model=None, exe=None):
    """백엔드 하나를 만든다. 'none'(또는 None)은 None. 알 수 없는 종류/사용 불가면 AgentError."""
    if kind in (None, '', 'none'):
        return None
    if kind == 'api':
        return ApiBackend(api_key=api_key, model=model)
    if kind == 'claude-cli':
        return ClaudeCliBackend(exe=exe, model=model)
    if kind == 'codex-cli':
        return CodexCliBackend(exe=exe, model=model)
    raise AgentError(f'unknown backend: {kind} (use one of {", ".join(BACKEND_KINDS)})')


def backend_available(kind):
    """(사용 가능?, 이유) — GUI 가 콤보박스에 상태를 보여줄 때 쓴다. 이유는 ASCII 영문."""
    if kind in (None, '', 'none'):
        return True, 'no AI backend (deterministic pipeline only)'
    if kind == 'api':
        if os.environ.get('ANTHROPIC_API_KEY'):
            return True, 'ANTHROPIC_API_KEY found'
        return False, 'ANTHROPIC_API_KEY not set'
    if kind == 'claude-cli':
        path = find_claude_cli()
        return (True, path) if path else (False, 'claude CLI not found in PATH')
    if kind == 'codex-cli':
        path = find_codex_cli()
        return (True, path) if path else (False, 'codex CLI not found in PATH')
    return False, f'unknown backend: {kind}'


# ---------- 제안 ----------

@dataclass
class Suggestion:
    agent: str                                  # 'pin_type' | 'net_diff' | 'review'
    kind: str = 'note'                          # 'pin_type'|'add_board_only_part'|'footprint_choice'|'note'
    target: str = ''                            # 'SYMBOL:PIN' | 레퍼런스 | ''
    payload: dict = field(default_factory=dict)
    reason: str = ''
    confidence: float = 0.5
    selected: bool = False                      # 사용자가 고른 것만 apply_suggestions 가 반영

    def to_dict(self):
        return {'agent': self.agent, 'kind': self.kind, 'target': self.target,
                'payload': dict(self.payload), 'reason': self.reason,
                'confidence': self.confidence, 'selected': self.selected}


def suggestions_to_json(suggestions):
    return [s.to_dict() for s in suggestions]


def _confidence(item):
    try:
        c = float(item.get('confidence', 0.5))
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, c))


def _text(item, key, limit=300):
    v = item.get(key)
    return str(v).strip()[:limit] if v is not None else ''


# ---------- 에이전트 ----------

SYSTEM_PROMPT = ('당신은 OrCAD Capture 회로도를 KiCad 로 변환하는 도구의 보조자다. '
                 '전자회로 설계 지식을 바탕으로 판단하고, 결과는 지정된 JSON 객체 하나로만 답한다. '
                 '설명 문장이나 코드펜스를 덧붙이지 않는다. 확실하지 않으면 항목을 비워 둔다.')

PIN_TYPE_SCHEMA = {
    'type': 'object',
    'properties': {
        'pins': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'number': {'type': 'string'},
                           'type': {'type': 'string', 'enum': list(KICAD_PIN_TYPES)},
                           'reason': {'type': 'string'},
                           'confidence': {'type': 'number'}},
            'required': ['number', 'type']}}},
    'required': ['pins']}


class PinTypeAgent:
    """EDIF 에 전기 타입 정보가 없어 전부 passive 로 떨어진 IC 심볼의 핀 타입을 제안한다.

    KiCad ERC 는 핀 전기 타입으로 경고를 내므로(전원 핀이 passive 면 power flag 관련 경고 등)
    큰 심볼만이라도 타입을 채우면 검토 품질이 올라간다. 값 자체는 넷 연결에 영향을 주지 않는다."""
    name = 'pin_type'

    def __init__(self, max_symbols=5, max_pins=64):
        self.max_symbols = max_symbols
        self.max_pins = max_pins

    def candidates(self, result):
        """핀 8개 이상인데 보이는 핀이 전부 passive 인 심볼(이름 순). 자리표시/전원 심볼은 제외."""
        write_result = getattr(result, 'write_result', None)
        symset = getattr(write_result, 'symset', None)
        if symset is None:
            return []
        out = []
        for name in sorted(symset.symbols):
            sym = symset.symbols[name]
            if name.startswith(PLACEHOLDER_PREFIX) or getattr(sym, 'is_power', False):
                continue
            pins = [p for u in sorted(sym.units) for p in sym.units[u].pins]
            visible = [p for p in pins if not p.hidden]
            if len(pins) < MIN_IC_PINS or not visible:
                continue
            if all(p.etype_kicad == 'passive' for p in visible):
                out.append(sym)
        return out

    def needed(self, result):
        return bool(self.candidates(result))

    def prompt(self, sym):
        pins = [p for u in sorted(sym.units) for p in sym.units[u].pins]
        shown = pins[:self.max_pins]
        rows = '\n'.join(f'  {p.number} / {p.name}' for p in shown)
        more = f'\n  (핀 {len(pins) - len(shown)}개 생략)' if len(shown) < len(pins) else ''
        return (
            'OrCAD EDIF 에는 핀 전기 타입 정보가 없어 아래 심볼의 핀이 전부 passive 로 변환됐다.\n'
            '심볼 이름과 핀 이름을 보고 각 핀의 올바른 KiCad 전기 타입을 판정하라.\n\n'
            f'심볼: {sym.name}\n'
            f'값(Value): {sym.value}\n'
            f'풋프린트: {sym.footprint}\n'
            f'핀 (번호 / 이름):\n{rows}{more}\n\n'
            f'허용 타입: {", ".join(KICAD_PIN_TYPES)}\n'
            '- 전원 입력 핀(VCC/VDD/GND 등)은 power_in, 레귤레이터 출력은 power_out.\n'
            '- 확실하지 않은 핀은 결과에서 빼라(추측 금지). passive 그대로 두어도 되는 핀도 뺀다.\n\n'
            '출력 형식(JSON 객체 하나만):\n'
            '{"pins": [{"number": "1", "type": "power_in", "reason": "전원 입력", "confidence": 0.8}]}')

    def run(self, result, backend, timeout=DEFAULT_TIMEOUT):
        out = []
        cands = self.candidates(result)
        for sym in cands[:self.max_symbols]:
            data = backend.complete(SYSTEM_PROMPT, self.prompt(sym),
                                    schema=PIN_TYPE_SCHEMA, timeout=timeout)
            numbers = {p.number for u in sym.units for p in sym.units[u].pins}
            seen = set()
            for item in (data.get('pins') if isinstance(data, dict) else None) or []:
                if not isinstance(item, dict):
                    continue
                num = str(item.get('number', '')).strip()
                etype = str(item.get('type', '')).strip().lower()
                if num not in numbers or etype not in KICAD_PIN_TYPES or etype == 'passive':
                    continue                 # 없는 핀·모르는 타입·바뀌는 게 없는 제안은 버린다
                if num in seen:
                    continue
                seen.add(num)
                out.append(Suggestion(agent=self.name, kind='pin_type',
                                      target=f'{sym.name}:{num}', payload={'type': etype},
                                      reason=_text(item, 'reason'), confidence=_confidence(item)))
        if len(cands) > self.max_symbols:
            out.append(Suggestion(
                agent=self.name, kind='note', target='',
                payload={'markdown': f'핀 타입 후보 심볼 {len(cands)}개 중 '
                                     f'{self.max_symbols}개만 검토했다.'},
                reason='max_symbols limit'))
        return out


NET_DIFF_SCHEMA = {
    'type': 'object',
    'properties': {
        'items': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'target': {'type': 'string'},
                           'action': {'type': 'string',
                                      'enum': ['add_board_only_part', 'footprint_choice',
                                               'ignore', 'note']},
                           'choice': {'type': 'string', 'enum': ['board', 'orcad']},
                           'reason': {'type': 'string'},
                           'confidence': {'type': 'number'}},
            'required': ['target', 'action', 'reason']}}},
    'required': ['items']}

# 모델이 고른 액션 -> Suggestion.kind ('ignore'/'note' 는 정보성 note 로 남고 적용은 ignore_refs)
_ACTION_KIND = {'add_board_only_part': 'add_board_only_part',
                'footprint_choice': 'footprint_choice',
                'ignore': 'note', 'note': 'note'}


class NetDiffAgent:
    """[4] 보드 vs 회로도 차이 항목마다 원인 설명과 해소 방법을 제안한다."""
    name = 'net_diff'

    def __init__(self, max_items=30):
        self.max_items = max_items

    @staticmethod
    def _diff_json(result):
        diff = getattr(result, 'board_diff', None)
        if diff is None:
            return None
        return result_to_json(result).get('board_diff')

    def needed(self, result):
        d = self._diff_json(result)
        if not d:
            return False
        nc = d.get('net_compare') or {}
        return bool(d['only_board_refs'] or d['only_sch_refs'] or d['missing_pins']
                    or d['extra_pads'] or d['footprint_diff'] or (nc and not nc.get('ok', True)))

    def prompt(self, result):
        d = self._diff_json(result) or {}
        n = self.max_items

        def cut(seq):
            seq = list(seq)
            tail = f' (+{len(seq) - n} more)' if len(seq) > n else ''
            return (', '.join(str(x) for x in seq[:n]) or '(없음)') + tail

        nc = d.get('net_compare') or {}
        fp = [f'{ref}: OrCAD={a or "(없음)"} / board={b}' for ref, a, b in d.get('footprint_diff', [])]
        return (
            'PADS 보드(.asc 를 KiCad 로 임포트한 결과)와 OrCAD 회로도를 대조했더니 아래 차이가 나왔다.\n'
            '항목마다 원인을 설명하고 해소 방법을 하나 고르라.\n\n'
            f'보드에만 있는 부품: {cut(d.get("only_board_refs", []))}\n'
            f'회로도에만 있는 부품: {cut(d.get("only_sch_refs", []))}\n'
            f'회로도 핀 중 보드 패드에 없는 것: {cut(f"{k}({v})" for k, v in (d.get("missing_pins") or {}).items())}\n'
            f'보드 패드 중 회로도 핀에 없는 것: {cut(f"{k}({v})" for k, v in (d.get("extra_pads") or {}).items())}\n'
            f'풋프린트가 다른 부품: {cut(fp)}\n'
            f'넷 비교: 일치 {nc.get("matched", 0)}개, 불일치 {len(nc.get("mismatches", []))}개, '
            f'보드에만 있는 넷 {len(nc.get("only_ours", []))}개, 기준에만 있는 넷 {len(nc.get("only_ref", []))}개\n\n'
            '해소 방법(action):\n'
            '  add_board_only_part - 보드에만 있는 실부품(커넥터·테스트포인트 등). 회로도에 자리표시 심볼로 추가.\n'
            '  footprint_choice    - 풋프린트 이름이 다를 때 어느 쪽을 쓸지 선택(choice: "board" 또는 "orcad").\n'
            '  ignore              - 기구 부품·펜스·마운팅홀처럼 회로도에 없어도 되는 것. 리포트에서 숨긴다.\n'
            '  note                - 위 셋으로 해결되지 않는 것. 원인 설명만 남긴다.\n\n'
            '출력 형식(JSON 객체 하나만):\n'
            '{"items": [{"target": "J19", "action": "add_board_only_part", '
            '"reason": "보드에만 있는 전원 커넥터", "confidence": 0.8}]}')

    def run(self, result, backend, timeout=DEFAULT_TIMEOUT):
        data = backend.complete(SYSTEM_PROMPT, self.prompt(result),
                                schema=NET_DIFF_SCHEMA, timeout=timeout)
        out = []
        for item in (data.get('items') if isinstance(data, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get('action', '')).strip().lower()
            kind = _ACTION_KIND.get(action)
            target = str(item.get('target', '')).strip()
            if kind is None or (action != 'note' and not target):
                continue
            payload = {}
            if kind == 'footprint_choice':
                choice = str(item.get('choice', '')).strip().lower()
                if choice not in ('board', 'orcad'):
                    continue                 # 선택지가 없거나 이상하면 적용할 수 없는 제안이다
                payload = {'choice': choice}
            elif kind == 'note':
                payload = {'action': action}
            out.append(Suggestion(agent=self.name, kind=kind, target=target, payload=payload,
                                  reason=_text(item, 'reason'), confidence=_confidence(item)))
        return out


REVIEW_SCHEMA = {'type': 'object', 'properties': {'markdown': {'type': 'string'}},
                 'required': ['markdown']}


class ReviewAgent:
    """변환 결과 전체를 한국어 마크다운으로 요약·검토한다(항상 실행, 제안은 note 하나)."""
    name = 'review'

    def __init__(self, max_issues=40):
        self.max_issues = max_issues

    def needed(self, result):
        return True

    def prompt(self, result):
        lines = []
        if getattr(result, 'design', None) is not None:
            lines.append(summary(result.design))
        for key in ('edif', 'geometry', 'kicad'):
            cmp = (result.verifications or {}).get(key)
            if cmp is not None:
                lines.append(f'검증 {key}: {"PASS" if cmp.ok else "FAIL"} '
                             f'(일치 {cmp.matched}, 불일치 {len(cmp.mismatches)}, '
                             f'우리에게만 {len(cmp.only_ours)}, 기준에만 {len(cmp.only_ref)})')
        if result.erc:
            by_type = ' '.join(f'{k}={v}' for k, v in sorted(result.erc['by_type'].items()))
            lines.append(f'ERC 위반: {result.erc["count"]} ({by_type})')
        diff = getattr(result, 'board_diff', None)
        if diff is not None:
            lines.append(f'보드 대조: 보드 전용 {len(diff.only_board_refs)}, '
                         f'회로도 전용 {len(diff.only_sch_refs)}, '
                         f'풋프린트 차이 {len(diff.footprint_diff)}')
        issues = list(result.issues or [])
        shown = issues[:self.max_issues]
        if shown:
            lines.append('이슈:')
            lines.extend('  - ' + str(i) for i in shown)
            if len(issues) > len(shown):
                lines.append(f'  - (외 {len(issues) - len(shown)}건 생략)')
        return (
            'OrCAD -> KiCad 변환 결과다. 엔지니어가 KiCad 에서 무엇을 먼저 확인해야 하는지\n'
            '한국어 마크다운으로 짧게 정리하라(제목 없이 소제목과 목록, 15줄 이내).\n'
            '검증이 통과했더라도 남은 위험(핀 타입, 풋프린트, 보드 차이 등)을 짚어라.\n\n'
            + '\n'.join(lines) + '\n\n'
            '출력 형식(JSON 객체 하나만): {"markdown": "## 확인 사항\\n- ..."}')

    def run(self, result, backend, timeout=DEFAULT_TIMEOUT):
        data = backend.complete(SYSTEM_PROMPT, self.prompt(result),
                                schema=REVIEW_SCHEMA, timeout=timeout)
        text = data.get('markdown') if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            return []
        return [Suggestion(agent=self.name, kind='note', target='',
                           payload={'markdown': text.strip()}, reason='review summary',
                           confidence=0.5)]


AGENTS = {'pin_type': PinTypeAgent, 'net_diff': NetDiffAgent, 'review': ReviewAgent}
DEFAULT_AGENTS = ('pin_type', 'net_diff', 'review')


def run_agents(result, backend, enabled=DEFAULT_AGENTS, log=None, timeout=DEFAULT_TIMEOUT):
    """활성 에이전트를 차례로 돌려 제안을 모은다. **어떤 경우에도 예외를 올리지 않는다.**

    에이전트 하나가 실패하면 그 사실을 `Suggestion(kind='note', payload={'error': ...})` 로
    남기고 다음 에이전트를 계속 돌린다(호출자가 이슈 목록처럼 훑어볼 수 있다).
    log 는 진행 메시지를 받는 callable(str) — 콘솔/GUI 로 나가므로 ASCII 영문만 쓴다."""
    out = []

    def emit(msg):
        if log is not None:
            log(msg)

    if backend is None:
        emit('agents: no backend (skipped)')
        return out
    for name in enabled or ():
        cls = AGENTS.get(name)
        if cls is None:
            emit(f'agents: unknown agent "{name}" (skipped)')
            continue
        try:
            agent = cls()                    # 생성자에서 나는 예외도 여기서 잡아야 한다
            if not agent.needed(result):
                emit(f'agent {name}: not needed')
                continue
            got = agent.run(result, backend, timeout=timeout)
        except Exception as e:               # 백엔드 오류든 응답 형식 오류든 파이프라인을 막지 않는다
            emit(f'agent {name}: failed ({type(e).__name__})')
            out.append(Suggestion(agent=name, kind='note', target='',
                                  payload={'error': f'{type(e).__name__}: {e}'},
                                  reason='agent failed', confidence=0.0))
            continue
        emit(f'agent {name}: {len(got)} suggestion(s)')
        out.extend(got)
    return out


def apply_suggestions(options: PipelineOptions, suggestions) -> PipelineOptions:
    """selected=True 인 제안만 `Resolutions` 에 반영한 **새** PipelineOptions 를 만든다.

    원본 옵션은 절대 바뀌지 않는다(깊은 복사). 적용할 수 없는 제안(핀 타입 목록 밖의 값,
    빈 레퍼런스, 잘못된 풋프린트 선택 등)은 조용히 버린다 — 사용자가 고른 것이라도 결정적
    파이프라인이 이해할 수 있는 형태만 들어간다."""
    new = copy.deepcopy(options)
    if new.resolutions is None:
        new.resolutions = Resolutions()
    rez = new.resolutions
    for s in suggestions or ():
        if not getattr(s, 'selected', False):
            continue
        target = (getattr(s, 'target', '') or '').strip()
        payload = getattr(s, 'payload', None) or {}
        kind = getattr(s, 'kind', '')
        if kind == 'pin_type':
            if ':' not in target:
                continue
            sym_name, number = target.rsplit(':', 1)
            etype = str(payload.get('type', '')).strip().lower()
            if not sym_name or not number or etype not in KICAD_PIN_TYPES:
                continue
            rez.pin_type_overrides[(sym_name, number)] = etype
        elif kind == 'add_board_only_part':
            if target and target not in rez.add_board_only_parts:
                rez.add_board_only_parts.append(target)
        elif kind == 'footprint_choice':
            choice = str(payload.get('choice', '')).strip().lower()
            if target and choice in ('board', 'orcad'):
                rez.footprint_choice[target] = choice
        elif kind == 'note' and payload.get('action') == 'ignore':
            if target and target not in rez.ignore_refs:
                rez.ignore_refs.append(target)
    return new
