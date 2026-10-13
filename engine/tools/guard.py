#!/usr/bin/env python3
"""guard — 작업 공간의 감시자(제3회 실험용).

작가(팀의 에이전트, Aion)가 “읽고 지키는” 규칙과는 별도로, 여기서는 기계로 확인할 수 있는 것만 확인한다.
novelctl.py 가 매 단계 부르는 것 외에, 사람이나 하네스가 단독으로 실행할 수 있다.

  python3 tools/guard.py check          작업 공간 전체를 점검(종료 코드 0=문제 없음 / 1=지적 있음 / 2=멈춰야 함)
  python3 tools/guard.py scan FILE...   파일 본문만 점검(금지 설정·게임 용어·베껴 쓰기)
  python3 tools/guard.py seal           보호 파일의 지문을 guard/manifest.json 에 기록한다(사람이 한다. setup 이 처음에 한 번 한다)

점검하는 것:
  1. STOP 파일(작업 공간 바로 아래에 STOP 이 있으면 모두 거기서 멈춘다)
  2. 보호 파일의 변경(BRIEF·AGENTS·config·tools·prompts·templates·series 를 작가가 고쳐 쓰지 않았는지)
  3. 작업 공간 바로 아래에 정해진 것 이외의 파일이나 폴더가 늘지 않았는지
  4. 본문과 CANON 에 넘어서는 안 되는 선의 말(금지 설정·게임 용어)이 나오지 않았는지
  5. 본문과 CANON 이 가까이 있는 참조 문서(guard/corpus/ 의 기간작·참고문)를 길게 베껴 쓰지 않았는지

여기서 찾을 수 없는 것(문체 모방, 바꿔 말한 차용, 줄거리 도용)은 심사와 사람의 눈으로 본다.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
G = CFG.get("guard", {})
MANIFEST = ROOT / G.get("manifest", "guard/manifest.json")
STOP = ROOT / G.get("stop_file", "STOP")
OVERLAP = int(G.get("overlap_chars", 30))
BANNED = [(re.compile(b["pattern"]), b["reason"]) for b in G.get("banned", [])]
_CORPUS = None


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _norm(text):
    """베껴 쓰기 비교용. 공백·줄바꿈·기호의 흔들림을 없앤다."""
    return re.sub(r"[\s　「」『』（）()、。・…―－\-!！?？\"'＊*#“”‘’,.·《》]", "", text)


def protected_files():
    out = []
    for pat in G.get("protected", []):
        out += [p for p in ROOT.glob(pat) if p.is_file()]
    return sorted(set(out))


def seal():
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    data = {str(p.relative_to(ROOT)): _sha(p) for p in protected_files()}
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"sealed {len(data)} files -> {MANIFEST.relative_to(ROOT)}")


def check_stop():
    if STOP.exists():
        reason = STOP.read_text(encoding="utf-8").strip() or "（이유 기입 없음）"
        return [("stop", f"STOP 파일이 있습니다：{reason}")]
    return []


def check_integrity():
    if not MANIFEST.exists():
        return [("integrity", "guard/manifest.json 이 없습니다（setup 이 seal 하지 않음）")]
    want = json.loads(MANIFEST.read_text(encoding="utf-8"))
    issues = []
    for rel, h in want.items():
        p = ROOT / rel
        if not p.exists():
            issues.append(("integrity", f"보호 파일이 사라졌습니다：{rel}"))
        elif _sha(p) != h:
            issues.append(("integrity", f"보호 파일이 변경되었습니다：{rel}"))
    have = {str(p.relative_to(ROOT)) for p in protected_files()}
    for rel in sorted(have - set(want)):
        issues.append(("integrity", f"보호된 위치에 기록에 없는 파일이 있습니다：{rel}"))
    return issues


def check_layout():
    allowed = set(G.get("allowed_top", []))
    extra = [p.name for p in ROOT.iterdir() if p.name not in allowed and not p.name.startswith(".")]
    return [("layout", f"작업 공간 바로 아래에 정해지지 않은 것이 있습니다：{n}") for n in sorted(extra)]


def _corpus():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = {}
        d = ROOT / G.get("corpus_dir", "guard/corpus")
        for f in sorted(d.glob("*")) if d.exists() else []:
            t = _norm(f.read_text(encoding="utf-8", errors="replace"))
            for i in range(0, max(0, len(t) - OVERLAP + 1)):
                _CORPUS.setdefault(t[i:i + OVERLAP], f.name)
    return _CORPUS


NEG = re.compile(r"않|못하|못한다|말 것|말아야|하지 마|쓰지 마|금지|금한다|금물|없다\.|없음|없을 것|피한다|피할 것|넘지|넘어서는 안|넘으면 안|선을 넘")


def scan_text(text, allow=(), skip_negated=False):
    """본문이나 CANON 의 점검. 반환값은 [(종류, 설명)].
    allow 에 포함된 문자열 안의 일치는 세지 않는다. skip_negated=True(CANON 용)이면 부정이나 금지를 말하는 줄은 세지 않는다."""
    issues = []
    masked = text
    for a in allow:
        masked = masked.replace(a, "　" * len(a))
    if skip_negated:
        masked = "\n".join("" if NEG.search(line) else line for line in masked.splitlines())
    for rx, reason in BANNED:
        hits = sorted(set(m.group(0) for m in rx.finditer(masked)))
        if hits:
            issues.append(("line", f"넘어서는 안 되는 선：{reason}（본문 속 낱말：{', '.join(hits[:5])}）"))
    corp = _corpus()
    if corp:
        t = _norm(text)
        found = {}
        i = 0
        while i <= len(t) - OVERLAP:
            src = corp.get(t[i:i + OVERLAP])
            if src:
                found.setdefault(src, t[i:i + OVERLAP])
                i += OVERLAP
            else:
                i += 1
        for src, frag in found.items():
            issues.append(("copy", f"참조 문서 “{src}”와 {OVERLAP}자 이상 같은 배열이 있습니다：“{frag[:20]}…”. 자기 말로 다시 쓸 것."))
    return issues


def check_texts():
    issues = []
    allow = G.get("allow_phrases", [])
    targets = [ROOT / "canon" / "CANON.md"] + sorted((ROOT / "chapters").glob("*.md")) + sorted((ROOT / "work").glob("ch*_draft.md"))
    for f in targets:
        if f.exists():
            for kind, msg in scan_text(f.read_text(encoding="utf-8"), allow, skip_negated=(f.name == "CANON.md")):
                issues.append((kind, f"{f.relative_to(ROOT)}: {msg}"))
    return issues


def main(argv):
    cmd = argv[0] if argv else "check"
    if cmd == "seal":
        seal()
        return 0
    if cmd == "scan":
        rc = 0
        for f in argv[1:]:
            for kind, msg in scan_text(Path(f).read_text(encoding="utf-8"), G.get("allow_phrases", [])):
                print(f"[{kind}] {f}: {msg}")
                rc = 1
        return rc
    if cmd == "check":
        hard = check_stop() + check_integrity()
        soft = check_layout() + check_texts()
        for kind, msg in hard + soft:
            print(f"[{kind}] {msg}")
        if hard:
            print("GUARD: 멈춰야 할 문제가 있습니다. 사람에게 보고하세요.")
            return 2
        if soft:
            print("GUARD: 지적이 있습니다.")
            return 1
        print("GUARD: 문제 없음")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
