"""Render an X-sized card of the measured menu-snip token savings."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("snip2md-token-savings-x.png")
W, H = 1600, 900
BG = (243, 244, 246)
WHITE = (255, 255, 255)
TEXT = (28, 28, 30)
MUTED = (142, 142, 147)
ACCENT = (74, 144, 226)
LINE = (197, 220, 245)

ROWS = [
    ("Opus 5", "1,116", "250", "78%"),
    ("Fable 5", "1,116", "250", "78%"),
    ("GPT 5.6", "765", "250", "67%"),
    ("Grok 4.6", "1,792", "250", "86%"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((48, 48, W - 48, H - 48), radius=28, fill=WHITE)
    draw.rectangle((48, 48, 64, H - 48), fill=ACCENT)

    draw.text((96, 80), "Snip2MD", font=font(28, True), fill=ACCENT)
    draw.text((96, 128), "78% fewer tokens", font=font(56, True), fill=TEXT)
    draw.text(
        (96, 204),
        "Same 864×998 menu snip. Screenshot in chat vs local Markdown.",
        font=font(24),
        fill=MUTED,
    )

    y = 280
    headers = ("Model", "Screenshot", "Markdown", "Cut")
    xs = (96, 520, 900, 1240)
    for x, head in zip(xs, headers):
        draw.text((x, y), head, font=font(20, True), fill=MUTED)
    y = 328
    draw.line((96, y, W - 96, y), fill=LINE, width=2)

    for i, (model, shot, md, cut) in enumerate(ROWS):
        row_y = 360 + i * 88
        if i % 2 == 0:
            draw.rounded_rectangle(
                (80, row_y - 16, W - 80, row_y + 56), radius=12, fill=(247, 250, 253)
            )
        draw.text((96, row_y), model, font=font(28, True), fill=TEXT)
        draw.text((520, row_y), shot, font=font(28), fill=TEXT)
        draw.text((900, row_y), md, font=font(28), fill=ACCENT)
        draw.text((1240, row_y), cut, font=font(28, True), fill=ACCENT)

    draw.text(
        (96, H - 120),
        "Opus 5 · Fable 5 · GPT 5.6 · Grok 4.6   ·   list API input rates, Aug 2026",
        font=font(18),
        fill=MUTED,
    )
    draw.text(
        (96, H - 88),
        "Local OCR is $0. Vision tokens follow each model’s published image formula.",
        font=font(18),
        fill=MUTED,
    )
    img.save(OUT, "PNG")
    print(OUT)


if __name__ == "__main__":
    main()
