# CodeGuard — Phase-Wise Implementation Plan

## What we are building

A self-hosted, Python-only code-intelligence tool built from scratch (no cloning
of any existing repo) that gives a team three things a shared codebase never
gets automatically:

1. **Dead code / orphan symbol detection** — find functions and classes that
   nothing in the codebase calls anymore, so they can be safely reviewed and
   removed.
2. **Change-impact analysis** — given an edit (a diff against git), tell the
   developer exactly which other parts of the codebase will break, before
   they merge.
3. **Scoped context retrieval** — given a plain-English description of a
   problem ("login isn't working"), return only the small, relevant slice of
   the codebase needed to understand and fix it, instead of requiring the
   whole repository to be read.

All three features are built on **one shared foundation**: a parser that
breaks Python files into functions/classes, and a hand-built graph that
records who calls or imports whom. The three features are different ways of
asking questions of that same foundation.

## Why we are building this

Once more than one person works on a codebase, nobody keeps the whole
structure in their head anymore. This creates three recurring, expensive
problems that currently have no automatic answer:

- Dead code piles up silently because nobody is tracking which functions are
  still actually used.
- A signature change can break code written by someone who isn't even in the
  room anymore, and this is usually discovered late — in testing, or in
  production.
- Handing an AI agent (or a new teammate) the entire codebase to fix one
  small problem is slow, expensive, and actually hurts accuracy once the
  codebase is large, because irrelevant context drowns out the relevant
  part.

All three problems come from the same root cause: nobody has a structural,
always-current map of "what depends on what." This project builds that map
once, by hand, and then uses it three different ways.

---

## Final Architecture Decision (confirmed)

- **Language target**: Python codebases only, for the entire project.
- **Parsing**: tree-sitter with the Python grammar. Used to turn a file into
  a syntax tree, from which we extract chunks (functions, classes, methods)
  and raw relationships (calls, imports).
- **Graph algorithms**: fully hand-rolled. We design and implement our own
  adjacency-list structures and our own forward/reverse traversal logic — no
  `networkx`, no external graph library. This is the part of the project
  meant to build real understanding of how call graphs work internally.
  LanceDB (below) only ever stores the raw facts on disk; it never performs
  any graph reasoning — that stays 100% our own code, rebuilt in memory on
  every run. This same "hand-roll it ourselves" convention was later reused
  for whole-repo orientation (Phase 12's PageRank-style ranker) — no
  external graph or ranking library was introduced at any point.
- **Persistence — LanceDB used as the single storage layer from the start**,
  not just for embeddings later. What started as two tables grew to four as
  later phases needed genuinely different shapes of data, never by
  repurposing an existing table for something it wasn't designed to hold:
  - `chunks` — one row per function/class/method: `chunk_id`, `file_path`,
    `symbol_name`, `kind`, `start_line`, `end_line`, `content`, `blob_hash`,
    and (from Phase 6 onward) `vector`.
  - `edges` — one row per relationship: `kind` (`calls` / `imports`),
    `file_path`, plus source/target fields — added in Phase 2.
  - `annotations` — one row per persisted note left on a symbol — added in
    Phase 12.
  - `repo_meta` — a single row recording which embedder/dimension the
    on-disk index was actually built with — added in Phase 12.
  Using one storage system for everything (instead of separate files or
  stores per feature) means one place to persist and inspect data, and it
  means each new need only ever costs "add a column" or "add a table," never
  a second storage system.
- **Staleness detection**: the `blob_hash` column on `chunks`, compared
  against a freshly computed git blob hash for each file on every run — no
  reliance on file modification timestamps. A mismatch means: delete that
  file's existing rows from both `chunks` and `edges`, re-parse, re-insert.
- **Vector storage** (only needed for the scoped-retrieval feature): the
  same LanceDB `chunks` table, with a `vector` column added once Phase 6
  introduces embeddings — run embedded/local, no server process required.
  Search itself is a **brute-force scan** over that column (not an ANN
  index) — a deliberate choice at this project's scale (a single project's
  functions, typically thousands of rows, not millions), documented directly
  in `storage/db.py`.
- **Embeddings**: a local `sentence-transformers` model
  (`all-MiniLM-L6-v2`), so the whole tool runs free and offline, with no API
  key required. Built behind a small swappable interface so a paid/API
  embedder could be added later without changing anything else.
