"""KiCad 나이틀리 '포터블' 준비 — 설치하지 않고 kicad-cli 만 확보한다.

배경
----
`.DSN` 네이티브 임포트(`sch import --format orcad`)와 새 회로도 포맷(20260830) 읽기는
KiCad 개발 빌드(나이틀리, 10.99.x)에만 있다. 그런데 나이틀리를 정식으로 설치하면 시스템에
파일 연결·레지스트리·환경이 바뀌고, 이미 쓰고 있는 정식 KiCad 와 섞일 위험이 있다.

그래서 이 모듈은 **설치 프로그램을 절대 실행하지 않는다.** 공식 나이틀리 설치 파일(NSIS
자체 압축 실행 파일)을 내려받은 뒤 7-Zip 또는 Bandizip 으로 *압축만 푼다*. 그 안의
`bin/kicad-cli.exe` 는 설치 없이 그대로 실행된다(실측 확인).

  나이틀리 목록 페이지 -> 최신 x86_64 lite(.exe, 약 234 MB) -> 다운로드 -> 압축 해제
  -> 불필요한 부분 삭제(약 727 MB -> 약 300 MB) -> kicad-cli 실행 확인 -> portable.json 기록

주의/한계
--------
- Windows 전용이다(나이틀리 배포본이 .exe NSIS 설치본이라 압축 해제 대상이 된다).
- 압축 해제 도구가 없는 PC 여도 된다. 7-Zip 콘솔판을 공식 사이트에서 받아 **역시 풀기만**
  해서 쓴다(`ensure_extractor`, 아래 "자급" 절). 7-Zip 설치 프로그램도 실행하지 않는다.
- 시스템에 이미 7-Zip(정식 `7z.exe`)이나 Bandizip 이 있으면 그것을 쓴다. **`7za.exe`/`7zr.exe`
  단독판은 NSIS 를 열지 못하므로**(실측: Files 0) 시스템 후보로 받아들이지 않는다.

자급(self-provision) 사슬 — 전부 "다운로드 + 압축 해제"뿐이다
------------------------------------------------------------
1. `https://www.7-zip.org/a/7zr.exe` (약 590 KB, 퍼블릭 도메인) 를 받는다. 이것은 NSIS 를 열지
   못하지만, **7-Zip 자신의 배포용 `.exe`(7z SFX 아카이브)는 풀 수 있다.**
2. `https://www.7-zip.org/a/7zNNNN-x64.exe` (약 1.6 MB) 를 받아 `7zr x` 로 푼다 → 정식
   `7z.exe`(+`7z.dll`, `License.txt`, 약 6 MB). **설치기로 실행하지 않는다.**
3. 그 `7z.exe` 로 KiCad 나이틀리 NSIS 설치본을 완전히 푼다(실측 1136 파일).
- 표준 라이브러리만 쓴다(urllib). 콘솔/로그 문자열은 ASCII.
"""
from __future__ import annotations
import glob
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_ROOT = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                            'orcad2kicad', 'kicad-nightly')
NIGHTLY_INDEX = 'https://downloads.kicad.org/kicad/windows/explore/nightlies'
PORTABLE_JSON = 'portable.json'

# 압축을 푼 뒤 지워도 kicad-cli 동작에 지장이 없는 것들(실측: 727 MB -> 약 300 MB).
TRIM_TARGETS = (os.path.join('share', 'kicad', 'demos'),      # 약 350 MB, 예제 프로젝트
                os.path.join('share', 'kicad', 'internat'),   # 약 49 MB, 번역 카탈로그
                '$PLUGINSDIR',                                # NSIS 설치기 자체의 임시 파일
                'uninstall.exe',                              # 설치하지 않았으므로 쓸 일이 없다
                'uninstall.exe.nsis')                         # 7-Zip 으로 풀면 이 이름으로 나온다

_USER_AGENT = 'orcad2kicad-portable/1.0'

