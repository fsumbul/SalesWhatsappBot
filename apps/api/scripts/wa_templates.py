"""Standalone WhatsApp Cloud API admin script (no `src` imports, stdlib only).

Reads credentials from a .env file so it can run on the prod host with the
embedded Python runtime, where the app package/deps may not be importable.

Usage on the server (C:\\sites\\ashiraai):

    runtime\\python312-embed\\python.exe app\\scripts\\wa_templates.py --list
    runtime\\python312-embed\\python.exe app\\scripts\\wa_templates.py \\
        --to +905464867775 --template <name> --lang tr [--params "A" "B"]

Locally: python apps/api/scripts/wa_templates.py --list --env-file apps/api/.env

Secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

REQUIRED = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_BUSINESS_ACCOUNT_ID")


def graph_base(env: dict[str, str]) -> str:
    version = env.get("WHATSAPP_GRAPH_API_VERSION", "v20.0")
    if not version.startswith("v") or not version[1:].replace(".", "", 1).isdigit():
        raise SystemExit("WHATSAPP_GRAPH_API_VERSION must look like v20.0")
    return f"https://graph.facebook.com/{version}"


def load_env(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        raise SystemExit(f"env file not found: {path}")
    env: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise SystemExit(f"missing/empty in {path}: {', '.join(missing)}")
    return env


def _request(url: str, token: str, payload: dict | None = None) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "graph.facebook.com":
        raise SystemExit("refusing non-Meta or non-HTTPS Graph API URL")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(  # noqa: S310 - URL constrained above
        url, data=data, method="POST" if data else "GET"
    )
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - URL constrained
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"Graph API {exc.code}:\n{body}") from exc


def cmd_list(env: dict[str, str]) -> None:
    url = f"{graph_base(env)}/{env['WHATSAPP_BUSINESS_ACCOUNT_ID']}" "/message_templates?limit=100"
    data = _request(url, env["WHATSAPP_ACCESS_TOKEN"]).get("data", [])
    if not data:
        print("No templates found on this WABA.")
        return
    print(f"{'STATUS':<10} {'NAME':<35} {'LANG':<6} {'CATEGORY':<12} VARS")
    for t in data:
        body = next((c for c in t.get("components", []) if c.get("type", "").upper() == "BODY"), {})
        text = body.get("text", "")
        n_vars = len({p for p in text.split("{{") if p[:1].isdigit()})
        print(
            f"{t.get('status', ''):<10} {t.get('name', ''):<35} "
            f"{t.get('language', ''):<6} {t.get('category', ''):<12} {n_vars}"
        )


def cmd_diagnose(env: dict[str, str]) -> None:
    """Read-only health check: template quality, phone number rating, WABA status."""
    token = env["WHATSAPP_ACCESS_TOKEN"]
    waba = env["WHATSAPP_BUSINESS_ACCOUNT_ID"]
    pnid = env["WHATSAPP_PHONE_NUMBER_ID"]
    graph = graph_base(env)

    print("=== TEMPLATES (detail) ===")
    fields = "name,status,category,language,quality_score,rejected_reason,components"
    tpls = _request(f"{graph}/{waba}/message_templates?limit=100&fields={fields}", token)
    for t in tpls.get("data", []):
        qs = t.get("quality_score") or {}
        print(f"  {t.get('name')} [{t.get('language')}]")
        print(f"     status={t.get('status')}  category={t.get('category')}")
        print(
            f"     quality={qs.get('score', 'n/a')}  rejected_reason={t.get('rejected_reason', '-')}"
        )
        for c in t.get("components", []):
            if c.get("type", "").upper() == "BODY":
                print(f"     body={c.get('text', '')[:160]!r}")

    print("=== PHONE NUMBER ===")
    pf = "verified_name,display_phone_number,quality_rating,messaging_limit_tier,status,name_status"
    ph = _request(f"{graph}/{pnid}?fields={pf}", token)
    for k in pf.split(","):
        print(f"  {k} = {ph.get(k, 'n/a')}")

    print("=== WABA ===")
    wf = "name,account_review_status,business_verification_status,message_template_namespace"
    wa = _request(f"{graph}/{waba}?fields={wf}", token)
    for k in wf.split(","):
        print(f"  {k} = {wa.get(k, 'n/a')}")


def cmd_webhook(env: dict[str, str]) -> None:
    """Read-only: which app (if any) is subscribed to this WABA's webhooks."""
    token = env["WHATSAPP_ACCESS_TOKEN"]
    waba = env["WHATSAPP_BUSINESS_ACCOUNT_ID"]
    print("=== SUBSCRIBED APPS ===")
    subs = _request(f"{graph_base(env)}/{waba}/subscribed_apps", token).get("data", [])
    if not subs:
        print("  NONE — no app subscribed; Meta sends no status/message callbacks at all.")
    for s in subs:
        app = s.get("whatsapp_business_api_data", {})
        print(f"  app_id={app.get('id')}  name={app.get('name')}  link={app.get('link', '-')}")


