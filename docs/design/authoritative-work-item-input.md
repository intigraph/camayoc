# Authoritative work-item input for entity linking

**Status: implemented in source; runtime adoption requires the reviewed linker
and caller versions together.**

The harness resolves tracker routing. The linker accepts that resolved text
through `--work-item-json PATH` (`-` reads stdin), so it does not resolve the same
ID against a different tracker. Version 1 accepts this envelope:

```json
{
  "version": 1,
  "items": [
    {"id": "project-123", "title": "Fix the target", "description": "Original text"}
  ]
}
```

All items must have unique IDs, nonempty string titles and string descriptions.
An empty description is valid. IDs use letters, numbers, dots, underscores and
hyphens. Unknown versions, missing fields and invalid rows refuse before any
retrieval or write. The interface cannot be combined with positional IDs or an
explicit `--db`; the positional-ID interface retains its legacy reader.

Input is text, not authority to promote knowledge. Entity retrieval, model
abstention, optional `--write` and inferred quarantine routing are unchanged.
The caller must supply the original text from its authoritative tracker and
report an unavailable read as unknown. It must not substitute an empty title or
retry through a different tracker. Stdin keeps work-item bodies out of command
arguments and avoids shell interpolation.

Deploy the additive linker interface first. A newer caller talking to an older
linker receives a visible unsupported-argument failure and does not fall back to
the legacy reader. A hint failure must leave the already verified dispatch or
cycle checkpoint intact.
