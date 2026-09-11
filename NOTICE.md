# orcad2kicad 고지 (Notice)

이 문서는 orcad2kicad의 사용 범위, 상표, 책임에 관한 고지입니다. 라이선스 본문은 [LICENSE](LICENSE)(MIT)를 참조하십시오.

## OrCAD·상표·책임 범위

- **이 소프트웨어는 OrCAD `.DSN` 파일을 직접 읽거나 변환하지 않습니다.** `.DSN`(Cadence 고유의 바이너리
  형식)의 해석은 전적으로 KiCad 프로젝트가 배포하는 KiCad 자체의 OrCAD 임포터(kicad-cli, 나이틀리
  10.99+)가 수행하며, orcad2kicad는 그 kicad-cli를 실행하고 그 결과(KiCad 파일)를 정리·검증할 뿐입니다.
  orcad2kicad 안에는 `.DSN` 형식을 해석하는 코드가 들어 있지 않습니다. 따라서 `.DSN` 임포트 결과의
  정확성(누락·왜곡 여부)은 KiCad 임포터의 동작에 따르며, 나이틀리는 KiCad 프로젝트의 불안정 빌드입니다.
- **OrCAD 제품의 설치, 라이선스, 라이브러리, DLL 등 어떤 Cadence/OrCAD 파일도 요구하거나 포함하지
  않습니다.** 이 프로젝트는 Cadence의 어떤 파일 형식도 역공학하지 않았습니다. 읽는 형식은 EDIF 2.0.0
  (EIA 공개 표준 — 사용자가 OrCAD에서 직접 내보낸 텍스트 파일), PADS ASCII(사용자가 내보내는 문서화된
  텍스트 형식), IPC-D-356(IPC 표준), KiCad S-expression(공개 형식)뿐입니다.
- **변환 대상에 대한 권리와 결과 사용 책임은 사용자에게 있습니다.** 변환하는 설계 파일과 그 안의 심볼·
  라이브러리 그래픽(추출된 `orcad_import.kicad_sym` 포함)에 대한 권리는 사용자가 보유하고 있어야 하며,
  변환 결과를 어디에 어떻게 쓰는지는 사용자의 책임입니다.
- **검증 결과는 보증이 아닙니다.** 이 도구의 "검증"은 사용자가 제공한 기준 넷리스트와의 대조일 뿐이며,
  설계 자체의 정확성이나 제조 적합성을 보증하지 않습니다. 회로도·보드의 최종 확인 책임은 사용자에게
  있습니다(라이선스의 무보증 조항 참조).
- **이름에 대하여.** "orcad2kicad"라는 이름은 이 도구가 다루는 파일 형식(OrCAD에서 내보낸 파일 → KiCad
  프로젝트)을 설명하기 위한 것일 뿐이며, 어떤 제휴·보증도 뜻하지 않습니다. 이 프로젝트는 Cadence·Siemens·
  KiCad·Raspberry Pi의 로고를 사용하지 않습니다.
- **상표.** OrCAD, Allegro, Cadence는 Cadence Design Systems, Inc.의 상표입니다. PADS는 Siemens Industry
  Software Inc.의 상표입니다. KiCad는 KiCad 프로젝트의 상표입니다. IPC-D-356은 IPC의 표준입니다. Raspberry
  Pi는 Raspberry Pi Ltd의 상표입니다. 이 프로젝트는 위 어느 곳과도 제휴·보증 관계가 없습니다.

## 네트워크·데이터

- 텔레메트리는 없습니다. 네트워크 접근은 (1) 사용자가 명시적으로 요청한 KiCad 나이틀리·7-Zip 콘솔판 다운로드,
  (2) 사용자가 직접 설정·활성화한 AI 백엔드 호출, 두 가지뿐입니다.
- AI 백엔드를 활성화하면 넷 이름·핀 이름·레퍼런스·풋프린트 이름 등 설계 정보의 일부가 선택한 서비스로 전송되며,
  그 처리에는 해당 서비스의 약관과 개인정보 정책이 적용됩니다. 기밀 설계라면 사용 전에 소속 조직의 정책을
  확인하십시오. 기본값(백엔드 없음)에서는 아무것도 전송되지 않습니다.
- KiCad 나이틀리와 7-Zip 다운로드는 각 프로젝트의 공식 배포 서버에서 이루어지며 그 내용물의 무결성·안전성은 각
  배포자의 책임입니다. 조직의 네트워크·소프트웨어 정책상 허용 여부는 사용자가 확인해야 합니다. 두 소프트웨어는
  orcad2kicad에 포함되어 재배포되는 것이 아니며 각자의 라이선스(GPLv3, LGPL)를 따릅니다
  ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

## 예시 화면의 출처

README와 웹사이트의 스크린샷에 쓰인 예시 회로는 Raspberry Pi Compute Module IO Board V3 공개 설계 파일
(`RPI-CMIO-V3_0-PUBLIC.DSN`, © 2015 Raspberry Pi (Trading) Ltd)을 변환한 것입니다. 설계 파일은 이 저장소에
포함되어 있지 않으며, Raspberry Pi Ltd는 이 도구와 무관하고 이 도구를 보증하지 않습니다.
