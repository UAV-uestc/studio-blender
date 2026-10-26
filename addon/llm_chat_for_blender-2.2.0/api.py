"""Other models than Claude: any OpenAI-compatible chat API, standard library only.

DeepSeek, OpenRouter, Google Gemini, Groq, Ollama and LM Studio all accept the same
/chat/completions request with function calling, so one client covers them.
The loop runs in a background thread: it asks the model, runs the tools it calls
(the same Blender tools Claude gets, on Blender's main thread via bridge.call), sends
the results back, and repeats until the model answers without calling a tool.

The API key only ever goes to the provider's own base URL, in the Authorization
header. Nothing else leaves this computer except what the model is sent.
"""

import itertools
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import bridge, deps, mcp_server

# id: (label, base URL, default model, where to get a key, needs a key)
PROVIDERS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com", "deepseek-flash",
                 "https://platform.deepseek.com/api_keys", True),
    "openrouter": ("OpenRouter (many models)", "https://openrouter.ai/api/v1",
                   "", "https://openrouter.ai/keys", True),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-flash-latest", "https://aistudio.google.com/apikey", True),
    "groq": ("Groq", "https://api.groq.com/openai/v1",
             "", "https://console.groq.com/keys", True),
    "ollama": ("Ollama (local)", "http://localhost:11434/v1", "", "https://ollama.com", False),
    "lmstudio": ("LM Studio (local)", "http://localhost:1234/v1", "", "https://lmstudio.ai", False),
    "custom": ("Custom (OpenAI-compatible)", "", "", "", False),
}

# cfg["max_steps"]: tool rounds per message (the user's Step limit preference; 0 = no limit).
WRAP_UP = ("(Note from the add-on: 3 tool rounds are left in this message. Bring the scene to a "
           "consistent state and reply; the user can say continue.)")
KEEP_IMAGES = 3         # older screenshots are dropped from the history to save tokens
MAX_TOOL_TEXT = 20000   # characters of tool output sent back per call
RETRIES = (10, 30, 60)  # waits (s) after a rate limit, when the server gives no Retry-After
RETRY_CODES = {429, 500, 502, 503, 504}  # rate limited or overloaded: worth trying again
# Used in turn when the default model is overloaded or out of free quota (each Gemini model
# has its own free limits). Only when the user hasn't picked a model.
FALLBACKS = {"gemini": ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-lite-latest"]}

AUDIT = ("(Automatic check by the add-on, not a message from the user.) The scene now "
         "actually contains:\n{audit}\n\nYou are not done while problems are listed: fix "
         "each one (or say why it is intended), and finish anything the request or "
         "instructions call for that is missing. The scene is checked again when you stop. Your final summary must match "
         "the checks; every number in it must come from a check, not from memory.")
AUDIT_ROUNDS = 3  # scene checks per message before the remaining problems go to the user
NUDGE = ("(Your last reply was cut off or empty before you acted. Think less: make one tool "
         "call now, or answer in a few words.)")


# The tools Claude gets (mcp_server.py), in the OpenAI function format.
SCHEMAS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                             "parameters": t["inputSchema"]}}
           for t in mcp_server.TOOLS]
PRIVACY = ("The provider receives what you send (scene names, code, screenshots); free tiers "
           "may use it to improve their models. For private work use a local model.")


class ApiError(Exception):
    pass


