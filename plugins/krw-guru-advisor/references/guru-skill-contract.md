# Guru Skill Contract

Each guru skill is a thin adapter for one preselected `author_key`.

The skill may:

```text
call krw_guru_query_context with its fixed author_key
call krw_guru_trace or krw_guru_chain for selected reviewed_ids
compose a Korean investor-facing answer from the ResearchPack
```

The skill must not:

```text
select or route to another guru
hard-code guru principles or persona traits
invent lenses not present in the ResearchPack
invoke the KRW Ontology router automatically
create or edit ontology artifacts
```

The application code owns guru selection. The selected skill only executes that one lens.

