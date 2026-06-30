# Provenance Guard — Planning

## 1. Overview

Provenance Guard is a Flask backend that a creative-sharing platform calls when a piece of text is submitted, so it can show readers a transparency label about whether the content looks AI-generated, human-written, or uncertain. It also lets a creator appeal a label they believe is wrong, and it keeps a structured audit trail of every decision and appeal.

This document is the spec used to prompt AI coding tools during Milestones 3–5. Every
section below is written to be concrete enough to hand directly to a code-generation prompt.

## 2. Architecture

### 2.1 Submission flow

```
            raw text + creator_id
                   |
                   v
        POST /submit  (Flask route, rate-limited)
                   |
                   v
   +---------------+----------------+
   |                                |
   v                                v
Signal 1: Groq LLM judge      Signal 2: Stylometric heuristics
(llama-3.3-70b-versatile)     (pure Python, no network call)
   |                                |
   | ai_likelihood (0-1)            | ai_likelihood (0-1)
   | + rationale string             | + per-feature breakdown
   v                                v
   +---------------+----------------+
                   |
                   v          combined_score = 0.5*signal1 + 0.5*signal2
        Confidence Scoring Module
                   |
                   v          combined_score (0-1) + threshold lookup
        Transparency Label Generator
                   |
                   v          label_text, label_variant, combined_score
          Audit Log Writer (SQLite)
                   |
                   v
        JSON response to caller  {submission_id, label, confidence_score,
                                   signals, status}
```

### 2.2 Appeal flow

```
   creator_id + submission_id + reasoning text
                   |
                   v
        POST /appeal  (Flask route)
                   |
                   v
   Look up submission_id in store -> exists? -> 404 if not
                   |
                   v
   Create appeal record (reasoning, timestamp, original decision snapshot)
                   |
                   v
   Update submission.status = "under_review"
                   |
                   v
   Audit Log Writer (SQLite)  -- appended, original decision row untouched
                   |
                   v
   JSON response  {appeal_id, status: "under_review"}
```

### 2.3 Narrative

A submission's raw text is sent to two independent signal functions — an LLM-judge call to
Groq and a pure-Python stylometric scorer — each of which returns its own 0–1 "AI-likelihood"
estimate. The confidence-scoring module averages the two into a single combined score, which
the label generator maps to one of three label variants via fixed thresholds; the result is
written to the audit log and returned to the caller in the same request. An appeal does not
re-run detection — it records the creator's reasoning, flips the submission's status to
`under_review`, and writes a second audit log entry that references the original decision so
a human reviewer can see both side by side.

## 3. Detection Signals

### Signal 1 — LLM judge (Groq, `llama-3.3-70b-versatile`)

- **What it measures:** Asks the model to act as a zero-shot judge of authorship, prompted
  to weigh things like idea originality, voice consistency, idiosyncrasy, and whether phrasing
  reads as templated/instruction-following.
- **Output shape:** Strict JSON: `{"ai_likelihood": float in [0,1], "rationale": str}`.
  Called with `temperature=0` to reduce run-to-run variance.
- **Why it differs human vs. AI:** General-purpose LLMs are trained on enormous amounts of
  both human and machine text and pick up on diffuse stylistic patterns (cliché phrasing,
  over-explanation, "balanced both sides" hedging) that are hard to enumerate as discrete
  rules but that an LLM can recognize holistically.
- **Blind spot:** It is a single LLM call judging another LLM's output, so it inherits the
  judge model's own biases — notably, LLM-based AI-text detectors are documented in the
  literature to score non-native-English writing and simple, formulaic prose as more
  "AI-like" than it is. It is also not perfectly deterministic even at `temperature=0`, and
  it gives no visibility into *why* beyond a self-reported rationale that itself could be a
  post-hoc rationalization rather than the model's real basis for the score.

### Signal 2 — Stylometric heuristics (pure Python)

Composite of three sub-features, each normalized to `[0,1]` ("AI-likeness"), then averaged:

| Sub-feature | What it measures | Computation sketch |
|---|---|---|
| Burstiness | Variance in sentence length. Human writing tends to mix short and long sentences; AI output is often more uniform. | `stdev(sentence_lengths)`, then `score = clamp(1 - stdev/8, 0, 1)` (8-word stdev ≈ typical human variance baseline) |
| Lexical repetition | Vocabulary diversity. Lower type–token ratio (more repeated word choices relative to text length) skews toward the "AI-like" end of our heuristic. | `ttr = unique_words / total_words`, then `score = clamp(1 - ttr, 0, 1)` |
| Discourse-marker density | Frequency of stock transitional phrases ("moreover", "in conclusion", "it is important to note", "overall", "additionally"). Instruction-tuned LLMs overuse these relative to typical creative writing. | `marker_count / total_words`, scaled and clamped to `[0,1]` |

