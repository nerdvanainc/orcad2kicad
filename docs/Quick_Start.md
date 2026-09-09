# orcad2kicad Quick Start (핵심만)

제공: Nerdvana Inc. (㈜너드바나) — https://www.nerdvana.co.kr · 다운로드/문서: https://github.com/nerdvanainc/orcad2kicad

OrCAD `.DSN` 하나를 정식 KiCad 10.0 프로젝트로 바꾸고 검증까지 끝내는 최단 경로다. 자세한 설명은
각 장으로.

**1. 준비 (한 번만)**
- 정식 KiCad 10.0 이 설치돼 있으면 된다(편집·검증은 전부 정식판으로 한다).
- `dist\orcad2kicad.exe` 를 실행한다(설치 없음). 소스라면 `python -m orcad2kicad`.
- 입력 프레임의 **[나이틀리 KiCad 포터블 준비(설치 없음)]** 를 한 번 누른다 — `.DSN` 을 읽는
  임포터가 아직 나이틀리에만 있어서다. 내려받아 압축만 풀며(약 300 MB) 설치 프로그램은 실행하지
  않는다. 이후로는 자동 탐지된다.

**2. 입력 파일 3개 (같은 폴더에 두면 자동으로 채워진다)**
| 파일 | 필수 | 어디서 | 용도 |
|---|---|---|---|
| `이름.DSN` | 필수 | OrCAD 프로젝트 | 회로도 원본 |
| `이름.asc` (PADS2000 넷리스트) | 권장 | OrCAD Create Netlist → PADS | 검증 정답([3]). 없으면 연결 검증을 못 한다 |
| 배선된 PADS Layout ASCII `.asc` | 권장 | PADS Layout File → Export ASCII(전체 섹션) | 풋프린트 라이브러리(`PADS.pretty`)·보드 연결·[4] 대조 |

**3. GUI 실행**
1. 입력 종류에서 **OrCAD .DSN** 을 고르고 `.DSN` 파일을 찾는다. 출력 폴더(`<이름>_kicad`),
   프로젝트 이름, 같은 폴더의 넷리스트/보드가 비어 있던 칸에 자동으로 들어간다.
2. **[변환 실행]**. 상태가 `완료 (모든 검증 PASS)` 이면 끝. `검증 FAIL 있음` 이면 검증 탭의 넷 표를 본다.
3. **결과 탭의 "요약 설명"** 을 읽는다 — 각 검증이 무엇을 확인했는지, ERC 항목이 조치가 필요한지,
   결론이 한 번에 적혀 있다.
4. **[KiCad 프로젝트 열기]** 로 정식 KiCad 10.0 에서 연다. 회로도는 루트 시트 + 페이지별 하위 시트
   구조다.

CLI 로 같은 일:
```
orcad2kicad-cli.exe --dsn 이름.DSN --netlist 이름.asc --board 보드.asc --explain
```
(출력 폴더는 `.DSN` 옆 `이름_kicad`, `-o` 로 바꿀 수 있다.)

**4. 결과 읽는 법 (요약)**
- `[3] … RESULT: PASS` — KiCad 회로도의 연결이 OrCAD 넷리스트와 100% 같다. 이것이 핵심 판정.
- `[4] refs only on board: []`, `BOARD NETS: PASS` — 보드와 회로도의 부품·넷이 같다. 차이가 있으면
  "보드 차이" 탭에서 항목별로 처리한다(보드에만 있는 부품은 회로도에 자리표시 추가/무시 등).
- ERC 수백 건은 대부분 임포터 특성·KiCad 관례 차이(격자 이탈, 미접속 표시 없음, PWR_FLAG 없음)로
  연결과 무관하다. 요약 설명이 유형별로 "정보/확인 권장/조치 필요"를 표시한다.

**5. KiCad 에서 마무리**
- Pcbnew 에서 **Update PCB from Schematic(F8)** 을 실행해 보드와 회로도를 연결한다. 풋프린트는
  `PADS:<데칼>` 로 보드 라이브러리(`PADS.pretty`)와 이어져 있어 부품이 다시 놓이지 않는다.
- 이후 회로도 수정 → F8 반복. 나이틀리는 더 이상 필요 없다.

---

자세한 내용은 같은 폴더의 `사용자설명서.md`(0장: OrCAD 없이 변환하기, 5장: GUI, 6장: CLI, 7장: 검증 결과 읽는 법) 참고.

## 문의

개발 의뢰·문의: Nerdvana Inc. (www.nerdvana.co.kr)
