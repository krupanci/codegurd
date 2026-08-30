# CodeGuard — Decision Log

Tracks what was decided, in which phase, and briefly why. Add a new section
per phase as the project progresses.

---

## Phase 0 — Project Scaffolding

- Package layout: `src/codeguard/` with subpackages `parsing/`, `graph/`,
  `retrieval/`, `storage/`. Empty subpackages created up front for phases
  not yet started (`graph/`, `retrieval/`), so the shape of the project is
  visible from day one without importing unused code yet.

---

## Phase 1 — Parsing Layer (Chunking)

### Decision A — How to find functions/classes in the syntax tree
**Chosen: manual recursive tree walk** (check `node.type` ourselves),
not tree-sitter's `.scm` query language.
**Why:** the project's own stated goal is to build real understanding of
how these tools work internally (same reasoning applied later to the
hand-rolled graph in Phase 3) — a query file would just hide the tree shape
behind a second syntax to learn. The manual walk also made it natural to
track "what am I currently nested inside" while walking, which Decision B
below depends on.

### Decision B — What a chunk's stable ID is built from
**Chosen: `hash(file_path + qualified_name)`** — never line numbers, never
content.
**Why:** Phase 5 (change-impact) needs to match the *same* chunk across an
old and a new version of a file specifically *because* its content changed.
An ID built from content or line numbers would break that matching. A
qualified name (e.g. `LoginHandler.validate`) only changes if the symbol is
genuinely renamed or moved — which is the correct notion of "this is a new
identity" for this tool. Verified with a test that inserts unrelated lines
above every symbol in a file and confirms all IDs stay identical.

### Decision C — How to compute the file staleness hash
**Chosen: compute git's blob hash formula ourselves**
(`sha1("blob " + len(content) + "\0" + content)`), not by shelling out to
`git hash-object` via gitpython.
**Why:** it's git-repo-agnostic (works on any file, tracked or not) and
removes a hard dependency on being inside a git repo just to do basic
staleness checking. `gitpython` is still planned for Phase 5, where its
job (reading old file content at a ref) is something this hash function
can't do anyway. Verified against real `git hash-object` output in a test —
hashes match exactly.

### Decision D — Where the LanceDB connection lives
**Chosen: a `Storage` class**, instantiated once per CLI run and passed
around, instead of a module-level global connection.
**Why:** avoids global state leaking between test runs — each test can spin
up its own `Storage` pointed at a temp directory. Barely more code than a
global connection for the benefit.

### Other Phase 1 notes
- Class bodies and function bodies are both walked recursively, so **nested
  functions** (a function defined inside another function, or inside a
  method) are correctly found and correctly typed as `"function"`, not
  `"method"` — only a `function_definition` whose *immediate* enclosing
  scope is a class becomes `"method"`.
- `file_path` on every chunk is stored **relative to the project root**,
  not absolute, so the same project indexed from different machines/paths
  produces identical chunk IDs.
- All chunks parsed from the same file share one `blob_hash` (a file-level
  property), even though `chunk_id` is per-symbol.
- Verified end-to-end manually and with pytest: index a file → mark fresh →
  edit the file → correctly detected as stale → delete old rows for that
  file → re-parse → re-insert → correctly detected as fresh again.


  =====================================================================================


  ## Phase 2 — Raw Relationship Extraction

### Decision A — One tree walk, or a separate walker for edges
**Chosen: extend the existing `Chunker._walk`**, don't write a second walker.
**Why:** the walk already tracks "which function/class am I currently inside"
(needed for Phase 1's qualified names) — that's the exact same information
Phase 2 needs to know who a call or import belongs to. A second walker would
either re-derive that scope tracking from scratch or re-walk the tree twice
for no benefit. To avoid `chunker.py` turning into one large file mixing two
concerns, the *interpretation* logic (what does this node mean) was split
into a new `parsing/edges.py` of small pure functions; `chunker.py` still
does the one walk and calls into them.

### Decision B — What name to record for a call
**Chosen: both.** `target_name` holds the bare rightmost identifier
(`"save"` from `self.db.save()`), matching the plan's own wording. A second
field, `target_expression`, holds the full text (`"self.db.save"`) at
essentially zero extra cost, since the source text is already in hand.
**Why:** the bare name is what Phase 3's resolver needs to match against;
the full expression is free information that would be annoying to have to
recompute later once Phase 3 already depends on the table's shape.

### Decision C — Resolve the source of an edge now, or defer it like the target
**Chosen: resolve `source_chunk_id` immediately; leave `target_name` raw.**
**Why:** the plan's "raw extraction" principle exists specifically because
resolving *who a call refers to* is genuinely ambiguous (multiple `save`s
could exist) and needs Phase 3's ranked-candidate logic. The *source* has no
such ambiguity — while walking, the enclosing chunk is already known with
certainty from Phase 1's own pass over the same node. Storing it raw would
just make Phase 3 redo work Phase 2 could do for free. This also cleanly
handles calls sitting outside any function (e.g. `if __name__ == "__main__":
main()`): `source_chunk_id` is simply `None` and `source_qualified_name` is
`"<module>"` — no pseudo-chunk needed, and Phase 4's dead-code finder still
sees the real edge into `main`, avoiding a false "orphan" result.

### Decision D — Edge model: dataclass or pydantic
**Chosen: plain frozen `dataclass`**, matching `Chunk`.
**Why:** `Edge` is built entirely from source code this project's own code
already parsed successfully — never from untrusted external input — so
pydantic's validation isn't buying anything here. Keeping the parsing layer
free of that dependency also matches the boundary already drawn for
`CodeGuardSettings`: pydantic is for config/edges-of-the-system talking to
the outside world (env vars), not for internal data produced by trusted code.

### Decision E — Import edges: one row per statement, or one per name
**Chosen: one Edge row per imported name.** `from typing import List,
Optional as Opt` produces two separate edges, not one.
**Why:** verified directly against tree-sitter's actual output that a
single `import_from_statement` node can cover several names, one of them
aliased (`aliased_import` with separate `name`/`alias` fields). Keeping one
row = one relationship means Phase 3 never has to re-parse statement
structure when reading rows back out of LanceDB - it can just iterate rows.
Lazy/local imports inside a function body are handled through the exact
same scope-tracking as calls, rather than being treated as a special case.



### Other Phase 2 notes
- `chunker.chunk_file()` (Phase 1's method) is unchanged and still returns
  only chunks - it now just calls the new `chunker.parse_file()` internally
  and discards the edges, so no Phase 1 caller or test needed to change.
- `parse_file()` is the new entry point going forward - it returns
  `(chunks, edges)` from the one walk.
- Verified end-to-end against a real sample file covering: a bare call, an
  attribute call (`self.db.save`), a two-level attribute call
  (`os.path.join`), a bare `import`, a `from ... import`, a `from ... import`
  with multiple names, an aliased import, and a module-level call inside
  `if __name__ == "__main__":`. All produced exactly the expected edges.



