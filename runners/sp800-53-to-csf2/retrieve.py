#!/usr/bin/env python3
"""Item 4: the retriever.

    python3 -I retrieve.py            embed the active CSF 2.0 subcategories through the adapter and write index.json
    python3 -I retrieve.py --replay   the same with every outbound call disabled: any attempt is counted and the
                                      runner exits 5 (the replay report goes to replay_report.json)
    python3 -I retrieve.py --query <unit id> [--replay]   embed one unit's statement and print its top-k by cosine

index.json holds the embedding model id, the vector dimension, k from protocol.json, the hub revision, the
sha256 of the vectors, and one row per subcategory. It is written only after protocol.sha256 exists, and
its embedding calls are the rows stage "embedding" in accounting.jsonl.

Exit codes: 0 ok; 4 budget refused; 5 outbound attempt in replay mode; 6 protocol.sha256 missing or not
matching protocol.json; 7 active subcategory count differs from sources.json.
"""
import hashlib
import importlib.util
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
INDEX = os.environ.get("GSTACK_INDEX") or os.path.join(HERE, "index.json")              # checks redirect artifacts
REPLAY_REPORT = os.environ.get("GSTACK_REPLAY_REPORT") or os.path.join(HERE, "replay_report.json")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def active_subcategories():
    d = read_json(os.path.join(DATA, "csf2_core_cprt.json"))["response"]["elements"]
    els = d["elements"]
    gone = {e["element_identifier"][3:] for e in els if e["element_type"] == "withdraw_reason"}
    return [{"id": e["element_identifier"], "text": e.get("text", "").strip()}
            for e in els if e["element_type"] == "subcategory" and e["element_identifier"] not in gone]


def unit_text(unit_id):
    cat = read_json(os.path.join(DATA, "sp800-53r5_catalog.json"))["catalog"]

    def prose(parts):
        out = []
        for p in parts or []:
            if p.get("prose"):
                out.append(p["prose"])
            out += prose(p.get("parts"))
        return out

    def walk(c):
        if c["id"] == unit_id:
            return " ".join(prose([p for p in c.get("parts", []) if p.get("name") == "statement"]))
        for e in c.get("controls", []):
            t = walk(e)
            if t is not None:
                return t
        return None

    for g in cat["groups"]:
        for c in g.get("controls", []):
            t = walk(c)
            if t is not None:
                return t
    raise KeyError(unit_id)


def protocol_checked():
    """Exit 6 unless protocol.sha256 exists and matches protocol.json byte for byte."""
    p, h = os.path.join(HERE, "protocol.json"), os.path.join(HERE, "protocol.sha256")
    if not (os.path.isfile(p) and os.path.isfile(h)):
        print("protocol.sha256 missing: freeze first", file=sys.stderr)
        sys.exit(6)
    with open(p, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(h, encoding="utf-8") as f:
        pinned = f.read().strip()
    if digest != pinned:
        print(f"protocol.json hash {digest} does not match protocol.sha256 {pinned}", file=sys.stderr)
        sys.exit(6)
    return read_json(p)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def build(adapter, protocol, sources):
    subs = active_subcategories()
    expected = sources["counts"]["csf2"]["subcategories"]
    if len(subs) != expected:
        print(f"active subcategory count {len(subs)} differs from sources.json {expected}", file=sys.stderr)
        sys.exit(7)
    model = protocol["roles"]["embedding"]["model"]
    rows = []
    for s in subs:
        q = providers.identity("retrieval", hub_entry=s["id"], hub_revision=1, retriever="embed-cosine-v1", model=model)
        rows.append({"id": s["id"], "vector": adapter.embed(model, s["text"], q)})
    vec_hash = hashlib.sha256(json.dumps([r["vector"] for r in rows]).encode()).hexdigest()
    index = {"model": model, "dimension": len(rows[0]["vector"]), "k": protocol["budget"]["k"], "hub_revision": 1,
             "retriever": "embed-cosine-v1", "count": len(rows), "vectors_sha256": vec_hash,
             "protocol_sha256": open(os.path.join(HERE, "protocol.sha256")).read().strip(), "rows": rows}
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f)
    print(f"{os.path.basename(INDEX)}: {len(rows)} subcategories, dimension {index['dimension']}, k {index['k']}, vectors {vec_hash[:16]}")


def query(adapter, protocol, unit_id):
    index = read_json(INDEX)
    text = unit_text(unit_id)
    q = providers.identity("retrieval", unit_release=f"{unit_id}@{protocol['sources']['sp800-53r5_catalog.json'][:12]}",
                           retriever=index["retriever"], index=index["vectors_sha256"], hub_revision=index["hub_revision"], k=index["k"])
    v = adapter.embed(index["model"], text, q)
    scored = sorted(((cosine(v, r["vector"]), r["id"]) for r in index["rows"]), reverse=True)[: index["k"]]
    for score, sid in scored:
        print(f"{sid}\t{score:.4f}")
    return scored


providers = load("providers")


def main(argv):
    replay = "--replay" in argv
    protocol = protocol_checked()
    sources = read_json(os.path.join(HERE, "sources.json"))
    adapter = providers.Adapter(protocol, replay=replay)

    def body():
        if "--query" in argv:
            query(adapter, protocol, argv[argv.index("--query") + 1])
        else:
            build(adapter, protocol, sources)

    rc = providers.run_pass(body)
    if replay:
        with open(REPLAY_REPORT, "w", encoding="utf-8") as f:
            json.dump({"outbound_attempts": adapter.replay_counter.get("attempts", 0), "exit": rc}, f)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
