"""POST-DEPLOYMENT VERIFICATION (Mentor Rulings 014.6 + 015.5) — read-only.

Run ON the server after any deployment. Proves the installed service and the
EFFECTIVE Nginx configuration correspond to the audited versions and expose
nothing beyond the three approved read-only files, and that the runtime
environment matches the pinned lock. Prints PASS/FAIL per check and exits
non-zero on any failure. Never prints secrets (it never reads env values).

Nginx policy (015.5): the exact live.akraarena.online server block(s) are
extracted from `nginx -T` and must
  * contain ONLY the two approved location blocks — `location /` (404 only)
    and the approved three-file regex location (GET-only, CORS, no-store);
  * contain NO proxy_pass/fastcgi_pass/uwsgi_pass/scgi_pass/grpc_pass,
    no dav_methods, no other location, no upload/write surface;
  * serve from root /var/www/arena.
Effective HTTPS is verified live: https://live.akraarena.online/health.json
answers 200 and plain http redirects (301/308) to https.

usage: venv/bin/python scripts/verify_deployment.py \
           --engine-digest <64hex> --site-digest <64hex> [--skip-https]
Both externally issued digests are REQUIRED (Ruling 016.6).
"""
import hashlib
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

INSTALLED_UNIT = "/etc/systemd/system/arena-official.service"
HOSTNAME = "live.akraarena.online"

APPROVED_LOCATION_RE = (
    r"^/(live_payload\.js|live_payload\.sha256|health\.json)$")
FORBIDDEN_DIRECTIVES = ("proxy_pass", "fastcgi_pass", "uwsgi_pass",
                        "scgi_pass", "grpc_pass", "dav_methods", "alias",
                        "autoindex", "rewrite", "client_body_in_file_only",
                        "upload_store")
REQUIRED_IN_PAYLOAD_LOCATION = (
    "limit_except GET { deny all; }",
    'add_header Access-Control-Allow-Origin "*" always;',
    'add_header Cache-Control "no-store, must-revalidate" always;',
)
# certbot's managed HTTP->HTTPS redirect, the ONLY if-block allowed anywhere
CERTBOT_REDIRECT_IF = re.compile(
    r"if\s*\(\s*\$host\s*=\s*" + re.escape(HOSTNAME)
    + r"\s*\)\s*\{\s*return\s+301\s+https://\$host\$request_uri;\s*\}")


def _blocks(text, keyword):
    """Yield the full text of `keyword ... { ... }` blocks, brace-matched."""
    for m in re.finditer(rf"(^|\n)\s*{keyword}\b[^{{;]*{{", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        yield text[m.start():i]


def _strip_comments(text):
    return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())


def _server_level_returns(block):
    """`return` statements at SERVER level (outside location/if blocks)."""
    inner = block.split("{", 1)[1].rsplit("}", 1)[0]
    for sub in list(_blocks(inner, "location")) + list(_blocks(inner, "if")):
        inner = inner.replace(sub, "")
    return re.findall(r"return\s+[^;]+;", inner)


def analyze_nginx(effective_text):
    """Pure analysis of the effective nginx config (Rulings 015.5 + 016.6),
    understanding the normal certbot layout: EXACTLY one HTTPS content block
    for the frozen hostname exposing only the three approved files, and
    EXACTLY one HTTP block doing nothing but the exact HTTPS redirect.
    Returns (check_name, ok, detail) tuples."""
    out = []
    text = _strip_comments(effective_text)
    servers = [b for b in _blocks(text, "server")
               if re.search(rf"server_name\s+[^;]*\b{re.escape(HOSTNAME)}\b",
                            b)]
    https = [b for b in servers if re.search(r"listen\s+[^;]*443", b)]
    http = [b for b in servers if b not in https]
    out.append((f"exactly one HTTPS content block for {HOSTNAME}",
                len(https) == 1, f"{len(https)} found"))
    out.append((f"exactly one HTTP redirect block for {HOSTNAME}",
                len(http) == 1, f"{len(http)} found"))

    for b in servers:
        for d in FORBIDDEN_DIRECTIVES:
            out.append((f"no {d} in {HOSTNAME} blocks",
                        not re.search(rf"\b{d}\b", b), ""))

    if https:
        b = https[0]
        # server-level returns are forbidden in the content block
        rets = _server_level_returns(b)
        out.append(("no server-level return in the HTTPS block", not rets,
                    "; ".join(rets)[:60]))
        out.append(("no if-blocks in the HTTPS block",
                    not list(_blocks(b.split("{", 1)[1], "if")), ""))
        out.append(("root /var/www/arena in the HTTPS block",
                    "root /var/www/arena;" in b, ""))
        payload_loc_seen = False
        for loc in _blocks(b.split("{", 1)[1], "location"):
            header = loc.split("{", 1)[0].strip()
            if re.fullmatch(r"location\s+/", header):
                body = loc.split("{", 1)[1].rsplit("}", 1)[0]
                ok = re.fullmatch(r"\s*return\s+404;\s*", body) is not None
                out.append(("location / returns 404 and nothing else",
                            bool(ok), ""))
            elif APPROVED_LOCATION_RE in loc:
                payload_loc_seen = True
                for req in REQUIRED_IN_PAYLOAD_LOCATION:
                    out.append((f"payload location has: {req[:44]}",
                                req in loc, ""))
            else:
                out.append(("no unexpected location block", False,
                            header[:60]))
        out.append(("approved three-file payload location present",
                    payload_loc_seen, ""))

    if http:
        b = http[0]
        body = b.split("{", 1)[1].rsplit("}", 1)[0]
        ifs = list(_blocks(body, "if"))
        redirect_ok = len(ifs) == 1 and bool(CERTBOT_REDIRECT_IF.search(
            re.sub(r"\s+", " ", ifs[0])))
        out.append(("HTTP block contains exactly the approved HTTPS "
                    "redirect", redirect_ok, ""))
        out.append(("no location blocks in the HTTP redirect block",
                    not list(_blocks(body, "location")), ""))
        residue = body
        for sub in ifs:
            residue = residue.replace(sub, "")
        stmts = [s.strip() for s in residue.split(";") if s.strip()]
        allowed = all(s.startswith(("listen", "server_name", "return 404"))
                      for s in stmts)
        out.append(("HTTP block does nothing else (listen/server_name/"
                    "return 404 only)", allowed,
                    "; ".join(s for s in stmts
                              if not s.startswith(("listen", "server_name",
                                                   "return 404")))[:60]))
    return out


