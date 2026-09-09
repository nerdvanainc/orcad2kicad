# Changelog

이 파일은 orcad2kicad의 릴리스별 변경 사항을 기록합니다.

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
