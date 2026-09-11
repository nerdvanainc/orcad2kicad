"""PyInstaller GUI 진입점 — orcad2kicad.exe 가 실행하는 스크립트.

PyInstaller 는 이 스크립트를 그대로 얼려 넣으므로(frozen), 여기서는 패키지를
임포트해서 main() 을 호출하는 것 외에 아무것도 하지 않는다. `orcad2kicad.spec` 의
`pathex` 에 `src` 가 들어 있어야 개발 환경(비-frozen)에서도 이 스크립트를 직접
실행해 확인할 수 있다.
"""
from __future__ import annotations
import sys

from orcad2kicad.gui import main

if __name__ == '__main__':
    sys.exit(main())