def cmd_check_callback(env: dict[str, str], url: str) -> None:
    """Simulate Meta's webhook verification handshake against the public URL."""
    token = env.get("WHATSAPP_VERIFY_TOKEN", "")
    if not token:
        raise SystemExit("WHATSAPP_VERIFY_TOKEN is empty in .env")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("callback URL must be an absolute HTTPS URL")
    challenge = "1234567890"
    q = urllib.parse.urlencode(
        {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": challenge}
    )
    full = f"{url}?{q}"
    print(f"GET {url}?hub.mode=subscribe&hub.verify_token=<hidden>&hub.challenge={challenge}")
    try:
        with urllib.request.urlopen(full, timeout=20) as resp:  # noqa: S310 - HTTPS checked
            body = resp.read().decode().strip()
            print(f"  HTTP {resp.status}  body={body!r}")
            if body.strip('"') == challenge:
                print("  OK - handshake would succeed in Meta's Configuration screen.")
            else:
                print("  MISMATCH - endpoint reachable but did not echo the challenge.")
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code}  body={exc.read().decode(errors='replace')[:300]!r}")
        print("  Handshake would FAIL.")
    except Exception as exc:
        print(f"  UNREACHABLE: {exc}")


def cmd_text(env: dict[str, str], to: str, body: str) -> None:
    """Free-form session message. Only works inside a 24h customer-service window."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }
    url = f"{graph_base(env)}/{env['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    print(json.dumps(_request(url, env["WHATSAPP_ACCESS_TOKEN"], payload), indent=2))


def cmd_subscribe(env: dict[str, str]) -> None:
    """Subscribe this app to the WABA's webhooks (POST /{waba}/subscribed_apps)."""
    token = env["WHATSAPP_ACCESS_TOKEN"]
    waba = env["WHATSAPP_BUSINESS_ACCOUNT_ID"]
    graph = graph_base(env)
    print(f"POST {graph}/{waba}/subscribed_apps")
    print(json.dumps(_request(f"{graph}/{waba}/subscribed_apps", token, {}), indent=2))
    print("--- verifying ---")
    cmd_webhook(env)


def cmd_send(env: dict[str, str], to: str, name: str, lang: str, params: list[str]) -> None:
    components = []
    if params:
        components.append(
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang},
            "components": components,
        },
    }
    url = f"{graph_base(env)}/{env['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    print(json.dumps(_request(url, env["WHATSAPP_ACCESS_TOKEN"], payload), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=DEFAULT_ENV)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="template/number/WABA health check")
    ap.add_argument("--webhook", action="store_true", help="show webhook app subscription")
    ap.add_argument("--subscribe", action="store_true", help="subscribe this app to the WABA")
    ap.add_argument("--check-callback", metavar="URL", help="test the webhook verify handshake")
    ap.add_argument("--to")
    ap.add_argument("--text", help="send a free-form session message instead of a template")
    ap.add_argument("--template")
    ap.add_argument("--lang", default="tr")
    ap.add_argument("--params", nargs="*", default=[])
    args = ap.parse_args()

    env = load_env(args.env_file)
    if args.list:
        cmd_list(env)
        return
    if args.diagnose:
        cmd_diagnose(env)
        return
    if args.webhook:
        cmd_webhook(env)
        return
    if args.subscribe:
        cmd_subscribe(env)
        return
    if args.check_callback:
        cmd_check_callback(env, args.check_callback)
        return
    if args.to and args.text:
        cmd_text(env, args.to, args.text)
        return
    if not (args.to and args.template):
        ap.error("--to and --template are required unless --list is used")
    cmd_send(env, args.to, args.template, args.lang, args.params)


if __name__ == "__main__":
    main()
