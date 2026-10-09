"""
Run the B4 summary evaluation.

Offline (default; deterministic fake model, no network):
    python -m evaluation.run_eval --out eval_report.json

Live (opt-in; real OpenAI calls on SYNTHETIC fixtures only):
    set BEAR_LIVE_EVAL=1 and BEAR_EVAL_OPENAI_KEY=<a non-production key>, then
    python -m evaluation.run_eval --live --model gpt-4o-mini [--model <candidate>]
        [--neighbor-context] [--price-in USD_PER_1M --price-out USD_PER_1M]

Live mode refuses to run without both variables, and refuses a key equal to
OPENAI_KEY (the application's key). It never reads OPENAI_KEY for requests.
"""
import argparse
import json
import os
import sys


def _setup_django():
    # placeholder key, cache sessions, no database or AWS
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.test_settings")
    import django
    django.setup()


class LiveEvalNotAllowed(RuntimeError):
    pass


def live_eval_key(environ=os.environ) -> str:
    """The evaluation key, or LiveEvalNotAllowed explaining why live mode is off."""
    if environ.get("BEAR_LIVE_EVAL") != "1":
        raise LiveEvalNotAllowed("live model comparison not run: BEAR_LIVE_EVAL=1 is not set")
    key = (environ.get("BEAR_EVAL_OPENAI_KEY") or "").strip()
    if not key:
        raise LiveEvalNotAllowed("live model comparison not run: BEAR_EVAL_OPENAI_KEY is not set")
    if key == (environ.get("OPENAI_KEY") or "").strip():
        raise LiveEvalNotAllowed("refusing: BEAR_EVAL_OPENAI_KEY equals OPENAI_KEY; use a separate non-production key")
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="call real models (opt-in, see above)")
    parser.add_argument("--model", action="append", default=[], help="model to evaluate (live; repeatable, max 3)")
    parser.add_argument("--neighbor-context", action="store_true", help="enable SUMMARY_NEIGHBOR_CONTEXT for this run")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens (live cost estimate)")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens (live cost estimate)")
    parser.add_argument("--out", help="write the JSON report here (default: stdout)")
    args = parser.parse_args(argv)

    if args.live:
        try:
            key = live_eval_key()
        except LiveEvalNotAllowed as exc:
            print(exc, file=sys.stderr)
            return 2
        if not args.model or len(args.model) > 3:
            print("live mode needs 1–3 --model values (baseline first)", file=sys.stderr)
            return 2
    _setup_django()

    from django.test import override_settings
    from evaluation import harness
    from server.summary import ai_clients

    runs = []
    with override_settings(SUMMARY_NEIGHBOR_CONTEXT=args.neighbor_context):
        if args.live:
            for model in args.model:
                summary, translation = ai_clients.evaluation_clients(model, key)
                report = harness.evaluate(summary, translation, price_in=args.price_in, price_out=args.price_out)
                runs.append({"model": model, "temperature": ai_clients.temperature_for(model),
                             "structured_output": ai_clients.structured_output_mode(model), **report})
            status = "live comparison run"
        else:
            report = harness.evaluate(harness.ReferenceFakeSummaryModel(), harness.ReferenceFakeTranslator())
            runs.append({"model": "reference-fake (offline)", **report})
            status = "live model comparison not run"

    output = {"live_eval": status, "neighbor_context": args.neighbor_context, "runs": runs}
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    for run in runs:
        print(f"{run['model']}: {json.dumps(run['totals'])}", file=sys.stderr)
    print(status, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
