# 서드파티 라이선스 고지 (Third-Party Notices)

`orcad2kicad.exe` / `orcad2kicad-cli.exe`는 PyInstaller로 빌드된 단일 실행 파일이며,
내부에 아래 서드파티 구성 요소가 포함되어 배포됩니다. 각 항목은 라이선스 명칭과
공식 참고 링크만 표기합니다(전문은 각 링크의 공식 페이지 참조).

| 구성 요소 | 라이선스 | 참고 링크 |
|---|---|---|
| Python 3.13 | PSF License | https://docs.python.org/3/license.html |
| Tcl/Tk | BSD 계열 | https://www.tcl.tk/software/tcltk/license.html |
| OpenSSL 3 | Apache License 2.0 | https://www.openssl.org/source/license.html |
| libffi | MIT License | https://github.com/libffi/libffi/blob/master/LICENSE |
| zlib | zlib License | https://zlib.net/zlib_license.html |
| bzip2 | BSD 계열 | https://sourceware.org/bzip2/ |
| xz / liblzma | Public Domain / 0BSD | https://tukaani.org/xz/ |
| Microsoft Visual C++ 런타임 | Microsoft 재배포 라이선스 | https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist |
| PyInstaller 부트로더 | GPL(번들 애플리케이션 예외 포함) | https://pyinstaller.org/en/stable/license.html |

위 구성 요소들은 orcad2kicad의 실행 파일 안에 정적으로 포함되어 함께 배포됩니다.
각 라이선스가 요구하는 고지 의무는 이 문서로 갈음합니다.

## 런타임에 사용자가 직접 내려받는 소프트웨어 (실행 파일에 포함되지 않음)

orcad2kicad는 사용자가 GUI의 "나이틀리 KiCad 포터블 준비" 버튼을 클릭하거나
CLI에서 `--fetch-kicad-nightly` 옵션을 지정하는 등 **명시적으로 요청한 경우에만**
아래 소프트웨어를 각자의 공식 배포처에서 내려받아 압축을 풉니다(설치 프로그램은
실행하지 않습니다).

- **KiCad** (나이틀리 빌드 포함) — GNU General Public License v3 (GPLv3)
  공식 사이트: https://www.kicad.org
- **7-Zip 콘솔판** (`7za` 등, 압축 도구가 없는 환경에서만 보조로 사용) —
  GNU Lesser General Public License (LGPL), 일부 unRAR 코드에 대한 제한 조건 포함
  공식 사이트: https://www.7-zip.org/

이 두 소프트웨어는 orcad2kicad 실행 파일 안에 재배포되는 것이 아니라, 사용자의
실행 시점 행위에 의해 각자의 공식 사이트에서 새로 내려받아지는 완전히 별개의
독립 소프트웨어입니다. 따라서 위 표에 정리된 "실행 파일에 포함된 구성 요소"와는
성격이 다르며, orcad2kicad의 라이선스(LICENSE, MIT)가 아니라 각 소프트웨어 자체의
라이선스(GPLv3, LGPL)의 적용을 받습니다. orcad2kicad 제작사는 이들 소프트웨어의
배포자가 아니며, 이들 소프트웨어에 대한 어떠한 보증도 제공하지 않습니다.
