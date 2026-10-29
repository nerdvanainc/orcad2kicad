# Changelog

이 파일은 orcad2kicad의 릴리스별 변경 사항을 기록합니다.

## 1.0.6 (2026-10-29)

PADS 보드를 함께 줄 때, KiCad PADS 임포터가 틀리게 옮기는 솔더마스크를 PADS 파일의 CAM 설정대로 바로잡습니다.

- **PADS 보드 솔더마스크 정리**(`--board .asc`, 기본 켜짐, `--keep-board-masks` 로 끔): KiCad PADS 임포터의 세 결함을
  임포트 직후 바로잡는다(출력 `.kicad_pcb`·`PADS.pretty` 공통, 패드 크기·구멍·배선·넷 불변).
  (1) CAM 의 "솔더마스크에 패드 포함" 설정을 읽지 않아 스루홀 패드 마스크 개구가 없거나 뒷면만 빠지던 것을 `.asc` 의
  CAM 솔더마스크 문서대로 `F.Mask`/`B.Mask` 복원(비아는 CAM 이 구리 비아를 그리는 면만 개구). (2) 보드 여유값
  `pad_to_mask_clearance` 에 숫자 3 을 파일 단위 그대로 넣던 것(METRIC 3 mm, BASIC 0.000002 mm)을 0 으로. PADS CAM 패드
  오버사이즈는 적용하지 않고 로그·`auto_changes.txt` 로 알린다(쪽당 환산값, 넣으면 미세 피치 IC 핀 사이 마스크가 사라질 수
  있음). `kicad_board.pads_mask_cam` / `restore_pad_mask_openings` / `fix_importer_mask_clearance` /
  `mask_clearance_from_cam`, `result.board_mask`(`cam_clearance`), 자동 변경 목록 `PCB:` 항목(EDIF 입력 경로도 이제 자동
  변경을 고지). 실측: 비공개 실무 PADS 보드 31개 전부 해당(METRIC 14개는 여유값 3 mm), PADS 제조 거버와 대조해 개구 위치
  90/90 일치. (3) 용도 미지정(UNASSIGNED) PADS 레이어 25/28 을 임포터가 F.Mask/B.Mask 로 보내 디칼의 문서용 그림이 마스크
  개구가 되던 것을, CAM 마스크 문서가 그 레이어를 그리지 않을 때 디칼 조각과 좌표·선폭이 맞는 그림만 `Cmts.User` 로
  옮김(`move_spurious_mask_graphics`, 샘플 10개 — KiCad DRC 마스크 위반 199 → 0).
  `.kicad_pcb` 를 직접 주면 고치지 않고 개구 없는 패드 수만 경고.

## 1.0.5 (2026-10-29)

`.DSN` 임포트 결과의 연결 오류(버스로 합쳐진 넷)를 고치고, 남는 숨은(투명) 라벨을 설명·선택할 수 있게 했으며, 도구가
자동으로 바꾼 모든 것을 알리게 했습니다.

- **보드 없이 `--netlist`만 주면 풋프린트 필드를 비워 둔다**(`.DSN`/KiCad 프로젝트 입력): 예전에는 넷리스트의
  풋프린트 이름을 라이브러리 없이(`0603`) 채워 KiCad ERC `footprint_link_issues` 가 부품 수만큼 생겼다. 이제 비워 두고
  요약 이슈 한 줄(`footprints left empty (no board): N refs ...`)과 결과 요약으로 알린다
  (`result.footprint_names_unset`). 보드(`--board`)를 주면 예전처럼 `PADS:<데칼>` 로 채운다.
