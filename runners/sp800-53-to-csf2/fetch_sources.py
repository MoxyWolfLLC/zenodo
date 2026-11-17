#!/usr/bin/env python3
"""Item 1: pin the sources.

Fetches SP 800-53 Rev. 5 (OSCAL JSON), the CSF 2.0 core (CPRT JSON export, with its
family-level external references to SP 800-53) and the CSF 2.0 informative-references
workbook (control-level references), hashes each, counts what the pinned catalog holds,
and tags every reference entry by the semantics its source supports.

    python3 -I fetch_sources.py            fetch, hash, count, tag; writes data/ and the three JSON files
    python3 -I fetch_sources.py --check    re-hash data/ against sources.json; exit 2 on a mismatch

Exit codes: 0 ok; 2 a fetched or re-read file's hash differs from the pinned one; 3 a fetch failed.
Stdout carries one line per file (name, bytes, SHA-256) and the counts. Stderr carries failures.
"""
import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile
from datetime import date, timezone, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SOURCES = {
    "sp800-53r5_catalog.json": {
        "url": "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
        "what": "SP 800-53 Rev. 5 catalog, OSCAL JSON, usnistgov/oscal-content main",
    },
    "csf2_core_cprt.json": {
        "url": "https://csrc.nist.gov/extensions/nudp/services/json/nudp/framework/version/csf_2_0_0/export/json?element=all",
        "what": "CSF 2.0 core, CPRT JSON export: functions, categories, subcategories, implementation examples, and external_reference links to SP 800-53 families",
    },
    "csf2_informative_references.xlsx": {
        "url": "https://csrc.nist.gov/extensions/nudp/services/json/csf/download?olirids=all",
        "what": "CSF 2.0 Reference Tool workbook: control-level informative references per subcategory (SP 800-53 Rev 5.1.1 and 5.2.0 among others)",
    },
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, url):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    req = urllib.request.Request(url, headers={"User-Agent": "sp800-53-to-csf2 fetch_sources.py"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def count_catalog(path):
    """Active base controls and enhancements from the pinned OSCAL catalog, never typed in."""
    cat = json.load(open(path, encoding="utf-8"))["catalog"]
    base, enh, withdrawn = 0, 0, 0

    def withdrawn_ctl(c):
        return any(p.get("name") == "status" and p.get("value") == "withdrawn" for p in c.get("props", []))

    def walk(controls):
        nonlocal base, enh, withdrawn
        for c in controls:
            if withdrawn_ctl(c):
                withdrawn += 1
            elif "." in c["id"]:
                enh += 1
            else:
                base += 1
            walk(c.get("controls", []))

    for g in cat.get("groups", []):
        walk(g.get("controls", []))
    return {"title": cat["metadata"]["title"], "version": cat["metadata"]["version"],
            "last_modified": cat["metadata"]["last-modified"], "active_base_controls": base,
            "active_enhancements": enh, "withdrawn": withdrawn}


def csf_core(path):
    d = json.load(open(path, encoding="utf-8"))["response"]["elements"]
    els, rels = d["elements"], d["relationships"]
    # A withdrawn CSF 1.1 element keeps its row in the 2.0 export beside a "WR-<id>" withdraw_reason element
    # (and a moved_to or incorporated_into relationship from that WR element). Active = no withdraw reason.
    gone = {e["element_identifier"][3:] for e in els if e["element_type"] == "withdraw_reason"}
    subs = [e for e in els if e["element_type"] == "subcategory" and e["element_identifier"] not in gone]
    funcs = [e for e in els if e["element_type"] == "function" and e["element_identifier"] not in gone]
    cats = [e for e in els if e["element_type"] == "category" and e["element_identifier"] not in gone]
    ext = [r for r in rels if r["relationship_identifier"] == "external_reference"]
    return subs, funcs, cats, ext, d["relationship_types"]


def xlsx_strings(path):
    with zipfile.ZipFile(path) as z:
        ss = z.read("xl/sharedStrings.xml").decode("utf-8")
    return re.findall(r"<t[^>]*>(.*?)</t>", ss, re.S)


def main(argv):
    check_only = "--check" in argv
    today = date.today().isoformat()
    pinned = json.load(open(os.path.join(HERE, "sources.json"))) if check_only else None
    manifest = {"retrieved": today, "files": {}}
    for name, src in SOURCES.items():
        path = os.path.join(DATA, name)
        if not check_only:
            try:
                fetch(name, src["url"])
            except Exception as e:  # noqa: BLE001
                print(f"fetch failed: {name}: {e}", file=sys.stderr)
                return 3
        digest, size = sha256(path), os.path.getsize(path)
        print(f"{name} {size} {digest}")
        if check_only and pinned["files"][name]["sha256"] != digest:
            print(f"hash mismatch: {name}: pinned {pinned['files'][name]['sha256']} now {digest}", file=sys.stderr)
            return 2
        manifest["files"][name] = {"url": src["url"], "what": src["what"], "bytes": size, "sha256": digest,
                                   "retrieved": pinned["files"][name]["retrieved"] if check_only else today}
    if check_only:
        print("sources.json verified")
        return 0

    cat = count_catalog(os.path.join(DATA, "sp800-53r5_catalog.json"))
    subs, funcs, cats, ext, rel_types = csf_core(os.path.join(DATA, "csf2_core_cprt.json"))
    strs = xlsx_strings(os.path.join(DATA, "csf2_informative_references.xlsx"))
    ctl_refs = sorted({s for s in strs if re.match(r"SP 800-53 Rev [\d.]+: [A-Z]{2}-\d+(\(\d+\))?$", s)})
    by_rev = {}
    for s in ctl_refs:
        by_rev.setdefault(s.split(":")[0], 0)
        by_rev[s.split(":")[0]] += 1
    manifest["counts"] = {
        "sp800_53": cat,
        "csf2": {"functions": len(funcs), "categories": len(cats), "subcategories": len(subs)},
        "references": {"cprt_external_reference_family_level": len(ext),
                       "workbook_control_level_distinct_strings_by_revision": by_rev},
    }
    manifest["direction"] = "SP 800-53 unit -> CSF 2.0 subcategory"
    manifest["scale"] = ["subset_of", "intersects_with", "equal", "superset_of", "no_relationship"]
    manifest["finding"] = ("No pinned NIST source carries IR 8477 relationship types. The CPRT export's relationship types are "
                           + ", ".join(t["relationship_identifier"] for t in rel_types)
                           + "; the workbook lists references as bare identifiers. Every NIST reference is therefore an "
                             "association, not a five-class label, under the frozen policy.")
    json.dump(manifest, open(os.path.join(HERE, "sources.json"), "w"), indent=2)

    semantics = {"policy": {"five_class": "a reference whose source states one of IR 8477's set-theory types for the pair",
                            "association": "a published link with no stated relationship type",
                            "absent": "no link in any pinned source; never evidence of no relationship"},
                 "entries": []}
    for r in ext:
        semantics["entries"].append({"source": "csf2_core_cprt.json", "subcategory": r["source_element_identifier"],
                                     "target": r["dest_element_identifier"], "target_doc": r["dest_doc_identifier"],
                                     "granularity": "family", "direction": "CSF subcategory -> SP 800-53 family",
                                     "tag": "association", "justification": "relationship_identifier is external_reference; no type field"})
    for s in ctl_refs:
        rev, ctl = s.split(": ")
        semantics["entries"].append({"source": "csf2_informative_references.xlsx", "subcategory": None,
                                     "target": ctl, "target_doc": rev, "granularity": "control",
                                     "direction": "CSF subcategory -> SP 800-53 control (subcategory read per row at item 7)",
                                     "tag": "association", "justification": "workbook cell is a bare identifier; no type field"})
    semantics["summary"] = {"five_class": 0, "association": len(semantics["entries"]), "absent": "not enumerated; absence is not a label"}
    json.dump(semantics, open(os.path.join(HERE, "reference_semantics.json"), "w"), indent=2)

    print(json.dumps(manifest["counts"]))
    print(f"reference_semantics: five_class 0, association {len(semantics['entries'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