`signal2_score = mean(burstiness_score, repetition_score, marker_score)`

- **Why it differs human vs. AI:** These are well-documented surface-level regularities that
  fall out of how LLMs are trained (next-token prediction smooths out the high-variance,
  idiosyncratic choices a human writer makes) — none of them require understanding meaning.
- **Blind spot:** All three are length-sensitive and English-specific, are trivially gameable
  by an AI prompted to "vary sentence length" or "write more casually," and conflate
  deliberate stylistic choices (refrains in poetry, repetition as a literary device, simple
  vocabulary in children's writing or by ESL authors) with machine generation. This signal
  captures *surface form only* — it has no access to whether the ideas themselves are
  original.

### Combining into one confidence score

```
combined_score = 0.5 * signal1_score + 0.5 * signal2_score
```

An even split is the deliberate starting point: we have no labeled validation set yet to
justify weighting one signal more heavily than the other, and an even split is the most
defensible/explainable default. This weighting is a named candidate for recalibration if we
later test against labeled examples (see Section 11, AI Tool Plan, M4 verification).

## 4. Uncertainty Representation

`combined_score` is always interpreted as **"probability this content is AI-generated"**,
on a 0–1 scale, where 0 = confidently human, 1 = confidently AI, and 0.5 = the system has no
useful signal either way.

Calibration approach: both signals already produce values clamped to `[0,1]` before
averaging, so no additional rescaling is applied — the average of two `[0,1]` scores is
itself in `[0,1]` by construction. We treat distance from 0.5 as the "confidence" dimension
and the position relative to 0.5 as the "direction" dimension; both are read off the same
single number rather than tracked as two separate values, which is what keeps the score
simple enough to explain to a non-technical reader.

Thresholds (symmetric around 0.5, each band is 0.30 wide on the human/AI ends, 0.40 wide in
the uncertain middle):

| `combined_score` range | Label variant |
|---|---|
| `>= 0.70` | High-confidence AI |
| `<= 0.30` | High-confidence human |
| `0.30 < score < 0.70` | Uncertain |

A score of 0.62 and a score of 0.95 both fall in different bands relative to this scale
(uncertain vs. high-confidence AI) — and even within the same band, the exact `combined_score`
is always returned alongside the label text so the percentage shown to a reader changes
continuously even when the label category doesn't.

## 5. Transparency Label Design

Exact text returned by the API and shown to a reader (`{score}` is the combined score
formatted as a percentage, e.g. `92%`):

| Variant | Condition | Exact label text |
|---|---|---|
| High-confidence AI | `combined_score >= 0.70` | `"This content is likely AI-generated ({score} confidence). Our detection system found strong indicators of machine authorship."` |
| High-confidence human | `combined_score <= 0.30` | `"This content is likely human-written ({score} confidence that it is AI-generated, i.e. {100-score}% likely human). Our detection system found few indicators of machine authorship."` |
| Uncertain | `0.30 < combined_score < 0.70` | `"We could not confidently determine whether this content is AI-generated or human-written ({score} confidence it is AI-generated). Treat this attribution with caution."` |

These are revisited after Milestone 2 once real Groq + stylometric outputs are available on
sample text (see Section 11, M4 verification) — wording may change but the three-variant structure
and the explicit numeric score in every variant will not.

## 6. Appeals Workflow

- **Who can submit:** The original creator who submitted the content (identified by the
  `creator_id` captured at submission time — no separate auth system in scope for this
  project).
- **What they provide:** `submission_id`, `creator_id` (must match the original submitter),
  and free-text `reasoning` explaining why they believe the classification is wrong.
- **What the system does on receipt:**
  1. Looks up `submission_id`; 404 if it doesn't exist.
  2. Creates an appeal record: `appeal_id`, `submission_id`, `reasoning`, `created_at`, a
     snapshot of the original decision (`label`, `confidence_score`, `signals`).
  3. Updates the submission's `status` field to `"under_review"` (the original label and
     score are *not* overwritten or recomputed — automated re-classification is out of
     scope).
  4. Writes an audit log entry of type `appeal` that references the original `submission_id`.
- **What a human reviewer sees** (`GET /appeals`): a list of appeal records, each showing the
  original submitted text, the original label/confidence/signals, the creator's reasoning,
  the appeal timestamp, and current status (`under_review` / resolved states are a stretch
  feature, not required).

## 7. API Surface

| Endpoint | Method | Request body | Response |
|---|---|---|---|
| `/submit` | POST | `{content: str, creator_id: str}` | `{submission_id, label, confidence_score, signals: {signal1, signal2}, status: "final"}` |
| `/appeal` | POST | `{submission_id: str, creator_id: str, reasoning: str}` | `{appeal_id, submission_id, status: "under_review"}` |
| `/appeals` | GET | — | `[{appeal_id, submission_id, original_label, original_score, reasoning, created_at, status}, ...]` |
| `/submissions/<id>` | GET | — | full stored record for one submission (label, score, signals, status, appeal history) |
| `/log` | GET | — | structured audit log entries (see Section 9) |