- **버스 위 접속점으로 합쳐진 넷 나누기(연결이 바뀜)**: 배선이 버스 엔트리 없이 버스에 바로 닿는 점에 나이틀리
  임포터가 찍은 접속점(junction) 때문에 KiCad 가 그 버스에 닿은 배선들을 한 넷으로 합치던 것을, 접속점을 지워
  원래대로 나눈다(`kicad_cleanup.remove_bus_wire_junctions` / `split_bus_wire_junctions_guarded`). 버스 분기
  접속점은 남긴다. 넷리스트가 **나뉘기만** 했을 때만 남기고 아니면 원복한다. 연결이 바뀌므로 `AUTO CHANGES` 맨 앞,
  나뉜 넷마다 `WARNING net '...' split into ...`(이슈 포함), `auto_changes.txt` 의 `[나뉜 넷]`, 결과 요약에 알린다.
  `result.import_cleanup['bus_split']`. 끄려면 `--keep-bus-junctions`.
  이어서 버스에 엔트리 없이 닿은 배선 끝을 KiCad 식(배선 2.54 mm 줄임 + 45도 `bus_entry`)으로 다시 그린다
  (`draw_bus_entries`, 넷리스트 이름+핀 동일일 때만 — 접속점을 지운 뒤 생기던 ERC `unconnected_wire_endpoint` 가 사라짐). 실측: 비공개 실무 설계 113개 중 2개에서
  발생, 둘 다 지운 뒤 PADS 정답 넷리스트와 연결 100% 일치(전에는 7~17개 넷이 하나로 합쳐짐), 나머지 111개는 변화 없음.
- **숨은 라벨 중복 제거 확대**: 배선 없이 같은 이름 전원 심볼 핀에 바로 얹힌 투명 전역 라벨과, 같은 배선에 같은
  이름 지역 라벨이 있는 투명 **지역** 라벨도 중복으로 지운다(넷리스트 이름·핀 대조). 남는 것은 실제 연결을 실어
  나르는 것뿐이다.
- **`--hidden-labels {keep,show,shrink}`**(기본 `keep`, 예전 `keep`/`shrink` 호환): `show` 는 남은 라벨을 보이는
  전역 라벨로 바꾸고(같은 배선의 OrCAD 별칭 라벨이 전역 라벨이 되고 배선 중간의 것은 빈 배선 끝으로 이동 —
  ERC `same_local_global_label`·`unconnected_wire_endpoint` 가 그만큼 준다), `shrink` 는 글자를 0.01 mm 로
  줄여 PDF/SVG 에서도 감춘다. 둘 다 넷리스트의 넷 이름과 핀을 대조해 달라지면 원복한다.
- **남은 라벨 보고**: 로그에 `hidden labels: N left - ...` 로 이름·페이지·종류(`alias`/`renamed`/`wire`/`free`/`local`)와
  이유, 처리 결과를 보여 주고 `result.hidden_labels`(JSON/MCP 포함)에 담는다.
- **GUI**: 체크박스를 3택("그대로 두기"/"보이게 바꾸기"/"PDF에서도 감추기")으로 바꾸고, 파란 설명 줄에 고른
  방식의 모양을 덧붙이며(`.DSN`/KiCad 프로젝트 입력일 때만), 결과 요약에 남은 개수·이름 5개·이유·처리 결과와
  keep 일 때 대안 안내를 표시. 저장된 옛 설정값은 그대로 읽는다.
- **MCP** `convert` 의 `hidden_labels` 가 `keep`/`show`/`shrink` 를 받는다.
- **버스 버그 수정**: 라벨 끝점 이동이 버스 끝점을 빈 배선 끝으로 오인해 넷 라벨을 버스 위로 옮기는 바람에
  버스 멤버 넷이 합쳐지던 문제를 고쳤다(비공개 실무 설계 113개 실측 중 2개에서 발생, 수정 뒤 0개).
- **넷리스트 검증 정리(`cleanup_project_guarded`)**: `.DSN`/KiCad 프로젝트의 정리 단계마다 kicad-cli 넷리스트
  (넷 이름+핀)를 대조하고, 달라지면 라벨 끝점 이동을 뺀 정리 -> 안전한 단계만 -> 전부 되돌림 순으로 물러난다.
  `result.import_cleanup['guard']`(`level`/`verified`/`tried`)와 로그 `import cleanup:` 줄의 접두어, 결과 요약의
  쉬운 말 설명으로 알린다.
- **H1 숨은 전원 라벨 -> 전원 심볼**: 부품 핀에 바로 얹힌 숨은 전원 이름 라벨(예: GND)을 같은 이름 전원 심볼로
  바꾼다(넷리스트 검증). `result.hidden_labels['power_symbols_placed']`, 로그 `hidden labels: N hidden power
  labels sitting on part pins replaced by power symbols`.
