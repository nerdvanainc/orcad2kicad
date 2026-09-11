# Changelog

이 파일은 orcad2kicad의 릴리스별 변경 사항을 기록합니다.

## 1.0.2 (unreleased)

- 기준 넷리스트(`--netlist`/GUI "PADS 넷리스트(.asc) 또는 IPC-D-356"/MCP `convert`·`verify`·
  `board_diff` 의 `netlist`)가 PADS2000 ASCII `.asc` 뿐 아니라 Cadence Allegro 등에서 내보낸
  IPC-D-356/IPC-D-356A 넷리스트(`.ipc`/`.356`/`.d356`, 확장자 없거나 다른 경우 내용으로도
  판별)도 받는다(`src/orcad2kicad/ipc356.py`, `pads_netlist.load_reference_netlist`). [3]
  검증 등 기존 소비 코드는 변경 없이 그대로 동작 — 실측(Raspberry Pi CMIO 보드, IPC-D-356
  152개 다핀 넷)으로 확인했다.

## 1.0.1 (2026-09-11)

나이틀리 OrCAD 임포터 결과의 결함 정리와 GUI 사용성 개선, 그리고 같은 폴더에 다시 변환할 때의
버그 수정입니다.

- 임포터 결함 후처리(`kicad_cleanup`, 기본 켜짐, `--no-import-cleanup`로 끔): `.DSN`/`.kicad_pro`
  입력에서 나이틀리 임포터가 교차점마다 배선을 끊고 그래픽 선으로 메워 두던 "hop 갭"을 하나의
  배선으로 병합(넷리스트 전후 동일, ERC `unconnected_wire_endpoint`·`endpoint_off_grid` 대폭 감소),
  OrCAD 타이틀블록과 KiCad 기본 도면 양식이 겹쳐 보이던 문제를 빈 도면 양식(`blank.kicad_wks`)으로 해소,
  배선 중간에 얹혀 있던 넷 별칭 라벨을 배선의 자유단으로 옮기고 회전을 맞춰(`snap_labels_to_wire_ends`)
  hop 갭 병합만으로는 남아 있던 `unconnected_wire_endpoint`까지 실측 프로젝트에서 0건까지 마저 해소
- 버그 수정: 같은 출력 폴더에 `.DSN`을 다시 변환하면 이전 실행의 첫 페이지 파일이 남아 정식 포맷
  재구성이 건너뛰어지고(루트가 나이틀리 포맷으로 남아 KiCad 10.0이 열지 못함) 첫 페이지가 옛 내용으로
  남던 문제 — 포맷 판정을 시트 중 최대 버전으로, 재임포트 전 이전 시트 파일 삭제
- GUI: 언어 선택에 "자동(시스템 언어)" 추가(기본값, 시작할 때마다 OS 로캘을 다시 감지하고 감지
  결과를 저장하지 않음), 로그 줄 종류별 색 구분, 탭 강조와 항목 개수 표시, 상태 문구 강조와 상태별 색,
  종료 코드가 0이어도 확인할 항목이 있으면 "확인할 항목 있음"으로 표시, 보드 차이 탭에 선택한 행의
  상세 창, 글꼴·여백 테마(맑은 고딕/Segoe UI 10pt), 앱·exe 아이콘
- 결과 요약 끝의 회사 문의 문구 제거
- 검증 표기: 로그·요약·검증 탭의 "vs PADS" 를 "vs 기준 넷리스트(형식)" 로 바꿔 IPC-D-356 기준일 때도 맞게
  표시(`PipelineResult.ref_format`, JSON `ref_format`).
- 결과 탭: 요약 설명이 남는 공간을 다 쓰고 스크롤바가 붙으며 결론 줄이 보이도록 정렬. ERC 가 전부 정보성이면
  유형별 줄 대신 한 줄로 요약. 기본 창 높이 960.
- 설명서: 결과는 `.kicad_pro` 로 열 것(하위 시트 파일을 단독으로 열면 KiCad 기본 도면 양식이 겹쳐 보임) 안내.


## 1.0.0 (2026-09-09)

첫 공개 릴리스. 소스 코드를 GitHub에 MIT 라이선스로 공개하며, Windows용
단일 파일 exe 릴리스도 함께 제공합니다.

- 소스 공개: GitHub(`github.com/nerdvanainc/orcad2kicad`)에 MIT 라이선스로
  공개, Windows exe 릴리스와 병행 배포
- 입력 모드: EDIF 2.0.0 익스포트 / OrCAD 네이티브 `.DSN` / 이미 KiCad로 임포트해 둔
  `.kicad_pro` 프로젝트, 세 가지 입력 방식 지원
- 결정적(deterministic) EDIF → KiCad 변환 파이프라인: `.kicad_sym`, `.kicad_sch`,
  `.kicad_pro`를 직접 생성
- PADS `.asc` 기준 넷리스트 검증: 커넥티비티 조인 검사, 배선 지오메트리 검사,
  kicad-cli로 내보낸 넷리스트 대조까지 서로 독립적인 3중 검증
- PADS 보드 연동: 풋프린트 라이브러리 추출, 보드 vs 회로도 레퍼런스/핀 대조,
  4차 검증 패스
- 나이틀리 KiCad 포터블 준비 기능: 설치 프로그램 실행 없이 다운로드·압축 해제만
  수행하여, OrCAD 없이도 `.DSN` 파일을 읽을 수 있는 환경을 구성
- 나이틀리로 임포트된 프로젝트를 정식(stable) 포맷으로 자동 재구성하여, 정식
  KiCad 10.0에서도 문제없이 열리도록 처리
- GUI(tkinter): 검증 결과 / 이슈 / 보드 차이 / 에이전트 제안 탭을 갖춘 그래픽
  인터페이스
- CLI: 스크립트·자동화에서 사용할 수 있는 커맨드라인 인터페이스
- MCP 서버(stdio, JSON-RPC): Claude Code 등 AI 코딩 도구가 변환·검증 기능을
  툴로 직접 호출할 수 있도록 지원
- 선택적 AI 백엔드(Claude API / claude-cli / codex-cli) 연동: 핀 타입 추정,
  넷 차이 분석, 리뷰 제안 기능 — 어디까지나 제안만 하며 결과는 자동 적용되지
  않고 사용자가 GUI에서 직접 검토·결정
- Windows용 단일 파일 exe 패키징(GUI 포함 버전 / CLI 전용 버전 두 가지)
- GUI 한국어/영어 지원(i18n), 영문 사용자 문서(`docs/Quick_Start.en.md`,
  `docs/User_Manual.en.md`), 개발자용 개요 문서(`docs/HOW_IT_WORKS.md`)
