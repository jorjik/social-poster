#!/usr/bin/env python3
"""Генерация картинки для Instagram-поста и загрузка в WP-медиатеку master-jenya.com.

Использование:
    python3 ig_image.py "<промпт для картинки>" [--slug имя-файла] [--aspect 1:1|4:5|16:9]

Печатает публичный URL картинки — его передаёшь в
buffer_post.py add --service instagram --image-url <url>

Ключи: OPENROUTER_API_KEY, WP_USER, WP_APP_PASSWORD из /root/.hermes/.env.
Только stdlib.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

ENV_FILE = "/root/.hermes/.env"
MODEL = "inclusionai/ming-image-0.1-design"


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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slug = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--slug" and i + 2 < len(sys.argv):
            slug = sys.argv[i + 2]
    if not args:
        sys.exit("нужен промпт: python3 ig_image.py \"<промпт>\" [--slug имя]")
    prompt = args[0]
    slug = slug or ("ig-" + "-".join(prompt.lower().split()[:5]).strip(".,!?")[:40]) or "ig-image"
    png = gen_image(prompt)
    url = upload_to_wp(png, slug)
    print(url)