def is_local(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def check_url(url):
    """Plain http is only allowed to this computer, so a key never travels unencrypted."""
    p = urllib.parse.urlparse(url)
    if p.scheme == "https" or (p.scheme == "http" and is_local(url)):
        return None
    return "Use an https:// address (http:// is only allowed for localhost)."


def _request(base, key, path, payload=None, timeout=300):
    req = urllib.request.Request(base.rstrip("/") + path, method="POST" if payload else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(req, data, timeout=timeout, context=deps.ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _error_text(e):
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        if isinstance(body, list):
            body = body[0]
        err = body.get("error", body)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001
        msg = ""
    return f"{e.code} {e.reason}: {msg}".strip(": ")[:600]


def _usable(m):
    """Leave out models the provider says can't call tools or see images (OpenRouter
    lists both); the add-on needs tools, and screenshots to check its work."""
    params = m.get("supported_parameters")
    inputs = (m.get("architecture") or {}).get("input_modalities")
    return (params is None or "tools" in params) and (inputs is None or "image" in inputs)


def list_models(base, key):
    """Newest first where the provider says when (OpenRouter lists hundreds: alphabetical,
    the first 40 hid DeepSeek and Gemini), else by name; no batch variants or ~aliases."""
    data = _request(base, key, "/models", timeout=30)
    found = [(-float(m.get("created") or 0), m.get("id", "").removeprefix("models/"))
             for m in data.get("data", data.get("models", [])) if _usable(m)]
    return [i for _, i in sorted(found) if i and not i.endswith(":batch") and not i.startswith("~")]


def _prune_images(messages):
    """Keep only the newest screenshots; replace older ones with a note."""
    seen = 0
    for m in reversed(messages):
        if not isinstance(m.get("content"), list):
            continue
        parts = []
        for part in m["content"]:
            if part.get("type") == "image_url":
                seen += 1
                if seen > KEEP_IMAGES:
                    part = {"type": "text", "text": "[older image removed]"}
            parts.append(part)
        m["content"] = parts


def _wait(seconds, is_stopped):
    for _ in range(int(seconds * 10)):
        if is_stopped():
            raise ApiError("stopped")
        time.sleep(0.1)


FAST_TPS = 120.0  # OpenRouter: providers at least this fast (tokens/s) come first; a reasoning
#                  model thinks ~9k tokens a step: 79 tok/s made 80 s steps (2026-10-20)
_ROUTES = {}     # OpenRouter model -> provider order


def _route(cfg):
    """OpenRouter serves a model from many providers; its default favours the cheapest, which
    can be slow or low precision (a 6-minute step). Precise providers at FAST_TPS or more go
    first, cheapest per agent step, then the rest by speed (as the Godot plugin does). The
    user's privacy settings at OpenRouter still filter them."""
    model = cfg["model"]
    if "openrouter.ai" not in cfg["base"] or "/" not in model:
        return []
    if model not in _ROUTES:
        _ROUTES[model] = []
        try:
            owner, name = model.split("/", 1)
            data = _request(cfg["base"], cfg["key"], f"/models/{owner}/{name}/endpoints", timeout=15)
            eps = (data.get("data") or {}).get("endpoints") or []
        except Exception:  # noqa: BLE001  (routing is an optimisation; never fail the chat)
            eps = []
        fast, rest = [], []
        for e in eps:
            if not isinstance(e, dict) or not e.get("tag"):
                continue
            pr = e.get("pricing") or {}
            inp = float(pr.get("prompt") or 0)
            cached = float(pr["input_cache_read"]) if pr.get("input_cache_read") is not None else inp
            step = 45000 * cached + 5000 * inp + 600 * float(pr.get("completion") or 0)
            tps = float((e.get("throughput_last_30m") or {}).get("p50") or 0)
            up = e.get("uptime_last_30m")
            low = str(e.get("quantization", "")) in ("fp4", "mxfp4", "fp6", "int4")
            if tps >= FAST_TPS and not low and (up is None or float(up) >= 95.0):
                fast.append((step, e["tag"]))
            else:
                rest.append((-tps, e["tag"]))
        _ROUTES[model] = [t for _, t in sorted(fast)] + [t for _, t in sorted(rest)]
        _ROUTES[model] = _ROUTES[model][:12]
    return _ROUTES[model]


def _chat(cfg, messages, emit, is_stopped):
    """One request. Overload and rate limits are retried; with fallbacks configured the
    run switches model instead of waiting (cfg["model"] keeps the switch for the run)."""
    fallbacks = cfg.setdefault("fallbacks", [])
    waits = list(RETRIES)
    quick = True  # one fast retry first: overload spikes are often momentary
    while True:
        payload = {"model": cfg["model"], "messages": messages, "tools": SCHEMAS,
                   "tool_choice": "auto"}
        order = _route(cfg)
        if order:
            payload["provider"] = {"order": order}
        try:
            data = _request(cfg["base"], cfg["key"], "/chat/completions", payload)
            break
        except urllib.error.HTTPError as e:
            text = _error_text(e)
            busy = e.code in RETRY_CODES
            if busy and quick:
                quick = False
                emit("status", f"{cfg['model']} is busy ({e.code}), retrying…")
                _wait(3, is_stopped)
                continue
            if (busy or e.code == 404) and fallbacks:
                cfg["model"] = fallbacks.pop(0)
                quick = True
                emit("status", f"Switching to {cfg['model']} (the model was busy or unavailable)…")
                continue
            if busy and waits:
                wait = waits.pop(0)
                try:
                    wait = max(1, min(120, int(e.headers.get("Retry-After", wait))))
                except (TypeError, ValueError):
                    pass
                emit("status", f"{cfg['model']} is busy ({e.code}), retrying in {wait}s…")
                _wait(wait, is_stopped)
                continue
            if e.code in (401, 403):
                text += "\nCheck the API key in the add-on preferences."
            elif e.code == 429:
                text += "\nThe rate limit is used up for now. Wait, or pick another model."
            elif e.code == 402:
                text += "\nThe account has no credit left. Top it up, or pick another model."
            elif busy:
                text += "\nThe provider is overloaded. Try again in a few minutes."
            elif e.code in (400, 404) and "tool" in text.lower():
                text += "\nThis model may not support tool calling. Pick another model."
            raise ApiError(text)
        except urllib.error.URLError as e:
            raise ApiError(f"Can't reach {cfg['base']}: {e.reason}")
        except (TimeoutError, OSError) as e:
            raise ApiError(f"Connection problem: {e}")
    choices = data.get("choices") or []
    if not choices:
        raise ApiError("The provider returned no answer: " + json.dumps(data)[:300])
    return choices[0].get("message") or {}, choices[0].get("finish_reason")


def _result_messages(call_id, result):
    """A tool result as API messages: the tool reply, plus an image message if any."""
    if "error" in result:
        return [{"role": "tool", "tool_call_id": call_id, "content": "Error: " + result["error"]}], []
    if "image" in result:
        img = {"role": "user", "content": [
            {"type": "text", "text": "Viewport screenshot. " + result.get("caption", "")},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + result["image"]}}]}
        return [{"role": "tool", "tool_call_id": call_id,
                 "content": "Screenshot taken; it follows as an image."}], [img]
    text = result.get("text", "")
    if len(text) > MAX_TOOL_TEXT:
        text = text[:MAX_TOOL_TEXT] + f"\n… [{len(text) - MAX_TOOL_TEXT} more characters cut]"
    return [{"role": "tool", "tool_call_id": call_id, "content": text}], []


def run(cfg, messages, emit, is_stopped):
    """Worker thread. `messages` is this run's own copy of the API history.
    emit(kind, value) reports progress: text, tool, status, error, done, and
    history (a copy of the messages whenever they are in a consistent state, so a
    stopped run never leaves tool calls without their results)."""
    try:
        nudged = False
        acted = False
        audits = 0
        problems = []
        held = ""  # a summary held back until the scene check has run
        limit = int(cfg.get("max_steps") or 0)
        for step in itertools.count():
            if is_stopped():
                return
            if limit and step >= limit:
                emit("error", f"Stopped after {limit} tool rounds (the Step limit preference). "
                              "Send a message to continue.")
                emit("done", None)
                return
            if limit > 3 and step == limit - 3:
                messages.append({"role": "user", "content": WRAP_UP})
            _prune_images(messages)
            msg, finish = _chat(cfg, messages, emit, is_stopped)
            if is_stopped():
                return
            calls = msg.get("tool_calls") or []
            if not calls and not (msg.get("content") or "").strip():
                # Nothing usable came back: never end the turn silently.
                if not nudged:
                    nudged = True
                    emit("status", "The model gave no answer; asking it again…")
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                why = "it ran out of reply length while thinking" if finish == "length" \
                    else "it returned an empty reply"
                emit("error", f"The model didn't answer ({why}). Try a shorter, more specific "
                              "request, or another model.")
                emit("done", None)
                return
            nudged = False
            # Keep tool calls exactly as received: some providers (Gemini) attach data
            # to them that must come back unchanged on the next request.
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                entry["tool_calls"] = calls
            if msg.get("reasoning_content"):  # DeepSeek's thinking must come back within a turn
                entry["reasoning_content"] = msg["reasoning_content"]
            messages.append(entry)
            acted = acted or bool(calls)
            if not calls and acted and (audits == 0 or problems) and audits < AUDIT_ROUNDS:
                # The model says it's done after changing the scene: check the scene
                # ourselves, and while problems remain let it fix them before the user
                # sees a summary.
                audits += 1
                held = msg.get("content") or held
                emit("tool", ("scene check by the add-on", None))
                audit = bridge.call("scene_audit", {})
                problems = audit.get("problems", [])
                messages.append({"role": "user", "content": AUDIT.format(
                    audit=audit.get("text") or audit.get("error", "unavailable"))})
                emit("history", list(messages))
                continue
            if msg.get("content"):
                emit("text", msg["content"])
            elif not calls and held:
                emit("text", held)
            if not calls:
                if acted and audits:
                    final = bridge.call("scene_audit", {}).get("problems", [])
                    if final:  # never let a tidy summary hide what the checks still find
                        emit("error", "The add-on's checks still find problems:\n" +
                             "\n".join("- " + p for p in final[:8]) +
                             (f"\n… and {len(final) - 8} more" if len(final) > 8 else ""))
                emit("history", list(messages))
                emit("done", None)
                return
            images = []
            for c in calls:
                fn = c.get("function") or {}
                # Some open models leak chat-format tokens into the name
                # ("execute_blender_code<|channel|>commentary"): keep the real name.
                name = fn.get("name", "").split("<|")[0].strip()
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = None
                emit("tool", (name, args))
                if not isinstance(args, dict):
                    result = {"error": "The arguments weren't valid JSON."}
                else:
                    args.pop("user_prompt", None)
                    result = bridge.call(name, args)
                tool_msgs, imgs = _result_messages(c.get("id", ""), result)
                messages.extend(tool_msgs)
                images += imgs
            messages.extend(images)  # after all tool replies, which must follow the call
            emit("history", list(messages))
    except ApiError as e:
        if str(e) != "stopped":
            emit("error", str(e))
        emit("done", None)
    except Exception as e:  # noqa: BLE001  (never let the thread die silently)
        emit("error", f"{type(e).__name__}: {e}")
        emit("done", None)
