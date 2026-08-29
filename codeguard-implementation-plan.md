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
  every run.
- **Persistence — LanceDB used as the single storage layer from the start**,
  not just for embeddings later. Two plain tables, created early:
  - `chunks` — one row per function/class/method: `chunk_id`, `file_path`,
    `symbol_name`, `kind`, `start_line`, `end_line`, `content`, `blob_hash`.
    No vector column yet — that gets added in Phase 6 once embeddings exist.
  - `edges` — one row per relationship: `src_symbol`, `dst_name`,
    `edge_kind` (`calls` / `imports`), `file_path`. No vector column at all,
    same pattern codebase-rag uses for its own `edges` table.
  Using one storage system for everything (instead of a separate JSON file
  for the graph and LanceDB only for vectors) means one place to persist and
  inspect data, and it means Phase 6 only has to *add a column* to an
  existing table rather than stand up a second storage system.
- **Staleness detection**: the `blob_hash` column on `chunks`, compared
  against a freshly computed git blob hash for each file on every run (same
  deterministic approach proven in codebase-rag) — no reliance on file
  modification timestamps. A mismatch means: delete that file's existing
  rows from both `chunks` and `edges`, re-parse, re-insert.
- **Vector storage** (only needed for the scoped-retrieval feature): the
  same LanceDB `chunks` table, with a `vector` column added once Phase 6
  introduces embeddings — run embedded/local, no server process required.
- **Embeddings**: a local `sentence-transformers` model
  (`all-MiniLM-L6-v2`), so the whole tool runs free and offline, with no API
  key required. Built behind a small swappable interface so a paid/API
  embedder could be added later without changing anything else.
- **Diffing for change-impact**: `gitpython`, used to pull a file's old
  content at a given commit/ref, re-parsed with the same tree-sitter chunker
  used at index time, so old and new versions are compared like-for-like.
- **Interface**: a CLI tool first (built with `typer`), used to build and
  test every feature independently. An MCP server wrapper is added only in
  the final phase, once the underlying logic is trusted — the MCP layer will
  be a thin pass-through to the same functions the CLI calls, not a
  reimplementation.

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

**What & why:** this is the same foundational step codebase-rag relies on,
and for the same reason — chunking is a structural problem, not a meaning
problem, so a fast syntactic parser is the right tool, not an LLM. Each chunk
needs: a name, its kind (function/class/method), the file it came from, its
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
tables built in Phases 1–2 (a plain read — `SELECT * FROM edges`, no vector
search involved), and assemble them **in memory** into two adjacency
structures you design and own:

- a **forward map** (given a symbol, what does it call/depend on),
- a **reverse map** (given a symbol, what calls/depends on it).

This also requires solving **name resolution**: when an edge says "this
function calls something named `authenticate`," decide which actual chunk
that most likely refers to. A practical, honest approach (proven to work in
codebase-rag) is a ranked-candidate strategy: prefer a match in the same
file, then a match in an explicitly imported module, then any exact name
match elsewhere — and if it's still ambiguous, keep multiple ranked
candidates rather than silently guessing one.

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

## Phase 5 — Feature Two: Change-Impact Analyzer

**Goal:** given a code change (a diff against a git ref), determine exactly
which callers would break, structurally, with no LLM involved.

**What & why:** this phase has three distinct steps:

1. **Get old vs new content.** Use `gitpython` to pull a file's content as it
   existed at a given ref, and compare it against the current working
   version, to know which files actually changed.
2. **Diff signatures structurally.** Re-parse both the old and new version of
   each changed file with the Phase 1 chunker, match up chunks by their
   stable ID, and compare old vs new parameter lists directly — was a
   parameter removed, renamed, reordered, or did the count change.
3. **Check real breakage, not just "this changed."** For every symbol whose
   signature changed, use the Phase 3 reverse map to find every caller, then
   look at each caller's actual call-site arguments (how many positional
   args, which keyword args) and decide whether that specific call would now
   fail against the new signature — this is what separates a genuinely
   useful tool from one that just says "5 callers exist, good luck."

**Output of this phase:** a report per changed symbol: old signature vs new
signature, and a clearly split list of "will break" callers vs "unaffected"
callers.

---

## Phase 6 — Embedding and Vector Storage Setup

**Goal:** stand up the semantic-search half of the project, entirely separate
from the graph work so far, needed only for the third feature.

**What & why:** set up a local `sentence-transformers` embedder behind a
small interface (so it can be swapped later), and extend the existing
LanceDB `chunks` table (created back in Phase 1) with a `vector` column,
rather than creating a separate table or storage system. Populate it by
embedding each chunk's stored content. This phase is intentionally scoped
narrowly — just "can I embed a chunk and get its nearest neighbors back" —
before it gets combined with the graph in the next phase.

**Output of this phase:** the ability to embed all chunks from the sample
codebase once, and run a semantic query against them, returning the closest
matching chunks by meaning.

---

## Phase 7 — Feature Three: Scoped Context Retrieval

**Goal:** given a plain-English problem description, return the smallest
relevant, purposeful bundle of code — not just "nearby" code.

**What & why:** this is the most nuanced feature, and should be built in two
passes:

- **Pass 1 (baseline):** embed the user's query, run a semantic search
  against the Phase 6 vector index to find the best-matching entry point
  chunk, then walk a fixed number of hops outward using the Phase 3 forward
  and reverse maps, similar in spirit to codebase-rag's hybrid search. Get
  this working end-to-end first.
- **Pass 2 (the actual improvement over existing tools):** instead of
  including everything within N hops indiscriminately, apply selection
  rules based on the type of request — for a bug-fix-style query, prioritize
  the entry point, its direct callers (who is affected), and its direct
  dependencies (likely root cause), while deliberately excluding
  distant/unrelated branches of the graph even if they're technically within
  the hop limit. The goal of this pass is a genuinely minimal, purposeful
  bundle, not just a wider net.

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
`codeguard find "<query>"` (scoped retrieval). This phase also wires in the
staleness/re-indexing logic properly for the first time — deciding when the
graph and vector index need to be rebuilt versus reused from cache, using
the git blob hash approach, so repeated runs on an unchanged codebase are
fast.

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

---

## Phase 10 — MCP Server Wrapper (final phase)

**Goal:** expose all three features as MCP tools, so any MCP-compatible AI
agent can call them directly, the same pattern seen in codebase-rag.

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

---

## Optional Future Phase — Multi-Language Support

Not part of the initial build, but worth noting as the natural next step
once everything above works reliably for Python: add a second tree-sitter
grammar (e.g. JS/TS), and confirm the Phase 3 graph, Phase 4/5/7 features
all generalize without needing to be rewritten — since chunking is the only
language-specific part of the whole design, this should mostly be a matter
of writing a second chunker, not restructuring the project.
