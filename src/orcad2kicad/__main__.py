"""`python -m orcad2kicad` — tkinter GUI 를 띄운다.

명령행으로 쓰려면 `python -m orcad2kicad.cli ...` 를 쓴다.
"""
import sys

from .gui import main

if __name__ == '__main__':
    sys.exit(main())