- **Diffing for change-impact**: `gitpython`, used to pull a file's old
  content at a given commit/ref, re-parsed with the same tree-sitter chunker
  used at index time, so old and new versions are compared like-for-like.
- **Interface**: a CLI tool (built with `typer`), used to build and test
  every feature. As of Phase 12, this is the only interface that exists —
  an MCP server wrapper (originally planned as Phase 10) has not been
  started; see that phase's entry in the decision log for the current,
  accurate status.

---

## Phase 0 — Project Scaffolding

**Goal:** get a clean, runnable Python project skeleton in place before any
real logic is written.

**What & why:** set up the package structure, dependency management, and a
`.codeguard/` working directory convention (where the graph cache, embeddings
index, and any metadata will live per project). Getting this right early
avoids restructuring later once features exist. Also decide on one small
real-world sample Python project (or a few files you write yourself) to use
as a running test case throughout every phase — you need something concrete
to point the tool at from Phase 1 onward.

**Output of this phase:** an installable local package with no real features
yet, and a small sample codebase to test against.

---

## Phase 1 — The Parsing Layer (Chunking)

**Goal:** turn any given Python file into a list of meaningful code pieces —
functions, classes, and methods — using tree-sitter, with no logic beyond
"what is this piece of code and where does it live."

**What & why:** this is a structural problem, not a meaning problem, so a
fast syntactic parser is the right tool, not an LLM. Each chunk needs: a
name, its kind (function/class/method), the file it came from, its
start/end lines, its raw source text, and a stable, deterministic ID (so the
same function always maps to the same ID across re-runs — needed later for
caching and diffing).

