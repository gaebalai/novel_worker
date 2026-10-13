<!-- STUDIO: brief-revise -->
# 집필 지시서(BRIEF) 개정

아래 BRIEF에 프로듀서가 지시를 붙였습니다. 지시를 반영한 BRIEF 전체를 같은 JSON 형태로 돌려주세요.

<자료 이름="테마">
{{THEME}}
</자료>

<자료 이름="현재 BRIEF">
{{BRIEF}}
</자료>

<자료 이름="프로듀서의 지시">
{{INSTRUCTION}}
</자료>
{{LINT_BLOCK}}
## 할 일
- 지시를 반영한다. 지시에 없는 부분은 그대로 남긴다.
- 하나를 바꿨다면, 그것을 참조하는 칸(chapters의 function·hook, slots의 plan, ending)도 맞춘다.
- 장의 수：{{CHAPTERS}}(length.chapters와 chapters의 개수를 일치시킨다).
- slots 규칙: 설치는 정확히 1번, 회수는 설치보다 뒤의 장에서 1번까지.

## 출력
JSON만 돌려주세요. {"brief":{...현재 BRIEF와 같은 형태...},"changes":"무엇을 어떻게 바꿨는지(150자 이내)"}