# 7-Zip 자급용. 7zr.exe 는 퍼블릭 도메인 단독 실행 파일, 정식 배포본은 LGPL(+unRAR 제한).
# 우리는 정식 배포본을 '풀기만' 하고 설치하지 않는다.
SEVENZIP_DOWNLOAD_PAGE = 'https://www.7-zip.org/download.html'
SEVENZR_URL = 'https://www.7-zip.org/a/7zr.exe'
# download.html 이 최신판 링크를 GitHub 릴리스로 걸어 두더라도, 파일 자체는 7-zip.org 의
# a/ 아래에도 그대로 있다(실측). 그래서 버전 번호만 뽑아 이 형식으로 URL 을 만든다.
SEVENZIP_URL_FORMAT = 'https://www.7-zip.org/a/{}'
# 페이지를 못 읽거나 형식이 바뀌었을 때 쓰는 최후의 기본값(이 세션에서 실제로 받아 본 판).
FALLBACK_7ZIP_NAME = '7z2603-x64.exe'
TOOLS_SUBDIR = 'tools'                 # <root>/tools/7zr.exe, <root>/tools/7zip/7z.exe
SEVENZIP_SUBDIR = os.path.join(TOOLS_SUBDIR, '7zip')

# 압축 도구를 끝내 확보하지 못했을 때의 안내. **설치기 실행을 대안으로 제시하지 않는다.**
NO_EXTRACTOR_HINT = (
    'no archiver available. Either install 7-Zip or Bandizip, or allow this tool to download '
    'the 7-Zip console build from www.7-zip.org (about 6 MB, extracted only, never installed), '
    'or extract the nightly installer yourself. Do NOT run the KiCad installer.')


class PortableError(Exception):
    """포터블 KiCad 준비 실패(다운로드·압축 해제·검증). 메시지는 ASCII."""


def _log(log, msg):
    """로그 콜백 호출(없으면 조용히 무시). 메시지는 ASCII 로 강제한다."""
    if log:
        log(str(msg).encode('ascii', 'replace').decode('ascii'))


# 네트워크에서 나올 수 있는 예외. urllib 는 OSError(URLError 포함) 말고도
# http.client.HTTPException(연결이 끊기면 IncompleteRead 등)과, 형식이 틀린 URL 에는
# ValueError 를 낸다. 셋 다 잡지 않으면 날 트레이스백이 그대로 튀어나온다.
_NET_ERRORS = (OSError, http.client.HTTPException, ValueError)


def _require_https(url, what='URL'):
    """https 가 아닌 주소는 아예 열지 않는다(중간자 공격으로 실행 파일이 바뀌는 것을 막는다)."""
    if not isinstance(url, str) or not url.lower().startswith('https://'):
        raise PortableError(f'refusing to fetch a non-https {what}: {url!r}')
    return url


def _cli_name():
    return 'kicad-cli.exe' if sys.platform.startswith('win') else 'kicad-cli'


def cli_path_in(root):
    """압축을 푼 디렉터리에서 kicad-cli 가 있어야 할 경로(존재 여부는 확인하지 않는다)."""
    return os.path.join(root, 'bin', _cli_name())


# ---------- 나이틀리 목록 ----------

# 목록 페이지의 다운로드 링크. 예:
#   .../nightlies/download/kicad-nightly-10.99.0.3703.gaa01e4fd3b-x86_64-lite.exe
_HREF_RE = re.compile(r'href=["\'](?P<url>[^"\']*/download/(?P<name>kicad-nightly-[^"\'/]+\.exe))["\']',
                      re.IGNORECASE)
# 파일명에서 버전(10.99.0.3703) · 아키텍처 · lite 여부를 뽑는다.
_NAME_RE = re.compile(r'^kicad-nightly-(?P<ver>\d+(?:\.\d+)*)\.(?P<hash>g[0-9a-f]+)'
                      r'-(?P<arch>x86_64|arm64)(?P<lite>-lite)?\.exe$', re.IGNORECASE)