**Why this matters for later phases:** every one of the three end features
depends on chunks being correctly and consistently identified. Getting this
wrong early would surface as confusing bugs much later (e.g. the impact
analyzer "losing track" of a function because its ID wasn't stable).

**Persistence, introduced in this phase:** create the LanceDB `chunks` table
(schema described in the architecture section above, no vector column yet)
and write each parsed chunk into it, including its git blob hash. This is
also the phase where the staleness check itself gets written: given a file
path, compute its current blob hash and compare it against what is stored,
to decide whether re-parsing is needed at all.

**Output of this phase:** a working function that takes a `.py` file path and
returns a clean list of chunks, verified by hand against the sample codebase,
persisted into LanceDB with working staleness detection on repeated runs.

---

## Phase 2 — Raw Relationship Extraction

**Goal:** while walking the same syntax tree from Phase 1, also extract the
raw relationships between chunks: which function calls which (by name, not
yet resolved to a specific chunk), and which module imports which names.

**What & why:** this step deliberately does **not** try to resolve ambiguity
yet (e.g. if there are two `save()` methods in the codebase, we don't decide
which one a given call refers to here). We just record the fact "this
function contains a call to a name called `save`". Keeping raw extraction and
resolution as two separate steps mirrors a real, sound engineering pattern:
capture facts cheaply and immediately, defer judgment calls to whenever they
are actually needed.

**Persistence, introduced in this phase:** create the LanceDB `edges` table
and write each raw edge into it as a plain row — no resolution, no vectors,
just the fact that a relationship of a given kind exists between two names.

**Output of this phase:** for any parsed file, a list of raw edges of two
kinds — "calls" and "imports" — each referencing chunks by name and file,
persisted into LanceDB alongside the chunks from Phase 1.

---

## Phase 3 — The Hand-Rolled Graph

**Goal:** build your own graph structure — this is the core learning phase of
the whole project.

**What & why:** load all the chunks and raw edges back out of the LanceDB
tables built in Phases 1–2 (a plain read, no vector search involved), and
assemble them **in memory** into two adjacency structures you design and own:

- a **forward map** (given a symbol, what does it call/depend on),
- a **reverse map** (given a symbol, what calls/depends on it).

This also requires solving **name resolution**: when an edge says "this
function calls something named `authenticate`," decide which actual chunk
that most likely refers to. A practical, honest approach is a
ranked-candidate strategy: prefer a match in the same file, then a match in
an explicitly imported module, then any exact name match elsewhere — and if
it's still ambiguous, keep multiple ranked candidates rather than silently
guessing one.

Implement traversal yourself: a function that walks the forward map outward
N steps from a starting symbol, and a function that walks the reverse map
outward N steps — these two traversal functions are what every later feature
will be built on top of.

**Output of this phase:** a graph you built and understand line-by-line,
rebuilt in memory from the persisted LanceDB rows on every run, with working
forward-traversal and reverse-traversal functions you can call and inspect
directly.

---

## Phase 4 — Feature One: Dead Code / Orphan Finder

**Goal:** use the reverse map from Phase 3 to find every symbol with zero
incoming edges from anywhere else in the codebase.

**What & why:** the core logic is simple once the graph exists — a symbol
with an empty reverse-adjacency list is a candidate for dead code. The real
engineering work in this phase is handling false positives correctly, since
naive orphan detection would incorrectly flag things that are actually used:

- entry points like `main()` or a `if __name__ == "__main__"` block,
- functions only ever called dynamically or via a framework (e.g. route
  handlers found by a decorator, not a direct call),
- anything defined in test files, or only called from test files.

This phase should explicitly decide and document a small set of exclusion
rules for these cases, since a dead-code tool that cries wolf on real code
will be ignored by any team using it.

**Output of this phase:** a report (plain text or structured) listing
orphaned symbols, grouped by file, with confidence noted where an exclusion
rule might apply.

---

## Phase 5 — Feature Two: Change-Impact Analyzer (Blast Radius, v1)

**Goal:** given a git ref, find every symbol whose signature changed, and show
the full set of callers (direct and indirect) that could be affected —
the "blast radius" — using the existing graph. This phase deliberately
stops at *"here's what touches this"*, not yet *"here's what will actually
error"* — that judgment layer is a follow-up phase once this foundation is
proven.

**What & why:** this phase has four steps, each reusing something already
built rather than inventing new machinery:

1. **Get changed files.** Use `gitpython` to diff the working tree against
   a given ref and get the list of changed `.py` files.
2. **Parse old vs new.** Pull each changed file's content at the old ref
   and parse it with the *same* Phase 1 chunker used everywhere else — via a
   new `Chunker.parse_source()` entry point that works on raw text instead
   of requiring a file on disk. Parse the current on-disk version the normal
   way (`parse_file`), so both sides go through identical logic and are
   truly comparable.
3. **Detect changed symbols.** Match old and new chunks by their stable
   `chunk_id`. For every function/method present in both, re-extract just
   its parameter-list text and compare old vs new. A symbol only counts as
   "changed" here if its *signature* text differs — a changed function
   body with the same signature is out of scope for this feature.
4. **Walk the reverse graph and report blast radius.** For every changed
   symbol, use the Phase 3 `Graph.walk_reverse()` to find every caller,
   transitively, with hop distance. Display this as a simple tree: the
   changed symbol, its old vs new signature, and every affected caller
   grouped by how many hops away it is.

**Explicitly deferred to a later phase:** deciding whether a specific
call site's arguments would actually fail against the new signature.
v1 answers "what could be affected," not "what will break" — the report
lists every caller in the blast radius without a verdict attached, so
nothing is silently hidden or falsely marked safe.

**Output of this phase:** running `codeguard impact <ref>` prints, for
every changed symbol: its old signature, its new signature, and a full
list of affected callers grouped by hop distance from the change.

---

## Phase 6 — Embedding and Vector Storage Setup

**Goal:** stand up the semantic-search half of the project, entirely separate
from the graph work so far, needed only for the third feature.

**What & why:** set up a local `sentence-transformers` embedder behind a
small interface (so it can be swapped later), and extend the existing
LanceDB `chunks` table (created back in Phase 1) with a `vector` column,
rather than creating a separate table or storage system. Populate it by
embedding each chunk's stored content. The model is loaded lazily, on first
real use, so commands that never touch embeddings (`scan`, `impact`) don't
pay the cost of importing `sentence_transformers` or loading model weights.

This phase also had to add the first real project-indexing pass
(`index_project`) ahead of the originally planned Phase 8 — Phase 6 can't
demonstrate "embed all chunks and query them" without something actually
walking the project and putting chunks into LanceDB in the first place.

**Output of this phase:** the ability to embed all chunks from the sample
codebase once, and run a semantic query against them, returning the closest
matching chunks by meaning.

---

## Phase 7 — Feature Three: Scoped Context Retrieval

**Goal:** given a plain-English problem description, return the smallest
relevant, purposeful bundle of code — not just "nearby" code.

**What & why:** this feature combines two signals that each cover the
other's blind spot: a semantic search (Phase 6) finds the entry point chunk
that best matches the query by *meaning*; a graph walk (Phase 3), outward
from that entry point, finds what it calls and what calls it. Neither
signal alone is enough — semantic search misses structurally-connected code
that doesn't share vocabulary with the query, and a graph walk has no way to
choose a starting point from a plain-English description on its own.

The first working version weighted these two signals using a fixed
classifier (is this a "bug fix" question or an "explore" question?) with a
lookup table of weights per bucket. This was later replaced — see Phase 11.

**Output of this phase:** given a query like "login isn't working," a small,
ranked list of files/functions with a one-line reason each for why they were
included, sized to comfortably fit in an LLM's context window without
unrelated noise.

---

## Phase 8 — Unified CLI

**Goal:** tie all three features together behind one consistent command-line
tool, so the project is usable end-to-end by a real person, not just as
separate test scripts.

**What & why:** commands such as `codeguard scan` (dead code report),
`codeguard impact <ref>` (change-impact report against a git ref), and
`codeguard find "<query>"` (scoped retrieval), each going through two shared
steps first: resolve the project root and open storage, then bring the index
up to date via Phase 6's `index_project` (cheap on an unchanged project,
since its own blob-hash check skips every unchanged file).

