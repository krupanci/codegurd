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


  =====================================================================
  ## Phase 3 — The Hand-Rolled Graph

### Decision A — Do `imports` edges become part of the call graph too
**Chosen: no.** Only `calls` edges are resolved and wired into the
forward/reverse adjacency maps. `imports` edges are still read back out of
LanceDB and used *during* resolution (to know what a name refers to when
it wasn't defined in the same file), but an import itself never becomes a
forward/reverse graph edge.
**Why:** the graph's whole purpose, per the plan, is "who calls/depends on
whom" so Phase 4 can find zero-incoming-edge symbols and Phase 5 can find
real callers of a changed signature. An import doesn't have call-site
arguments and isn't a place execution actually happens, so folding it into
the same adjacency structure would blur two different kinds of fact
together for no benefit to either later phase. If a future need shows up
(e.g. "flag re-exported names as used even with zero direct callers"),
that's a separate, explicit lookup against the raw `imports` edges already
sitting in LanceDB — not a reason to widen the graph's definition now.

### Decision B — How the graph is represented in memory
**Chosen: a `Graph` class wrapping two plain dicts of sets**
(`dict[str, set[str]]` for forward and reverse), not raw dicts passed
around everywhere, and not a dict of lists.
**Why a class:** Phases 4, 5, and 7 all need to walk this same structure.
Giving them one object with `callees()`, `callers()`, `walk_forward()`,
`walk_reverse()` means the traversal logic is written once and lives in
one place, instead of every feature re-implementing its own BFS over raw
dicts.
**Why sets, not lists:** traversal only cares whether an edge exists
between two nodes, not how many times a call happens to repeat; sets give
free de-duplication and O(1) "have I seen this neighbor" checks, which
BFS needs constantly. The trade-off — losing call *counts* — was judged
not worth carrying, since none of Phases 4/5/7 currently ask "how many
times," only "does a path exist."

### Decision C — What to do with module-level calls (no enclosing chunk)
**Chosen: a synthetic pseudo-node id, `"<module>::<file_path>"`**, used as
the source of the edge instead of skipping it or reusing a shared
`"<module>"` id across every file.
**Why:** Phase 2's Decision C already made sure a call like `main()` inside
`if __name__ == "__main__":` produces a real edge with `source_chunk_id =
None`. Dropping that edge here would silently undo that work and make
`main` look orphaned to Phase 4. A single shared `"<module>"` id across
every file was considered and rejected — it would incorrectly merge
"top-level code in file A" and "top-level code in file B" into one node,
which is harmless for basic orphan detection but would be a wrong
foundation to build later features on (e.g. Phase 5 potentially reasoning
about *which* file's module-level code is the caller). One id per file
costs nothing extra and keeps the graph honest.

### Decision D — Name resolution: priority chain vs. a numeric score
**Chosen: a plain if/elif-style priority chain** (same file → imported
module → global), returning as soon as any tier produces a match, over a
weighted-scoring approach.
**Why:** the plan's own spec is already exactly three ordered tiers, not
a continuum of signals to balance — a numeric score (e.g. same-file=100,
imported=50, global=10) would just be the same three buckets wearing
extra arithmetic. A priority chain is directly readable: for any resolved
edge you can say in one sentence which rule fired and why. Building the
per-file import index *before* resolving any calls (`build_import_index`)
was necessary either way, since tier 2 can't work without knowing what a
file imported ahead of time — this isn't a fork, both approaches need it.

### Decision E — What happens when a tier matches more than one chunk
**Chosen: keep every match from that tier as a ranked candidate on the
`ResolvedEdge`, and wire a graph edge to ALL of them** — never guess a
single "best" one and never drop the edge.
**Why:** the plan is explicit that silent guessing is worse than admitted
ambiguity here ("if it's still ambiguous, keep multiple ranked candidates
rather than silently guessing one"). Concretely: if two `save` methods
exist and both are visible to a caller at the same tier, marking only one
of them as "has a caller" would risk Phase 4 wrongly reporting the other
as dead code. Adding an edge to every candidate is the conservative
choice — it can occasionally suppress a real orphan report, but it can
never fabricate a false one, which is the safer failure mode for a tool
whose credibility depends on not crying wolf (see Phase 4's own stated
goal). Every `ResolvedEdge` with more than one candidate is also kept on
`Graph.ambiguous` for later inspection — nothing here is silently
swallowed even in the accepted trade-off case.

### Decision F — Traversal: BFS or DFS, recursive or iterative
**Chosen: iterative BFS, using `collections.deque` and an explicit visited
set.**
**Why BFS over DFS:** the plan's own language for both this phase and
Phase 7 is "walk outward N steps / N hops" — that's precisely what BFS
measures for free, since everything found at queue-depth *k* really is
*k* hops away. DFS would need the same depth-tracking bolted on by hand to
answer the same question, with no upside in return.
**Why iterative over recursive:** call graphs commonly contain cycles —
mutual recursion, or a function calling itself. A recursive walk needs a
visited set threaded through every call anyway to avoid looping forever,
and still risks Python's recursion limit on a long, unbroken chain. An
iterative loop with an explicit queue and visited set handles cycles
safely with no such risk, at the cost of a few more lines of plain loop
code — judged a clearly better trade here, not a close call.

### Decision G — Resolved edge as its own type, not a mutated `Edge`
**Chosen: a new frozen `ResolvedEdge` dataclass** (wrapping the original
`Edge` plus a tuple of `ResolvedCandidate`s and a confidence tier), rather
than adding a `resolved_chunk_id` field onto `Edge` itself.
**Why:** `Edge` is frozen (Phase 2, Decision D) specifically because it
represents an immutable raw fact — it can't be mutated in place once
resolution happens, and monkeying around that with `dataclasses.replace()`
would blur the same raw-fact/judgment-call boundary Phase 2 deliberately
drew between "a call to a name called `save` was found" and "that name
most likely refers to chunk X." Keeping `ResolvedEdge` as a distinct type
means the original `Edge` rows in LanceDB are never reinterpreted or
touched — Phase 3 only ever reads them and produces new, separate objects
in memory.

### Decision H — Loading strategy from LanceDB
**Chosen: one unfiltered `table.search().to_list()` per table**
(`Storage.all_chunks()`, `Storage.all_edges()`), loading everything into
memory in one shot — no pagination or batching.
**Why:** this project's own architecture decision already commits to
rebuilding the entire graph in memory on every run, for a single project's
worth of code (thousands of rows at most, not millions). Pagination would
be solving a scale problem this project doesn't have, at the cost of real
code complexity today. Matches the existing per-file read pattern in
`Storage` exactly (`table.search().where(...)`), just without the
`.where()` filter.

### Other Phase 3 notes
- `build_graph(chunks, edges)` takes plain lists and does no I/O itself —
  `build_graph_from_storage(storage)` is the thin wrapper that does the
  actual LanceDB reads and hands off to it. This split means the graph
  construction logic (the part actually worth testing carefully) can be
  unit-tested with hand-built `Chunk`/`Edge` lists and zero database setup.
- Import resolution (`_guess_file_for_module`) only ever matches names
  back to files that were actually parsed into the `chunks` table — a call
  to a stdlib or third-party function (`os.path.join`, `requests.get`)
  correctly ends up `unresolved`, since there's no project chunk for it to
  point to. This is expected, not a gap: those calls aren't part of "what
  in *our* codebase depends on what."
- Verified by hand against the sample project's `if __name__ ==
  "__main__": main()` case, an ambiguous two-`save()`-methods case, and a
  same-file recursive call — all three landed in the tier and
  candidate-count the design above predicts.



