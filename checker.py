"""Separate process: read-only source records, separate verdict database."""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DATA = Path(os.environ.get("CUSTOMER_RESOLUTION_DATA", str(Path(__file__).resolve().parent / "data")))


def read(name):
    db = sqlite3.connect((DATA / (name + ".db")).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    result = {}
    for kind, ident, value in db.execute("SELECT kind,id,data FROM records"):
        result.setdefault(kind, {})[ident] = json.loads(value)
    db.close()
    return result


def inspect(case_id):
    runtime, world = read("runtime"), read("world")
    case = runtime.get("case", {}).get(case_id)
    if not case:
        raise ValueError("No runtime case")
    complaint = world["complaint"][case_id]
    order = world["order"][complaint["order_id"]]
    ident = order["id"]
    errors, witnesses = [], []
    action = case["plan"]["action"]
    effects = [e for e in world.get("effect", {}).values() if e["case_id"] == case_id]
    if action == "escalate":
        accepted = [h for h in world.get("handoff", {}).values() if h["case_id"] == case_id and h.get("owner") and h.get("accepted") is True]
        if len(accepted) != 1:
            errors.append("Conflicting evidence needs one named, accepted handoff")
        witnesses.extend(accepted)
    else:
        if len(effects) != 1:
            errors.append("Expected exactly one saved resolution effect")
        elif effects[0].get("amount") != case["plan"]["amount"] or effects[0].get("order_id") != ident or effects[0].get("reference") != case.get("reference") or effects[0].get("action") != action:
            errors.append("Saved effect does not match the exact resolution intent")
        if action == "refund":
            payment = world["payment"][ident]
            prior = sum(r["amount"] for r in world.get("refund", {}).values() if r["order_id"] == ident and r["case_id"] != case_id)
            expected = payment["captured"] - payment["authorized"] - prior if complaint["category"] == "duplicate_payment" else order["amount"]
            refunds = [r for r in world.get("refund", {}).values() if r["case_id"] == case_id]
            if len(refunds) != 1 or sum(r["amount"] for r in refunds) != expected:
                errors.append("Refund count or exact amount differs from verified payment entitlement")
            if any(r["order_id"] != ident for r in refunds):
                errors.append("Refund is linked to the wrong order")
            if complaint["category"] not in {"duplicate_payment", "damage"} or (complaint["category"] == "damage" and world["delivery"][ident]["status"] != "damaged"):
                errors.append("Source evidence does not establish refund entitlement")
            if expected <= 0:
                errors.append("No unrefunded money entitlement remains in the payment ledger")
            witnesses.extend(refunds)
        elif action == "replacement":
            shipments = [s for s in world.get("shipment", {}).values() if s["case_id"] == case_id]
            if len(shipments) != 1 or any(s.get("order_id") != ident or s.get("sku") != order["sku"] or s.get("qty") != order["qty"] or s.get("status") != "dispatched" or s.get("amount") != case["plan"]["amount"] or not s.get("tracking") for s in shipments):
                errors.append("Replacement lacks one exact dispatched shipment and tracking record")
            if complaint["category"] != "damage" or world["delivery"][ident]["status"] != "damaged" or complaint.get("preference") != "replacement":
                errors.append("Source evidence does not establish replacement entitlement")
            witnesses.extend(shipments)
        else:
            errors.append("Unsupported resolution action")
        if case["plan"]["amount"] > 500_000:
            approval = case.get("approval", {})
            if approval.get("amount") != case["plan"]["amount"] or approval.get("version") != case["version"] or approval.get("premise") != case.get("premise") or approval.get("action") != action:
                errors.append("Resolution has no matching exact approval")
        tickets = [t for t in world.get("ticket", {}).values() if t["case_id"] == case_id]
        if len(tickets) != 1 or tickets[0].get("status") != "resolved" or tickets[0].get("order_id") != ident:
            errors.append("Support application has no matching resolved customer notice")
        witnesses.extend(tickets)
    verdict = {"case_id": case_id, "status": "FAIL" if errors else "PASS", "errors": errors, "witnesses": witnesses, "at": time.time(),
               "separation": "Independent process and read-only source connections; same OS account"}
    db = sqlite3.connect(DATA / "checker.db")
    db.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))")
    db.execute("INSERT INTO records VALUES('verdict',?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data", (case_id, json.dumps(verdict)))
    db.commit()
    db.close()
    return verdict


if __name__ == "__main__":
    print(json.dumps(inspect(sys.argv[1])))
