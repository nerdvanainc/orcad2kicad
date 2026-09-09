"""kicad-cli 연동: 탐색·실행, 넷리스트 파싱·정규화, ERC/PDF. (Task 3에서 확장)"""
from __future__ import annotations
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from .sexp import parse, child, children
from .verify import compare_netlists, alias_net_name


def _version_key(path):
    """kicad-cli 경로(...\\KiCad\\<버전>\\bin\\kicad-cli.exe)에서 버전 폴더 이름을 뽑아
    내림차순 정렬에 쓸 숫자 튜플로 바꾼다. 문자열 정렬(9.0 > 10.0으로 오판)을 피하기 위함."""
    folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return tuple(int(x) for x in re.findall(r'\d+', folder))


def _stable_candidates():
    """정식(설치된) kicad-cli 후보 — 기존 탐색 순서 그대로."""
    cands = []
    if os.environ.get('KICAD_CLI'):
        cands.append(os.environ['KICAD_CLI'])
    w = shutil.which('kicad-cli')
    if w:
        cands.append(w)
    if sys.platform.startswith('win'):
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramW6432', r'C:\Program Files')):
            found = glob.glob(os.path.join(base, 'KiCad', '*', 'bin', 'kicad-cli.exe'))
            cands.extend(sorted(found, key=_version_key, reverse=True))
    elif sys.platform == 'darwin':
        cands.append('/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
    else:
        cands.extend(['/usr/bin/kicad-cli', '/usr/local/bin/kicad-cli'])
    return cands


def _nightly_candidates():
    """나이틀리(개발 빌드) kicad-cli 후보.

    `.DSN` 네이티브 임포트(`sch import --format orcad`)와 새 회로도 포맷(20260830 등) 읽기는
    아직 나이틀리에만 있다. 순서: 환경변수 O2K_KICAD_NIGHTLY(실행 파일 직접 지정) ->
    'KiCad Nightly*' 설치 폴더 -> 사용자 설치(%LOCALAPPDATA%\\Programs\\KiCad\\*) ->
    이 도구가 준비한 포터블 빌드(`kicad_portable.DEFAULT_ROOT`) ->
    KICAD_NIGHTLY_DIR 아래에 압축만 풀어 둔 포터블 빌드."""
    cands = []
    if os.environ.get('O2K_KICAD_NIGHTLY'):
        cands.append(os.environ['O2K_KICAD_NIGHTLY'])
    if sys.platform.startswith('win'):
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramW6432', r'C:\Program Files')):
            found = glob.glob(os.path.join(base, 'KiCad Nightly*', 'bin', 'kicad-cli.exe'))
            found += glob.glob(os.path.join(base, 'KiCad Nightly*', '*', 'bin', 'kicad-cli.exe'))
            cands.extend(sorted(found, reverse=True))
        local = os.environ.get('LOCALAPPDATA')
        if local:
            cands.extend(sorted(glob.glob(os.path.join(local, 'Programs', 'KiCad', '*', 'bin', 'kicad-cli.exe')),
                                key=_version_key, reverse=True))
    try:                                    # 순환 import 회피용 지연 import
        from .kicad_portable import DEFAULT_ROOT, portable_clis
        cands.extend(portable_clis(DEFAULT_ROOT))
    except Exception:                       # 이 모듈이 없어도 탐색 자체는 계속되어야 한다
        pass
    root = os.environ.get('KICAD_NIGHTLY_DIR')
    if root and os.path.isdir(root):
        exe = 'kicad-cli.exe' if sys.platform.startswith('win') else 'kicad-cli'
        found = glob.glob(os.path.join(root, '**', 'kicad-nightly*', '**', 'bin', exe), recursive=True)
        found += glob.glob(os.path.join(root, '**', 'bin', exe), recursive=True)
        cands.extend(sorted(set(found)))
    return cands


def list_kicad_clis(explicit=None, prefer_nightly=False):
    """존재하는 kicad-cli 후보 경로 목록(우선순위 순, 중복 제거).

    prefer_nightly=True 면 나이틀리 후보를 앞에 둔다. 기본(False)에서는 정식 빌드를 먼저 쓰고
    나이틀리는 맨 뒤에 붙인다 — 기존 EDIF 경로의 동작(정식 kicad-cli 사용)을 그대로 유지하기
    위해서다."""
    groups = ([explicit] if explicit else [])
    if prefer_nightly:
        groups += _nightly_candidates() + _stable_candidates()
    else:
        groups += _stable_candidates() + _nightly_candidates()
    out, seen = [], set()
    for c in groups:
        if not c or not os.path.isfile(c):
            continue
        key = os.path.normcase(os.path.abspath(c))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def find_kicad_cli(explicit=None, prefer_nightly=False):
    """kicad-cli 실행 파일 경로. 순서: 인자 -> 환경변수 KICAD_CLI -> PATH -> OS별 표준 설치 경로(최신 버전 우선)
    -> 나이틀리 후보. prefer_nightly=True 면 나이틀리를 먼저 찾는다. 없으면 None."""
    cands = list_kicad_clis(explicit, prefer_nightly=prefer_nightly)
    return cands[0] if cands else None


# ---------- kicad-cli 능력 판별 ----------

NIGHTLY_MINOR = 99          # KiCad 개발 빌드(나이틀리)의 부 버전 (예: 10.99.0)
UNLIMITED_SCH_VERSION = 99999999
# kicad-cli 버전 (major, minor) -> 그 빌드가 읽을 수 있는 최대 .kicad_sch 포맷 버전.
# 10.0 값은 실측이다: 10.0.6 으로 `sch upgrade` 한 파일의 (version …) 이 20260306 이고,
# 나이틀리가 만든 20260830 시트는 "Unable to load schematic" 으로 거부된다.
# 표에 없는 버전은 판단하지 않는다(None = 모름).
SCH_FORMAT_CEILING = {(8, 0): 20231120, (9, 0): 20250114, (10, 0): 20260306}

_cli_info_cache = {}


def _cli_key(cli):
    return os.path.normcase(os.path.abspath(cli))


def _probe(args, timeout=60):
    """짧은 조회용 실행. 실패해도 예외를 내지 않고 (returncode, 출력) 을 돌려준다."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ''
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def cli_version(cli):
    """`kicad-cli version` 문자열(예: '10.0.6'). 실행할 수 없으면 ''. 결과는 캐시한다."""
    key = _cli_key(cli)
    info = _cli_info_cache.setdefault(key, {})
    if 'version' not in info:
        rc, out = _probe([cli, 'version'])
        info['version'] = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ''
    return info['version']


def supports_orcad_import(cli):
    """`sch import` 가 OrCAD(.DSN) 형식을 지원하는지. 결과는 캐시한다."""
    key = _cli_key(cli)
    info = _cli_info_cache.setdefault(key, {})
    if 'orcad' not in info:
        _, out = _probe([cli, 'sch', 'import', '--help'])
        info['orcad'] = 'orcad' in out.lower()
    return info['orcad']


def _version_tuple(text):
    return tuple(int(x) for x in re.findall(r'\d+', text or ''))[:3]


def sch_format_ceiling(cli):
    """이 kicad-cli 가 읽을 수 있는 최대 회로도 포맷 버전. 모르면 None(개발 빌드는 무제한)."""
    v = _version_tuple(cli_version(cli))
    if len(v) >= 2 and v[1] >= NIGHTLY_MINOR:
        return UNLIMITED_SCH_VERSION
    return SCH_FORMAT_CEILING.get(v[:2]) if len(v) >= 2 else None


def pick_cli_for_schematic(sch_version, explicit=None):
    """주어진 회로도 포맷 버전을 읽을 수 있는 kicad-cli 를 고른다(없으면 None).

    포맷 버전이 정식 빌드의 한계보다 높으면(임포트 결과의 20260830 등) 그 빌드는 파일을 아예
    열지 못하므로 후보에서 뺀다. 표에 없는 버전(모름)은 확실한 후보 다음에 시도한다.
    `explicit`(--kicad-cli)로 직접 지정한 것은 사용자의 선택이므로 그대로 존중한다."""
    if explicit and os.path.isfile(explicit):
        return explicit
    known, unknown = [], []
    for c in list_kicad_clis():
        ceiling = sch_format_ceiling(c)
        if ceiling is None:
            unknown.append(c)
        elif not sch_version or ceiling >= int(sch_version):
            known.append(c)
    ordered = known + unknown
    return ordered[0] if ordered else None


def find_stable_cli(explicit=None):
    """정식(포맷 상한이 있는, 즉 나이틀리가 아닌) kicad-cli 후보 중 첫 번째. 없으면 None.

    보드 임포트는 정식 kicad-cli 로 해야 정식판이 여는 `.kicad_pcb` 가 나온다(나이틀리로 임포트한
    보드는 `(version 20260831)` 등이라 정식 10.0 이 못 연다). `explicit` 이 나이틀리를 가리켜도
    (사용자가 `--kicad-cli` 로 나이틀리를 직접 줬을 때 등) 여기서는 무시하고 자동 탐지한 정식
    빌드를 우선한다 — ERC/[3]에 쓰는 `pick_cli_for_schematic`/`find_cli_with_orcad_import` 는
    `explicit` 을 그대로 존중하는 것과 다르다(보드는 정식판이 열 수 있어야 하므로)."""
    for c in list_kicad_clis(explicit):
        if sch_format_ceiling(c) != UNLIMITED_SCH_VERSION:
            return c
    return None


def find_cli_with_orcad_import(explicit=None):
    """`.DSN` 네이티브 임포트가 가능한 kicad-cli(없으면 None)."""
    for c in list_kicad_clis(explicit, prefer_nightly=True):
        if supports_orcad_import(c):
            return c
    return None


def import_kicad_project(cli, inputs, out_stem):
    """`kicad-cli import -o <out_stem> <inputs…>` 실행 -> (프로젝트 .kicad_pro 경로, 출력 텍스트).

    회로도(.DSN)와 (선택) 보드(.asc)를 한 번에 임포트해 `<out_stem>.kicad_pro` 프로젝트를 만든다.
    이 명령은 리포트 파일 옵션이 없어 진행 내용이 stdout 으로만 나오므로 그대로 돌려준다
    (로캘에 따라 비ASCII 가 섞일 수 있어 파일로만 남기고 콘솔에는 요약만 찍는다)."""
    args = [cli, 'import', '-o', out_stem] + list(inputs)
    rc, out = _probe(args, timeout=600)
    pro = out_stem + '.kicad_pro'
    if not os.path.isfile(pro):
        raise RuntimeError(f'kicad-cli import failed ({rc}): {out.strip()[:800]}')
    return pro, out


_AUTO_KICAD = re.compile(r'^Net-\(.*\)$')
_UNCONNECTED = 'unconnected-'


def _run(args):
    # 한국어 로캘 등에서 kicad-cli 출력이 UTF-8 로 나오므로 콘솔 코드페이지(cp949)로 디코딩하지
    # 않도록 인코딩을 못박는다(디코딩 실패로 출력 수집 스레드가 죽는 것을 막는다).
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(f'kicad-cli failed ({r.returncode}): {" ".join(args)}\n{r.stdout}\n{r.stderr}')
    return r.stdout


def export_netlist(kicad_cli, root_sch, out_path):
    _run([kicad_cli, 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', out_path, root_sch])
    return out_path


def export_pdf(kicad_cli, root_sch, out_pdf):
    _run([kicad_cli, 'sch', 'export', 'pdf', '-o', out_pdf, root_sch])
    return out_pdf


def run_erc(kicad_cli, root_sch, out_json):
    """ERC 실행 후 요약 dict. 위반이 있어도(=위반 존재로 종료코드가 0이 아니어도) 예외 없이 결과를
    돌려준다 — 그래서 성공 판정은 종료코드가 아니라 out_json 파일 생성 여부로 한다. 실행 자체가
    실패하면(잘못된 경로 등) out_json 이 생성되지 않으므로 RuntimeError 로 알린다. 실행 전에 이전
    실행의 out_json 이 남아 있으면 지워서, 실패 시 오래된 결과를 조용히 재사용하지 않게 한다."""
    if os.path.exists(out_json):
        os.remove(out_json)
    r = subprocess.run([kicad_cli, 'sch', 'erc', '--format', 'json', '--severity-all', '-o', out_json, root_sch],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not os.path.isfile(out_json):
        raise RuntimeError(f'kicad-cli erc failed ({r.returncode}): {r.stderr}\n{r.stdout}')
    with open(out_json, encoding='utf-8') as f:
        data = json.load(f)
    items = []
    for sheet in data.get('sheets', []):
        for v in sheet.get('violations', []):
            items.append((v.get('type', ''), sheet.get('path', ''), v.get('description', '')))
    by_type = {}
    for t, _, _ in items:
        by_type[t] = by_type.get(t, 0) + 1
    return {'count': len(items), 'by_type': by_type, 'items': items}


def parse_kicad_netlist(text):
    """kicadsexpr 넷리스트 -> {넷 이름: {REF.PIN}}. #PWR 노드와 unconnected- 넷은 제외."""
    tree = parse(text)[0]
    nets_node = child(tree, 'nets')
    out = {}
    for n in children(nets_node, 'net') if nets_node is not None else []:
        name = str(child(n, 'name')[1])
        if name.startswith(_UNCONNECTED):
            continue
        pins = set()
        for node in children(n, 'node'):
            ref = str(child(node, 'ref')[1])
            if ref.startswith('#'):
                continue
            pins.add(f'{ref}.{child(node, "pin")[1]}')
        if pins:
            out[name] = pins
    return out


def normalize_kicad_nets(nets, ref_nets):
    """비교용 정규화: 시트 경로 제거, 정답과 핀 집합이 같은 넷은 정답 이름으로 치환(자동 이름
    `Net-(…)` 뿐 아니라 접미 번호가 붙은 중복 라벨 `XTAL1_1642…` 도 이렇게 맞는다), 그 밖에는
    오버바·대소문자 표기 차이만 맞춘다(`verify.alias_net_name`). 자동 이름 넷이 정답에 없으면 이슈."""
    issues = []
    out = {}
    by_pins = {frozenset(v): k for k, v in ref_nets.items() if v}
    for name, pins in nets.items():
        base = name.rsplit('/', 1)[-1] if name.startswith('/') else name
        match = by_pins.get(frozenset(pins)) if pins else None
        if match is not None:
            if match != base and not _AUTO_KICAD.match(base) and base not in ref_nets:
                issues.append(f'kicad net {base} renamed to {match} (identical pins)')
            base = match
        elif _AUTO_KICAD.match(base):
            issues.append(f'kicad net {name} pins {sorted(pins)} has no reference net with the same pins')
        else:
            base = alias_net_name(base, ref_nets)     # /RESET <- RESET, {slash}RESET, ~{RESET}; 대소문자
        if base in out:
            issues.append(f'kicad nets merged under name {base}: {sorted(out[base])} + {sorted(pins)}')
            out[base] |= set(pins)
        else:
            out[base] = set(pins)
    return out, issues


def verify_with_kicad(kicad_cli, root_sch, ref_nets, workdir):
    path = export_netlist(kicad_cli, root_sch, os.path.join(workdir, 'kicad_netlist.txt'))
    with open(path, encoding='utf-8') as f:
        nets = parse_kicad_netlist(f.read())
    norm, issues = normalize_kicad_nets(nets, ref_nets)
    return compare_netlists(norm, ref_nets), issues