**Output of this phase:** a single installable CLI tool a developer can run
against any Python repo and get real, usable reports from all three
features.

---

## Phase 9 — Testing and Accuracy Review

**Goal:** deliberately stress-test each feature against tricky real-world
cases, not just the happy path.

**What & why:** dead code detection should be checked against decorated
routes and dynamically-invoked functions. Change-impact should be checked
against renamed parameters, reordered arguments, and keyword-only changes.
Scoped retrieval should be checked against vague queries and queries that
could plausibly match multiple unrelated areas of the codebase. This phase
is about building trust in the tool's judgment before considering it done,
since all three features are only useful if their output is trustworthy
enough to act on without double-checking by hand every time.

**Output of this phase:** a documented set of test cases and known
limitations for each feature.

> **Status note (accurate as of Phase 12):** this phase, as originally
> scoped, has not actually been executed — see the decision log's Phase 9
> entry for the honest current state and what Phase 12 did and did not do
> toward it.

---

## Phase 10 — MCP Server Wrapper

**Goal:** expose all three features as MCP tools, so any MCP-compatible AI
agent can call them directly.

**What & why:** by this point, each feature already exists as a plain,
tested Python function/CLI command. This phase should be close to
mechanical — write thin async wrapper functions around the existing logic,
register them as MCP tools with clear descriptions, and confirm an agent can
call `find_relevant_code`, `check_dead_code`, and `check_change_impact`
correctly. Deliberately doing this last, after the logic is trusted, avoids
debugging both the core logic and the protocol layer at the same time.

**Output of this phase:** a working MCP server exposing the three features,
usable by any agent, completing the full path from raw idea to an
agent-usable tool.

> **Status note (accurate as of Phase 12):** not started. No MCP code exists
> in the repository yet. The CLI (Phase 8) remains the only interface.

---

## Phase 11 — Unified Context Engine + Query-Driven Retrieval Weighting

*(Added retroactively to this plan — this phase was designed and built, but
never had a corresponding section written here at the time. Recorded now so
this document doesn't jump straight from Phase 10 to Phase 12 with a silent
gap. Full decisions are in the decision log.)*

**Goal:** give a caller (a person or an agent) a single entry point —
"I'm about to do X, what do I need to know?" — instead of requiring them to
already know which of the three underlying features (retrieval, impact,
dead-code) is the right one to call, and to reach for it explicitly.

**What & why:** a new `context` command always runs scoped retrieval (Phase
7), and additionally runs change-impact or dead-code checking only when the
caller supplies an explicit signal — a git ref to diff against, or a
specific symbol they're considering touching. This keeps routing
deterministic: no guessing what *kind* of question `task` represents from
its wording. Phase 7's original fixed intent classifier (bug-fix vs.
explore) was retired here in favor of computing each query's semantic/graph
trust weighting from signals measured in that query's own results —
removing the need for a hand-maintained bucket/keyword list that would have
needed a new entry every time a new kind of question showed up.

