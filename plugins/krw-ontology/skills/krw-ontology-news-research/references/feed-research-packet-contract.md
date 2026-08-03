# Feed Research Packet Contract

`get_feed_context` version `krw-feed-context/v2` returns three distinct layers:

```text
items
-> public feed cards

source_materials
-> bounded original X wording plus media extraction records

research_packets
-> internal routing hints generated from the same source materials
```

## Reading Order

For a selected issue, read the matching records in this order:

```text
1. source_materials.original_text
2. source_materials.media[].extraction when status is ready
3. items for the user-facing event summary
4. research_packets.packet for candidate research clauses
5. KRW ontology company evidence for the conclusion
```

The original wording is an observed source statement. A media extraction is only a transcription of what the model could read; it is not an independent source. A research packet is routing metadata, not evidence.

## Packet Schema

```text
schema: krw-market-research-packet/v1
source_context_hash: source snapshot used by the composer
event: issue identity and publication context
source_materials: evidence IDs that map to the linked X material
entity_candidates: possible companies or relationships
ontology_seeds: candidate company-research clauses
impact_hypotheses: explicitly unverified event-to-business paths
research_targets: official evidence to inspect next
open_questions: unresolved decision-relevant questions
contradictions: source conflicts or ambiguity
missing_materials: unavailable or unreadable context
quality: extraction completeness and safety flags
```

Every packet insight that claims a source fact must include `evidence_ids`. An evidence ID is valid only if it exists in the packet's `source_materials`.

## Safety and Evidence Rules

```text
original X wording conflicts with packet
-> use original wording and flag the conflict

image extraction is failed, skipped, or unreadable
-> do not infer the missing visual content

packet names an entity absent from source material
-> do not use it as a research target without resolving the entity first

packet proposes an impact hypothesis
-> translate it into a SearchPlan v2 clause, then require company evidence
```

Never quote the packet, its IDs, source hashes, model names, or internal labels in a normal user answer. Never treat a packet recommendation as a trade recommendation.
