#!/usr/bin/env python3
"""proofread_apply — 교정 수정안(output/proofread_edits.json)을 적용해 교정판을 별도 파일로 써낸다. 원고는 바꾸지 않는다.

  python3 tools/proofread_apply.py            enabled=true 인 수정만 적용한다
  python3 tools/proofread_apply.py --dry-run  적용할 수 있는지만 확인한다

v1.0：작품마다 Python 리스트에 손으로 쓰던 EDITS 를 JSON 으로 바꿨다.
수정안은 `novelctl.py proofread`(상위 모델)가 만들고, 사람이나 studio 가 enabled 를 바꾼 뒤 적용한다. 손으로 추가해도 된다.

  {"edits": [{"chapter": 4, "before": "원문(그 장에서 한 곳만 일치하는 길이)", "after": "수정", "kind": "오탈자|표현|연속성|설정",
              "reason": "이유", "enabled": true}]}

입력:  polished/chNN.md(문체 마무리 원고). 없으면 chapters/chNN.md
출력:
  output/final_proofread.md       교정판 결합 원고(make_epub.py --source proofread 의 입력)
  output/proofread_changes.md     적용한 수정 목록(장별·원문→수정·이유)과 적용하지 못한 것
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N_CH = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["chapters"]


def main(argv):
    dry = "--dry-run" in argv
    f = ROOT / "output" / "proofread_edits.json"
    edits = json.loads(f.read_text(encoding="utf-8")).get("edits", []) if f.exists() else []
    canon = (ROOT / "canon" / "CANON.md").read_text(encoding="utf-8")
    m = re.search(r"제목\s*[：:]\s*(.+)", canon)
    title = m.group(1).strip().strip("『』「」《》“”\"") if m else "무제"
    log, parts, missing = [], [], []
    for n in range(1, N_CH + 1):
        src = ROOT / "polished" / f"ch{n:02d}.md"
        if not src.exists():
            src = ROOT / "chapters" / f"ch{n:02d}.md"
        text = src.read_text(encoding="utf-8")
        for e in edits:
            if e.get("chapter") != n or not e.get("enabled", True) or e.get("rejected"):  # 기계로 기각된 안은 손으로 켜도 적용하지 않는다
                continue
            old, new = e["before"], e.get("after", "")
            c = text.count(old)
            if c != 1:
                missing.append(f"제{n}장: 찾을 수 없음/여러 곳（{c}건）: {old[:30]}")
                continue
            text = text.replace(old, new)
            log.append((n, e.get("kind", "표현"), old, new or "（삭제）", e.get("reason", "")))
        text = re.sub(r"[ \t]+\n", "\n", text)
        parts.append(text.strip().replace("# 제", "## 제", 1))
    if dry:
        print(f"적용할 수 있는 수정 {len(log)}건, 적용할 수 없는 수정 {len(missing)}건")
        for x in missing:
            print("  미적용:", x)
        return 0 if not missing else 1
    (ROOT / "output" / "final_proofread.md").write_text(f"# {title}\n\n" + "\n\n".join(parts) + "\n", encoding="utf-8")
    lines = ["# 적용한 수정 목록", "", "대상：chapters/（또는 polished/）→ output/final_proofread.md（원고는 바꾸지 않았습니다）", ""]
    cats = {}
    for n, cat, *_ in log:
        cats[cat] = cats.get(cat, 0) + 1
    lines.append("건수：" + ("　".join(f"{k} {v}건" for k, v in sorted(cats.items())) or "0건"))
    cur = None

    def esc(s):
        return s.replace("|", "｜").replace("\n", "⏎")
    for n, cat, old, new, why in log:
        if n != cur:
            lines += ["", f"## 제{n}장", "", "| 분류 | 원문 | 수정 | 이유 |", "|---|---|---|---|"]
            cur = n
        lines.append(f"| {cat} | {esc(old)} | {esc(new)} | {esc(why)} |")
    if missing:
        lines += ["", "## 적용하지 못한 수정", ""] + [f"- {x}" for x in missing]
    (ROOT / "output" / "proofread_changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    body_chars = sum(len(re.sub(r"\s", "", p.split("\n", 1)[1])) for p in parts if "\n" in p)
    print(f"output/final_proofread.md（본문 {body_chars}자）와 output/proofread_changes.md 를 작성. 적용 {len(log)}건, 실패 {len(missing)}건")
    for x in missing:
        print("  미적용:", x)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
