import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import appcore as core
import checker


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_checker, self.old_check = core.DATA, checker.DATA, core.check
        core.DATA = checker.DATA = Path(self.temp.name)
        core.check = checker.inspect
        core.seed()

    def tearDown(self):
        core.DATA, checker.DATA, core.check = self.old_data, self.old_checker, self.old_check
        self.temp.cleanup()

    def case(self, ident):
        with core.connect("runtime") as db:
            return core.get(db, "case", ident)

    def test_replacement_requires_exact_approval_and_dispatch(self):
        core.start("CR-101", "reference")
        core.step()
        case = self.case("CR-101")
        self.assertEqual(case["status"], "AWAITING_APPROVAL")
        self.assertEqual(core.state()["effects"], [])
        self.assertEqual(checker.inspect("CR-101")["status"], "FAIL")
        core.approve("CR-101", case["version"])
        core.step()
        self.assertEqual(self.case("CR-101")["status"], "RESOLVED")
        self.assertEqual(len(core.state()["shipments"]), 1)
        self.assertEqual(next(i["stock"] for i in core.state()["inventory"] if i["sku"] == "BLEND-01"), 2)

    def test_duplicate_payment_refund_exact_excess_not_entire_payment(self):
        core.start("CR-102", "reference")
        core.step()
        refund = core.state()["refunds"][0]
        self.assertEqual(refund["amount"], 299_900)
        self.assertEqual(self.case("CR-102")["status"], "RESOLVED")
        core.step()
        self.assertEqual(len(core.state()["refunds"]), 1)

    def test_changed_source_invalidates_old_approval(self):
        core.start("CR-101", "reference")
        core.step()
        version = self.case("CR-101")["version"]
        with core.connect("world") as db:
            inventory = core.get(db, "inventory", "BLEND-01")
            inventory["cost"] += 50_000
            inventory["version"] += 1
            core.put(db, "inventory", "BLEND-01", inventory)
        case = core.approve("CR-101", version)
        self.assertEqual(case["status"], "QUEUED")
        core.step()
        case = self.case("CR-101")
        self.assertEqual(case["version"], version + 1)
        self.assertEqual(case["plan"]["amount"], 610_000)
        self.assertEqual(core.state()["effects"], [])
        with self.assertRaises(ValueError):
            core.approve("CR-101", version)

    def test_locked_source_recheck_prevents_time_of_check_write_gap(self):
        core.start("CR-101", "reference")
        core.step()
        core.approve("CR-101", 1)
        case = self.case("CR-101")
        with core.connect("world") as db:
            inventory = core.get(db, "inventory", "BLEND-01")
            inventory["stock"] = 0
            inventory["version"] += 1
            core.put(db, "inventory", "BLEND-01", inventory)
        with self.assertRaises(ValueError):
            core.apply_effect(case)
        self.assertEqual(core.state()["shipments"], [])

    def test_actual_post_commit_crash_reconciles_without_second_write(self):
        core.start("CR-102", "reference")
        with core.connect("runtime") as db:
            core.put(db, "meta", "crash", {"armed": True})
        environment = dict(os.environ, CUSTOMER_RESOLUTION_DATA=str(core.DATA))
        result = subprocess.run([sys.executable, "-c", "import appcore; appcore.step()"], cwd=core.ROOT, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 67, result.stderr)
        self.assertEqual(self.case("CR-102")["status"], "DISPATCHING")
        self.assertEqual(len(core.state()["refunds"]), 1)
        core.step()
        self.assertEqual(self.case("CR-102")["status"], "RESOLVED")
        self.assertEqual(len(core.state()["refunds"]), 1)
        self.assertTrue(any("reconciled" in e["text"] for e in self.case("CR-102")["events"]))

    def test_checker_rejects_wrong_amount_duplicate_and_wrong_dispatch(self):
        core.start("CR-102", "reference")
        core.step()
        with core.connect("world") as db:
            refund = core.rows(db, "refund")[0]
            refund["amount"] += 1
            core.put(db, "refund", refund["reference"], refund)
        self.assertEqual(checker.inspect("CR-102")["status"], "FAIL")
        with core.connect("world") as db:
            refund["amount"] -= 1
            core.put(db, "refund", refund["reference"], refund)
            core.put(db, "refund", "duplicate", dict(refund, reference="duplicate"))
        self.assertEqual(checker.inspect("CR-102")["status"], "FAIL")
        core.start("CR-101", "reference")
        core.step()
        core.approve("CR-101", 1)
        core.step()
        with core.connect("world") as db:
            shipment = core.rows(db, "shipment")[0]
            shipment["status"] = "missing"
            core.put(db, "shipment", shipment["reference"], shipment)
        self.assertEqual(checker.inspect("CR-101")["status"], "FAIL")

    def test_complaint_instructions_cannot_authorize_conflicting_refund(self):
        core.start("CR-103", "reference")
        core.step()
        self.assertEqual(self.case("CR-103")["status"], "ESCALATED")
        self.assertEqual(core.state()["refunds"], [])
        self.assertEqual(checker.inspect("CR-103")["status"], "FAIL")
        with core.connect("world") as db:
            core.put(db, "handoff", "CR-103", {"case_id": "CR-103", "owner": "Support Lead", "accepted": True})
        self.assertEqual(checker.inspect("CR-103")["status"], "PASS")

    def test_reset_refuses_an_active_job_and_reseeds_idle_world(self):
        core.start("CR-101", "reference")
        with self.assertRaises(ValueError):
            core.reset()
        core.step()
        core.reset()
        value = core.state()
        self.assertEqual(value["cases"], [])
        self.assertEqual(len(value["complaints"]), 3)
        self.assertEqual(next(i["stock"] for i in value["inventory"] if i["sku"] == "BLEND-01"), 3)

    def test_missing_authoritative_source_makes_the_separate_check_fail_closed(self):
        core.start("CR-102", "reference")
        core.step()
        self.assertEqual(self.case("CR-102")["check"]["status"], "PASS")
        with core.connect("world") as db:
            db.execute("DELETE FROM records WHERE kind='order' AND id='V-2102'")
        with patch.dict(os.environ, {"CUSTOMER_RESOLUTION_DATA": str(core.DATA)}):
            result = self.old_check("CR-102")
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["unavailable"])

    def test_prior_refund_is_deducted_from_the_remaining_excess(self):
        with core.connect("world") as db:
            core.put(db, "refund", "PRIOR", {"reference": "PRIOR", "order_id": "V-2102", "case_id": "PRIOR-SUPPORT", "amount": 50_000})
        core.start("CR-102", "reference")
        core.step()
        case = self.case("CR-102")
        self.assertEqual(case["status"], "RESOLVED")
        self.assertEqual(case["receipt"]["amount"], 249_900)
        self.assertEqual(sum(r["amount"] for r in core.state()["refunds"]), 299_900)


    def test_failed_model_can_explicitly_retry_with_fresh_sources_and_no_fallback(self):
        core.start("CR-101", "claude")
        with patch.object(core, "claude_plan", side_effect=RuntimeError("Provider unavailable")):
            core.step()
        blocked = self.case("CR-101")
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(blocked["mode"], "claude")
        self.assertEqual(core.state()["effects"], [])
        with core.connect("world") as db:
            inventory = core.get(db, "inventory", "BLEND-01")
            inventory["cost"] += 50_000
            inventory["version"] += 1
            core.put(db, "inventory", "BLEND-01", inventory)
        retried = core.retry_plan("CR-101", "reference", blocked["version"])
        self.assertEqual(retried["version"], blocked["version"] + 1)
        self.assertNotIn("plan", retried)
        with self.assertRaises(ValueError):
            core.retry_plan("CR-101", "reference", blocked["version"])
        with patch.object(core, "claude_plan", side_effect=AssertionError("No hidden model call")):
            core.step()
        case = self.case("CR-101")
        self.assertEqual(case["mode"], "reference")
        self.assertEqual(case["status"], "AWAITING_APPROVAL")
        self.assertEqual(case["plan"]["amount"], 610_000)
        self.assertNotIn("approval", case)
        self.assertNotIn("error", case)
        self.assertEqual(core.state()["effects"], [])

    def test_failed_model_can_retry_live_without_weakening_policy(self):
        core.start("CR-102", "claude")
        with patch.object(core, "claude_plan", side_effect=RuntimeError("Provider unavailable")):
            core.step()
        core.retry_plan("CR-102", "claude", self.case("CR-102")["version"])
        with patch.object(core, "claude_plan", side_effect=lambda facts, allowed: (allowed, {"mode": "claude"})) as planner:
            core.step()
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(self.case("CR-102")["status"], "RESOLVED")
        self.assertEqual(len(core.state()["refunds"]), 1)
        self.assertEqual(core.state()["refunds"][0]["amount"], 299_900)

    def test_retry_refuses_executed_or_non_model_blocked_case(self):
        core.start("CR-102", "reference")
        core.step()
        with core.connect("runtime") as db:
            case = core.get(db, "case", "CR-102")
            case["status"] = "BLOCKED"
            core.put(db, "case", case["id"], case)
        with self.assertRaises(ValueError):
            core.retry_plan("CR-102", "reference", case["version"])
        with core.connect("runtime") as db:
            case["error_kind"] = "model_planning"
            case.pop("receipt")
            core.put(db, "case", case["id"], case)
        with self.assertRaisesRegex(ValueError, "Saved business effects"):
            core.retry_plan("CR-102", "claude", case["version"])
        self.assertEqual(len(core.state()["refunds"]), 1)


if __name__ == "__main__":
    unittest.main()
