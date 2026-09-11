"""IPC-D-356 / IPC-D-356A 넷리스트(Cadence Allegro 등에서 내보낸 것) 파서.

고정 컬럼 텍스트 포맷. 넷 멤버십(REF.PIN)에 필요한 건 피처 레코드 두 종류뿐이다
(컬럼은 1-based):
    1-3   레코드 종류 — 317: through-hole/도금 피처, 327: SMD 피처
    4-17  넷 이름 (14자, 공백으로 채움)
    18-20 레퍼런스 디자이너 접두(보통 공백)
    21-26 레퍼런스 디자이너
    27    '-'
    28-31 핀 번호
    (그 뒤 피처 종류·좌표는 넷 비교에 쓰지 않는다)

무시하는 레코드:
    - `P ` 로 시작하는 파라미터(`P  VER IPC-D-356A` 등), `C ` 로 시작하는 주석, `999` 종료.
    - 연속 레코드 `017`/`027` — 같은 REF.PIN 을 다른 접근 레이어로 반복할 뿐이라 317/327 로
      이미 잡힌 것과 같은 정보다.
    - 레퍼런스 디자이너가 `VIA` 인 레코드 — 비아는 넷의 끝점(부품 핀)이 아니다.
    - 넷 이름이 `N/C`(또는 빈 문자열)인 레코드 — 미접속 핀.
    - 위 넷/피처 레코드 형식 외의 3자리 코드(예: 보드 외곽 테스트점 `367` 등)는 넷 멤버십과
      무관하므로 건너뛴다.

파일은 latin-1 로 읽는다(아스키 범위 밖 바이트가 섞여 있어도 깨지지 않게). CRLF/트레일링
공백은 raw 줄에서 그대로 잘라낸다.
"""
from __future__ import annotations

from .pads_netlist import PadsNetlist

_FEATURE_CODES = ('317', '327')
_CONTINUATION_CODES = ('017', '027')
_END_CODE = '999'


def parse_ipc356(text: str) -> PadsNetlist:
    nl = PadsNetlist(source_format='IPC-D-356')
    for raw in text.splitlines():
        line = raw.rstrip('\r\n')
        if not line.strip():
            continue
        code = line[:3]
        if code == _END_CODE:
            break
        if code in _CONTINUATION_CODES:
            continue
        if code not in _FEATURE_CODES:
            continue   # P/C 헤더, 기타 레코드 종류 — 넷 멤버십과 무관
        net_name = line[3:17].strip()
        ref = line[20:26].strip()
        pin = line[27:31].strip()
        if not net_name or net_name == 'N/C':
            continue   # 미접속
        if not ref or ref == 'VIA' or not pin:
            continue   # 비아이거나 핀 번호가 없는 레코드
        nl.nets.setdefault(net_name, set()).add(f'{ref}.{pin}')
    return nl


def load_ipc356(path: str) -> PadsNetlist:
    with open(path, encoding='latin-1') as f:
        return parse_ipc356(f.read())


def is_ipc356_file(path: str) -> bool:
    """확장자로 판별이 안 될 때 내용을 살짝 들여다보고 IPC-D-356 인지 짐작한다.

    파일 앞부분에 `IPC-D-356` 문자열이 있으면 바로 그렇다고 본다(대부분의 Allegro 출력에
    `P  VER IPC-D-356A` 줄이 있다). 아니면 첫 비어있지 않은 줄이 파라미터(`P `)·주석(`C `)·
    3자리 레코드 코드로 시작하는지만 본다 — PADS2000 ASCII 는 항상 `*PADS2000*` 으로
    시작하므로 이 조건에 걸리지 않는다."""
    try:
        with open(path, encoding='latin-1', errors='replace') as f:
            chunk = f.read(4096)
    except OSError:
        return False
    if 'IPC-D-356' in chunk.upper():
        return True
    for raw in chunk.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('P ') or line.startswith('C '):
            return True
        code = line[:3]
        return len(code) == 3 and code.isdigit()
    return False