def check_time_sync(run=None):
    """Mentor Ruling 019.5: every official deadline anchors to the VPS UTC
    clock — NTP synchronization must be active. `run` is injectable for
    tests; default queries systemd-timedated."""
    if run is None:
        def run():
            return subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        out = run()
    except Exception as e:
        out = f"error: {e}"
    return [("system clock is NTP-synchronized", out == "yes",
             f"NTPSynchronized={out!r}")]


def check_https(fetch=None):
    """(name, ok, detail) checks for effective HTTPS + redirect. `fetch` is
    injectable for tests; default uses urllib against the live host."""
    if fetch is None:
        def fetch(url):
            req = urllib.request.Request(url, method="GET")
            # never follow redirects: we want to SEE the 301
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(req, timeout=15) as r:
                    return r.status, dict(r.headers)
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers)
    out = []
    code, _ = fetch(f"https://{HOSTNAME}/health.json")
    out.append((f"https://{HOSTNAME}/health.json answers 200", code == 200,
                f"got {code}"))
    code, headers = fetch(f"http://{HOSTNAME}/health.json")
    loc = (headers or {}).get("Location", "")
    out.append(("http redirects to https",
                code in (301, 308) and loc.startswith(f"https://{HOSTNAME}/"),
                f"got {code} -> {loc[:60]}"))
    return out


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def arg(name):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    from engine import config, official
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}"
              + (f"  ({detail})" if detail else ""))

    # 1. BOTH externally issued digests are REQUIRED (Ruling 016.6) and the
    # deployed tree must match them exactly.
    issued_eng = (arg("--engine-digest") or "").strip().lower()
    issued_site = (arg("--site-digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", issued_eng) \
            or not re.fullmatch(r"[0-9a-f]{64}", issued_site):
        print("REFUSED: --engine-digest and --site-digest (both externally "
              "issued 64-hex values) are required.")
        sys.exit(2)
    man = config.build_manifest()["combined"]
    site = config.build_site_manifest()["combined"]
    check("engine digest matches externally issued value", man == issued_eng,
          man[:16] + "…")
    check("site digest matches externally issued value", site == issued_site,
          site[:16] + "…")

    # 2. installed systemd unit byte-identical to the audited template
    tmpl = os.path.join(ROOT, "deploy", "arena-official.service")
    ok = os.path.exists(INSTALLED_UNIT) and sha(INSTALLED_UNIT) == sha(tmpl)
    check("installed systemd unit == audited template", ok, INSTALLED_UNIT)

    # 3. effective nginx config: approved-only exposure
    try:
        eff = subprocess.run(["nginx", "-T"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception as e:
        eff = ""
        check("nginx -T readable", False, str(e)[:80])
    for name, ok, detail in analyze_nginx(eff):
        check(name, ok, detail)

    # 4. effective HTTPS + redirect for the frozen hostname
    if "--skip-https" not in sys.argv:
        try:
            for name, ok, detail in check_https():
                check(name, ok, detail)
        except Exception as e:
            check("effective https reachable", False, str(e)[:80])

    # 4b. NTP synchronization (Ruling 019.5) — deadline anchor integrity
    for name, ok, detail in check_time_sync():
        check(name, ok, detail)

    # 5. audited venv python + pinned packages
    venv_py = os.path.join(ROOT, "venv", "bin", "python")
    check("executed by the audited venv python",
          os.path.realpath(sys.executable) == os.path.realpath(venv_py),
          sys.executable)
    import importlib.metadata as md
    for line in open(os.path.join(ROOT, "deploy",
                                  "requirements-official.txt")):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg, want = line.split("==")
        try:
            have = md.version(pkg)
        except md.PackageNotFoundError:
            have = "MISSING"
        check(f"pinned {pkg}=={want}", have == want, f"have {have}")

    # 6. canonical endpoint constant is the frozen hostname
    check("canonical endpoint constant",
          official.OFFICIAL_PAYLOAD_URL
          == f"https://{HOSTNAME}/live_payload.js")

    n_fail = results.count(False)
    print(f"\nDEPLOYMENT VERIFICATION: "
          f"{'PASS' if n_fail == 0 else f'FAIL ({n_fail} checks failed)'}")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