- **H2 숨은 라벨 이름 충돌 경고**: 같은 배선에 숨은 라벨과 다른 이름의 전원 심볼/보이는 전역 라벨이 있어 KiCad 가
  두 넷을 조용히 합치는 상황(ERC 는 `multiple_net_names` 경고뿐)을 로그 `WARNING hidden label name conflicts`,
  `result.hidden_labels['conflicts']`, 결과 요약의 경고 구역(결론 "확인할 항목 있음")과 GUI 이슈 탭으로 알린다.
  직접 고친 프로젝트를 `--kicad-project` 로 다시 돌려 검사하는 데도 쓸 수 있다.
- **라벨 정리(label tidy)**: EDIF 출력에서 별칭 라벨을 배선의 빈 끝으로 옮기고(로그 `label tidy:`), 두 경로 모두
  배선이 계속 이어지는 핀 쪽 끝의 라벨도 정리하며, 배선 끝에서 0.254 mm 모자란 오프페이지 전역 라벨을 끝으로
  붙인다(`import_cleanup['globals_snapped']`).
- **`--duplicate-alias-labels {keep,remove}`**(기본 `keep`): 같은 배선의 보이는 전역 라벨과 이름이 같은 지역 별칭
  라벨을 지운다(ERC `same_local_global_label` 이 사라지는 대신 그림에서 글자가 하나씩 줄어듦, 넷리스트 검증).
  `result.duplicate_alias_removed`. GUI 체크박스 "중복 별칭 라벨 지우기", MCP `convert` 의 `duplicate_alias_labels`.
- **숨은 지역 라벨 제거**: 넷 이름 `kicad` 모드에서 숨은 *지역* 라벨도 지운다(핀 분할 검증). 남은 목록의 종류
  `local` 은 "그 배선에 시트 안 이름을 주는 유일한 숨은 지역 라벨"이다.
- 로그 문구 변경: `hidden global labels removed` -> `importer hidden labels removed`(`net names: kicad - N ...`).
- **문서**: 사용자 설명서(한/영)에 문제 해결 항목 "전원 심볼의 넷 이름이 엉뚱하게 보이면 / 두 전원 넷이 합쳐짐"과
  위 기능 설명을 추가.
- **숨은 라벨이 PDF 에서 배선을 덮지 않게**(기본 `keep` 유지): KiCad 화면에는 안 보이고 PDF/SVG 에는 검게 찍히는
  숨은 라벨을 배선 반대 방향으로 돌리거나, 같은 배선의 비어 있는 끝으로 옮긴다(넷리스트 대조).
  `import_cleanup['hidden_oriented']`.
- **넷 이름 정리 뒤 2차 중복 제거**: 1차에서 이름 때문에 보호됐던 중복 숨은 라벨을 넷 이름 정리 뒤 한 번 더 지운다
  (넷리스트 대조). 로그 `hidden labels: N more redundant hidden labels removed after net-name cleanup ...`.
- **`--hidden-labels show` 모양 개선**: OrCAD 에서 글자가 안 보이던 라벨은 작고 옅은 회색(`--hidden-label-style
  subtle`, 기본; `normal` 은 일반 모양, CLI 전용) 전역 라벨로, 보이던 OrCAD 별칭 글자는 일반 라벨로 드러낸다. 드러낸
  라벨은 배선 반대쪽을 향하고 글자 크기를 1.27 mm 이하로 제한해 2.54 mm 간격 이웃과 겹치지 않는다.
- **배선 없이 부품 핀에 바로 얹힌 라벨**: 정렬(justify)이 없어 글자가 핀 끝에 가운데로 걸리던 것을 회전·정렬을
  부품 몸체 반대쪽으로 맞춘다(연결점은 그대로). `import_cleanup['pin_labels_faced']`.
