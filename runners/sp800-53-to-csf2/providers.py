#!/usr/bin/env python3
"""Item 3: provider adapter and budget guard.

One entry point for every model call the runner makes: OpenAI (extraction, proposer, embeddings),
Anthropic and Google called directly, Mistral through OpenRouter (the three voters), and the council
(three voices, the same adapter, majority ruling, the pattern /council:deliberate uses). Every call
records the per-stage question and evidence identities, token usage and provider-reported cost where
the response carries it, in accounting.jsonl. The guard refuses, before any request leaves, a call whose
maximum expected charge (token ceiling times unit price) exceeds the remaining allowance, which is the
smaller of the global allowance (cap - spend - reserve, the reserve released only when the phase is
"deliverables") and the provider's allowance (its cap - its spend). Money is exact (Decimal); nothing is
rounded on the way to the ledger.

events.jsonl records call_started before a transport is invoked, fixture transports included, and
outbound_attempt for every attempt replay mode refuses.

Stdlib only. Keys come from the files GSTACK_OPENAI_ENV, GSTACK_ANTHROPIC_ENV, GSTACK_GEMINI_ENV and
GSTACK_OPENROUTER_ENV name (the vault's <provider>.env), or from the environment.

Exit codes for a pass that uses this adapter (run_pass): 4 after a refused call, 5 on an outbound
attempt in replay mode. A refused call prints exactly `refused: budget <remaining>` to stderr, appends a
zero-cost accounting row with refused: true and evidence "refused", and returns no output.

    python3 -I providers.py probe     one real embedding call through run_pass, the smallest calling pass
"""
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL = os.path.join(HERE, "protocol.json")
ACCOUNTING = os.environ.get("GSTACK_ACCOUNTING") or os.path.join(HERE, "accounting.jsonl")
EVENTS = os.environ.get("GSTACK_EVENTS") or os.path.join(HERE, "events.jsonl")
STATE = os.environ.get("GSTACK_RUN_STATE") or os.path.join(HERE, "run_state.json")
REQUEST_OVERHEAD = 16      # tokens a chat request spends on role framing
DECODING = {"openai": {"reasoning_effort": "none"}, "anthropic": {"thinking": {"type": "disabled"}}}
WIRE_NAMES = {"anthropic/claude-haiku-5.5": "claude-haiku-5-5"}   # the protocol's id is the identity; this is the provider's spelling   # gpt-6-luna reasons into its output ceiling otherwise; recorded on every row
MILLION = Decimal(1_000_000)

PROMPT_VERSIONS = {"extraction": "extract-v1", "proposal": "propose-v1", "vote": "vote-v1", "council": "council-v1"}
PROMPT_VARIANTS = {"proposal": ["propose-v1", "propose-v2"]}   # the wordings calibration may choose between

PROMPTS = {
    "extract-v1": ("Extract the required activities from this SP 800-53 control statement. Return JSON "
                   "{{\"activities\": [short imperative phrases], \"quotes\": [verbatim spans from the statement]}}.\n\n"
                   "Control {unit_id}: {unit_text}"),
    "propose-v1": ("Unit {unit_id} control statement: {unit_text}\nRequired activities extracted from it: {activities}\n"
                   "Candidate CSF 2.0 subcategory {hub_id}: {hub_text}\n"
                   "Propose the set-theory relationship from the unit's required activities to the subcategory's, one of "
                   "{levels} or {null}. Quote the verbatim spans of the control statement that support it. Return JSON "
                   "{{\"level\": ..., \"quotes\": [...], \"confidence\": 0..1}}."),
    "propose-v2": ("You compare one SP 800-53 control with one CSF 2.0 subcategory.\nControl {unit_id} states: {unit_text}\n"
                   "It requires these activities: {activities}\nSubcategory {hub_id} says: {hub_text}\nAnswer with the set-theory relationship "
                   "from the control's required activities to the subcategory's outcome: {levels}, or {null} when the "
                   "subcategory does not cover any of them. Copy the exact words of the control statement that carry each "
                   "activity you rely on. Return JSON {{\"level\": ..., \"quotes\": [...], \"confidence\": 0..1}} and nothing else."),
    "vote-v1": ("A proposer rated unit {unit_id} against CSF 2.0 subcategory {hub_id} as {level} with these quotes: "
                "{quotes}.\nUnit text: {unit_text}\nSubcategory text: {hub_text}\n"
                "Vote: do the quotes appear verbatim in the unit text, and is {level} the right one of {levels} or {null}? "
                "Return only the JSON object {{\"vote\": one of those levels, \"quotes_verbatim\": true|false, \"confidence\": 0..1}}, "
                "with no words before or after it."),
    "council-v1": ("{rubric}\n\nUnit {unit_id}: {unit_text}\nContext: {context}\nCandidate subcategory {hub_id}: {hub_text}\n"
                   "Rule the relationship. Return JSON {{\"level\": one of {levels} or {null}, \"reason\": short}}."),
}


