#!/usr/bin/env python3
"""make_cover — 표지를 만든다. 이미지 생성 모델에게 “글자 없는 그림”을 그리게 하고, 제목과 저자명은 이쪽에서 조판해 겹친다.

  python3 tools/novelctl.py cover --style illust --motif "안개 속 종루" --title-dir horizontal --font mincho --author "김구름"
  python3 tools/make_cover.py ...(같은 인자)

  --model ID        이미지 생성 모델(OpenRouter 등의 /images). none 이면 그림 없이 글자와 색만으로 된 표지(novel_worker 의 기본.
                    중계기로는 그림 생성을 할 수 없다). 어느 경우든 글자 조판에 Chrome·Edge·Brave(헤드리스)가 필요
  --style           simple(심플) | illust(일러스트) | photo(사실적)
  --motif TEXT      그려 주었으면 하는 것(자유 입력)
  --title-dir       vertical(세로쓰기) | horizontal(가로쓰기, 기본값)
  --font            mincho(명조) | gothic(고딕) | casual(캐주얼)
  --author TEXT     저자명(EPUB 의 저자에도 쓴다)
  --title TEXT      제목(생략하면 CANON 의 “제목：”)
  --reuse-art       지난번 그림을 재사용하고 글자만 다시 조판한다(모델을 부르지 않는다)

왜 글자를 그림에 그리게 하지 않는가：이미지 생성 모델은 한글 글자를 바르게 그리지 못할 때가 많다.
그림은 글자 없이 그리게 하고, 제목과 저자명은 HTML 로 조판해 Chrome(헤드리스)으로 1600×2560 이미지로 만든다.

출력(images/ 아래. 작품 폴더의 보호 대상이 아니다):
  cover_art.png      모델이 그린 그림(글자 없음)
  cover.html         조판 원본
  cover.png          표지(EPUB 과 스토어의 표지 이미지로 쓴다)
  cover.json         이 표지의 설정(제목·저자명·스타일·모델·비용). make_epub.py 가 저자명과 표지를 여기서 읽는다
비용은 logs/usage.jsonl 에 역할 cover 로 남긴다.
"""
import argparse
import base64
import datetime
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "images"
W, H = 1600, 2560
PAD = 240  # 헤드리스 Chrome 은 창 아래 수십 px(버전마다 다름)을 그리지 않으므로, 창을 크게 잡아 찍고 아래를 잘라 낸다

STYLE = {
    "simple": ("심플", "minimalist graphic design for a literary book cover, flat shapes, a limited palette of two or three colors, "
                          "generous negative space, one symbolic motif, quiet and modern"),
    "illust": ("일러스트", "hand-painted book cover illustration, gouache and watercolor textures, soft natural light, "
                          "atmospheric storybook mood, detailed but calm composition"),
    "photo": ("사실적", "photorealistic image for a novel cover, cinematic lighting, shallow depth of field, natural colors, "
                     "high detail, like a fine-art photograph"),
}
FONTS = {
    # (Google Fonts 의 이름, 단말에 있는 서체 후보). Google Fonts 를 읽지 못할 때(오프라인)는 뒤의 후보로 조판한다
    "mincho": ("Nanum+Myeongjo:wght@700", '"Nanum Myeongjo", "NanumMyeongjo", "Noto Serif KR", "AppleMyungjo", "Batang", "Noto Serif CJK KR", serif'),
    "gothic": ("Noto+Sans+KR:wght@700", '"Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", sans-serif'),
    "casual": ("Gaegu:wght@700", '"Gaegu", "Jua", "NanumPen", "Nanum Pen Script", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'),
}
FONT_JA = {"mincho": "명조", "gothic": "고딕", "casual": "캐주얼"}


def cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def title_from_canon(c):
    import re
    f = ROOT / "canon" / "CANON.md"
    if f.exists():
        m = re.search(r"제목\s*[：:]\s*(.+)", f.read_text(encoding="utf-8"))
        if m and "（기입）" not in m.group(1):
            return m.group(1).strip().strip("『』「」《》“”\"")
    return c.get("publish", {}).get("fallback_title", "무제")


def mood_from_brief():
    f = ROOT / "brief.json"
    if not f.exists():
        return ""
    b = json.loads(f.read_text(encoding="utf-8"))
    parts = [b.get("genre", ""), (b.get("setting") or "")[:160], (b.get("core") or "")[:80]]
    return " / ".join(p for p in parts if p)


