#!/usr/bin/env python3
"""make_epub — 확정된 장(chapters/chNN.md)으로 가로쓰기·왼쪽 넘김의 EPUB3 를 만든다. 표준 라이브러리만.

  python3 tools/novelctl.py epub            (novelctl 경유. 보통은 이쪽)
  python3 tools/make_epub.py [--source final|proofread]

입력:
  polished/chNN.md                 문체 다듬기·AI 티 윤문을 거친 장(있으면 이것을 쓴다. novel_worker)
  chapters/chNN.md                 각 장(첫 줄 `# 제N장 장 제목`). polished 가 없는 장
  output/final_proofread.md        --source proofread 일 때, 교정을 마친 결합 원고(`## 제N장` 구분)
  canon/CANON.md                   제목(“제목：” 줄)
  config.json 의 "publish"         크레디트, AI 생성 표기, 평가 페이지 URL
출력:
  output/<제목>.epub
  output/book_meta.json            평가 페이지에 읽힐 작품 정보(장 제목·설계상의 긴장도·각 장의 장치)
"""
import datetime
import html
import json
import re
import sys
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
PUB = dict(CFG.get("publish", {}))
# v1.0：표지를 make_cover.py(novelctl cover)로 만들었으면 그 표지와 저자명을 쓴다(config.json 은 보호 대상이라 고쳐 쓰지 않는다)
_COVER = ROOT / "images" / "cover.json"
if _COVER.exists():
    try:
        _cv = json.loads(_COVER.read_text(encoding="utf-8"))
        if _cv.get("cover") and (ROOT / "images" / _cv["cover"]).exists():
            PUB["cover_image"] = f"images/{_cv['cover']}"
        if _cv.get("author"):
            PUB["author"] = _cv["author"]
    except ValueError:
        pass
N_CH = CFG["chapters"]


def kn(n):
    """표시용 장 번호. 가로쓰기 한국어 책이므로 아라비아 숫자를 쓴다(파서와는 무관)."""
    return str(n)


EV_JA = {"planted": "설치", "reinforced": "보강", "recovered": "회수"}


def esc(s):
    return html.escape(s, quote=True)


def tcy(s):
    """본문 한 줄을 HTML 로 이스케이프한다. 가로쓰기이므로 세로쓰기용 숫자 가로 짜기(tcy)는 하지 않는다."""
    return esc(s)


def title_from_canon():
    f = ROOT / "canon" / "CANON.md"
    if f.exists():
        m = re.search(r"제목\s*[：:]\s*(.+)", f.read_text(encoding="utf-8"))
        if m and "（기입）" not in m.group(1):
            return m.group(1).strip().strip("『』「」《》“”\"")
    return PUB.get("fallback_title", "무제")


def load_chapters(source):
    chs = []
    if source == "proofread":
        f = ROOT / "output" / "final_proofread.md"
        if not f.exists():
            sys.exit("output/final_proofread.md 가 없습니다（교정 전이면 --source final）.")
        parts = re.split(r"^#{1,2}\s*(?:제|第)\s*(\d+)\s*(?:장|章)[ 　]*(.*)$", f.read_text(encoding="utf-8"), flags=re.M)
        for k in range(1, len(parts), 3):
            chs.append((int(parts[k]), parts[k + 1].strip(), parts[k + 2].strip()))
    else:
        for i in range(1, N_CH + 1):
            f = ROOT / "polished" / f"ch{i:02d}.md"  # 다듬기·윤문본이 있으면 그것(교정판과 같은 기준：proof_source)
            if not f.exists():
                f = ROOT / "chapters" / f"ch{i:02d}.md"
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8").strip()
            head, _, body = text.partition("\n")
            m = re.match(r"#\s*(?:제|第)\s*\d+\s*(?:장|章)[ 　]*(.*)", head)
            chs.append((i, (m.group(1).strip() if m else ""), body.strip()))
    if not chs:
        sys.exit("확정된 장이 없습니다.")
    return chs


def paragraphs(body):
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[＊*◇◆・·―\-]{1,}[ 　＊*◇◆]*", line):
            out.append('<p class="break">＊</p>')
        elif line[0] in "“‘\"「『（(―…":
            out.append(f'<p class="talk">{tcy(line)}</p>')
        else:
            out.append(f"<p>{tcy(line)}</p>")
    return "\n".join(out)