def list_nightlies(html, arch='x86_64', lite=True, base=NIGHTLY_INDEX):
    """나이틀리 목록 HTML 에서 조건에 맞는 설치 파일을 찾는다(순수 함수, 네트워크 없음).

    반환: `(build_no, filename, url)` 리스트를 **최신(빌드 번호 큰 것) 우선**으로 정렬한 것.
    `build_no` 는 버전 `10.99.0.3703` 의 마지막 성분(3703). 같은 파일이 여러 번 나오면 한 번만
    담는다. 디버그 zip(`-pdbs.zip`)과 요청하지 않은 아키텍처/변형은 걸러진다.

    상대 경로 href 는 `base` 를 기준으로 절대 주소로 바꾸고, **https 가 아닌 것은 버린다.**"""
    found = {}
    for m in _HREF_RE.finditer(html or ''):
        name, url = m.group('name'), m.group('url')
        nm = _NAME_RE.match(name)
        if not nm:
            continue
        url = urllib.parse.urljoin(base, url)
        if not url.lower().startswith('https://'):
            continue
        if nm.group('arch').lower() != arch.lower():
            continue
        if bool(nm.group('lite')) != bool(lite):
            continue
        parts = tuple(int(x) for x in nm.group('ver').split('.'))
        build = parts[-1] if parts else 0
        found.setdefault(name, (parts, build, url))
    rows = [(v[1], name, v[2]) for name, v in found.items()]
    rows.sort(key=lambda r: (found[r[1]][0], r[1]), reverse=True)
    return rows


