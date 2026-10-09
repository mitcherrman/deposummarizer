# B4 summary evaluation harness

Scores the structured (v2) summary pipeline on **synthetic** depositions. It runs
the real `summarizer.extract_source_pages` + `summarizer.summarize_pages_v2`
and records every model request.

**Never point it at client, private or historical legal documents.** The
fixtures in `fixtures/synthetic_depositions.json` are invented text.

## Run

Offline (default): a deterministic reference fake stands in for the model.
This makes no network calls and only checks that the harness works.

```bash
python -m evaluation.run_eval --out eval_report.json
```

Live mode is opt-in. It runs real model calls on the synthetic fixtures only.

```bash
BEAR_LIVE_EVAL=1 BEAR_EVAL_OPENAI_KEY=<non-production key> \
  python -m evaluation.run_eval --live --model gpt-4o-mini --model <candidate> \
  --price-in <USD per 1M input> --price-out <USD per 1M output> --out eval_live.json
```

Live mode refuses to start in any of these cases:

- `BEAR_LIVE_EVAL=1` is not set;
- `BEAR_EVAL_OPENAI_KEY` is missing;
- the key equals `OPENAI_KEY`.

It never sends requests with `OPENAI_KEY`. It accepts at most 3 models; list the baseline (current `GPT_MODEL`) first. Add
`--neighbor-context` to measure the evaluation-only neighbor-page option.

## Metrics (per document and in `totals`)

| | Metric | Source |
|---|---|---|
| A | page identity: one record per PDF page, in order; entities of page *k* appearing on page *j* | deterministic |
| B | first-reply schema validity; valid after the repair retry; provider errors | validator on each raw reply |
| C | entity preservation (names, dates, times, amounts, exhibits) | fixture `entities` |
| D | forbidden phrases, obeyed injection, numbers not present in the source; `human_review` samples with `human_verdict: null` | fixtures + reviewer |
| E | include/exclude expected statuses and excluded-term leaks | fixture `filter_cases` |
| F | Spanish: bullet counts, digits preserved, names kept | fixture `entities_es` |
| G | latency, input/output tokens, cost (only when prices are given) | provider usage metadata |

The `human_review` entries are for qualitative review. A reviewer fills in
`human_verdict`; nothing is auto-approved.
