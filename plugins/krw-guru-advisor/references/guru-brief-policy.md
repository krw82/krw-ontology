# Guru Retrieval Brief Policy

The pre-query Guru retrieval brief is private and English-first. It makes a
Korean investor question searchable against English-first ontology material; it
does not create a philosophy, an evidence claim, or a rendered answer.

Keep only:

```text
original user question
fixed author key
English investment retrieval wording
intent and decision stage
company or asset context supplied by the application
whether company or portfolio evidence is required
English retrieval terms, with Korean wording as secondary context
```

Do not put a favorite checklist, personal voice, target price, company thesis,
or unsupported fact into the brief. Do not show it to the user.

After `krw_guru_query_context`, returned `philosophy_context` replaces any
assumption in the retrieval brief. For company research, use that returned
philosophy plus `GuruLightCompanyContext` to draft the one sealed key question
in the investigation brief described in `investigation-brief.md`.