**Output of this phase:** `codeguard context "<task>"` returns one unified,
ranked bundle of evidence, correctly assembled from whichever underlying
engines actually had something to say for that particular request.

---

## Phase 12 — Practical Hardening: Token Budgets, Whole-Repo Orientation, Persisted Notes, and Index Integrity

**Goal:** close a purpose-alignment gap that had existed since Phase 7 —
`find` and Phase 11's `context` could return an unbounded amount of raw code
with no cap at all — add the one real capability gap the project's own
stated goals had flagged (no way to get oriented in an unfamiliar repo
without already having a specific query), and fix a handful of smaller
correctness/usability issues found by a close review of the whole codebase
against its own documented intentions.

**What & why:** six related, independently-motivated changes:

1. **Token budget.** A single shared `fit_to_budget()` helper, applied at
   the two places raw code content actually leaves the system (`find`'s
   return value, and `context`'s final bundle): every result up to a
   caller-specified `max_tokens` is kept in full; the first result that
   would overflow is truncated with a short marker instead of silently
   disappearing; everything after that is dropped, since results already
   arrive ranked best-first.
2. **Whole-repo orientation.** A new `codeguard map` command and an
   `orient` flag on `context`, answering "where do I even start in a
   codebase I've never seen" using a small hand-rolled PageRank-style pass
   over the existing Phase 3 graph — no new dependency, same "build the
   graph algorithm yourself" convention Phase 3 established.
3. **Persisted notes.** A `codeguard note` command and a new `annotations`
   table: a note left on a symbol now survives across sessions and shows up
   automatically the next time that symbol appears in a `find`/`context`
   result, closing the gap where an observation like "this is fragile" had
   nowhere to go.
4. **Index-integrity guard.** A new `repo_meta` table records which
   embedder/dimension the current index was actually built with, checked on
   every indexing run; a mismatch (e.g. someone changing
   `embedding_model_name` in `config.py` without a full re-index) now raises
   a clear error instead of silently mixing incompatible vector spaces and
   returning wrong search results with no visible symptom at all.
5. **Ignore rules.** The existing hardcoded skip-list gained `build`/`dist`,
   and an optional `.codeguardignore` file (one `fnmatch` glob per line,
   gitignore-flavored) lets a project exclude anything else without
   introducing a config system.
6. **CLI completeness.** `codeguard impact` gained a `--max-hops` flag
   (`Graph` already supported a hop limit; nothing surfaced it), and
   `codeguard callers`/`codeguard callees` expose Phase 3's own
   `graph.callers()`/`graph.callees()` lookups directly, previously only
   reachable indirectly through another command.

**Also found and fixed, not originally planned:** `retrieval/retriever.py`
had a stray trailing quotation mark on its last line that made the entire
module fail to import. Found via a full `py_compile` pass while validating
this phase's changes; fixed as a one-line correction.

**Output of this phase:** `codeguard find` and `codeguard context` both
respect an explicit `max_tokens` ceiling with no way to silently exceed it;
`codeguard map` gives a ranked, structural entry point into an unfamiliar
repo; notes left with `codeguard note` persist and resurface automatically;
a changed embedding model is caught immediately instead of silently
corrupting search; and every capability the graph already had is directly
reachable from the CLI. Verified with 30 new unit tests covering the
previously-untested pure functions (`parsing/ids.py`, `storage/hashing.py`,
`graph.py`'s BFS, `impact/differ.py`) plus the two new modules
(`token_budget.py`, `overview/ranker.py`), and a full end-to-end smoke test
(index → stale-skip → embedder-mismatch guard firing correctly →
graph/dead-code/retrieval/overview/annotations all correct → real CLI
subprocess calls for every new command).

---

## Optional Future Phase — Multi-Language Support

Not part of the initial build, but worth noting as the natural next step
once everything above works reliably for Python: add a second tree-sitter
grammar (e.g. JS/TS), and confirm the Phase 3 graph, Phase 4/5/7 features
all generalize without needing to be rewritten — since chunking is the only
language-specific part of the whole design, this should mostly be a matter
of writing a second chunker, not restructuring the project.