#!/usr/bin/env python3
"""Item 4: the store.

    python3 -I store.py init [path]     create the SQLite schema (default run.sqlite beside this file); idempotent
    python3 -I store.py tables [path]   print the tables and their columns

Every table that holds a stage output carries that stage's identity, the fields the plan names:
  extraction  unit release, attempt number
  retrieval   unit release, extraction output id, retriever and index versions, hub revision, route parameters
  proposal    unit release, extraction output id, candidate hub entry and revision
  vote        the proposal's evidence, the quoted spans shown, the vote-stage parameters (confidence threshold)
  ruling      route, reason code, the exact packet shown by stored output ids
  decision    unit, candidate, route, reason code, level, card id, writer kind (card | human), revision, trace
Each identity is stored as its sorted-JSON hash (providers.identity) beside the fields, so a policy change
can derive expected invalidations from the identities alone (item 8).
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "run.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
  unit_release TEXT PRIMARY KEY, unit_id TEXT NOT NULL, catalog_sha256 TEXT NOT NULL, family TEXT NOT NULL,
  grp TEXT NOT NULL, kind TEXT NOT NULL, split TEXT NOT NULL, statement TEXT NOT NULL, in_scope INTEGER NOT NULL, scope_reason TEXT);
CREATE TABLE IF NOT EXISTS hub_entries (
  hub_entry TEXT NOT NULL, hub_revision INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (hub_entry, hub_revision));
CREATE TABLE IF NOT EXISTS extractions (
  output_id TEXT PRIMARY KEY, unit_release TEXT NOT NULL, attempt INTEGER NOT NULL, identity TEXT NOT NULL,
  activities TEXT NOT NULL, quotes TEXT NOT NULL, prompt_version TEXT NOT NULL, created TEXT NOT NULL,
  complete INTEGER, work_object INTEGER, provider TEXT, model TEXT,
  UNIQUE (unit_release, attempt));
CREATE TABLE IF NOT EXISTS retrievals (
  output_id TEXT PRIMARY KEY, unit_release TEXT NOT NULL, extraction_output_id TEXT NOT NULL, retriever TEXT NOT NULL,
  index_version TEXT NOT NULL, hub_revision INTEGER NOT NULL, route_params TEXT NOT NULL, identity TEXT NOT NULL,
  candidates TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (
  output_id TEXT PRIMARY KEY, unit_release TEXT NOT NULL, extraction_output_id TEXT NOT NULL, hub_entry TEXT NOT NULL,
  hub_revision INTEGER NOT NULL, identity TEXT NOT NULL, level TEXT NOT NULL, quotes TEXT NOT NULL,
  confidence REAL, prompt_version TEXT NOT NULL, created TEXT NOT NULL, conflict INTEGER, needs_hub INTEGER,
  provider TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS votes (
  output_id TEXT PRIMARY KEY, proposal_output_id TEXT NOT NULL, voter TEXT NOT NULL, evidence_identity TEXT NOT NULL,
  quoted_spans TEXT NOT NULL, vote_params TEXT NOT NULL, identity TEXT NOT NULL, vote TEXT NOT NULL,
  quotes_verbatim INTEGER, confidence REAL, prompt_version TEXT NOT NULL, revision TEXT NOT NULL, created TEXT NOT NULL,
  provider TEXT, unit_release TEXT, hub_entry TEXT, superseded_by TEXT);
CREATE TABLE IF NOT EXISTS rulings (
  output_id TEXT PRIMARY KEY, proposal_output_id TEXT NOT NULL, route TEXT NOT NULL, reason_code TEXT NOT NULL,
  packet_output_ids TEXT NOT NULL, identity TEXT NOT NULL, decision TEXT NOT NULL, card TEXT NOT NULL,
  revision TEXT NOT NULL, created TEXT NOT NULL, level TEXT, origin TEXT, unit_release TEXT, hub_entry TEXT, superseded_by TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, unit_release TEXT, hub_entry TEXT, route TEXT NOT NULL,
  reason_code TEXT NOT NULL, level TEXT, card_id TEXT NOT NULL, writer_kind TEXT NOT NULL, ruling_output_id TEXT,
  revision TEXT NOT NULL, trace TEXT NOT NULL, superseded_by TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sets (
  revision TEXT PRIMARY KEY, active INTEGER NOT NULL, sha256 TEXT NOT NULL, gates TEXT NOT NULL, created TEXT NOT NULL, spec TEXT);
CREATE TABLE IF NOT EXISTS frozen (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS council_rulings (
  packet_id TEXT NOT NULL, voice TEXT NOT NULL, packet_class TEXT NOT NULL, split TEXT NOT NULL, blind INTEGER NOT NULL,
  level TEXT NOT NULL, reason TEXT, session TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY (packet_id, voice, session));
CREATE TABLE IF NOT EXISTS review_queue (
  proposal_output_id TEXT PRIMARY KEY, reason TEXT NOT NULL, queued TEXT NOT NULL, resolved TEXT);
"""


def init(path=DEFAULT):
    con = sqlite3.connect(path)
    with con:
        con.executescript(SCHEMA)
    con.close()
    return path


def write_decisions(path, decisions):
    """Append decision rows (engine.run_case output) to the ledger; returns what was written, as read back."""
    con = sqlite3.connect(path)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with con:
        for d in decisions:
            con.execute("INSERT OR REPLACE INTO decisions (decision_id, unit_id, unit_release, hub_entry, route, reason_code, level, "
                        "card_id, writer_kind, ruling_output_id, revision, trace, superseded_by, created) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (d["decision_id"], d["unit_id"], d.get("unit_release"), d["candidate_id"], d["route"], d["reason"],
                         d["level"], d["card_id"], d["writer_kind"], d.get("ruling_output_id"), d["set_revision"],
                         json.dumps(d["trace"]), None, created))
    ids = [d["decision_id"] for d in decisions]
    rows = []
    for row in con.execute("SELECT decision_id, unit_id, hub_entry, route, reason_code, level, card_id, writer_kind, revision "
                           "FROM decisions WHERE decision_id IN (%s)" % ",".join("?" * len(ids)), ids):
        rows.append(dict(zip(("decision_id", "unit_id", "candidate_id", "route", "reason", "level", "card_id", "writer_kind",
                              "set_revision"), row)))
    con.close()
    return rows


def tables(path=DEFAULT):
    con = sqlite3.connect(path)
    out = {}
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        out[name] = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
    con.close()
    return out


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT
    if argv[:1] == ["init"]:
        print(f"schema ready: {init(path)}")
        return 0
    if argv[:1] == ["tables"]:
        for name, cols in tables(path).items():
            print(f"{name}: {', '.join(cols)}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
