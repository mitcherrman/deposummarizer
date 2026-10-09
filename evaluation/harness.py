"""
BEAR-V2-B4 summary evaluation harness.

Runs the real v2 page pipeline (summarizer.extract_source_pages +
summarizer.summarize_pages_v2) over synthetic deposition PDFs and scores:

  A. page identity           (deterministic; must be 100%)
  B. schema validity         (first attempt and after the repair retry)
  C. entity preservation     (names, dates, numbers, amounts, exhibits)
  D. unsupported claims      (fixture assertions + unsupported numbers;
                              plus samples flagged for human review)
  E. filtering behavior      (include / exclude cases)
  F. Spanish structure       (bullet counts, numbers, names)
  G. latency / tokens / cost (recorded for every call; cost only when
                              per-token prices are supplied)

Fixtures are synthetic (evaluation/fixtures). Never point this at client or
private documents. The default "fake" mode uses a deterministic offline model
and makes no network call; live mode is in run_eval.py and is opt-in.

Requires Django settings to be configured (run_eval.py uses
server.test_settings: placeholder key, cache sessions, no database).
"""
import io
import json
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic_depositions.json"


def load_fixtures(path=FIXTURES) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["documents"]


def build_pdf(doc: dict) -> bytes:
    """One PDF page per fixture page (None = blank), text inside the extraction margins."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    pdf = fitz.open()
    for page in doc["pages"]:
        p = pdf.new_page(width=612, height=792)
        if page.get("text"):
            p.insert_textbox(fitz.Rect(90, 90, 520, 700), page["text"], fontsize=10)
    data = pdf.tobytes()
    pdf.close()
    return data


# ─────────────────── Call recording ──────────────────────────────────────
class RecordingClient:
    """Wraps a client; records latency, token usage, raw reply and errors per request."""

    def __init__(self, client, role):
        self.client, self.role, self.calls = client, role, []

    def invoke(self, messages):
        started = time.perf_counter()
        entry = {"role": self.role, "messages": messages}
        try:
            response = self.client.invoke(messages)
        except Exception as exc:
            entry.update(latency_s=time.perf_counter() - started, error=exc.__class__.__name__)
            self.calls.append(entry)
            raise
        usage = getattr(response, "usage_metadata", None) or {}
        entry.update(latency_s=time.perf_counter() - started,
                     raw=getattr(response, "content", ""),
                     input_tokens=usage.get("input_tokens"),
                     output_tokens=usage.get("output_tokens"))
        self.calls.append(entry)
        return response


# ─────────────────── Deterministic offline model ─────────────────────────
_ANSWER_RE = re.compile(r"^A\.\s*(.+)$", re.MULTILINE)


class ReferenceFakeSummaryModel:
    """
    Offline stand-in: bullets are the page's first answers ("A." lines),
    filtered by naive topic matching. It exists to exercise the harness, not
    to measure model quality.
    """

    def invoke(self, messages):
        payload = next(json.loads(m["content"]) for m in messages
                       if m["role"] == "user" and m["content"].lstrip().startswith("{"))
        text = payload["source_page_text"]
        mode, topics = payload["filter"]["mode"], [t.lower() for t in payload["filter"]["topics"]]
        hit = any(t in text.lower() for t in topics)
        answers = _ANSWER_RE.findall(text) or [" ".join(text.split())[:300]]
        if (mode == "include" and not hit) or (mode == "exclude" and hit):
            reply = {"status": "no_relevant_content", "bullets": [], "uncertain": False}
        else:
            reply = {"status": "summarized",
                     "bullets": [f"The witness answered: {a.strip()}" for a in answers[:3]],
                     "uncertain": False}
        return SimpleNamespace(content=json.dumps(reply), usage_metadata=None)


class ReferenceFakeTranslator:
    def invoke(self, messages):
        payload = json.loads(messages[-1]["content"])
        return SimpleNamespace(content=json.dumps({"bullets": [f"ES: {b}" for b in payload["bullets"]]}),
                               usage_metadata=None)


# ─────────────────── Running one case ────────────────────────────────────
def run_case(doc, summary_client, translation_client, lang="both", mode="none", topics=()):
    """
    Run the v2 pipeline on one synthetic document inside a throwaway session.
    Returns (records as dicts, recorded calls).
    """
    from importlib import import_module
    from django.conf import settings
    from server.summary import summarizer

    engine = import_module(settings.SESSION_ENGINE)
    job_id = uuid.uuid4().hex
    session = engine.SessionStore()
    session.update({"db_len": -1, "job_id": job_id})
    session.create()
    session.save()

    summary = RecordingClient(summary_client, "summary")
    translation = RecordingClient(translation_client, "translation")
    saved = summarizer.llm, summarizer.translator_llm
    summarizer.llm, summarizer.translator_llm = summary, translation
    try:
        pages = summarizer.extract_source_pages(io.BytesIO(build_pdf(doc)), session.session_key, job_id)
        records = summarizer.summarize_pages_v2(pages, session.session_key, lang, list(topics),
                                                mode == "exclude", job_id)
    finally:
        summarizer.llm, summarizer.translator_llm = saved
        session.delete()
    return [r.to_dict() for r in records], summary.calls + translation.calls


# ─────────────────── Scoring ─────────────────────────────────────────────
def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


def score_page_identity(doc, records) -> dict:
    expected = list(range(1, len(doc["pages"]) + 1))
    got = [r["pdf_page"] for r in records]
    misattributed = []
    for r in records:
        bullets = " ".join(r["bullets"])
        for other_no, other in enumerate(doc["pages"], start=1):
            if other_no == r["pdf_page"]:
                continue
            own_text = doc["pages"][r["pdf_page"] - 1].get("text") or ""
            for entity in other.get("entities", []):
                # an entity of another page that this page's own text doesn't contain
                if not _contains(own_text, entity) and _contains(bullets, entity):
                    misattributed.append({"pdf_page": r["pdf_page"], "entity": entity, "from_page": other_no})
    return {"pages_expected": len(expected), "records": len(got),
            "identity_exact": got == expected, "misattributed_entities": misattributed}


def score_schema(records, calls, validate_summary, validate_translation) -> dict:
    from server.summary.schema import SchemaError
    summary_calls = [c for c in calls if c["role"] == "summary" and "raw" in c]
    valid = 0
    for c in summary_calls:
        try:
            validate_summary(c["raw"])
            valid += 1
        except SchemaError:
            pass
    attempted = [r for r in records if r["status"] != "skipped_no_text"]
    failed = [r for r in attempted if r["status"] == "failed"]
    return {
        "summary_requests": len(summary_calls),
        "summary_replies_valid": valid,
        "first_reply_valid_rate": (valid / len(summary_calls)) if summary_calls else None,
        "pages_attempted": len(attempted),
        "pages_failed": len(failed),
        "valid_after_retry_rate": (1 - len(failed) / len(attempted)) if attempted else None,
        "provider_errors": sum(1 for c in calls if "error" in c),
        "translation_requests": sum(1 for c in calls if c["role"] == "translation"),
    }


def score_entities(doc, records, spanish=False) -> dict:
    found = expected = 0
    missing = []
    key = "entities_es" if spanish else "entities"
    field = "spanish_bullets" if spanish else "bullets"
    for r in records:
        if r["status"] != "summarized":
            continue
        page = doc["pages"][r["pdf_page"] - 1]
        text = " ".join(r[field])
        for entity in page.get(key, []):
            expected += 1
            if _contains(text, entity):
                found += 1
            else:
                missing.append({"pdf_page": r["pdf_page"], "entity": entity})
    return {"expected": expected, "found": found,
            "rate": (found / expected) if expected else None, "missing": missing}


_DIGITS = re.compile(r"\d+")


def score_unsupported(doc, records) -> dict:
    hits, numbers, review = [], [], []
    for r in records:
        if r["status"] != "summarized":
            continue
        page = doc["pages"][r["pdf_page"] - 1]
        source = page.get("text") or ""
        for b in r["bullets"]:
            for phrase in page.get("forbidden_phrases", []):
                if _contains(b, phrase):
                    hits.append({"pdf_page": r["pdf_page"], "phrase": phrase, "bullet": b})
            if _norm(b) in {_norm(x) for x in page.get("forbidden_bullets", [])}:
                hits.append({"pdf_page": r["pdf_page"], "phrase": b, "bullet": b, "kind": "obeyed_injection"})
            extra = sorted(set(_DIGITS.findall(b)) - set(_DIGITS.findall(source)))
            if extra:
                numbers.append({"pdf_page": r["pdf_page"], "numbers": extra, "bullet": b})
        review.append({"pdf_page": r["pdf_page"], "source_excerpt": source[:400],
                       "bullets": r["bullets"], "review_note": page.get("review_note"),
                       "human_verdict": None})
    return {"forbidden_hits": hits, "unsupported_numbers": numbers, "human_review": review}


def score_filter(case, records) -> dict:
    by_page = {r["pdf_page"]: r for r in records}
    checks, leaks = [], []
    for page, status in case.get("expected_status", {}).items():
        got = by_page[int(page)]["status"]
        checks.append({"pdf_page": int(page), "expected": status, "got": got, "ok": got == status})
    for page, terms in case.get("excluded_terms", {}).items():
        text = " ".join(by_page[int(page)]["bullets"])
        leaks.extend({"pdf_page": int(page), "term": t} for t in terms if _contains(text, t))
    ok = sum(c["ok"] for c in checks)
    return {"case": case["id"], "checks": checks, "excluded_term_leaks": leaks,
            "status_match_rate": (ok / len(checks)) if checks else None}


def score_spanish(doc, records) -> dict:
    pages = [r for r in records if r["status"] == "summarized"]
    translated = [r for r in pages if r["translation_status"] == "translated"]
    count_ok = sum(len(r["bullets"]) == len(r["spanish_bullets"]) for r in translated)
    numbers_ok = sum(
        all(set(_DIGITS.findall(en)) <= set(_DIGITS.findall(es))
            for en, es in zip(r["bullets"], r["spanish_bullets"]))
        for r in translated)
    return {"pages_summarized": len(pages), "pages_translated": len(translated),
            "translation_failed": sum(r["translation_status"] == "failed" for r in pages),
            "bullet_count_match": count_ok, "numbers_preserved": numbers_ok,
            "names": score_entities(doc, records, spanish=True)}


def score_cost(calls, price_in_per_m=None, price_out_per_m=None) -> dict:
    latencies = [c["latency_s"] for c in calls if "latency_s" in c]
    tin = sum(c.get("input_tokens") or 0 for c in calls)
    tout = sum(c.get("output_tokens") or 0 for c in calls)
    cost = None
    if price_in_per_m is not None and price_out_per_m is not None:
        cost = tin / 1e6 * price_in_per_m + tout / 1e6 * price_out_per_m
    return {"requests": len(calls), "latency_total_s": round(sum(latencies), 3),
            "latency_max_s": round(max(latencies), 3) if latencies else None,
            "input_tokens": tin, "output_tokens": tout, "cost_usd": cost}


def evaluate(summary_client, translation_client, documents=None, price_in=None, price_out=None) -> dict:
    """Score every fixture document (lang=both, no filter) and every filter case."""
    from server.summary import schema
    documents = documents if documents is not None else load_fixtures()
    report = {"documents": []}
    for doc in documents:
        records, calls = run_case(doc, summary_client, translation_client, lang="both")
        entry = {
            "id": doc["id"],
            "records": records,
            "page_identity": score_page_identity(doc, records),
            "schema": score_schema(records, calls, schema.parse_summary_response, None),
            "entities": score_entities(doc, records),
            "unsupported": score_unsupported(doc, records),
            "spanish": score_spanish(doc, records),
            "cost": score_cost(calls, price_in, price_out),
            "filters": [],
        }
        for case in doc.get("filter_cases", []):
            frecords, fcalls = run_case(doc, summary_client, translation_client, lang="en",
                                        mode=case["mode"], topics=case["topics"])
            result = score_filter(case, frecords)
            result["cost"] = score_cost(fcalls, price_in, price_out)
            entry["filters"].append(result)
        report["documents"].append(entry)
    report["totals"] = _totals(report["documents"])
    return report


def _totals(docs) -> dict:
    def rate(num, den):
        return (num / den) if den else None
    ent_f = sum(d["entities"]["found"] for d in docs)
    ent_e = sum(d["entities"]["expected"] for d in docs)
    attempted = sum(d["schema"]["pages_attempted"] for d in docs)
    failed = sum(d["schema"]["pages_failed"] for d in docs)
    return {
        "page_identity_exact": all(d["page_identity"]["identity_exact"] for d in docs),
        "misattributed_entities": sum(len(d["page_identity"]["misattributed_entities"]) for d in docs),
        "valid_after_retry_rate": rate(attempted - failed, attempted),
        "entity_preservation_rate": rate(ent_f, ent_e),
        "forbidden_hits": sum(len(d["unsupported"]["forbidden_hits"]) for d in docs),
        "unsupported_numbers": sum(len(d["unsupported"]["unsupported_numbers"]) for d in docs),
        "filter_status_match": [f["status_match_rate"] for d in docs for f in d["filters"]],
        "spanish_bullet_count_match": all(
            d["spanish"]["bullet_count_match"] == d["spanish"]["pages_translated"] for d in docs),
        "requests": sum(d["cost"]["requests"] + sum(f["cost"]["requests"] for f in d["filters"]) for d in docs),
    }