`/submit` is the only endpoint that is rate-limited (see Section 8); the others are internal/review
surfaces and not expected to receive creator-facing traffic volume.

## 8. Rate Limiting (summary — full reasoning in README)

`/submit` will use Flask-Limiter, scoped per `creator_id` (falls back to per-IP if missing).
Specific numeric limits and the reasoning behind them are documented in README once chosen
during implementation, alongside measured behavior.

## 9. Audit Log (summary — full sample in README)

Every `/submit` call writes one row capturing: timestamp, submission_id, creator_id,
signal1 output (score + rationale), signal2 output (score + sub-feature breakdown),
combined_score, label_variant, label_text. Every `/appeal` call writes one row capturing:
timestamp, appeal_id, submission_id, reasoning, snapshot of the referenced decision. Stored
in SQLite; `GET /log` returns it as JSON. README will include at least 3 real entries.

## 10. Anticipated Edge Cases

1. **Very short submissions** (e.g. a haiku or a tweet-length excerpt, under ~40 words).
   Sentence-length variance and type-token ratio are statistically meaningless on that little
   text — a single long word or one repeated line can swing `signal2_score` drastically. The
   system will under-report confidence here, but nothing currently *detects* "this text is too
   short to trust" — that's a known gap, not a handled case, and should bias toward "uncertain"
   rather than a confident wrong label once implemented.
2. **Formulaic poetic forms with deliberate repetition and simple vocabulary** (e.g. a
   villanelle or a pantoum, which by definition repeat whole lines, or a children's-style poem
   using intentionally plain words). Both the repetition sub-feature and the discourse-marker
   absence won't trigger, but the low lexical diversity and uniform short sentence length will
   push `signal2_score` toward "AI-like" purely because of the literary form, not because it's
   machine-written.
3. **Heavily human-edited AI drafts (or AI-assisted-then-rewritten human work).** Neither
   signal is designed to detect a hybrid — the LLM judge sees a single blended voice and the
   stylometric features average out over a mixed-origin text. The system will most likely (and
   correctly, in the "uncertain" sense) land in the uncertain band, but it cannot distinguish
   "ambiguous because mixed-origin" from "ambiguous because we just don't have a strong
   signal," which matters for how an appeal reviewer should interpret it.

## 11. AI Tool Plan

- **M3 — submission endpoint + first signal.**
  - *Provide:* Section 3 (Signal 1 description and output shape only) + Section 2.1 diagram + Section 7 (`/submit`
    row only).
  - *Ask for:* A Flask app skeleton with the `/submit` route wired to an in-memory store, and
    a standalone `get_llm_signal(text) -> {"ai_likelihood": float, "rationale": str}` function
    that calls Groq with a JSON-mode prompt.
  - *Verify:* Call `get_llm_signal` directly on 2–3 known texts (one obviously AI-generated,
    one obviously human) *before* wiring it into the route, and confirm the JSON parses and
    the score moves in the expected direction.

- **M4 — second signal + confidence scoring.**
  - *Provide:* Section 3 (full, both signals) + Section 4 (uncertainty representation) + Section 2.1 diagram.
  - *Ask for:* `get_stylometric_signal(text) -> {"ai_likelihood": float, "features": {...}}`
    implementing the three sub-features in Section 3, plus a `combine_scores(signal1, signal2)`
    function implementing the Section 4 formula and threshold lookup.
  - *Verify:* Run both signals end-to-end on a small set of clearly-AI and clearly-human
    sample texts and check that (a) `combined_score` is meaningfully higher for the AI samples
    than the human samples, and (b) the three label bands in Section 4 are all actually reachable
    with realistic inputs, not just mathematically possible.

- **M5 — production layer.**
  - *Provide:* Section 5 (label variants) + Section 6 (appeals workflow) + Section 2.2 diagram.
  - *Ask for:* The label-generation function implementing the exact Section 5 text, the `/appeal`
    route, the `/appeals` and `/log` read routes, and the SQLite audit-log writer described
    in Section 9.
  - *Verify:* Manually drive `combined_score` to a value in each of the three bands (e.g. by
    submitting crafted text or temporarily stubbing the signals) and confirm all three label
    variants render with the exact wording from Section 5; submit an appeal and confirm the
    submission's `status` flips to `under_review` and both the original decision row and the
    new appeal row are visible in `/log`.

## 12. Stretch Features

Not started. This section will be updated with a sub-plan (signals to add for ensemble
detection, certificate issuance flow, dashboard metrics, or second content modality) before
any stretch feature implementation begins, per the assignment's requirement to update
planning.md before starting each one.
