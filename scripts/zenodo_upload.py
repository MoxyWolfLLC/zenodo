"""Upload one artifact folder to Zenodo as its own record, using the folder's .zenodo.json.

Usage:
  ZENODO_TOKEN=... python3 scripts/zenodo_upload.py runners/<slug>            # creates a DRAFT, prints its URL
  ZENODO_TOKEN=... python3 scripts/zenodo_upload.py runners/<slug> --publish  # publishes the draft it finds (permanent DOI)
  add --sandbox to use sandbox.zenodo.org for a dry run

A draft can be edited or deleted on Zenodo; a published record cannot be deleted, only versioned.
The script never publishes without --publish. Standard library only.
"""
import io
import json
import os
import sys
import urllib.request
import zipfile

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) != 1:
    sys.exit(__doc__)
folder, publish = args[0].rstrip("/"), "--publish" in sys.argv
base = "https://sandbox.zenodo.org/api" if "--sandbox" in sys.argv else "https://zenodo.org/api"
token = os.environ.get("ZENODO_TOKEN") or sys.exit("set ZENODO_TOKEN (a Zenodo personal access token with deposit:write, plus deposit:actions to publish)")
meta = json.load(open(os.path.join(folder, ".zenodo.json")))
slug = os.path.basename(folder)
state_file = os.path.join(folder, ".zenodo-deposit.json")  # remembers the draft so --publish finds it


def call(method, url, body=None, ctype="application/json"):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    with urllib.request.urlopen(req) as r:
        out = r.read()
    return json.loads(out) if out else {}


def zip_folder():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                if f in (".zenodo-deposit.json",) or f.endswith(".pyc") or f == ".DS_Store":
                    continue
                path = os.path.join(root, f)
                z.write(path, os.path.join(slug, os.path.relpath(path, folder)))
    return buf.getvalue()


if publish:
    dep = json.load(open(state_file))
    done = call("POST", f"{base}/deposit/depositions/{dep['id']}/actions/publish")
    print(f"published: https://doi.org/{done['doi']}  ({done['links']['html']})")
    json.dump({**dep, "doi": done["doi"]}, open(state_file, "w"), indent=2)
    sys.exit(0)

dep = call("POST", f"{base}/deposit/depositions", {})
call("PUT", f"{dep['links']['bucket']}/{slug}.zip", zip_folder(), "application/octet-stream")
call("PUT", f"{base}/deposit/depositions/{dep['id']}", {"metadata": meta})
json.dump({"id": dep["id"], "html": dep["links"]["html"], "sandbox": "--sandbox" in sys.argv}, open(state_file, "w"), indent=2)
print(f"draft created: {dep['links']['html']}  (review it, then rerun with --publish)")
