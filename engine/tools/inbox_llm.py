#!/usr/bin/env python3
"""inbox_llm — 작가(사람·Claude 등 API로 부를 수 없는 작가)용 “받은 편지함” 어댑터.

novelctl.py 의 writer_cmd / reviewer_cmd 에 이것을 지정하면, 모델 호출 대신
work/inbox/<패킷 이름에서 .packet.md 를 뺀 이름>.md 를 답으로 읽어 표준 출력으로 돌려준다.
답이 놓여 있지 않으면 종료 코드 4로 멈추고, novelctl 은 AWAITING_AUTHOR 로 에스컬레이션한다.

  python3 tools/inbox_llm.py <packet> writer|reviewer

다 쓴 답은 work/inbox/done/ 으로 옮긴다(다시 쓰기에서 같은 패킷 이름이 재사용되어도 섞이지 않는다).
패킷과 답의 짝은 work/ 에 남으므로, 나중에 “누구에게 무엇을 건넸고 무엇이 돌아왔는지”를 모두 추적할 수 있다.
"""
import datetime
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: inbox_llm.py <packet> writer|reviewer")
    packet = Path(sys.argv[1])
    key = packet.name.removesuffix(".packet.md")
    inbox = ROOT / "work" / "inbox"
    ans = inbox / f"{key}.md"
    if not ans.exists():
        (inbox / "WAITING.txt").write_text(f"{packet}\n{ans}\n", encoding="utf-8")
        print(f"work/inbox/{key}.md 가 없습니다（패킷: work/{packet.name}）", file=sys.stderr)
        sys.exit(4)
    text = ans.read_text(encoding="utf-8")
    done = inbox / "done"
    done.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(ans), done / f"{key}.{stamp}.md")
    (inbox / "WAITING.txt").unlink(missing_ok=True)
    print(f"inbox: {key} ({len(text)} chars) author={sys.argv[2] if len(sys.argv) > 2 else '-'}", file=sys.stderr)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