- **핀 번호 글자 맞춤**: 핀보다 긴 핀 번호(임포터가 1.7 mm 글자를 그대로 복사)가 핀 이름·라벨 위로 넘치던 것을 핀
  길이의 70%(최소 0.6 mm)로 줄인다. 시트의 내장 `lib_symbols` 와 `.kicad_sym` 라이브러리 둘 다 바꿔 ERC
  `lib_symbol_mismatch` 가 생기지 않는다(KiCad 는 핀마다 번호 위치를 옮길 수 없어 크기만 조절 가능).
  `import_cleanup['pin_numbers_fitted']`.
- **오프페이지 커넥터 인식 확대**: 한 줄 갈매기(줄기 2.54 mm + 대각선 2조각) 모양, 임포터 각도가 틀렸거나 세로
  커넥터에서 0 인 라벨(자기 각도 우선, 이어서 다른 방향 시도), 가운데 정렬된 가로 글자의 세로 커넥터를 인식해 전역
  라벨로 바꾼다. 커넥터 글자가 숨은 라벨(=실제 넷) 이름과 **다르면**(OrCAD 가 한 넷에 커넥터 이름을 둘 준 경우) 바꾸면
  넷이 갈라지므로 **바꾸지 않고** 그림으로 두며 `WARNING off-page connector '...' (...) left as a drawing: its net is
  '...'` 로 로그·이슈 목록·결과 요약·`auto_changes.txt` 에 알린다(`import_cleanup['offpage_unconverted']`).
- **자동 변경 고지**: 도구가 회로도를 자동으로 바꾼 모든 것을 확실히 알린다 — 로그 `AUTO CHANGES:` 블록(항목별
  `  - N ...` 줄), 출력 폴더의 `auto_changes.txt`(UTF-8 BOM, 항목·개수·확인할 점·이름이 바뀐 넷 `이전 -> 이후` 목록·
  그림으로 남은 커넥터), 결과 요약 입력 줄 바로 아래 "자동 변경" 구역과 결론 줄 꼬리 문장, JSON/MCP `auto_changes`.
  공개 설계 Raspberry Pi CMIO V3(기본 옵션, 개발 빌드)의 9개 항목: 오프페이지 커넥터 108개 -> 전역 라벨,
  교차점 끊김 285개 병합, 라벨 117개 빈 배선 끝으로 이동, 숨은 라벨 235개 배선 반대로 돌림, 중복 숨은 라벨 1,071개
  제거, 배선 941개 기본 폭, 빈 도면 양식, 임포터 숨은 전역 라벨 152개 제거(넷 이름 `kicad`), 넷 84개 이름 변경.
  ERC 68(`power_pin_not_driven` 14, `same_local_global_label` 54), 남은 숨은 라벨 4개(`CD0_SCL`·`CD0_SDA`·
  `CD1_SCL`·`CD1_SDA`, 종류 `alias`).

## 1.0.4 (2026-10-28)

`.DSN` 임포트 결과 정리 강화(오프페이지 커넥터·배선 굵기·PDF 의 검은 숨은 라벨), 한글 표시, 넷 이름 방침 변경과 GUI 옵션입니다.

- **넷 이름 기본 동작 변경**(2026-10-27 사용자 결정, 예전 동작은 옵션):
  - `--net-names` 기본이 보드 유무와 관계없이 `kicad` — OrCAD 페이지 별칭은 지역 라벨(KiCad 넷 이름
    `/USB/DTR#`), 이름 없는 넷은 KiCad 자동 이름. 별칭·자동 넷을 전역 라벨로 만드는 인위적 전역화를 하지
    않는다. `.DSN`/KiCad 프로젝트 입력에서도 동작해 임포터가 덧붙인 투명 전역 라벨을 지운다(넷 핀 분할을
    kicad-cli 넷리스트로 대조, 이름을 잃는 넷은 보호, 실패 시 원복). 예전 동작은 `--net-names keep`.
  - `--board-net-names schematic`(기본) — 보드를 함께 줄 때 출력 보드의 넷 이름을 회로도에 맞춰 Update PCB
    에서 넷 이름이 바뀌지 않게 한다(`[4b]` 에 일치 수 보고). PADS 이름 그대로는 `--board-net-names pads`.
  - `--hidden-labels shrink` — 남은 투명 라벨이 PDF/SVG 에 검게 찍히는 것을 글자 축소로 감춘다(기본 `keep`).
  - GUI: "넷 이름"(KiCad 방식/PADS·OrCAD 이름 유지)·"보드 넷 이름"(회로도에 맞춤/PADS 그대로)·"PDF에서 숨은
    라벨 감추기"·"한글 글꼴" 옵션과, 고른 조합의 결과(넷 이름 예, 따로 PADS 를 임포트할 때 맞는지)를 바로
    보여 주는 설명 줄. 결과 요약에 바뀐 넷 이름 수·예와 보드/회로도 넷 이름 일치 수 표시. MCP `convert` 에도
    같은 옵션.
