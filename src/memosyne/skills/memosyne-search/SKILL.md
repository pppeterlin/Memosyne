---
name: memosyne-search
description: >-
  Search strategy for retrieving memories from the Memosyne vault — query
  decomposition, walk strategy, top_k tuning, and reading the result shape.
  Use when the user asks a multi-part / cross-entity question, or when a
  first search returned thin results and you need to refine. For the simple
  "when should I search at all?" decision, see memosyne-invocation.
---

# The Divination — Memosyne Search Protocol

> 神話定位：The Oracle 不只回答問題，她**選擇怎麼聽**。Same question, different
> listening posture, different revealed memory.

This skill governs *how* to search once you've decided you should. Bad
search posture turns a rich vault into a thin one.

---

## 0. Result shape

`search_memory` returns a list of result dicts. Each dict has:

```
score, path, summary, snippet, title, type, date, source
```

`path` is the canonical memory identity — use it for citing, dedup,
follow-up reads, and replay comparison. Internal chunk ids are
implementation detail; never surface them.

---

## 1. Strategy by query shape

### Before you search: is this a search question at all?
- **Enumerations and ordering** — "where have I travelled?", "who do I see
  most?", "what happened before the move?" — are not top-k questions. Call
  `get_digest("entities")` or `get_digest("timeline", year=…)` and read the
  whole picture, then `read_file` the memories that matter.
- **Who the user is** — call `get_profile` (and `get_profile("aggregates")`
  if present) first.

### Scope
- By default `search_memory` covers life memories: journal, profile,
  projects, knowledge.
- Pass `include_chats=True` only when the question is about a past AI
  conversation; transcripts are large and mostly not about the user.

### Single-entity recall — "what did I say about X?"
- `top_k=5`. If returns are thin, retry with `top_k=15`.

### Cross-entity / multi-hop — "Tokyo trip with friend-A"
- First try the digest's entity index: it lists every memory that mentions
  each person and place, so the intersection is one read away.
- With the labs graph enabled (`MEMOSYNE_SEARCH_PROFILE=full`), the Tapestry
  carries these links: `top_k=10`, `--walk deep` (PPR), or `--walk fast`.
  `auto_route=True` adds Muse routing in that profile.

### Time-anchored — "what was I working on last March?"
- Include the time phrase in the query verbatim — the temporal parser
  reads it and re-ranks by time distance.
- `query_memory_at_time` for the deeper time-travel variant (filters out
  memories created after the timestamp).

### Identity / profile — "what do I think about X?"
- `get_profile(section="all")` first, then `search_memory` only if Profile
  doesn't already answer.

### Long-tail / vague — "anything about creativity?"
- `top_k=15`, no muse routing, scan summaries; don't dump snippets.

---

## 2. Walk strategy

| flag | what it does | when |
|---|---|---|
| `--walk deep` (default) | PPR spreading on the Tapestry — slow, deep | multi-hop, cross-entity |
| `--walk fast` | Two-pass walk: anchor → 1–2 hop neighbors | interactive, latency-sensitive |
| `--walk off` | Dense + BM25 only | A/B testing, eval baseline |

The two-pass walk trades recall for ~5× latency reduction on most queries.
Both options merge into the existing RRF + ACT-R pipeline; switching only
changes the graph-walk step.

---

## 3. Top-k tuning

Default `top_k=5` is for human consumption (you'll quote one or two).
Use larger top_k when:

- Filling agent context for a downstream operation (10–15).
- Diagnosing why a memory isn't surfacing (15+).
- Running golden eval (k matches the eval config).

Never `top_k=50+` — Memosyne is a vault, not a search index. Large k
flattens the score signal and makes the ACT-R rerank meaningless.

---

## 4. Reading scores

Cosine scores are post-RRF + post-rerank, not pure dense similarity.
Useful for relative ordering within a single result list; **not**
comparable across queries. Don't tell the user "this is a 0.7 confidence
match" — say "this is the strongest match" instead.

---

## 5. Honoring Chronicle privacy

Every search records an access into `chronicle.db` (ACT-R weights). For:

- **Background / diagnostic** queries: pass `--no-record-access`.
- **Real user-initiated** queries: let it record — the access log is how
  recency works.
- **Replay / eval**: must use `record_access=False` to avoid polluting.

---

## 6. When search returns nothing

The Spring is still — three possible causes:

1. **Wrong vocabulary** — try synonyms / English-Chinese flip.
2. **Memory genuinely absent** — propose ingest via `memosyne-ingest`.
3. **Index stale** — `memosyne health` will surface; fix with `rebuild`.

Don't fabricate memory to fill silence. The vault's silence is also data.