class BudgetRefused(Exception):
    """The guard refused: the ceiling charge exceeds the remaining allowance, or the input exceeds its
    token ceiling. The calling pass exits 4 (run_pass)."""


class ReplayViolation(Exception):
    """An outbound call was attempted in replay mode. The calling pass exits 5 (run_pass)."""


def D(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value):
    """Exact decimal text for a ledger field; never a float."""
    return format(D(value).normalize(), "f") if D(value) != 0 else "0"


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


def _env_key(name, file_var):
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
    "openai": lambda: _env_key("OPENAI_API_KEY", "GSTACK_OPENAI_ENV"),
    "anthropic": lambda: _env_key("ANTHROPIC_API_KEY", "GSTACK_ANTHROPIC_ENV"),
    "anthropic_workspace": lambda: _env_key("ANTHROPIC_WORKSPACE_ID", "GSTACK_ANTHROPIC_ENV"),
    "google": lambda: _env_key("GOOGLE_GEMINI_API_KEY", "GSTACK_GEMINI_ENV"),
    "openrouter": lambda: _env_key("OPENROUTER_API_KEY", "GSTACK_OPENROUTER_ENV"),
}


BASE_KEYS = {"stage", "provider", "family", "model", "prompt_version", "question_identity", "evidence_identity", "decoding"}
BACKOFF = (2, 4, 8, 16, 32, 60)   # seconds between attempts the provider answered with 429, 502, 503 or 504


def identity(stage, **fields):
    """The per-stage question identity: the fields the plan names for that stage, hashed in sorted order."""
    body = json.dumps({"stage": stage, **fields}, sort_keys=True)
    return {"stage": stage, "fields": fields, "id": hashlib.sha256(body.encode()).hexdigest()[:16]}


class TransportError(RuntimeError):
    """A request that left and did not return a usable response. `answered` is True when the provider answered
    with an error status (nothing billed; retryable when `retryable`), False when the outcome is unknown (a timeout
    or a dropped connection after the request left): the adapter charges the ceiling for those, since it cannot
    prove the provider did not bill, and never retries them."""

    def __init__(self, msg, answered, retryable, status=None):
        super().__init__(msg)
        self.answered, self.retryable, self.status = answered, retryable, status


class Transport:
    """Real HTTP, one attempt per call. Tests and the fixture harness replace it; replay mode replaces it with a
    refuser. Retrying is the adapter's: every attempt is guarded, reserved and accounted."""
    kind = "live"
    RETRYABLE = (429,)   # the one status that says the request was not processed; a 5xx from a gateway says nothing

    def post(self, url, headers, body, timeout=120):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            # a 4xx is the provider's own answer, nothing billed; a 5xx may come from a gateway in front of a model that
            # did the work, so its billing is unknown. The error body is read defensively: a failure there is unknown too.
            try:
                detail = e.read().decode(errors="replace")[:300]
            except Exception as read_error:   # noqa: BLE001 - anything that stops the body being read
                raise TransportError(f"{url}: HTTP {e.code}, body unreadable: {read_error}", answered=False, retryable=False, status=e.code)
            raise TransportError(f"{url}: HTTP {e.code}: {detail}", answered=e.code < 500,
                                 retryable=e.code in self.RETRYABLE, status=e.code)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as e:
            # IncompleteRead and its siblings are HTTPException, not OSError: the request left, the outcome is unknown
            raise TransportError(f"{url}: {type(e).__name__}: {e}", answered=False, retryable=False)
        try:
            return json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError) as e:    # the request completed; the response is unusable: billing unknown
            raise TransportError(f"{url}: unusable response: {e}", answered=False, retryable=False)


