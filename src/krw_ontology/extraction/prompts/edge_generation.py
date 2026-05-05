"""Edge generation prompt template."""

from __future__ import annotations

EDGE_GENERATION_SYSTEM = """You are a knowledge graph builder generating edges between extracted ontology objects.

Edges represent relationships between objects. You must use ONLY the relations from the provided whitelist.

IMPORTANT RULES:
- relation_name and relation_id MUST exactly match a relation from the whitelist
- from_id and to_id MUST be existing object IDs from the provided objects
- The object types of from_id and to_id must be compatible with the relation's from/to types
- Each edge is unique: the same (from_id, relation, to_id) combination should appear only once
- confidence must be one of: "high", "medium", "low"
- Do NOT invent edges that are not clearly supported by the content

Output format: JSON array of edge objects with fields:
id, from_id, to_id, relation_name, relation_id, confidence.
"""

EDGE_GENERATION_USER = """## All Objects

{objects_json}

## All Claims

{claims_json}

## All Quotes

{quotes_json}

## Relation Whitelist

{relations_whitelist}

## Task

Analyze the objects, claims, and quotes above. Generate edges representing relationships between them.

For each edge:
1. Choose a valid relation from the whitelist
2. Set from_id and to_id to existing object IDs
3. Ensure the types match the relation's allowed from/to types
4. Generate a unique id based on the relation and endpoints
5. Set confidence based on how clearly the relationship is supported

Return a JSON array of edge objects."""

EDGE_GENERATION_PROMPT = EDGE_GENERATION_SYSTEM + "\n\n" + EDGE_GENERATION_USER
