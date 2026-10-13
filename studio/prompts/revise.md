<!-- STUDIO: revise -->
# 기획안 개고

프로듀서가 아래 기획안을 마음에 들어 하며, 지시를 붙여 개고를 요청하고 있습니다.

<자료 이름="테마">
{{THEME}}
</자료>

<자료 이름="원래 안">
{{PROPOSAL}}
</자료>

<자료 이름="프로듀서의 지시">
{{INSTRUCTION}}
</자료>

## 할 일
- 지시에 따라 안을 고쳐 주세요. 지시의 지적을 “지우는” 것이 아니라, 지적의 바탕에 있는 문제를 “푸는” 것(지적을 피하려고 주제에서 달아나지 않는다).
- 지시에 없는 부분은 가능한 한 원래 안 그대로 남겨 주세요.
- 칸은 원래 안과 같습니다(kind, genre, title, setting, synopsis, highlight, appeal, must_keep, genre_reason).

## 출력
JSON만 돌려주세요.
{"proposal":{"kind":"","genre":"","title":"","setting":"","synopsis":"","highlight":"","appeal":"","must_keep":"","genre_reason":""},"changes":"무엇을 어떻게 바꿨는지(150자 이내)"}
