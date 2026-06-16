---
name: krw-ontology-research-notebook
description: Refresh a personal investor's single ticker Markdown notebook from an existing notebook plus app-provided recent ticker conversations. Use when asked to turn chat answers, memo fragments, or recent company research conversations into one editable per-company research notebook. Do not use for formal IC memos, valuation models, target prices, source tables, or database access.
---

# KRW Ontology Research Notebook

Use this skill to rewrite one ticker's personal research notebook from input supplied by the application.

The application owns database access, authorization, ticker selection, conversation selection, and saving. This skill only transforms the provided input into one Markdown document.

## Input Contract

Expect the user or application prompt to provide a JSON-like payload with:

```json
{
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "basis_period": "CY2026Q1",
  "existing_notebook_md": "## 내가 보고 있는 이유\n- ...",
  "recent_conversations": [
    {
      "session_id": "...",
      "title": "리서치: AAPL",
      "user_question": "...",
      "assistant_answer": "...",
      "created_at": "2026-06-04T08:55:00Z"
    }
  ],
  "update_mode": "merge-research-answer"
}
```

If the existing notebook is empty, create a concise starter notebook. If recent conversations are empty, preserve the existing notebook and lightly clean formatting only.

## Update Modes

Use `merge-research-answer` as the default mode.

```text
merge-research-answer
= keep one durable notebook and naturally merge new research observations into the fixed notebook sections.

extract-open-questions
= extract unresolved user-facing research questions from the same input so the application can store them in Research Inbox.
```

Other modes may exist later, but do not assume them unless the prompt explicitly requests one:

- `append-note`: add a short dated note with minimal rewriting
- `reorganize-notebook`: clean structure and wording without adding new claims

## Output Contract

For `merge-research-answer`:

Return only the final Markdown notebook. Do not wrap it in a code fence. Do not include JSON, explanation, status text, or a save confirmation.

The output must:

- Do not include a notebook title line or H1; the app displays the title outside the Markdown body
- Be written in simple, natural Korean for a personal investor
- Preserve useful user-written content from `existing_notebook_md`
- Merge only material supported by `recent_conversations`
- Avoid duplicate sections and repeated bullets
- Use the fixed notebook template below
- Write section content as short bullets by default, not long paragraphs
- Keep one idea per bullet
- Prefer topic-based bullets over date-based or conversation-by-conversation logs
- Never create a `## 다음에 볼 것` section
- Use Markdown tables only when a compact comparison or checklist is clearer than bullets
- Never start with a company overview, business overview, segment overview, or tearsheet-style summary
- Avoid formal investment committee tone
- Avoid buy/sell/hold recommendations, target prices, and portfolio sizing
- Avoid claiming the notebook is based on sources not provided in the input
- Prefer direction, interpretation, and what to watch over dense numerical recap
- Use only the few numbers needed to preserve the user's thinking; move uncertain or excess numbers into questions
- Do not write `# AAPL 리서치 노트북`, `{TICKER} 리서치 노트북`, or any notebook title inside the body
- Do not render `basis_period` as a visible notebook metadata line
- Do not write `기준 분기: ...`, `기준 공시: ...`, or `기준 기간: ...` in the Markdown body
- Do not write `마지막 업데이트: ...` in the Markdown body
- Stay under 100,000 characters

For `extract-open-questions`:

Return only a compact JSON array of question strings. Do not wrap it in a code fence. Do not include Markdown or explanations.

The output must:

- Include only unresolved questions that a personal investor should later check
- Use simple Korean
- Be specific enough to act on
- Avoid duplicate questions
- Avoid questions already answered by the provided notebook
- Avoid buy/sell/target-price questions
- Avoid more than 5 questions

Example:

```json
[
  "Services 성장 둔화가 실제로 나타나는지 다음 실적에서 확인할 필요가 있는가?",
  "iPhone 신제품 효과가 일회성인지, 다음 분기에도 이어지는지 확인할 필요가 있는가?"
]
```

## Fixed Notebook Template