def latest_nightly(arch='x86_64', lite=True, timeout=30, index_url=NIGHTLY_INDEX):
    """나이틀리 목록 페이지를 받아 최신 설치 파일 `(filename, url)` 을 돌려준다."""
    _require_https(index_url, 'index page')
    req = urllib.request.Request(index_url, headers={'User-Agent': _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode('utf-8', 'replace')
    except _NET_ERRORS as e:
        raise PortableError(f'cannot read the nightly index page ({index_url}): {e}. Check the '
                            'network/proxy, or pass an already downloaded installer instead.')
    rows = list_nightlies(html, arch=arch, lite=lite, base=index_url)
    if not rows:
        raise PortableError(f'no nightly installer found for arch={arch} lite={lite}')
    _, name, url = rows[0]
    return name, url


# ---------- 압축 해제 도구 탐색 ----------

def _program_dirs():
    dirs = []
    for var, default in (('ProgramFiles', r'C:\Program Files'),
                         ('ProgramW6432', r'C:\Program Files'),
                         ('ProgramFiles(x86)', r'C:\Program Files (x86)')):
        d = os.environ.get(var, default)
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def find_extractor():
    """쓸 수 있는 압축 해제 도구를 찾는다.

    반환: `('7z', [실행경로])` 또는 `('bandizip', [실행경로])`, 없으면 None.
    7-Zip 을 먼저 본다(종료 코드가 정직하다). 환경변수 `O2K_EXTRACTOR` 로 강제할 수 있는데,
    값이 `7z`/`bandizip` 종류를 유추할 수 없는 이름이면 7-Zip 문법으로 취급한다.

    **`7za.exe`/`7zr.exe` 단독판은 NSIS 설치본을 열지 못하므로 후보에서 뺀다**(실측: 오류 없이
    "Files: 0" 만 나온다). 그것들은 자급 1단계(7-Zip 배포본 풀기)에서만 쓴다."""
    forced = os.environ.get('O2K_EXTRACTOR')
    if forced and os.path.isfile(forced):
        kind = 'bandizip' if 'bandizip' in forced.lower() or os.path.basename(
            forced).lower().startswith('bz') else '7z'
        return kind, [forced]

    w = shutil.which('7z')                 # 정식 7z.exe 만 — 7za/7zr 은 NSIS 를 못 연다
    if w:
        return '7z', [w]
    for base in _program_dirs():
        cand = os.path.join(base, '7-Zip', '7z.exe')
        if os.path.isfile(cand):
            return '7z', [cand]

    w = shutil.which('bz')
    if w:
        return 'bandizip', [w]
    for base in _program_dirs():
        cand = os.path.join(base, 'Bandizip', 'bz.exe')
        if os.path.isfile(cand):
            return 'bandizip', [cand]
    return None


def extractor_argv(kind, cmd, installer, dest_dir):
    """압축 해제 명령행. **설치 파일은 언제나 '인자'이지 실행 대상이 아니다.**"""
    if kind == 'bandizip':
        return list(cmd) + ['x', '-o:' + dest_dir, '-y', installer]
    return list(cmd) + ['x', '-y', '-o' + dest_dir, installer]


# ---------- 7-Zip 자급(압축 도구가 없는 PC 용) ----------

# download.html 안의 x64 설치 파일 이름. 최신판은 GitHub 릴리스 링크로 걸려 있지만
# 파일 이름 규칙은 같으므로 여기서는 '버전 번호'만 뽑는다.
_SEVENZIP_NAME_RE = re.compile(r'7z(\d{4})-x64\.exe', re.IGNORECASE)


def parse_7zip_download_page(html):
    """7-Zip 다운로드 페이지에서 **가장 새로운** x64 설치 파일 `(파일명, URL)` 을 고른다.

    순수 함수(네트워크 없음). 못 찾으면 None.
    페이지가 최신판을 GitHub 릴리스로 링크해도(현재 그렇다) 같은 파일이 `www.7-zip.org/a/`
    아래에도 있으므로(실측) URL 은 언제나 그쪽으로 만든다 — 리다이렉트를 타지 않는다."""
    nums = _SEVENZIP_NAME_RE.findall(html or '')
    if not nums:
        return None
    best = max(nums, key=lambda n: int(n))
    name = f'7z{best}-x64.exe'
    return name, SEVENZIP_URL_FORMAT.format(name)


def latest_7zip_installer_url(timeout=30, page_url=SEVENZIP_DOWNLOAD_PAGE):
    """최신 7-Zip x64 배포본 `(파일명, URL)`. 페이지를 못 읽으면 기본값으로 물러선다."""
    _require_https(page_url, 'download page')
    req = urllib.request.Request(page_url, headers={'User-Agent': _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode('utf-8', 'replace')
    except _NET_ERRORS:              # 페이지를 못 읽어도 치명적이지 않다 - 기본값으로 간다
        html = ''
    found = parse_7zip_download_page(html)
    if found:
        return found
    return FALLBACK_7ZIP_NAME, SEVENZIP_URL_FORMAT.format(FALLBACK_7ZIP_NAME)


def _sevenzip_works(exe, probe_archive=None):
    """이 `7z.exe` 가 쓸 만한지 확인한다: 배너에 '7-Zip' 이 있고, 주어진 압축 파일을
    실제로 나열(`7z l`)할 수 있는가."""
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run([exe], capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    if '7-Zip' not in ((r.stdout or '') + (r.stderr or '')):
        return False
    if probe_archive and os.path.isfile(probe_archive):
        try:
            r2 = subprocess.run([exe, 'l', probe_archive], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=300)
        except (OSError, subprocess.SubprocessError):
            return False
        if r2.returncode != 0 or 'files' not in (r2.stdout or '').lower():
            return False
    return True


def _write_source_note(dest_dir, entries):
    """어디서 무엇을 받아 풀었는지 기록한다(라이선스 고지 겸용). 내용은 ASCII."""
    lines = ['7-Zip console build provisioned by orcad2kicad.',
             'Downloaded and EXTRACTED ONLY - the 7-Zip installer was never executed.',
             '']
    for url, path in entries:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        lines.append(f'{url}  ->  {os.path.basename(path)}  ({size} bytes)')
    lines += ['',
              '7zr.exe is public domain. The full 7-Zip distribution is licensed under the',
              'GNU LGPL (with an unRAR restriction); see License.txt next to 7z.exe.',
              'Source code: https://www.7-zip.org/download.html',
              f'provisioned at {time.strftime("%Y-%m-%dT%H:%M:%S")}']
    try:
        with open(os.path.join(dest_dir, 'SOURCE.txt'), 'w', encoding='ascii') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass


def ensure_extractor(root=DEFAULT_ROOT, log=None, allow_download=True,
                     sevenzr=None, sevenzip_installer=None, probe_archive=None):
    """쓸 수 있는 압축 해제 도구를 반드시 확보한다 — 없으면 7-Zip 콘솔판을 스스로 마련한다.

    순서:
      (a) 시스템에 설치된 정식 7-Zip(`7z.exe`) 또는 Bandizip (`find_extractor()`)
      (b) 지난번에 마련해 둔 `<root>/tools/7zip/7z.exe`
      (c) 자급: `7zr.exe` 다운로드 -> 7-Zip x64 배포본 다운로드 -> `7zr x` 로 풀기 ->
          `7z.exe` 동작 확인. **설치 프로그램은 실행하지 않는다.**

    `sevenzr`/`sevenzip_installer` 로 이미 받아 둔 파일을 줄 수 있고(환경변수 `O2K_7ZR_EXE`,
    `O2K_7Z_INSTALLER` 도 같은 뜻), 그 경우 그만큼 다운로드를 건너뛴다.
    `allow_download=False` 면 (a)(b) 로 못 찾았을 때 곧바로 PortableError."""
    found = find_extractor()
    if found:
        return found

    tools = os.path.join(root, TOOLS_SUBDIR)
    sevenzip_dir = os.path.join(root, SEVENZIP_SUBDIR)
    ready = os.path.join(sevenzip_dir, '7z.exe')
    if _sevenzip_works(ready):
        _log(log, f'extractor: reusing {ready}')
        return '7z', [ready]

    sevenzr = sevenzr or os.environ.get('O2K_7ZR_EXE')
    sevenzip_installer = sevenzip_installer or os.environ.get('O2K_7Z_INSTALLER')
    if not allow_download and not (sevenzr and sevenzip_installer):
        raise PortableError(NO_EXTRACTOR_HINT)

    os.makedirs(tools, exist_ok=True)
    entries = []

    # 1단계 — 7zr.exe (퍼블릭 도메인 단독판). NSIS 는 못 열지만 7-Zip 배포본은 풀 수 있다.
    if sevenzr and os.path.isfile(sevenzr):
        _log(log, f'7zr: using {sevenzr}')
        entries.append(('(local file)', sevenzr))
    else:
        sevenzr = os.path.join(tools, '7zr.exe')
        if not os.path.isfile(sevenzr):
            if not allow_download:
                raise PortableError(NO_EXTRACTOR_HINT)
            _log(log, 'tools: fetching the 7-Zip console build (about 6 MB, extract only)')
            download(SEVENZR_URL, sevenzr, log=log)
        entries.append((SEVENZR_URL, sevenzr))

    # 2단계 — 정식 7-Zip x64 배포본(7z SFX). 받아서 '풀기만' 한다.
    if sevenzip_installer and os.path.isfile(sevenzip_installer):
        _log(log, f'7-zip archive: using {sevenzip_installer}')
        entries.append(('(local file)', sevenzip_installer))
    else:
        if not allow_download:
            raise PortableError(NO_EXTRACTOR_HINT)
        name, url = latest_7zip_installer_url()
        sevenzip_installer = os.path.join(tools, name)
        if not os.path.isfile(sevenzip_installer):
            download(url, sevenzip_installer, log=log)
        entries.append((url, sevenzip_installer))

    # 3단계 — 7zr 로 풀기. 설치기로 실행하지 않는다(설치본은 언제나 마지막 '인자').
    os.makedirs(sevenzip_dir, exist_ok=True)
    argv = extractor_argv('7z', [sevenzr], sevenzip_installer, sevenzip_dir)
    _log(log, f'tools: extracting 7-Zip into {sevenzip_dir}')
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise PortableError(f'cannot extract the 7-Zip archive: {e}')
    if not _sevenzip_works(ready, probe_archive=probe_archive):
        tail = ((proc.stdout or '') + (proc.stderr or '')).strip().splitlines()[-5:]
        raise PortableError(f'7-Zip provisioning failed (rc={proc.returncode}): {ready} does not '
                            'work; ' + ' | '.join(t.strip() for t in tail))
    if not os.path.isfile(os.path.join(sevenzip_dir, 'License.txt')):
        _log(log, 'warning: License.txt not found next to 7z.exe')
    _write_source_note(sevenzip_dir, entries)
    _log(log, f'extractor: 7z ({ready}, provisioned, not installed)')
    return '7z', [ready]


# ---------- 다운로드 ----------

def download(url, dest_file, log=None, timeout=60, chunk=1 << 20):
    """`url` 을 `dest_file` 로 내려받는다(스트리밍).

    `.part` 임시 파일에 받은 뒤 마지막에 rename 한다 — 중간에 끊긴 파일이 완성본으로 남지
    않게 하기 위해서다. 진행률은 약 10% 단위로 로그에 남긴다(ASCII)."""
    _require_https(url, 'download URL')
    parent = os.path.dirname(os.path.abspath(dest_file))
    if parent:
        os.makedirs(parent, exist_ok=True)
    part = dest_file + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    _log(log, f'download: {os.path.basename(dest_file)}')
    started = time.time()
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            done, next_mark = 0, 10
            with open(part, 'wb') as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if total:
                        pct = done * 100 // total
                        if pct >= next_mark:
                            _log(log, f'download: {pct}% ({done}/{total} bytes)')
                            next_mark = (pct // 10 + 1) * 10
        os.replace(part, dest_file)
        ok = True
    # 연결이 끊기면 urllib 는 OSError 가 아니라 http.client.IncompleteRead 를 내고,
    # 주소 형식이 틀리면 ValueError 를 낸다. 셋 다 여기서 안내 메시지로 바꾼다.
    except _NET_ERRORS as e:
        raise PortableError(f'download failed ({url}): {e}. Check the network/proxy and try '
                            'again, or download the file yourself and pass it in.')
    finally:
        if not ok:                     # 반쯤 받은 .part 는 어떤 경로로 끝나든 남기지 않는다
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
    _log(log, f'download: done, {os.path.getsize(dest_file)} bytes in {time.time() - started:.0f} s')
    return dest_file


# ---------- 압축 해제 ----------

def extract_installer(installer, dest_dir, extractor=None, log=None, timeout=1800,
                      root=None, allow_download=True):
    """NSIS 설치 파일을 `dest_dir` 에 **압축 해제만** 한다(설치기는 실행하지 않는다).

    `extractor` 는 `find_extractor()`/`ensure_extractor()` 가 준 `(kind, cmd)`; 주지 않으면
    `ensure_extractor(root or dest_dir, allow_download=...)` 로 확보한다(없으면 7-Zip 콘솔판을
    스스로 마련한다). Bandizip 은 성공해도 종료 코드 2 를 내는 일이 있어, 종료 코드보다
    결과물(`bin/kicad-cli.exe`)의 존재를 성공 판정 기준으로 삼는다."""
    if not os.path.isfile(installer):
        raise PortableError(f'installer not found: {installer}')
    if extractor is None:
        extractor = ensure_extractor(root=root or dest_dir, log=log,
                                     allow_download=allow_download, probe_archive=installer)
    kind, cmd = extractor
    os.makedirs(dest_dir, exist_ok=True)
    argv = extractor_argv(kind, cmd, installer, dest_dir)
    _log(log, f'extract: {kind} -> {dest_dir}')
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise PortableError(f'extract failed to start: {e}')
    cli = cli_path_in(dest_dir)
    if not os.path.isfile(cli):
        tail = ((proc.stdout or '') + (proc.stderr or '')).strip().splitlines()[-5:]
        raise PortableError(f'extract failed (rc={proc.returncode}): {_cli_name()} not found under '
                            f'{dest_dir}; ' + ' | '.join(t.strip() for t in tail))
    _log(log, f'extract: ok (rc={proc.returncode}) in {time.time() - started:.0f} s')
    return dest_dir


def dir_size(path):
    """디렉터리 전체 바이트 수(보고서·로그용). 읽을 수 없는 항목은 건너뛴다."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def trim_portable(dest_dir, log=None):
    """kicad-cli 실행에 필요 없는 것(데모·번역·설치기 잔여물)을 지운다.

    반환: 실제로 지운 경로 목록. 없는 항목은 조용히 건너뛴다."""
    removed = []
    for rel in TRIM_TARGETS:
        path = os.path.join(dest_dir, rel)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
            else:
                continue
        except OSError as e:
            _log(log, f'trim: cannot remove {rel}: {e}')
            continue
        removed.append(path)
        _log(log, f'trim: removed {rel}')
    return removed


# ---------- 검증 ----------

def verify_portable(dest_dir):
    """압축을 푼 디렉터리의 kicad-cli 를 실제로 실행해 확인한다.

    반환: `{'cli': 경로, 'version': '10.99.0', 'orcad_import': True/False}`.
    실행 파일이 없거나 `version` 조차 못 찍으면 PortableError."""
    cli = cli_path_in(dest_dir)
    if not os.path.isfile(cli):
        raise PortableError(f'kicad-cli not found: {cli}')
    from .kicad_netlist import cli_version, supports_orcad_import   # 순환 import 회피용 지연 import
    version = cli_version(cli)
    if not version:
        raise PortableError(f'kicad-cli cannot run: {cli}')
    return {'cli': cli, 'version': version, 'orcad_import': bool(supports_orcad_import(cli))}


# ---------- 전체 절차 ----------

def portable_clis(root=DEFAULT_ROOT):
    """이 도구가 준비해 둔 포터블 kicad-cli 경로 목록(최신 우선). `kicad_netlist` 가 쓴다."""
    if not root or not os.path.isdir(root):
        return []
    name = _cli_name()
    found = set(glob.glob(os.path.join(root, 'bin', name)))
    found |= set(glob.glob(os.path.join(root, '*', 'bin', name)))
    return sorted(found, reverse=True)


def read_portable_info(root=DEFAULT_ROOT):
    """`portable.json`(준비 기록). 없거나 깨졌으면 None."""
    try:
        with open(os.path.join(root, PORTABLE_JSON), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clear_extracted(root, log=None):
    """`force` 재설치 전에 이전 빌드의 잔재를 지운다(우리가 만든 디렉터리 안쪽만 건드린다)."""
    for rel in ('bin', 'lib', 'share', 'etc', '$PLUGINSDIR'):
        path = os.path.join(root, rel)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            _log(log, f'force: removed old {rel}')


def ensure_portable_kicad(root=DEFAULT_ROOT, force=False, log=None, installer=None,
                          arch='x86_64', lite=True, download_tools=True):
    """포터블 나이틀리 kicad-cli 를 준비하고 그 경로를 돌려준다.

    - 이미 `root/bin/kicad-cli.exe` 가 있고 `force` 가 아니면 그대로 돌려준다(네트워크 없음).
    - `installer` 를 주면 그 파일을 쓴다(다운로드 없음). 없으면 최신 나이틀리를
      `root/.cache/` 로 내려받는다(같은 이름이 이미 있으면 재사용).
    - 압축 해제 -> 정리(trim) -> 실행 확인 -> `root/portable.json` 기록 순서.
    - 압축 도구가 없으면 7-Zip 콘솔판을 `root/tools/` 아래에 스스로 마련한다
      (`download_tools=False` 면 마련하지 않고 안내 메시지와 함께 실패).
    - **설치 프로그램(KiCad·7-Zip 어느 쪽이든)은 어떤 경우에도 실행하지 않는다.**"""
    root = root or DEFAULT_ROOT
    cli = cli_path_in(root)
    if os.path.isfile(cli) and not force:
        _log(log, f'portable kicad: already prepared at {root}')
        return cli

    if installer and not os.path.isfile(installer):   # 디렉터리를 만들기 전에 인자부터 검사
        raise PortableError(f'installer not found: {installer}')
    os.makedirs(root, exist_ok=True)
    extractor = ensure_extractor(root=root, log=log, allow_download=download_tools,
                                 probe_archive=installer if installer else None)
    _log(log, f'extractor: {extractor[0]} ({extractor[1][0]})')

    os.makedirs(root, exist_ok=True)
    if force:
        _clear_extracted(root, log=log)

    url = None
    if installer:
        filename = os.path.basename(installer)
        _log(log, f'installer: {filename} (local file, no download)')
    else:
        filename, url = latest_nightly(arch=arch, lite=lite)
        _log(log, f'latest nightly: {filename}')
        installer = os.path.join(root, '.cache', filename)
        if os.path.isfile(installer) and os.path.getsize(installer) > 0:
            _log(log, f'installer: cached {installer}')
        else:
            download(url, installer, log=log)

    extract_installer(installer, root, extractor=extractor, log=log)
    before = dir_size(root)
    trim_portable(root, log=log)
    after = dir_size(root)
    _log(log, f'size: {before // (1 << 20)} MB -> {after // (1 << 20)} MB')

    info = verify_portable(root)
    _log(log, f'kicad-cli: {info["cli"]} version {info["version"]} '
              f'orcad import: {"yes" if info["orcad_import"] else "no"}')
    record = {'filename': filename, 'url': url, 'version': info['version'],
              'orcad_import': info['orcad_import'],
              'extracted_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    try:
        with open(os.path.join(root, PORTABLE_JSON), 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except OSError as e:                       # 기록 실패는 치명적이지 않다
        _log(log, f'warning: cannot write {PORTABLE_JSON}: {e}')
    return info['cli']
