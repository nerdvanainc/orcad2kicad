# -*- coding: utf-8 -*-
"""앱 아이콘 생성기 — `src/orcad2kicad/assets/orcad2kicad.ico`(+ 미리보기 `.png`)를 만든다.

exe(PyInstaller `icon=`)와 tkinter 창 제목줄(`iconbitmap`)이 같은 파일을 쓴다. 생성에만 Pillow 가
필요하고(개발 PC), 실행 시에는 만들어진 .ico 만 있으면 된다(저장소에 커밋).

디자인: KiCad 계열 짙은 파랑 둥근 사각 바탕에, 왼쪽 주황 "O"(OrCAD) → 흰 화살표 → 오른쪽 흰 "K"(KiCad).
16 px 에서도 "주황 점 · 흰 K" 로 읽히도록 요소를 세 개로 제한했다.

사용: python packaging/make_icon.py   (저장소 어디서 실행해도 된다)
"""
from __future__ import annotations
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:                                  # pragma: no cover - 개발 도구
    print('Pillow is required: pip install pillow', file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, 'src', 'orcad2kicad', 'assets')
ICO = os.path.join(OUT_DIR, 'orcad2kicad.ico')
PNG = os.path.join(OUT_DIR, 'orcad2kicad.png')

BG = (31, 58, 147)          # 바탕(짙은 파랑)
BG_DARK = (22, 41, 105)     # 바탕 아래쪽(살짝 어둡게 — 평면 단색보다 덜 밋밋)
ORANGE = (245, 130, 32)     # OrCAD 쪽 "O"
WHITE = (255, 255, 255)
SIZES = (256, 128, 64, 48, 32, 24, 16)


def _font(size):
    for name in ('segoeuib.ttf', 'arialbd.ttf', 'malgunbd.ttf', 'DejaVuSans-Bold.ttf'):
        for d in (r'C:\Windows\Fonts', '/usr/share/fonts/truetype/dejavu'):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render(size=512):
    """한 장(정사각, RGBA)을 크게 그린다. 축소는 호출자가 한다."""
    s = size
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 바탕: 둥근 사각 + 위아래 2단 그라데이션(줄 단위)
    radius = int(s * 0.22)
    mask = Image.new('L', (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=255)
    grad = Image.new('RGBA', (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        k = y / (s - 1)
        c = tuple(int(BG[i] * (1 - k) + BG_DARK[i] * k) for i in range(3)) + (255,)
        gd.line((0, y, s, y), fill=c)
    img.paste(grad, (0, 0), mask)

    # 왼쪽 "O": 두꺼운 주황 링
    cx, cy = int(s * 0.30), int(s * 0.50)
    r_out, r_in = int(s * 0.17), int(s * 0.095)
    d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out), fill=ORANGE)
    d.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill=None, outline=None)
    hole = Image.new('L', (s, s), 0)
    ImageDraw.Draw(hole).ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill=255)
    img.paste(grad, (0, 0), hole)            # 링 안쪽을 바탕색으로 다시 채워 구멍을 낸다

    # 가운데 화살표(흰색): 굵은 몸통 + 삼각 머리
    ax0, ax1 = int(s * 0.49), int(s * 0.60)
    th = int(s * 0.045)
    d.rectangle((ax0, cy - th, ax1, cy + th), fill=WHITE)
    head = int(s * 0.10)
    d.polygon([(ax1, cy - head), (ax1 + head, cy), (ax1, cy + head)], fill=WHITE)

    # 오른쪽 "K": 글꼴로 그린다
    font = _font(int(s * 0.46))
    text = 'K'
    bbox = d.textbbox((0, 0), text, font=font)
    tw, tht = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = int(s * 0.84) - tw // 2 - bbox[0]
    ty = cy - tht // 2 - bbox[1]
    d.text((tx, ty), text, font=font, fill=WHITE)
    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    big = render(512)
    big.resize((256, 256), Image.LANCZOS).save(PNG)
    frames = [big.resize((n, n), Image.LANCZOS) for n in SIZES]
    frames[0].save(ICO, format='ICO', sizes=[(n, n) for n in SIZES],
                   append_images=frames[1:])
    print(f'wrote {ICO} ({os.path.getsize(ICO)} bytes) and {PNG}')


if __name__ == '__main__':
    main()
