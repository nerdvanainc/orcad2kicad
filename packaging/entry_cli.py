"""PyInstaller CLI/MCP 진입점 — orcad2kicad-cli.exe 가 실행하는 스크립트.

첫 인자가 `--mcp` 이면 나머지 인자를 그대로 `mcp_server.main()` 에 넘긴다
(예: `orcad2kicad-cli.exe --mcp --selftest`). 그 밖에는 `cli.main()` 으로 넘긴다.
두 main() 모두 exit code(int)를 돌려주므로 그대로 `sys.exit()` 한다.
"""
from __future__ import annotations
import sys


def run(argv):
    if argv and argv[0] == '--mcp':
        from orcad2kicad import mcp_server
        return mcp_server.main(argv[1:])
    from orcad2kicad import cli
    return cli.main(argv)


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
