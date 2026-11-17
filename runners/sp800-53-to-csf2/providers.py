#!/usr/bin/env python3
"""Item 3: provider adapter and budget guard.

One entry point for every model call the runner makes: OpenAI (extraction, proposer, embeddings),
Anthropic and Google called directly, Mistral through OpenRouter (the three voters), and the council
(three voices, the same adapter, majority ruling, the pattern /council:deliberate uses). Every call
records the per-stage question and evidence identities, token usage and provider-reported cost where
the response carries it, in accounting.jsonl. The guard refuses a call whose maximum expected charge
(token ceiling times unit price, from protocol.json) exceeds the remaining allowance:
cap - spend - reserve, the reserve released only when phase is "deliverables".

Stdlib only. Keys come from the files GSTACK_OPENAI_ENV, GSTACK_ANTHROPIC_ENV, GSTACK_GEMINI_ENV and
GSTACK_OPENROUTER_ENV name (the vault's <provider>.env), or from the environment.

Exit codes for a pass that uses this adapter: 4 after a refused call (BudgetRefused), 5 on an outbound
attempt in replay mode (ReplayViolation). A refused call prints `refused: budget <remaining>` to stderr,
appends an accounting row with refused: true and evidence "refused" (zero cost), and returns no output.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL = os.path.join(HERE, "protocol.json")
ACCOUNTING = os.environ.get("GSTACK_ACCOUNTING") or os.path.join(HERE, "accounting.jsonl")
STATE = os.environ.get("GSTACK_RUN_STATE") or os.path.join(HERE, "run_state.json")
REQUEST_OVERHEAD = 16      # tokens a chat request spends on role framing

PROMPT_VERSIONS = {"extraction": "extract-v1", "proposal": "propose-v1", "vote": "vote-v1", "council": "council-v1"}

PROMPTS = {
    "extract-v1": ("Extract the required activities from this SP 800-53 control statement. Return JSON "
                   "{{\"activities\": [short imperative phrases], \"quotes\": [verbatim spans from the statement]}}.\n\n"
                   "Control {unit_id}: {unit_text}"),
    "propose-v1": ("Unit {unit_id} (activities: {activities}).\nCandidate CSF 2.0 subcategory {hub_id}: {hub_text}\n"
                   "Propose the set-theory relationship from the unit's required activities to the subcategory's, one of "
                   "{levels} or {null}. Quote the verbatim spans of the control text that support it. Return JSON "
                   "{{\"level\": ..., \"quotes\": [...], \"confidence\": 0..1}}."),
    "vote-v1": ("A proposer rated unit {unit_id} against CSF 2.0 subcategory {hub_id} as {level} with these quotes: "
                "{quotes}.\nUnit text: {unit_text}\nSubcategory text: {hub_text}\n"
                "Vote: do the quotes appear verbatim in the unit text, and is {level} the right one of {levels} or {null}? "
                "Return JSON {{\"vote\": one of those levels, \"quotes_verbatim\": true|false, \"confidence\": 0..1}}."),
    "council-v1": ("{rubric}\n\nUnit {unit_id}: {unit_text}\nContext: {context}\nCandidate subcategory {hub_id}: {hub_text}\n"
                   "Rule the relationship. Return JSON {{\"level\": one of {levels} or {null}, \"reason\": short}}."),
}


class BudgetRefused(Exception):
    """The guard refused: the ceiling charge exceeds the remaining allowance, or the input exceeds its
    token ceiling. The calling pass exits 4 (run_pass)."""


def token_bound(text):
    """An upper bound, not an estimate: a byte-level BPE token covers at least one byte, so the UTF-8 byte
    length plus the request overhead is never below the tokens a provider will count."""
    return len(text.encode("utf-8")) + REQUEST_OVERHEAD


def run_pass(fn):
    """Run a pass body; map the adapter's refusals to the plan's exit codes: 4 budget, 5 replay."""
    try:
        fn()
    except BudgetRefused:
        return 4
    except ReplayViolation:
        return 5
    return 0


class ReplayViolation(Exception):
    pass


def _env_key(name, env_var, file_var):
    if os.environ.get(name):
        return os.environ[name]
    path = os.environ.get(file_var)
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for raw in f:
                k, _, v = raw.strip().partition("=")
                if k.replace("export ", "").strip() == name and v.strip():
                    return v.strip().strip('"')
    return None


KEYS = {
    "openai": lambda: _env_key("OPENAI_API_KEY", "OPENAI_API_KEY", "GSTACK_OPENAI_ENV"),
    "anthropic": lambda: _env_key("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "GSTACK_ANTHROPIC_ENV"),
    "google": lambda: _env_key("GOOGLE_GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GSTACK_GEMINI_ENV"),
    "openrouter": lambda: _env_key("OPENROUTER_API_KEY", "OPENROUTER_API_KEY", "GSTACK_OPENROUTER_ENV"),
}


def identity(stage, **fields):
    """The per-stage question identity: the fields the plan names for that stage, hashed in sorted order."""
    body = json.dumps({"stage": stage, **fields}, sort_keys=True)
    return {"stage": stage, "fields": fields, "id": hashlib.sha256(body.encode()).hexdigest()[:16]}


class Transport:
    """Real HTTP. Tests replace it with a stub; replay mode replaces it with a refuser."""

    def post(self, url, headers, body, timeout=120):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{url}: HTTP {e.code}: {e.read().decode()[:300]}")


class ReplayTransport:
    def __init__(self, counter):
        self.counter = counter

    def post(self, *a, **k):
        self.counter["attempts"] = self.counter.get("attempts", 0) + 1
        raise ReplayViolation("outbound call attempted in replay mode")


class Adapter:
    def __init__(self, protocol=None, transport=None, accounting=ACCOUNTING, state=STATE, replay=False):
        if protocol is None:
            with open(PROTOCOL, encoding="utf-8") as f:
                protocol = json.load(f)
        self.protocol = protocol
        self.prices = self.protocol["budget"]["prices"]
        self.cap = float(self.protocol["budget"]["cap_usd"])
        self.reserve = float(self.protocol["budget"]["reserve_usd"])
        self.roles = self.protocol["roles"]
        self.accounting = accounting
        self.state_path = state
        self.replay_counter = {}
        self.transport = ReplayTransport(self.replay_counter) if replay else (transport or Transport())

    # ---- budget -------------------------------------------------------------------------------
    def spend(self):
        total = 0.0
        if os.path.isfile(self.accounting):
            with open(self.accounting, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        total += float(json.loads(line).get("usd", 0))
        return total

    def phase(self):
        if os.path.isfile(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f).get("phase", "run")
        return "run"

    def remaining(self):
        held = 0.0 if self.phase() == "deliverables" else self.reserve
        return self.cap - self.spend() - held

    def max_charge(self, model, tokens_in, tokens_out):
        p = self.prices[model]
        return (tokens_in * p["in"] + tokens_out * p["out"]) / 1e6

    def ceilings(self, role):
        r = self.roles[role]
        return r["in"], r["out"]

    # ---- accounting ---------------------------------------------------------------------------
    def record(self, row):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "phase": self.phase(), **row}
        with open(self.accounting, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    # ---- the one call -------------------------------------------------------------------------
    def call(self, role, model, prompt_version, prompt, question_identity, evidence_identity, json_out=True):
        tin, tout = self.ceilings(role)
        provider = self.prices[model]["provider"]
        charge = self.max_charge(model, tin, tout)
        remaining = self.remaining()
        base = {"stage": role, "provider": provider, "model": model, "prompt_version": prompt_version,
                "question_identity": question_identity, "evidence_identity": evidence_identity}
        self._guard(base, charge, remaining, token_bound(prompt), tin)
        text, usage, reported = self._dispatch(provider, model, prompt, tin, tout)
        tokens_in, tokens_out = usage.get("in"), usage.get("out")
        if reported is not None:
            usd, evidence = float(reported), "provider_reported"
        elif tokens_in is not None and tokens_out is not None:
            usd, evidence = self.max_charge(model, tokens_in, tokens_out), "usage_priced"
        else:
            usd, evidence = charge, "unpriced"
        self.record({**base, "refused": False, "evidence": evidence, "usd": round(usd, 6),
                     "tokens_in": tokens_in, "tokens_out": tokens_out, "max_charge": round(charge, 6)})
        return self._parse(text) if json_out else text

    def embed(self, model, text, question_identity):
        tin, tout = self.ceilings("embedding")
        provider = self.prices[model]["provider"]
        charge = self.max_charge(model, tin, tout)
        remaining = self.remaining()
        base = {"stage": "embedding", "provider": provider, "model": model, "prompt_version": None,
                "question_identity": question_identity, "evidence_identity": None}
        self._guard(base, charge, remaining, token_bound(text), tin)
        r = self.transport.post("https://api.openai.com/v1/embeddings",
                                {"Authorization": f"Bearer {KEYS['openai']()}"},
                                {"model": model.split("/", 1)[1], "input": text})
        vec = r["data"][0]["embedding"]
        tokens = (r.get("usage") or {}).get("prompt_tokens")
        usd = self.max_charge(model, tokens, 0) if tokens is not None else charge
        self.record({**base, "refused": False, "evidence": "usage_priced" if tokens is not None else "unpriced",
                     "usd": round(usd, 6), "tokens_in": tokens, "tokens_out": 0, "max_charge": round(charge, 6)})
        return vec

    def _guard(self, base, charge, remaining, estimated_in, tin):
        """Refuse before any request leaves: over the allowance, or an input past its ceiling (the ceiling is
        what the charge was priced on, so an oversized input is never sent and never charged)."""
        reason = None
        if charge > remaining:
            reason = "charge over allowance"
        elif estimated_in > tin:
            reason = f"input bound {estimated_in} tokens over ceiling {tin}"
        if reason:
            print(f"refused: budget {remaining:.4f}", file=sys.stderr)   # exactly this line; the reason is on the row
            self.record({**base, "refused": True, "refusal": reason, "evidence": "refused", "usd": 0.0,
                         "tokens_in": 0, "tokens_out": 0, "max_charge": round(charge, 6)})
            raise BudgetRefused(reason)

    # ---- providers ----------------------------------------------------------------------------
    def _dispatch(self, provider, model, prompt, tin, tout):
        name = model.split("/", 1)[1]
        if provider == "openai":
            r = self.transport.post("https://api.openai.com/v1/chat/completions",
                                    {"Authorization": f"Bearer {KEYS['openai']()}"},
                                    {"model": name, "messages": [{"role": "user", "content": prompt}],
                                     "max_completion_tokens": tout, "temperature": 0})
            u = r.get("usage") or {}
            return r["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}, None
        if provider == "anthropic":
            r = self.transport.post("https://api.anthropic.com/v1/messages",
                                    {"x-api-key": KEYS["anthropic"](), "anthropic-version": "2023-06-01"},
                                    {"model": name, "max_tokens": tout, "temperature": 0,
                                     "messages": [{"role": "user", "content": prompt}]})
            u = r.get("usage") or {}
            return "".join(b.get("text", "") for b in r["content"]), {"in": u.get("input_tokens"), "out": u.get("output_tokens")}, None
        if provider == "google":
            r = self.transport.post(f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
                                    {"x-goog-api-key": KEYS["google"]()},
                                    {"contents": [{"parts": [{"text": prompt}]}],
                                     "generationConfig": {"maxOutputTokens": tout, "temperature": 0}})
            u = r.get("usageMetadata") or {}
            text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
            return text, {"in": u.get("promptTokenCount"), "out": u.get("candidatesTokenCount")}, None
        if provider == "openrouter":
            r = self.transport.post("https://openrouter.ai/api/v1/chat/completions",
                                    {"Authorization": f"Bearer {KEYS['openrouter']()}"},
                                    {"model": model, "messages": [{"role": "user", "content": prompt}],
                                     "max_tokens": tout, "temperature": 0, "usage": {"include": True}})
            u = r.get("usage") or {}
            return r["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}, u.get("cost")
        raise ValueError(provider)

    @staticmethod
    def _parse(text):
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("{"):]
        start, end = t.find("{"), t.rfind("}")
        try:
            return json.loads(t[start:end + 1])
        except Exception:
            return {"parse_error": True, "raw": text[:500]}

    # ---- the council --------------------------------------------------------------------------
    def council(self, packet, question_identity, evidence_identity):
        """Three voices, each its own call; majority on the level; a three-way split rules the null with reason split."""
        votes = []
        for m in self.roles["council"]["models"]:
            out = self.call("council", m, PROMPT_VERSIONS["council"], render("council", **packet),
                            question_identity, evidence_identity)
            votes.append({"model": m, "level": out.get("level"), "reason": out.get("reason")})
        levels = [v["level"] for v in votes]
        winner = max(set(levels), key=levels.count)
        if levels.count(winner) < 2:
            return {"level": self.protocol["null"], "reason": "split", "votes": votes}
        return {"level": winner, "reason": "majority", "votes": votes}


def render(stage, **fields):
    return PROMPTS[PROMPT_VERSIONS[stage]].format(**fields)


def probe():
    """One real embedding call through the adapter and run_pass: the smallest calling pass, so the
    refusal-to-exit-4 path is a recorded fact, not a declaration."""
    a = Adapter()
    vec = a.embed(a.roles["embedding"]["model"], "probe", identity("embedding", probe=True))
    print(f"probe ok dim {len(vec)}")


if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        sys.exit(run_pass(probe))
    print(__doc__)
