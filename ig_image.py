#!/usr/bin/env python3
"""Генерация картинки для Instagram-поста и загрузка в WP-медиатеку master-jenya.com.

Использование:
    python3 ig_image.py "<промпт для картинки>" [--slug имя-файла] \
        [--title "Заголовок"] [--subtitle "Подзаголовок"] [--title-color #1B5E20]

Печатает публичный URL картинки — его передаёшь в
buffer_post.py add --service instagram --image-url <url>

Картинка генерируется моделью (prompt), затем заголовок и подзаголовок
накладываются шрифтом Inter (Pillow) — кириллица всегда точная.
Без --title/--subtitle картинка уходит как есть.

Ключи: OPENROUTER_API_KEY, WP_USER, WP_APP_PASSWORD из /root/.hermes/.env.
Только stdlib + Pillow.
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ENV_FILE = "/root/.hermes/.env"
MODEL = "inclusionai/ming-image-0.1-design"
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Inter-Bold.ttf")


def load_env() -> dict:
    out = {}
    for line in open(ENV_FILE):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def gen_image(prompt: str) -> bytes:
    key = os.environ.get("OPENROUTER_API_KEY") or ENV["OPENROUTER_API_KEY"]
    body = json.dumps({"model": MODEL, "prompt": prompt, "n": 1}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/images/generations",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    b64 = data["data"][0].get("b64_json")
    if not b64:
        sys.exit("OpenRouter не вернул b64_json: " + json.dumps(data)[:300])
    return base64.b64decode(b64)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def overlay_text(png: bytes, title: str, subtitle: str, color: str) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    title_font = ImageFont.truetype(FONT_PATH, max(36, W // 11))
    sub_font = ImageFont.truetype(FONT_PATH, max(26, W // 18))

    title_lines = wrap_text(draw, title, title_font, int(W * 0.86)) if title else []
    sub_lines = wrap_text(draw, subtitle, sub_font, int(W * 0.86)) if subtitle else []

    line_h = int(title_font.size * 1.25)
    total_h = len(title_lines) * line_h + len(sub_lines) * int(sub_font.size * 1.3) \
        + (int(H * 0.02) if title_lines and sub_lines else 0)
    pad = int(H * 0.035)
    y = H - total_h - pad - int(H * 0.02)

    # полупрозрачная подложка под текстом
    draw.rectangle([0, y - pad, W, H], fill=(0, 0, 0, 150))

    hex_color = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    for line in title_lines:
        draw.text(((W - draw.textlength(line, font=title_font)) / 2, y), line, font=title_font,
                  fill=hex_color + (255,))
        y += line_h
    if title_lines and sub_lines:
        y += int(H * 0.02)
    for line in sub_lines:
        draw.text(((W - draw.textlength(line, font=sub_font)) / 2, y), line, font=sub_font,
                  fill=hex_color + (255,))
        y += int(sub_font.size * 1.3)

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload_to_wp(png: bytes, slug: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:60]
    fname = f"{safe}.png"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(png)
        path = fh.name
    try:
        user = ENV["WP_USER"]
        pw = ENV["WP_APP_PASSWORD"]
        r = subprocess.run(
            [
                "curl", "-s", "-f", "-X", "POST",
                "https://master-jenya.com/wp-json/wp/v2/media",
                "-u", f"{user}:{pw}",
                "-F", f"file=@{path};type=image/png;filename={fname}",
                "-F", f"title={slug}",
                "-F", "alt_text=",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            sys.exit(f"WP upload failed: {r.stderr[:300]}")
        media = json.loads(r.stdout)
        url = media.get("source_url")
        if not url:
            sys.exit("WP не вернул source_url: " + r.stdout[:300])
        return url
    finally:
        os.unlink(path)


if __name__ == "__main__":
    ENV = load_env()
    argv = sys.argv[1:]
    kwargs = {}
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and i + 1 < len(argv):
            kwargs[a[2:]] = argv[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1
    if not positional:
        sys.exit('нужен промпт: python3 ig_image.py "<промпт>" [--title "..."] [--subtitle "..."]')
    prompt = positional[0]
    slug = kwargs.get("slug") or ("ig-" + "-".join(prompt.lower().split()[:5]).strip(".,!?")[:40]) or "ig-image"
    title = kwargs.get("title")
    subtitle = kwargs.get("subtitle")
    color = kwargs.get("title-color", "#FFFFFF")
    png = gen_image(prompt)
    if title or subtitle:
        png = overlay_text(png, title or "", subtitle or "", color)
    url = upload_to_wp(png, slug)
    print(url)