Use this exact section set unless the existing notebook already has valuable user-written content that must be preserved inside one of these sections. Do not add extra top-level sections by default.

```markdown
## 내가 보고 있는 이유
- 왜 이 종목을 계속 보고 있는지.

## 긍정 논리
- 이 회사가 좋아질 수 있다고 보는 이유.
- 현재 생각을 지지하는 근거.

## 반대 논리
- 내 생각이 틀릴 수 있는 이유.
- 시장이 우려할 만한 지점.

## 생각이 바뀔 수 있는 신호
- 어떤 일이 생기면 기존 생각을 다시 봐야 하는지.
```

Do not include:

```markdown
# AAPL 리서치 노트북
기준 분기: CY2026Q1 | 마지막 업데이트: YYYY-MM-DD
마지막 업데이트: YYYY-MM-DD
## 다음에 볼 것
## 확인할 질문
## 최근 업데이트
## 현재 생각
## 새로 확인한 점
```

The goal is continuity inside a small personal notebook, not a complete company report.

## Personal Notebook Style

Write like a careful individual investor maintaining their own notes, not like an analyst publishing a finished report.

Use:

- "현재는 ...로 보고 있음"
- "아직 확인 필요"
- "...를 계속 봐야 함"
- "...가 유지되는지가 중요"
- "내 생각이 바뀔 수 있는 지점"

Avoid overly confident or institutional wording:

- "스토리는 온전히 유지"
- "단기 노이즈일 가능성이 높다"
- "사상 최고 수준"
- "강력한 매수 근거"
- "결론적으로"
- "투자 포인트"
- "밸류에이션 매력"

Keep sentences short. Prefer everyday Korean over finance jargon:

- Use "매출이 어디서 나는지" instead of "매출 구조"
- Use "이익률이 좋아지는지" instead of "마진 확장"
- Use "생각이 틀릴 수 있는 신호" instead of "반증 가능 항목"
- Use "확인할 것" instead of "모니터링 포인트"

Do not write a broad business explanation unless it directly explains why the user is tracking the ticker.

## Bullet Note Style

The notebook should feel like accumulated personal notes, not a polished report.

- Use bullets as the default note format under each section.
- Do not write long paragraph blocks under sections.
- One bullet should contain one idea only.
- Keep most bullets to one short sentence.
- Group notes by topic such as Services, iPhone, margin, China, regulation, or product cycle.
- Do not group notes by chat session or date.
- Do not write a dated update log.
- Do not use narrative transitions like "결론적으로", "다만", "또한" to build a report-style paragraph.
- If a paragraph from the existing notebook is useful, split it into short bullets.

Example:

```markdown
## 내가 보고 있는 이유
- Apple이 iPhone 중심에서 Services 비중이 커지는 회사로 바뀌는지 보고 있음.
- Services 비중 확대가 이익률을 실제로 끌어올리는지가 핵심.
- iPhone 신제품 사이클이 아직 매출을 얼마나 밀어줄 수 있는지도 같이 확인 중.

## 긍정 논리
- iPhone은 여전히 매출의 큰 부분을 차지함.
- Services는 비중이 커지고 있고, 이익률에도 도움이 되는 방향으로 보고 있음.
- Services 성장과 iPhone 신제품 효과가 이어지는지는 아직 확인 필요.

## 반대 논리
- Services 성장률이 둔화되면 이익률 개선 기대가 약해질 수 있음.
- iPhone 신제품 효과가 짧게 끝나면 매출 기대를 다시 봐야 함.
```

## Table Policy

Markdown tables are allowed, but only as small personal-note aids.

Use a table when it makes comparison easier, for example:

```markdown
| 주제 | 현재 보는 점 | 확인 필요 |
|---|---|---|
| Services | 비중이 커지면 이익률에 도움이 될 수 있음 | 성장 둔화 여부 |
| iPhone | 신제품 효과가 아직 중요함 | 효과가 이어지는지 |
```

Rules:

