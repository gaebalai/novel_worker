<!-- STUDIO: brief -->
# 집필 지시서(BRIEF) 작성

프로듀서가 고른 기획안으로 소설의 집필 지시서(BRIEF)를 만듭니다. BRIEF는 이야기의 **구조**를 정하는 것이며, 인물의 세부·장면·대사·복선을 보여 주는 방식은 나중에 작가(다른 모델)가 CANON(설정서)에서 창작합니다.
너무 많이 쓰지 말 것. 정할 것은 “지키게 하고 싶은 것”과 “각 장의 기능”과 “복선의 배치”입니다.
작품은 한국어 소설로 쓰입니다. 제목·인물 이름·각 항목의 값은 한국어로 적어 주세요.

<자료 이름="테마">
{{THEME}}
</자료>

<자료 이름="선택된 기획안">
{{PROPOSAL}}
</자료>
{{INSTRUCTION_BLOCK}}
## 정해져 있는 것
- 장의 수：{{CHAPTERS}}
- 1장의 글자 수：기준 {{TARGET}}자({{MINC}}~{{MAXC}}자)
- 예상 독자：{{AUDIENCE}}

## 작성 규칙
- chapters는 전체 {{CHAPTERS}}장 분량, n은 1부터 차례로. 각 장의 function(기능)은 필수. tension(긴장도 1~5)과 reveal(드러내는 정도 0~5)로 완급을 설계한다.
- slots(복선 슬롯)는 3~9개. id는 영어 대문자 1자 + 숫자 1자(예: F1, C1, M1). plan은 [[장, "planted"], [장, "reinforced"], [장, "recovered"]]. 설치는 정확히 1번, 회수는 설치보다 뒤의 장에서 1번(이월한다면 회수 없음).
- 각 슬롯의 “내용”은 쓰지 않는다(name과 function만). 내용은 작가가 CANON에서 정한다.
- 회수를 마지막 장에 몰지 않는다. 마지막 장의 회수는 2개까지로 하고, 나머지는 앞 장에서 회수한다. 한 장당 복선 조작(설치·보강·회수)은 한 장의 글자 수 1,000자당 1건 안팎을 기준으로 한다(짧은 장에 몰아넣으면 뒷부분의 장이 다시 써도 통과하지 못하게 된다).
- characters는 이름과 골격만(고정하고 싶은 점). 세부는 작가에게 맡긴다. 이름 있는 인물에게 행동 원리가 필요하다는 것이 작가에게 전달되도록, 골격에 “원하는 것”이나 “두려워하는 것”의 실마리를 넣는다.
- rules.lines에는 “넘어서는 안 되는 선”(아무리 재미있어도 쓰지 않을 것)을 항목별로. 장르의 규범(미스터리의 페어플레이, SF의 과학적 설명)을 여기나 fairplay에 넣는다.
- ending은 결말의 방향성. 독자에게의 결말과 인물에게의 결말을 나눠 쓰면 좋다.
- 기획안의 must_keep(양보할 수 없는 포인트)은 반드시 어느 칸엔가 반영한다.
- banned는 본문에 나오면 안 되는 낱말의 정규 표현식(선택. 예: 마법을 금한다면 {"pattern":"마법|주문","reason":"마법 없음"}).

## 출력
JSON만 돌려주세요(머리말·코드 블록 없이). 형태는 다음과 같습니다.
{"brief":{
 "title":"", "logline":"결말까지 포함한 한 줄 요지", "genre":"", "audience":"",
 "length":{"chapters":{{CHAPTERS}},"chapter_target":{{TARGET}},"chapter_min":{{MINC}},"chapter_max":{{MAXC}}},
 "narration":"서술의 인칭과 시점 인물", "ending_type":"", "core":"이야기의 핵심", "genre_reason":"",
 "setting":"무대(고정할 점)",
 "characters":[{"name":"","role":"","fixed":""}],
 "free":"작가의 재량에 맡길 것",
 "rules":{"style":"","language":"","characters":"","lines":[""]},
 "chapters":[{"n":1,"title":"가칭","tension":2,"reveal":1,"function":"","hook":"장 끝의 훅","forbidden":"이 장에서 금지할 것"}],
 "slots":[{"id":"F1","name":"","function":"","plan":[[1,"planted"],[3,"recovered"]]}],
 "fairplay":[""], "ending":"", "design_notes":[""], "banned":[]
}}
