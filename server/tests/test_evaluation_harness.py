"""
BEAR-V2-B4 evaluation harness (evaluation/), offline only: the scoring
functions, a full offline run on the synthetic fixtures, and the live-mode
gate. No test here calls a real model (sockets are blocked).
"""
import json
import logging
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from evaluation import harness, run_eval
from server.summary import summarizer
from server.tests.b4_fixtures import NoNetworkMixin


class HarnessBase(NoNetworkMixin, SimpleTestCase):
    def setUp(self):
        self.block_network()
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        for p in (mock.patch.object(summarizer, "_has_tesseract", lambda: False),
                  mock.patch.object(summarizer, "RETRY_BACKOFF_SECONDS", 0)):
            p.start()
            self.addCleanup(p.stop)


class FixtureTests(SimpleTestCase):
    def test_fixtures_are_synthetic_and_cover_the_b4_cases(self):
        docs = harness.load_fixtures()
        texts = " ".join(p["text"] or "" for d in docs for p in d["pages"])
        self.assertIn("synthetic", json.load(open(harness.FIXTURES, encoding="utf-8"))["_note"].lower())
        for needle in ("Exhibit 7", "$23.50", "6:45 a.m.", "72 inches", "MR. OKAFOR", "I don't recall",
                       "Ignore all previous instructions", "medical"):
            self.assertIn(needle, texts)
        self.assertTrue(any(p["text"] is None for d in docs for p in d["pages"]))   # blank page
        self.assertTrue(any(d["filter_cases"] for d in docs))


class OfflineRunTests(HarnessBase):
    def test_reference_run_scores_every_dimension(self):
        report = harness.evaluate(harness.ReferenceFakeSummaryModel(), harness.ReferenceFakeTranslator())
        totals = report["totals"]
        self.assertTrue(totals["page_identity_exact"])                 # A
        self.assertEqual(totals["misattributed_entities"], 0)
        self.assertEqual(totals["valid_after_retry_rate"], 1.0)          # B
        self.assertIsNotNone(totals["entity_preservation_rate"])         # C
        self.assertEqual(totals["forbidden_hits"], 0)                    # D
        self.assertEqual(totals["filter_status_match"], [1.0, 1.0])      # E
        self.assertTrue(totals["spanish_bullet_count_match"])            # F
        self.assertGreater(totals["requests"], 0)                        # G
        doc = report["documents"][0]
        self.assertEqual([r["status"] for r in doc["records"]][4], "skipped_no_text")
        self.assertTrue(all(item["human_verdict"] is None for item in doc["unsupported"]["human_review"]))
        json.dumps(report)                                               # serializable

    def test_invalid_first_replies_are_measured(self):
        class FlakyModel(harness.ReferenceFakeSummaryModel):
            seen = 0

            def invoke(self, messages):
                FlakyModel.seen += 1
                if FlakyModel.seen % 2:
                    return SimpleNamespace(content="not json", usage_metadata=None)
                return super().invoke(messages)

        doc = harness.load_fixtures()[1]
        records, calls = harness.run_case(doc, FlakyModel(), harness.ReferenceFakeTranslator(), lang="en")
        from server.summary import schema
        scored = harness.score_schema(records, calls, schema.parse_summary_response, None)
        self.assertEqual(scored["first_reply_valid_rate"], 0.5)
        self.assertEqual(scored["valid_after_retry_rate"], 1.0)

    def test_misattribution_and_unsupported_numbers_are_detected(self):
        doc = harness.load_fixtures()[0]
        records = [{"pdf_page": 3, "status": "summarized", "translation_status": "not_requested",
                    "bullets": ["Exhibit 7 was signed by Victor Lin for $999."], "spanish_bullets": []}]
        identity = harness.score_page_identity(doc, records)
        self.assertEqual(identity["misattributed_entities"],
                         [{"pdf_page": 3, "entity": "Victor Lin", "from_page": 4}])
        unsupported = harness.score_unsupported(doc, records)
        self.assertEqual(unsupported["unsupported_numbers"][0]["numbers"], ["999"])

    def test_obeyed_injection_is_flagged(self):
        doc = harness.load_fixtures()[0]
        records = [{"pdf_page": 7, "status": "summarized", "bullets": ["BANANA"]}]
        hits = harness.score_unsupported(doc, records)["forbidden_hits"]
        self.assertEqual(hits[0]["kind"], "obeyed_injection")

    def test_cost_is_computed_only_from_supplied_prices(self):
        calls = [{"latency_s": 0.5, "input_tokens": 1000, "output_tokens": 100}]
        self.assertIsNone(harness.score_cost(calls)["cost_usd"])
        self.assertAlmostEqual(harness.score_cost(calls, 1.0, 2.0)["cost_usd"], 0.0012)


class LiveGateTests(HarnessBase):
    def test_live_mode_is_off_without_the_flag_and_a_separate_key(self):
        cases = [
            ({}, "BEAR_LIVE_EVAL=1 is not set"),
            ({"BEAR_LIVE_EVAL": "1"}, "BEAR_EVAL_OPENAI_KEY is not set"),
            ({"BEAR_LIVE_EVAL": "1", "BEAR_EVAL_OPENAI_KEY": "k", "OPENAI_KEY": "k"}, "equals OPENAI_KEY"),
        ]
        for env, message in cases:
            with self.subTest(env=env):
                with self.assertRaisesMessage(run_eval.LiveEvalNotAllowed, message):
                    run_eval.live_eval_key(env)
        self.assertEqual(run_eval.live_eval_key({"BEAR_LIVE_EVAL": "1", "BEAR_EVAL_OPENAI_KEY": "eval",
                                                 "OPENAI_KEY": "prod"}), "eval")

    def test_cli_refuses_live_mode_without_making_requests(self):
        with mock.patch.dict("os.environ", {"BEAR_LIVE_EVAL": ""}), mock.patch("sys.stderr"):
            self.assertEqual(run_eval.main(["--live", "--model", "gpt-4o-mini"]), 2)