def art_prompt(style, motif, title_dir, mood):
    space = ("keep the right third of the image calm and uncluttered from top to bottom (a vertical title will be placed there)"
             if title_dir == "vertical" else "keep the upper third of the image calm and uncluttered (a title will be placed there)")
    return (f"Cover artwork for a Korean novel. {STYLE[style][1]}. "
            f"Main motif: {motif or 'chosen to fit the story'}. "
            + (f"Story genre and setting (for mood only): {mood}. " if mood else "")
            + f"Portrait orientation. {space}. "
            "Absolutely no text, letters, characters, numbers, signatures, logos or watermarks anywhere in the image.")


# ---------------------------------------------------------------- 이미지 생성(OpenRouter)
def _key_and_base():
    sys.path.insert(0, str(ROOT / "tools"))
    import call_llm
    key = call_llm.find_key()
    if not key:
        sys.exit("API 키를 찾을 수 없습니다（call_llm.py 와 같은 위치를 찾고 있습니다）.")
    base = (call_llm.env_value("NOVEL_LLM_BASE_URL") or call_llm.LLM.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    return key, base, call_llm


def _post(url, key, body, timeout=300):
    sys.path.insert(0, str(ROOT / "tools"))
    import llm_wire
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=llm_wire.headers(key, "chat", title="novel_worker cover"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def generate_art(model, prompt):
    """반환값：(이미지 바이트열, 확장자, 기록). 먼저 /images(이미지 생성 창구), 안 되면 chat/completions 의 이미지 출력."""
    key, base, call_llm = _key_and_base()
    t0 = time.time()
    last = None
    for body in ({"model": model, "prompt": prompt, "aspect_ratio": "2:3", "n": 1},
                 {"model": model, "prompt": prompt, "aspect_ratio": "9:16", "n": 1},
                 {"model": model, "prompt": prompt, "n": 1}):
        try:
            d = _post(base + "/images", key, body)
            item = (d.get("data") or [{}])[0]
            if item.get("b64_json"):
                mt = item.get("media_type") or "image/png"
                ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mt, ".png")
                u = d.get("usage") or {}
                return base64.b64decode(item["b64_json"]), ext, {"model": model, "cost": u.get("cost"), "sec": round(time.time() - t0, 1),
                                                                  "aspect": body.get("aspect_ratio", "auto"), "endpoint": "images"}
            last = "응답에 이미지가 없음"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code == 400:
                continue  # 가로세로 비율이 맞지 않음 → 다음 비율로
            if e.code == 404:
                break  # /images 가 없는 창구 → chat/completions 로
            raise SystemExit(f"이미지 생성에 실패했습니다：{last}")
        except urllib.error.URLError as e:
            raise SystemExit(f"이미지 생성 창구에 연결되지 않습니다：{e}")
    # chat/completions 로 이미지를 내는 예전 방식(modalities)
    try:
        d = _post(base + "/chat/completions", key, {"model": model, "messages": [{"role": "user", "content": prompt}],
                                                   "modalities": ["image", "text"], "usage": {"include": True}})
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        for im in msg.get("images") or []:
            url = (im.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                head, b64 = url.split(",", 1)
                ext = ".jpg" if "jpeg" in head else ".webp" if "webp" in head else ".png"
                u = d.get("usage") or {}
                return base64.b64decode(b64), ext, {"model": model, "cost": u.get("cost"), "sec": round(time.time() - t0, 1),
                                                    "aspect": "auto", "endpoint": "chat"}
        last = "응답에 이미지가 없음（chat）"
    except urllib.error.HTTPError as e:
        last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
    raise SystemExit(f"이미지 생성에 실패했습니다：{last}")


def log_usage(rec):
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": "cover", "task": "cover", "model": rec["model"],
                                "in": None, "out": None, "cost": rec.get("cost"), "sec": rec.get("sec"), "chars": 0, "ok": True},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 조판
def cover_html(title, author, style, title_dir, font, art):
    gf, family = FONTS[font]
    e = html.escape
    band = style == "simple" and bool(art)  # 심플(그림 있음)은 제목 아래에 종이 띠를 깐다
    lines = split_title(title, 12 if title_dir == "vertical" else 10)
    n = max(len(x) for x in lines)
    avail = (1820 if title_dir == "vertical" else 1320) - (112 if band else 0)
    size = int(max(72, min(210 if title_dir == "vertical" else 190, avail / (n * 1.1))))  # 1.1 은 자간(.08em)만큼
    title_html = "<br>".join(e(x) for x in lines)
    a_size = 62 if len(author) <= 10 else 50
    if art:
        bg = f'<img class="art" src="{e(art)}" alt="">'
        ink, shadow = "#fffdf8", "0 3px 28px rgba(0,0,0,.55), 0 0 2px rgba(0,0,0,.6)"
        scrim = ("linear-gradient(to left, rgba(10,12,18,.62) 0, rgba(10,12,18,.35) 24%, rgba(10,12,18,0) 46%), "
                 "linear-gradient(to top, rgba(10,12,18,.55) 0, rgba(10,12,18,0) 22%)" if title_dir == "vertical" else
                 "linear-gradient(to bottom, rgba(10,12,18,.62) 0, rgba(10,12,18,.3) 26%, rgba(10,12,18,0) 44%), "
                 "linear-gradient(to top, rgba(10,12,18,.55) 0, rgba(10,12,18,0) 20%)")
    else:
        palettes = {"simple": ("#efe9dc", "#22252b", "#b4462f"), "illust": ("#1f3a4d", "#f6efe2", "#e2b04a"), "photo": ("#15171c", "#f2efe8", "#8aa4b8")}
        paper, ink_c, accent = palettes[style]
        bg = (f'<div class="plain" style="background:{paper}"></div>'
              f'<div class="rule" style="background:{accent}"></div>')
        ink, shadow, scrim = ink_c, "none", "none"
    # 심플(그림 있음)은 제목 아래에 종이 띠를 깔아 그림의 밝기와 관계없이 읽히게 한다
    if band:
        ink, shadow = "#1d2026", "none"
    tpos = ("top: 150px; right: 140px; height: 1900px;" if title_dir == "vertical" else "top: 210px; left: 140px; right: 140px; text-align: center;")
    apos = ("bottom: 170px; left: 150px; height: 900px;" if title_dir == "vertical" else "bottom: 170px; left: 140px; right: 140px; text-align: center;")
    band_css = ""
    if band:
        band_css = (".title { background: rgba(246,242,233,.92); padding: 56px 40px; }" if title_dir == "vertical"
                    else ".title { background: rgba(246,242,233,.92); padding: 40px 56px; }")
        band_css += ".author { background: rgba(246,242,233,.92); padding: 26px 22px; color: #1d2026; }"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family={gf}&display=block" rel="stylesheet">
<style>
html, body {{ margin: 0; width: {W}px; height: {H}px; }}
.stage {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; }}
.art, .plain {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
.rule {{ position: absolute; left: 140px; right: 140px; bottom: 120px; height: 10px; }}
.scrim {{ position: absolute; inset: 0; background: {scrim}; }}
.title, .author {{ position: absolute; font-family: {family}; color: {ink}; text-shadow: {shadow}; font-weight: 700; }}
.title {{ {tpos} font-size: {size}px; line-height: 1.25; letter-spacing: .08em; word-break: keep-all; {"writing-mode: vertical-rl; text-orientation: mixed;" if title_dir == "vertical" else ""} }}
.author {{ {apos} font-size: {a_size}px; letter-spacing: .18em; font-weight: 500; {"writing-mode: vertical-rl; text-align: end;" if title_dir == "vertical" else ""} }}
{band_css}
</style></head><body><div class="stage">{bg}<div class="scrim"></div>
<div class="title">{title_html}</div>{f'<div class="author">{e(author)}</div>' if author else ""}
</div></body></html>"""


def split_title(title, per_line):
    """긴 제목을 쉼표나 가운뎃점, 띄어쓰기 뒤에서 되도록 같은 길이의 두 줄(세로쓰기면 두 열)로 나눈다."""
    if len(title) <= per_line:
        return [title]
    mid = len(title) / 2
    cands = [i + 1 for i, ch in enumerate(title[:-1]) if ch in ",.·、。・　 ，─―〜~！？!?"]
    cut = min(cands, key=lambda i: abs(i - mid)) if cands and min(abs(i - mid) for i in cands) <= len(title) * 0.3 else round(mid)
    return [title[:cut].strip(), title[cut:].strip()]


def find_chrome():
    env = os.environ.get("NOVEL_CHROME")
    cands = [env] if env else []
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    pw = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if pw.exists():
        cands += [str(p) for p in sorted(pw.glob("chromium-*/chrome-linux*/chrome"))]
    return next((c for c in cands if c and Path(c).exists()), None)


def render(html_path, out_png):
    """Chrome(헤드리스)으로 HTML 을 1600×2560 이미지로 만든다. Python 패키지는 필요 없다."""
    chrome = find_chrome()
    if not chrome:
        return "Chrome / Chromium 을 찾을 수 없어 글자를 조판한 표지를 이미지로 만들지 못했습니다（NOVEL_CHROME 으로 경로를 지정할 수 있습니다）"
    with tempfile.TemporaryDirectory() as prof:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={prof}", f"--window-size={W},{H + PAD}", "--force-device-scale-factor=1",
               "--virtual-time-budget=10000", "--allow-file-access-from-files", f"--screenshot={out_png}", html_path.as_uri()]
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # root 로 도는 환경(컨테이너)에서는 sandbox 를 끈다
            cmd.insert(1, "--no-sandbox")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "Chrome 으로 이미지화하는 중 시간이 초과되었습니다"
    if not Path(out_png).exists():
        return "Chrome 으로 이미지화하는 데 실패했습니다：" + (r.stderr or r.stdout)[-300:]
    try:
        crop_png(out_png, H)
    except ValueError as e:
        return f"이미지 잘라 내기에 실패했습니다：{e}"
    return None


def crop_png(path, height):
    """PNG 아래를 잘라 낸다(표준 라이브러리만). 주사선 필터는 윗줄에만 의존하므로 뒤쪽 줄을 버리기만 하면 된다."""
    import struct
    import zlib
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG 가 아님")
    pos, chunks = 8, []
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunks.append((typ, data[pos + 8:pos + 8 + ln]))
        pos += 12 + ln
    ihdr = dict(chunks)[b"IHDR"]
    w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", ihdr)
    if h <= height:
        return
    if depth != 8 or ctype not in (2, 6) or inter:
        raise ValueError(f"예상 밖의 PNG（깊이 {depth}·색 {ctype}·인터레이스 {inter}）")
    stride = 1 + w * (3 if ctype == 2 else 4)
    raw = zlib.decompress(b"".join(d for t, d in chunks if t == b"IDAT"))[:stride * height]

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, height, depth, ctype, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(out)


def main(argv):
    ap = argparse.ArgumentParser(prog="cover")
    ap.add_argument("--model", default="none")
    ap.add_argument("--style", choices=list(STYLE), default="illust")
    ap.add_argument("--motif", default="")
    ap.add_argument("--title-dir", choices=["vertical", "horizontal"], default="horizontal")
    ap.add_argument("--font", choices=list(FONTS), default="mincho")
    ap.add_argument("--author", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--reuse-art", action="store_true")
    a = ap.parse_args(argv)
    c = cfg()
    IMG.mkdir(exist_ok=True)
    prev = json.loads((IMG / "cover.json").read_text(encoding="utf-8")) if (IMG / "cover.json").exists() else {}
    title = a.title or title_from_canon(c)
    author = a.author if a.author is not None else (prev.get("author") or c.get("publish", {}).get("author", ""))
    art, rec, prompt = None, None, None
    if a.reuse_art and prev.get("art") and (IMG / prev["art"]).exists():
        art = prev["art"]
        print(f"지난번 그림（images/{art}）을 재사용합니다.")
    elif a.model and a.model != "none":
        prompt = art_prompt(a.style, a.motif, a.title_dir, mood_from_brief())
        print(f"{a.model} 에 그림을 요청하고 있습니다（{STYLE[a.style][0]}）…", flush=True)
        data, ext, rec = generate_art(a.model, prompt)
        for old in IMG.glob("cover_art.*"):
            old.unlink()
        art = f"cover_art{ext}"
        (IMG / art).write_bytes(data)
        log_usage(rec)
        print(f"images/{art} 저장（{len(data) // 1024}KB, {rec['sec']}초, 비용 {rec.get('cost')}）")
    page = IMG / "cover.html"
    page.write_text(cover_html(title, author, a.style, a.title_dir, a.font, art), encoding="utf-8")
    out = IMG / "cover.png"
    tmp = IMG / "cover.render.png"
    tmp.unlink(missing_ok=True)
    err = render(page, str(tmp))
    if err:
        print("주의：" + err)
        if art:
            shutil.copy(IMG / art, IMG / ("cover" + Path(art).suffix))
            out = IMG / ("cover" + Path(art).suffix)
            print(f"그림만으로 표지를 만들었습니다（{out.name}）. 제목은 EPUB 의 속표지에 들어갑니다.")
        else:
            out = None
    else:
        for old in IMG.glob("cover.*"):
            if old.suffix in (".png", ".jpg", ".jpeg", ".webp") and old.name != tmp.name:
                old.unlink()
        tmp.replace(IMG / "cover.png")
    meta = {"title": title, "author": author, "style": a.style, "style_ja": STYLE[a.style][0], "motif": a.motif,
            "title_dir": a.title_dir, "font": a.font, "font_ja": FONT_JA[a.font], "model": a.model if not a.reuse_art else prev.get("model"),
            "art": art, "cover": out.name if out else None, "prompt": prompt or prev.get("prompt"),
            "usd": (rec or {}).get("cost") if rec else (prev.get("usd") if a.reuse_art else 0),
            "ts": datetime.datetime.now().isoformat(timespec="seconds"), "note": err}
    (IMG / "cover.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if out:
        print(f"표지：images/{out.name}（{FONT_JA[a.font]}·{'세로쓰기' if a.title_dir == 'vertical' else '가로쓰기'}·{STYLE[a.style][0]}）. EPUB 을 다시 만들면 들어갑니다.")
        return 0
    print("표지를 만들지 못했습니다.")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
