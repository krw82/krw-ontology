# Idea Generation Search Order

## Core Rule

Do not search every ontology layer equally. Idea generation is candidate
discovery and research prioritization, not deep single-company research.

Use ontology layers in this order:

```text
screen definition
-> global-spine candidate route
-> semantic exposure validation
-> recent change / why-now evidence
-> financial pathway and burden
-> quote, support, trace, or chain verification
```

For idea generation, search semantic exposure before numeric evidence. Use the
global spine to find candidate routes, company semantic objects to validate
exposure, recent change objects to establish why-now, and numeric/evidence
trace only to classify or verify top candidates.

## 1. Screen Definition

Before broad discovery, define:

```text
who qualifies
what filing evidence proves direct or operational exposure
what financial path must exist
what would make a candidate a false positive
what could make a candidate worth researching now
```

Do not use a theme label alone as the screen.

Bad:

```text
AI beneficiary
memory winner
defensive consumer company
```

Good:

```text
companies with direct operating exposure to AI data-center power demand where
filings show demand, orders, backlog, capacity, pricing, or cash-flow linkage
```

## 2. Candidate Route

For tickerless theme or condition questions, start with one bounded global-spine
route through `query_context`:

```text
query_context(
  question=internal idea-screen brief,
  tickers=[],
  limit_tickers<=5,
  limit_results<=3
)
```

Use this result only to identify candidate routes.

Inspect:

```text
ticker_candidates
top_objects
matched_object_counts
top_reasons
tier
```

Do not infer:

```text
high score = strong idea
many keyword hits = direct exposure
top reason = investment conclusion
candidate route = final ranking
```

## 3. Semantic Exposure Layer

After candidates are routed, validate direct exposure before metrics.

Prefer these object types:

```text
BusinessActivity
CompanyBusinessProfile
ExternalFactorExposure
BusinessFactor
```

Use this layer to answer:

```text
What does the company actually do that qualifies it for this screen?
Which product, service, customer, capacity, cost, demand, or business activity creates exposure?
Is the exposure direct, operationally related, or only thematic?
```

If exposure is only thematic, downgrade to C or Reject.

## 4. Recent Change And Why-Now Layer

After exposure is real, check whether the latest filing makes the candidate
more or less important to research now.

Prefer:

```text
ChangeEvent
TrendObservation
BusinessEvent
BusinessFactor
```

Use this layer to answer:

```text
Did the latest filing strengthen the exposure?
Did it weaken the pathway?
Did management become more specific?
Did the burden or risk become more important?
Is this just stale annual business-description language?
```

Why-now does not mean stock timing. It means research timing: the filing made
the exposure, conversion path, burden, or rejection risk important enough to
prioritize.

## 5. Financial Pathway And Burden Layer

Only after semantic exposure is real, check whether the driver can reach a
financial line item.

Prefer:

```text
MetricObservation
XBRLFact
Calculation
AgreementTerm
```

Use this layer to answer:

```text
Can the driver reach revenue, margin, operating cash flow, capex, debt, or balance sheet flexibility?
Is there a contract, backlog, commitment, covenant, or financing term that changes the path?
Does the benefit have a cost, capex, working-capital, concentration, or financing burden?
```

Do not use raw numbers to decorate the answer. Use numbers only when they
change the A / B / C / Reject classification.

## 6. Evidence Verification Layer

Use these only after a candidate or rejection path is selected:

```text
EvidenceQuote
SupportLink
trace
chain
```

Use this layer when:

```text
promoting a candidate to A
making a strong direct-exposure claim
making a strong financial-pathway claim
rejecting a tempting false positive
resolving a conflict between candidate route and actual filing evidence
```

Do not start broad discovery from quotes. Quotes verify selected routes; they
do not define the candidate universe.

## 7. Stop Rule

Stop once each material candidate can be classified:

```text
A - direct exposure + recent evidence + financial path + why-now + rejection risk
B - credible path with one material evidence gap
C - thematic or weakly connected candidate
Reject - false positive for this screen
```

Do not continue into full company research. The handoff for one selected
candidate is `krw-ontology-research`.