- 한글이 안 보이거나 깨지던 문제:
  - EDIF·`.DSN` 공통 — 한글이 든 글자·라벨·속성·핀 이름에 한글 글꼴(기본 `Malgun Gothic`)을 지정한다.
    KiCad 기본 글꼴은 한글을 그리지 않고 `Arial` 같은 영문 글꼴은 네모로 그린다. `--korean-font NAME` 으로
    바꾸고 `--korean-font ""` 로 끈다.
  - `.DSN` — 임포터가 CP949 한글을 Latin-1 로 읽어 깨뜨린 문자열(`´Â Ä¿³ØÅÍ`)을 되돌린다(정상 Latin-1 문자열은 그대로).
- `.DSN`/KiCad 프로젝트 입력의 임포터 결과 정리(기본 켜짐)에 세 가지 추가:
  - 오프페이지 커넥터를 KiCad 전역 라벨로 — 임포터는 OrCAD 오프페이지 커넥터를 빨간 갈매기 그림 +
    빨간 글자 + 투명 전역 라벨로 옮겨, 넷은 전역인데 화면에는 그림·글자만 보였다. 투명 라벨을 보이게
    하고 그림·글자를 지운다(넷 연결은 그대로).
  - PDF/SVG 에 검게 찍히던 중복 투명 라벨 제거 — 지우기 전후 KiCad 넷리스트를 넷 이름·핀까지 대조해
    달라지면 그 이름은 남기고, 그래도 다르면 되돌린다.
  - 배선 폭을 KiCad 기본값으로 — 임포터가 모든 배선에 0.254 mm 폭을 박아 새로 그린 배선보다 굵게
    보이던 것을 기본 폭(0)으로 바꾼다. 원래 폭을 유지하려면 `--keep-wire-width`.

## 1.0.3 (2026-10-20)

실제 설계(EDIF·`.DSN`·PADS 보드) 변환에서 드러난 버그 수정과 GUI 끌어다 놓기 추가입니다.

- 버그 수정: EDIF 입력에서 만든 `.kicad_pcb` 를 정식 KiCad 10.0 이 열지 못하던 문제 — GUI 에서
  나이틀리 포터블을 준비해 kicad-cli 칸이 나이틀리일 때 보드도 나이틀리로 임포트되어
  `(version 20260831)` 이 되었다. 이제 입력 종류와 관계없이 보드는 정식 kicad-cli 로 임포트한다.
- 버그 수정: EDIF 입력에서 일부 넷이 회로도에서 끊기던 문제 — `RTS#`·`DTR#` 처럼 이름에 특수 문자가
  있는 넷의 별칭 라벨과, 넷 이름과 다른 표시 이름의 별칭(`NET_NAME_OVERRIDE`, 예: 넷 `ETH_NRST` 의
  `nRST`)을 읽지 못해 라벨이 빠졌다. 두 형식 모두 라벨로 옮긴다.
- 버그 수정: 프로젝트 이름에 점이 있으면(예: `ABC_1.3`) `.DSN` 변환이 "kicad-cli import failed" 로
  실패하던 문제.
- GUI: 탐색기에서 파일 여러 개나 폴더를 창에 끌어다 놓으면 확장자(`.asc` 는 머리글)로 알맞은 입력
  칸에 자동으로 들어간다(Windows). 폴더는 바로 아래 파일만 보고, EDIF 와 `.DSN` 이 함께 있으면 EDIF 모드.

## 1.0.2 (2026-09-11)

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