class ReplayTransport:
    kind = "replay"

    def __init__(self, counter):
        self.counter = counter

    def post(self, *a, **k):
        self.counter["attempts"] = self.counter.get("attempts", 0) + 1
        raise ReplayViolation("outbound call attempted in replay mode")


class Adapter:
    def __init__(self, protocol=None, transport=None, accounting=ACCOUNTING, state=STATE, events=EVENTS, replay=False,
                 protocol_sha256=None):
        if protocol is None:
            with open(PROTOCOL, "rb") as f:
                raw = f.read()
            protocol, protocol_sha256 = json.loads(raw), hashlib.sha256(raw).hexdigest()
        self.protocol = protocol
        self.protocol_sha256 = protocol_sha256   # every call_started names the frozen protocol it ran under
        b = protocol["budget"]
        self.prices = {m: {"in": D(p["in"]), "out": D(p["out"]), "provider": p["provider"], "family": p.get("family", p["provider"])}
                       for m, p in b["prices"].items()}   # provider bills; family is the seat the independence rules read
        self.cap, self.reserve = D(b["cap_usd"]), D(b["reserve_usd"])
        self.provider_caps = {k: D(v) for k, v in b.get("provider_caps", {}).items()}
        self.roles = protocol["roles"]
        self.accounting, self.state_path, self.events = accounting, state, events
        self.replay_counter = {}
        self.transport = ReplayTransport(self.replay_counter) if replay else (transport or Transport())
        self._lock = threading.RLock()          # the guard and the ledger position are read and moved under it
        self._cache = None                      # (file size, total, by_provider): the ledger re-read only when it moved
        self._inflight = {}                     # ceiling charges of calls sent and not yet settled, globally and per biller

    # ---- budget -------------------------------------------------------------------------------
    def spend(self):
        size = os.path.getsize(self.accounting) if os.path.isfile(self.accounting) else 0
        with self._lock:
            if self._cache and self._cache[0] == size:
                return self._cache[1], dict(self._cache[2])
        total, by_provider = Decimal(0), {}
        if os.path.isfile(self.accounting):
            with open(self.accounting, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        if "charged_usd" not in row:
                            raise ValueError(f"accounting row without charged_usd: {line[:80]}")   # never read as zero
                        usd = D(row["charged_usd"])
                        total += usd
                        by_provider[row.get("provider")] = by_provider.get(row.get("provider"), Decimal(0)) + usd
        with self._lock:
            self._cache = (size, total, dict(by_provider))
        return total, by_provider

    def phase(self):
        if os.path.isfile(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f).get("phase", "run")
        return "run"

    def remaining(self, provider=None):
        total, by_provider = self.spend()
        held = Decimal(0) if self.phase() == "deliverables" else self.reserve
        remaining = self.cap - total - held - self._inflight.get("*", Decimal(0))   # a call in flight holds its ceiling until it settles
        if provider in self.provider_caps:
            remaining = min(remaining, self.provider_caps[provider] - by_provider.get(provider, Decimal(0))
                            - self._inflight.get(provider, Decimal(0)))
        return remaining

    def _reserve(self, provider, charge, sign):
        for key in ("*", provider):
            self._inflight[key] = self._inflight.get(key, Decimal(0)) + sign * charge

    def _attempts(self, base, charge, provider, bound, tin, role, send):
        """Every attempt is guarded and reserved on its own. An attempt the provider answered with a 4xx bills nothing:
        it is logged as call_failed and retried with backoff only on 429. An attempt whose outcome is unknown (a
        timeout, a dropped connection, a 5xx, an unusable response) is charged at the ceiling, under its reservation,
        and never retried. A completed attempt returns with its reservation still held: the caller settles the charge
        and then calls _release, so no other worker reads an allowance that counts neither the reservation nor the
        charge (review 20261117-084733 F1). Returns (raw response, remaining at the attempt)."""
        for attempt in range(len(BACKOFF) + 1):
            with self._lock:
                remaining = self.remaining(provider)
                self._guard(base, charge, remaining, bound, tin)
                self._reserve(provider, charge, +1)
            self.event("call_started", role=role, stage=base["stage"], provider=provider, model=base["model"],
                       transport=self.transport.kind, question_identity=base["question_identity"]["id"],
                       protocol_sha256=self.protocol_sha256, attempt=attempt + 1,
                       **{k: v for k, v in base.items() if k not in BASE_KEYS})
            try:
                return send(), remaining
            except ReplayViolation:
                self._release(provider, charge)
                self.event("outbound_attempt", role=role, stage=base["stage"], provider=provider)
                raise
            except TransportError as e:
                if not e.answered:   # outcome unknown: the ceiling is charged before the reservation goes, nothing is retried
                    with self._lock:
                        self.record({**base, "refused": False, "evidence": "unpriced", "charged_usd": money(charge),
                                     "tokens_in": None, "tokens_out": None, "max_expected_charge_usd": money(charge),
                                     "remaining_usd": money(remaining), "failure": str(e)[:200], "attempt": attempt + 1})
                        self._reserve(provider, charge, -1)
                    raise
                self._release(provider, charge)
                retry = e.retryable and attempt < len(BACKOFF)
                self.event("call_failed", role=role, stage=base["stage"], provider=provider, model=base["model"],
                           status=e.status, attempt=attempt + 1, retry=retry)
                if not retry:
                    raise
            except BaseException as e:   # anything the transport did not classify: the request may have left, charge the ceiling
                with self._lock:
                    self.record({**base, "refused": False, "evidence": "unpriced", "charged_usd": money(charge),
                                 "tokens_in": None, "tokens_out": None, "max_expected_charge_usd": money(charge),
                                 "remaining_usd": money(remaining), "failure": f"{type(e).__name__}: {e}"[:200], "attempt": attempt + 1})
                    self._reserve(provider, charge, -1)
                raise
            time.sleep(BACKOFF[attempt])
        raise AssertionError("unreachable")

    def _settle_or_ceiling(self, base, charge, remaining, provider, usage):
        """A completed request is on the ledger before its reservation goes, whatever its response looks like: priced
        from usage it carries, or at the ceiling when the usage is missing, malformed or anything raises on the way."""
        try:
            parsed = usage()              # outside the lock: reading the response needs no ledger
        except BaseException as e:       # noqa: BLE001
            parsed = e
        with self._lock:
            try:
                if isinstance(parsed, BaseException):
                    raise parsed
                self._settle(base, charge, remaining, *parsed)
            except BaseException as e:   # noqa: BLE001 - the request completed; what it cost is unknown: the ceiling
                self.record({**base, "refused": False, "evidence": "unpriced", "charged_usd": money(charge), "tokens_in": None,
                             "tokens_out": None, "max_expected_charge_usd": money(charge), "remaining_usd": money(remaining),
                             "failure": f"unpriceable response: {type(e).__name__}: {e}"[:200]})
            finally:
                self._reserve(provider, charge, -1)

    def _release(self, provider, charge):
        with self._lock:
            self._reserve(provider, charge, -1)

    def family(self, model):
        """The model's maker: the seat identity for the triad and the council, whoever bills the call."""
        return self.prices[model]["family"]

    def max_charge(self, model, tokens_in, tokens_out):
        p = self.prices[model]
        return (D(tokens_in) * p["in"] + D(tokens_out) * p["out"]) / MILLION

    def ceilings(self, role):
        r = self.roles[role]
        return r["in"], r["out"]

    # ---- records ------------------------------------------------------------------------------
    def record(self, row):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "phase": self.phase(), **row}
        with self._lock:
            with open(self.accounting, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            if self._cache:
                usd = D(row.get("charged_usd", "0"))
                by = dict(self._cache[2]); by[row.get("provider")] = by.get(row.get("provider"), Decimal(0)) + usd
                self._cache = (os.path.getsize(self.accounting), self._cache[1] + usd, by)
        return row

    def event(self, name, **fields):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": name, **fields}
        with self._lock:
            with open(self.events, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def _guard(self, base, charge, remaining, estimated_in, tin):
        """Refuse before any request leaves: over the allowance, or an input past its ceiling (the ceiling is
        what the charge was priced on, so an oversized input is never sent and never charged)."""
        reason = None
        if charge > remaining:
            reason = "charge over allowance"
        elif estimated_in > tin:
            reason = f"input bound {estimated_in} tokens over ceiling {tin}"
        if reason:
            print(f"refused: budget {money(remaining)}", file=sys.stderr)   # exactly this line; the reason is on the row
            self.record({**base, "refused": True, "refusal": reason, "evidence": "refused", "charged_usd": "0",
                         "tokens_in": 0, "tokens_out": 0, "max_expected_charge_usd": money(charge), "remaining_usd": money(remaining)})
            raise BudgetRefused(reason)

    def _settle(self, base, charge, remaining, usage, reported):
        """Price the completed request from what it carried, and record it before anything else is read."""
        tokens_in, tokens_out = usage.get("in"), usage.get("out")
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
        tokens_in, tokens_out = (tokens_in if num(tokens_in) else None), (tokens_out if num(tokens_out) else None)
        if reported is not None and (num(reported) or (isinstance(reported, str) and reported.replace(".", "", 1).isdigit())):
            usd, evidence = D(reported), "provider_reported"
        elif tokens_in is not None and tokens_out is not None:
            usd, evidence = self.max_charge(base["model"], tokens_in, tokens_out), "usage_priced"
        else:
            usd, evidence = charge, "unpriced"
        self.record({**base, "refused": False, "evidence": evidence, "charged_usd": money(usd),
                     "tokens_in": tokens_in, "tokens_out": tokens_out,
                     "max_expected_charge_usd": money(charge), "remaining_usd": money(remaining)})
        return usd

    # ---- the one call -------------------------------------------------------------------------
    def call(self, role, model, prompt_version, prompt, question_identity, evidence_identity, json_out=True, **context):
        tin, tout = self.ceilings(role)
        provider = self.prices[model]["provider"]
        charge = self.max_charge(model, tin, tout)
        base = {"stage": role, "provider": provider, "family": self.family(model), "model": model, "prompt_version": prompt_version,
                "question_identity": question_identity, "evidence_identity": evidence_identity,
                "decoding": DECODING.get(provider, {}), **context}
        raw, remaining = self._attempts(base, charge, provider, token_bound(prompt), tin, "council" if role == "council" else "model",
                                        lambda: self._send(provider, model, prompt, tout))
        self._settle_or_ceiling(base, charge, remaining, provider, lambda: self._usage(provider, raw))
        text = self._content(provider, raw)
        return self._parse(text) if json_out else text

    def embed(self, model, text, question_identity, **context):
        tin, tout = self.ceilings("embedding")
        provider = self.prices[model]["provider"]
        charge = self.max_charge(model, tin, tout)
        base = {"stage": "embedding", "provider": provider, "model": model, "prompt_version": None,
                "question_identity": question_identity, "evidence_identity": None, **context}
        raw, remaining = self._attempts(base, charge, provider, token_bound(text), tin, "retriever",
                                        lambda: self.transport.post("https://api.openai.com/v1/embeddings",
                                                                    {"Authorization": f"Bearer {KEYS['openai']()}"},
                                                                    {"model": model.split("/", 1)[1], "input": text}))
        def usage():
            tokens = (raw.get("usage") or {}).get("prompt_tokens") if isinstance(raw, dict) else None
            return {"in": tokens, "out": 0 if tokens is not None else None}, None
        self._settle_or_ceiling(base, charge, remaining, provider, usage)
        return raw["data"][0]["embedding"]

    # ---- providers ----------------------------------------------------------------------------
    def _send(self, provider, model, prompt, tout):
        name = WIRE_NAMES.get(model, model.split("/", 1)[1])
        if provider == "openai":
            return self.transport.post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {KEYS['openai']()}"},
                                       {"model": name, "messages": [{"role": "user", "content": prompt}],
                                        "max_completion_tokens": tout, **DECODING["openai"]})   # only its default temperature
        if provider == "anthropic":
            return self.transport.post("https://api.anthropic.com/v1/messages",
                                       {"x-api-key": KEYS["anthropic"](), "anthropic-version": "2023-06-01",
                                        **({"anthropic-workspace-id": KEYS["anthropic_workspace"]()} if KEYS["anthropic_workspace"]() else {})},
                                       {"model": name, "max_tokens": tout, **DECODING["anthropic"],   # no temperature: deprecated
                                        "messages": [{"role": "user", "content": prompt}]})
        if provider == "google":
            return self.transport.post(f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
                                       {"x-goog-api-key": KEYS["google"]()},
                                       {"contents": [{"parts": [{"text": prompt}]}],
                                        "generationConfig": {"maxOutputTokens": tout, "temperature": 0}})
        if provider == "openrouter":
            return self.transport.post("https://openrouter.ai/api/v1/chat/completions", {"Authorization": f"Bearer {KEYS['openrouter']()}"},
                                       {"model": model, "messages": [{"role": "user", "content": prompt}],
                                        "max_tokens": tout, "temperature": 0, "usage": {"include": True}})
        raise ValueError(provider)

    @staticmethod
    def _usage(provider, raw):
        """Usage and reported cost, read before any content is touched."""
        if provider == "google":
            u = raw.get("usageMetadata") or {}
            return {"in": u.get("promptTokenCount"), "out": u.get("candidatesTokenCount")}, None
        u = raw.get("usage") or {}
        if provider == "anthropic":
            return {"in": u.get("input_tokens"), "out": u.get("output_tokens")}, None
        return {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}, (u.get("cost") if provider == "openrouter" else None)

    @staticmethod
    def _content(provider, raw):
        if provider == "anthropic":
            return "".join(b.get("text", "") for b in raw["content"])
        if provider == "google":
            return "".join(p.get("text", "") for p in raw["candidates"][0]["content"]["parts"])
        return raw["choices"][0]["message"]["content"]

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
    def council(self, packet, question_identity, evidence_identity, **context):
        """Three voices, each its own call; majority on the level; a three-way split rules the null with reason split."""
        votes = []
        for m in self.roles["council"]["models"]:
            out = self.call("council", m, PROMPT_VERSIONS["council"], render("council", **packet),
                            question_identity, evidence_identity, **context)
            votes.append({"model": m, "level": out.get("level"), "reason": out.get("reason")})
        levels = [v["level"] for v in votes]
        winner = max(set(levels), key=levels.count)
        if levels.count(winner) < 2:
            return {"level": self.protocol["null"], "reason": "split", "votes": votes}
        return {"level": winner, "reason": "majority", "votes": votes}


def render(stage, **fields):
    return PROMPTS[PROMPT_VERSIONS[stage]].format(**fields)


def probe():
    """One real embedding call through the adapter and run_pass: the smallest calling pass."""
    a = Adapter()
    vec = a.embed(a.roles["embedding"]["model"], "probe", identity("embedding", probe=True))
    print(f"probe ok dim {len(vec)}")


if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        sys.exit(run_pass(probe))
    print(__doc__)