def page(title, body, cls=""):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ko" lang="ko">
<head><meta charset="UTF-8"/><title>{esc(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body class="{cls}">
{body}
</body>
</html>
"""


CSS = """@charset "UTF-8";
html { writing-mode: horizontal-tb; -webkit-writing-mode: horizontal-tb; -epub-writing-mode: horizontal-tb; }
body { margin: 0; font-family: "Noto Serif KR", "Nanum Myeongjo", "NanumMyeongjo", "AppleMyungjo", "Batang", "Noto Serif CJK KR", "Source Han Serif K", serif; line-height: 1.8; text-align: justify; word-break: keep-all; overflow-wrap: break-word; }
h1, h2 { font-weight: normal; }
h2.chapter { font-size: 1.3em; margin-bottom: 3em; margin-top: 2em; }
h2.chapter .no { display: block; font-size: 0.8em; margin-bottom: 0.5em; }
p { margin: 0; text-indent: 1em; }
p.talk { text-indent: 0; }
p.break { text-indent: 0; text-align: center; margin: 1em 0; }
.rate { margin-top: 3em; font-size: 0.85em; text-indent: 0; }
.rate a { text-decoration: none; }
body.titlepage { text-align: center; }
body.titlepage h1 { font-size: 2em; margin-top: 30%; }
body.titlepage .studio { margin-top: 3em; }
body.colophon p { text-indent: 0; }
.credit { margin-top: 1em; }
"""


def image_size(data):
    """JPEG / PNG 의 너비와 높이(표준 라이브러리만). 읽지 못하면 None."""
    import struct
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def image_page(title, href, w=1600, h=2560):
    """이미지 한 장을 화면 가득 담는 한 페이지(전자책에서 흔한 SVG 래퍼 방식)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ko" lang="ko">
<head><meta charset="UTF-8"/><title>{esc(title)}</title>
<style>html,body{{margin:0;padding:0;height:100%;writing-mode:horizontal-tb;}} svg{{display:block;}}</style></head>
<body epub:type="cover">
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="100%" height="100%" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">
<image width="{w}" height="{h}" xlink:href="{href}"/>
</svg>
</body>
</html>
"""


def rating_link(n):
    url = PUB.get("rating_url", "").strip()
    if not url:
        return ""
    return (f'<div class="rate"><p>――</p><p>제{kn(n)}장을 다 읽으셨다면 한마디 남겨 주세요.</p>'
            f'<p><a href="{esc(url)}#ch{n}">이 장 평가하기（5단계·스포일러 없는 한마디）</a></p></div>')


def design_curves():
    """BRIEF.md 의 장 구성표에서 설계상의 긴장도·두 사람의 거리를 읽는다."""
    tension, distance = [], []
    brief = (ROOT / "BRIEF.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^\|\s*(\d+)\s*\|[^|]*\|[^|]*\|\s*(\d)[^|]*\|\s*(\d)[^|]*\|\s*$", brief, flags=re.M):
        tension.append(int(m.group(2)))
        distance.append(int(m.group(3)))
    return tension[:N_CH], distance[:N_CH]


def devices():
    names = CFG.get("slot_names", {})
    out = {str(i): [] for i in range(1, N_CH + 1)}
    for sid, evs in CFG.get("slot_plan", {}).items():
        for c, ev in evs:
            out[str(c)].append(f"{names.get(sid, sid)}：{EV_JA.get(ev, ev)}")
    return out


def build(source="final"):
    title = title_from_canon()
    chs = load_chapters(source)
    book_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "novelctl:" + title))
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    studio = PUB.get("studio", "")
    credits = PUB.get("credits", [])
    disclosure = PUB.get("ai_disclosure", "")

    files = {}
    css = CSS
    font_items = []
    tf = PUB.get("title_font", "")
    if tf and (ROOT / tf).exists():
        # 제목·장 제목에만 쓰는 서체(서브셋화한 TTF 를 넣는다. 본문은 단말의 명조체 그대로)
        fdata = (ROOT / tf).read_bytes()
        font_items.append(("OEBPS/fonts/title.ttf", fdata))
        css += """
@font-face { font-family: "TitlePop"; src: url("fonts/title.ttf"); }
body.titlepage h1, h2.chapter { font-family: "TitlePop", sans-serif; }
body.titlepage h1 { color: #d86e8c; }
"""
    files["OEBPS/style.css"] = css
    byline = PUB.get("author", "") or studio
    files["OEBPS/title.xhtml"] = page(title, f"<h1>{esc(title)}</h1>" + (f'<p class="studio">{esc(byline)}</p>' if byline else ""), "titlepage")
    manifest, spine, nav_items, ncx_points = [], [], [], []
    images = {}
    cover_meta = ""
    for key, iid, page_id, label in [("cover_image", "cover-img", "cover", "표지"), ("frontispiece_image", "front-img", "frontis", "권두화")]:
        rel = PUB.get(key, "")
        src = ROOT / rel if rel else None
        if not (src and src.exists()):
            continue
        ext = src.suffix.lower()
        mt = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        name = f"images/{page_id}{ext}"
        images[f"OEBPS/{name}"] = src.read_bytes()
        prop = ' properties="cover-image"' if page_id == "cover" else ""
        manifest.append(f'<item id="{iid}" href="{name}" media-type="{mt}"{prop}/>')
        wh = image_size(images[f"OEBPS/{name}"]) or (1600, 2560)
        files[f"OEBPS/{page_id}.xhtml"] = image_page(label, name, *wh).replace('epub:type="cover"', 'epub:type="cover"' if page_id == "cover" else "")
        manifest.append(f'<item id="{page_id}" href="{page_id}.xhtml" media-type="application/xhtml+xml" properties="svg"/>')
        spine.append(f'<itemref idref="{page_id}"/>')
        if page_id == "cover":
            cover_meta = '<meta name="cover" content="cover-img"/>'
    manifest.append('<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="title"/>')
    for idx, (n, ctitle, body) in enumerate(chs, start=1):
        name = f"ch{n:02d}.xhtml"
        head = f'<h2 class="chapter"><span class="no">제{kn(n)}장</span>{esc(ctitle)}</h2>'
        files[f"OEBPS/{name}"] = page(f"제{n}장 {ctitle}", head + "\n" + paragraphs(body) + rating_link(n))
        manifest.append(f'<item id="c{n}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{n}"/>')
        label = f"제{kn(n)}장 {ctitle}".strip()
        nav_items.append(f'<li><a href="{name}">{esc(label)}</a></li>')
        ncx_points.append(f'<navPoint id="np{idx}" playOrder="{idx}"><navLabel><text>{esc(label)}</text></navLabel><content src="{name}"/></navPoint>')

    col = [f"<h2>{esc(title)}</h2>"]
    col += [f'<p class="credit">{esc(r)}　{esc(p)}</p>' for r, p in credits]
    if disclosure:
        col.append(f'<p class="credit">{esc(disclosure)}</p>')
    if PUB.get("rating_url"):
        col.append(f'<p class="credit"><a href="{esc(PUB["rating_url"])}">모두의 평가 보기</a></p>')
    col.append(f'<p class="credit">{datetime.date.today().isoformat()}　발행</p>')
    files["OEBPS/colophon.xhtml"] = page("판권", "\n".join(col), "colophon")
    manifest.append('<item id="colophon" href="colophon.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="colophon"/>')
    nav_items.append('<li><a href="colophon.xhtml">판권</a></li>')

    files["OEBPS/nav.xhtml"] = page("목차", f'<nav epub:type="toc" id="toc"><h2>목차</h2><ol>{"".join(nav_items)}</ol></nav>')
    files["OEBPS/toc.ncx"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="ko">
<head><meta name="dtb:uid" content="{book_id}"/></head>
<docTitle><text>{esc(title)}</text></docTitle>
<navMap>{"".join(ncx_points)}</navMap>
</ncx>
"""
    author = PUB.get("author", "")
    creators = (f'<dc:creator id="cr0">{esc(author)}</dc:creator>' if author else
                "".join(f'<dc:creator id="cr{i}">{esc(p)}</dc:creator>' for i, (r, p) in enumerate(credits[:3])))
    files["OEBPS/content.opf"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ko" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{book_id}</dc:identifier>
<dc:title>{esc(title)}</dc:title>
<dc:language>ko</dc:language>
{creators}
<dc:publisher>{esc(studio)}</dc:publisher>
<meta property="dcterms:modified">{now}</meta>
<meta name="primary-writing-mode" content="horizontal-lr"/>
{cover_meta}
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
{"".join('<item id="font-title" href="fonts/title.ttf" media-type="font/ttf"/>' for _ in font_items)}
{chr(10).join(manifest)}
</manifest>
<spine toc="ncx" page-progression-direction="ltr">
{chr(10).join(spine)}
</spine>
</package>
"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_") or "novel"
    epub = out_dir / f"{safe}.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        for name, content in files.items():
            z.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in font_items:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in images.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED)

    tension, distance = design_curves()
    meta = {
        "title": title,
        "chapters": [{"n": n, "title": t} for n, t, _ in chs],
        "design": {"tension": tension, "distance": distance},
        "devices": devices(),
        "edition": now,
    }
    (out_dir / "book_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(re.sub(r"\s", "", b)) for _, _, b in chs)
    print(f"{epub.relative_to(ROOT)} 작성（{len(chs)}장·본문 {total}자·가로쓰기 왼쪽 넘김）")
    print("output/book_meta.json 작성（평가 페이지의 “작품 정보 불러오기”에 붙여 넣기）")
    if not PUB.get("rating_url"):
        print("주의: config.json 의 publish.rating_url 이 비어 있어 장 끝의 평가 링크는 넣지 않았습니다.")
    return epub


if __name__ == "__main__":
    src = "proofread" if "--source" in sys.argv and sys.argv[sys.argv.index("--source") + 1] == "proofread" else "final"
    build(src)