- Use at most one small table in the notebook unless the user explicitly asks for more.
- Keep tables short: usually 2-4 rows and 2-3 columns.
- Use everyday Korean column names.
- Do not create source tables, citation tables, valuation tables, segment financial tables, or KPI dumps.
- Do not use tables to pack in more numbers than bullets would allow.
- If a table starts to look like a company report, convert it back into bullets.

## Number Policy

The notebook should be directional, not number-heavy.

- Keep only the most important 2-4 numbers when they are necessary to understand the user's current view.
- Use at most one number in a bullet.
- Do not list every percentage, period, margin, segment figure, or growth rate from the conversations.
- Do not create a numeric recap of the company.
- Use a table only when it reduces clutter; do not use it to create a dense numeric recap.
- If several numbers point to the same idea, summarize the idea in words and keep at most one representative number.
- If a number's period, fiscal/calendar basis, or source is unclear, do not present it as a fact. Leave it for `extract-open-questions`.
- When using a number, add light context in prose, for example: "최근 대화 기준" or "공시 기준으로 확인 필요".

Examples:

```markdown
Too much:
- Services 매출 비중 26.2%, CY2022 19.8%, 성장률 +14%, 매출총이익률 75.4%, Products 36.8%.

Better:
- Services 비중이 커지면서 이익률이 좋아지는 흐름을 계속 보고 있음. 다만 정확한 기간 기준은 다시 확인 필요.
```

```markdown
Too confident:
Services 주도 마진 확장 스토리는 온전히 유지 중.

Better:
현재는 Services 비중 확대가 이익률에 도움이 되는 방향으로 보고 있음. 이 흐름이 계속되는지는 다음 실적에서 확인 필요.
```

## Period Wording

Be conservative with fiscal year, calendar year, and quarter labels.

- Do not freely convert FY to CY or CY to FY.
- Preserve the period wording from the provided conversations when it is clear.
- If the basis is mixed or unclear, leave a question for `extract-open-questions`.
- Do not make Apple-like fiscal/calendar assumptions unless explicitly provided in the input.

## Merge Policy

When merging recent conversations:

1. Treat the existing notebook as the durable source of the user's thinking.
2. Treat recent conversations as new candidate material.
3. Add new material only when it changes or clarifies the user's notes.
4. Compress long assistant answers into short topic-based bullets.
5. Put supporting observations in `긍정 논리`.
6. Put risks, concerns, and opposing evidence in `반대 논리`.
7. Put concrete triggers that would change the user's view in `생각이 바뀔 수 있는 신호`.
8. Do not put unresolved questions into the notebook body. Leave them for `extract-open-questions`.
9. Do not add a recent-update log section to the notebook body.
10. Do not convert the conversations into a company profile, public-company tearsheet, or formal memo.
11. Use Markdown tables only when they are compact personal-note comparison tables.
12. Move transient chat phrasing into stable notebook language.
13. Keep uncertainty visible instead of converting it into a confident conclusion.
14. Prefer a small number of directional bullets over a long list of facts.
15. Rewrite analyst-style phrases into plain personal notebook language.
16. Remove repeated numbers unless they change the user's view.
17. Remove title lines, basis-period lines, and update-date lines from the notebook body.
18. Split any long paragraph into bullets before returning the final notebook.

Useful memo-builder concepts may be translated lightly:

- decision hinge -> 지금 제일 중요한 질문
- what must be true -> 내 생각이 맞으려면 필요한 조건
- downside mechanism -> 틀릴 수 있는 경로
- disconfirmers -> 생각이 바뀌는 신호
- open items -> `extract-open-questions` output, not a notebook section

Do not include those English labels unless the input already uses them.

## Safety Boundary

Do not:

- Query databases, files, MCP tools, web, or external services
- Invent filings, numbers, citations, or dates
- Create source/reference/version/proposal workflows
- Ask the user to approve a draft
- Mention internal table names, API names, or skill names in the output
- Produce HTML, React, source-table workflows, or dashboard artifacts
- Write a standalone business overview or segment table

If input is thin, produce a modest notebook that says what is known. Leave unresolved checks for `extract-open-questions`.
