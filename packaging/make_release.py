"""공개 릴리스 zip 조립 스크립트 — exe 빌드(옵션) + 문서 복사 + 금지 문자열 검사 + zip + SHA-256.

사용:
  python packaging/make_release.py                  # build_exe.cmd 로 exe 를 빌드한 뒤 조립
  python packaging/make_release.py --no-build        # 이미 dist/ 에 있는 exe 로 조립만 함
  python packaging/make_release.py --version-check   # 조립한 exe 의 --version 출력이
                                                       # branding.VERSION 과 같은지 확인

결과: dist/release/orcad2kicad-<VERSION>-win64/ (폴더) 와
      dist/release/orcad2kicad-<VERSION>-win64.zip + dist/release/SHA256SUMS.txt

공개 검사: 복사한 문서에 packaging/release/forbidden.txt 의 문자열(고객명·로컬 경로 등,
대소문자 무시)이 있으면 조립을 실패시킨다(exit 1, 파일:행 출력). 코드(exe)는 검사 대상이
아니다(사람이 읽는 데이터가 없으므로).
"""
from __future__ import annotations
import argparse
import hashlib
import os
import subprocess
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(REPO_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from orcad2kicad.branding import VERSION  # noqa: E402

PACKAGING_DIR = os.path.dirname(os.path.abspath(__file__))
RELEASE_SRC = os.path.join(PACKAGING_DIR, 'release')      # forbidden.txt, 운영 문서(비공개)
DOC_SRC = REPO_ROOT                                         # README/LICENSE/고지/CHANGELOG 원본(저장소 루트)
FORBIDDEN_FILE = os.path.join(RELEASE_SRC, 'forbidden.txt')
BUILD_CMD = os.path.join(PACKAGING_DIR, 'build_exe.cmd')

# 릴리스 zip 에 들어가는 저장소 루트 문서(고정 순서). packaging/release/ 의 GITHUB_SETUP.md 와
# WEBSITE_PAGE_ko.md 는 저장소·웹사이트 관리용이라 zip 에는 넣지 않는다.
DOC_FILES = ('README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md')
# docs/ 아래에서 그대로 복사할 사용자 문서.
USER_DOC_FILES = ('Quick_Start.md', 'Quick_Start.en.md', '사용자설명서.md', 'User_Manual.en.md')
EXE_NAMES = ('orcad2kicad.exe', 'orcad2kicad-cli.exe')


def load_forbidden(path=FORBIDDEN_FILE):
    """forbidden.txt 를 읽어 문자열 목록으로 돌려준다(빈 줄 제외).

    공개 저장소에는 이 파일을 넣지 않는다(목록 자체가 비공개 정보) — 없으면 빈 목록(검사 생략)."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def check_forbidden(paths, forbidden=None):
    """paths 각 파일에서 forbidden 문자열(대소문자 무시)을 찾는다.

    돌려주는 값: [(path, lineno, matched_term), ...] (없으면 빈 리스트). 파일이 없으면
    조용히 건너뛴다(호출자가 파일 존재를 이미 보장하거나, 별도로 확인한다)."""
    forbidden = load_forbidden() if forbidden is None else forbidden
    lowered = [(term, term.lower()) for term in forbidden]
    hits = []
    for path in paths:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            low = line.lower()
            for term, term_low in lowered:
                if term_low in low:
                    hits.append((path, lineno, term))
    return hits


def build_exes(cli_only=False):
    """packaging/build_exe.cmd 로 exe 를 빌드한다(exe 가 잠겨 있으면 PyInstaller 가 명확한
    오류를 낸다 - 여기서는 그 종료 코드만 그대로 전달)."""
    cmdline = f'"{BUILD_CMD}"' + (' --cli-only' if cli_only else '')
    r = subprocess.run(cmdline, cwd=REPO_ROOT, shell=True)
    if r.returncode != 0:
        raise RuntimeError(f'build_exe.cmd failed with exit code {r.returncode} '
                            f'(exe may be locked by a running instance - close it and retry)')


def assemble_release_dir(dest_dir, dist_dir=None, docs_root=None, cli_only=False):
    """exe(들) + 문서를 dest_dir 에 모은다. 존재하지 않는 파일은 RuntimeError.

    `dist_dir`/`docs_root` 는 테스트에서 실제 exe/문서 대신 더미 파일을 쓸 수 있게 하는
    오버라이드(기본은 저장소의 dist/, 저장소 루트)."""
    dist_dir = dist_dir or os.path.join(REPO_ROOT, 'dist')
    docs_root = docs_root or REPO_ROOT
    os.makedirs(dest_dir, exist_ok=True)

    exe_names = ('orcad2kicad-cli.exe',) if cli_only else EXE_NAMES
    for exe in exe_names:
        src = os.path.join(dist_dir, exe)
        if not os.path.isfile(src):
            raise RuntimeError(f'missing built exe: {src} (build it first, or pass --no-build '
                                f'only after a successful build)')
        _copy(src, os.path.join(dest_dir, exe))

    for name in DOC_FILES:
        src = os.path.join(DOC_SRC, name)
        if not os.path.isfile(src):
            raise RuntimeError(f'missing release doc: {src}')
        _copy(src, os.path.join(dest_dir, name))

    out_docs = os.path.join(dest_dir, 'docs')
    os.makedirs(out_docs, exist_ok=True)
    for name in USER_DOC_FILES:
        src = os.path.join(docs_root, 'docs', name)
        if not os.path.isfile(src):
            raise RuntimeError(f'missing user doc: {src}')
        _copy(src, os.path.join(out_docs, name))
    return dest_dir


def _copy(src, dst):
    with open(src, 'rb') as f_in, open(dst, 'wb') as f_out:
        f_out.write(f_in.read())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def make_zip(src_dir, zip_path):
    """src_dir 전체를 그 폴더 이름을 최상위 항목으로 삼아 zip_path 로 압축한다."""
    base = os.path.basename(os.path.normpath(src_dir))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.join(base, os.path.relpath(full, src_dir))
                zf.write(full, rel)
    return zip_path


def version_check(dest_dir):
    """dest_dir 의 orcad2kicad-cli.exe --version 출력에 branding.VERSION 이 들어 있는지 확인한다
    (GUI exe 는 인자 파싱을 하지 않으므로 --version 은 CLI exe 로만 확인한다)."""
    exe = os.path.join(dest_dir, 'orcad2kicad-cli.exe')
    r = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=60)
    out = (r.stdout or '') + (r.stderr or '')
    if VERSION not in out:
        raise RuntimeError(f'{exe} --version does not report {VERSION!r}: {out!r}')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Assemble the orcad2kicad public release zip (exe + docs).')
    ap.add_argument('--no-build', action='store_true',
                     help='skip build_exe.cmd; assemble from the exe(s) already in dist/')
    ap.add_argument('--cli-only', action='store_true',
                     help='package only orcad2kicad-cli.exe (no GUI exe)')
    ap.add_argument('--version-check', action='store_true',
                     help='after zipping, run the packaged CLI exe --version and confirm it '
                          'reports branding.VERSION')
    ap.add_argument('--dist-dir', default=None, help=argparse.SUPPRESS)   # 테스트용
    ap.add_argument('--out-dir', default=None, help=argparse.SUPPRESS)    # 테스트용
    ap.add_argument('--docs-root', default=None, help=argparse.SUPPRESS)  # 테스트용
    args = ap.parse_args(argv)

    if not args.no_build:
        print('Building exe(s)...')
        build_exes(cli_only=args.cli_only)

    release_name = f'orcad2kicad-{VERSION}-win64'
    out_root = args.out_dir or os.path.join(REPO_ROOT, 'dist', 'release')
    dest_dir = os.path.join(out_root, release_name)
    if os.path.isdir(dest_dir):
        for root, dirs, files in os.walk(dest_dir, topdown=False):
            for fn in files:
                os.remove(os.path.join(root, fn))
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        os.rmdir(dest_dir)

    print(f'Assembling {dest_dir} ...')
    assemble_release_dir(dest_dir, dist_dir=args.dist_dir, docs_root=args.docs_root,
                          cli_only=args.cli_only)

    print('Checking for forbidden strings in the copied release docs...')
    doc_paths = [os.path.join(dest_dir, name) for name in DOC_FILES]
    doc_paths += [os.path.join(dest_dir, 'docs', name) for name in USER_DOC_FILES]
    hits = check_forbidden(doc_paths)
    if hits:
        sys.stderr.write('FORBIDDEN STRINGS FOUND - release blocked:\n')
        for path, lineno, term in hits:
            sys.stderr.write(f'  {path}:{lineno}: {term!r}\n')
        return 1
    print(f'  OK - no forbidden strings in {len(doc_paths)} files.')

    zip_path = os.path.join(out_root, release_name + '.zip')
    print(f'Zipping {dest_dir} -> {zip_path}')
    make_zip(dest_dir, zip_path)
    digest = sha256_file(zip_path)
    sums_path = os.path.join(out_root, 'SHA256SUMS.txt')
    with open(sums_path, 'w', encoding='ascii', newline='\n') as f:
        f.write(f'{digest}  {os.path.basename(zip_path)}\n')
    print(f'SHA256  {digest}  {os.path.basename(zip_path)}')

    if args.version_check:
        print('Checking packaged CLI exe --version ...')
        version_check(dest_dir)
        print('  OK - version matches.')

    print(f'Release ready: {zip_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
