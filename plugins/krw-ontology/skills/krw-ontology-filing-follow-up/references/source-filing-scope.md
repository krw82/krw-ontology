# Source Filing Scope Policy

This policy applies only after the KRW web runtime has selected this skill for
one verified SEC filing card. The runtime, not the user or the model, owns the
active `source_filing_event_id` and the filing identity.

## Immutable Source Identity

- Use the original card's filing only. Do not accept a user-supplied filing ID,
  ticker, accession number, or URL as a replacement.
- Do not discover filings, search a filing catalog, compare filings, or broaden
  the request to company research.
- If the active source cannot supply the requested fact, say that the detail is
  not established by this filing. Do not substitute another document, model
  memory, current news, or market data.

## Bounded Reading Path

First use the verified filing metadata to identify the form and issuer.

| Filing type | Permitted reading path |
| --- | --- |
| 8-K, 8-K/A, 6-K, or 6-K/A | Use a ready brief only for orientation, identify the deterministic sections, then read only the section needed for the question. When that section identifies a relevant attachment, list this filing's verified documents and read only the returned text exhibit needed to answer. |
| Form 4 or 4/A | Use the verified metadata and structured owner/transaction facts. Treat transaction records as reported facts, not a narrative filing to expand beyond their scope. |

Read the smallest source portion that answers the question. Do not repeat an
unchanged retrieval or dump an entire filing when a specific section is enough.

An exhibit may be read only after it appears in the active filing's verified
document list. Use its returned document key; never guess a filename, compose a
SEC URL, or accept one supplied by the user. An attached exhibit is within the
same filing boundary, but another accession is not.

## Claim Boundary

Keep three layers distinct: reported filing fact, reasonable interpretation,
and what this one filing cannot establish. A single filing does not establish a
durable earnings outcome, valuation conclusion, or personalized investment
action unless the document itself directly supports the narrower factual claim.

For Form 4, never infer an executive's intent, future share-price direction, or
a buy/sell signal from a reported transaction alone.
