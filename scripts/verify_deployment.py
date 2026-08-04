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

usage: venv/bin/python scripts/verify_deployment.py [--engine-digest <64hex>]
                                                    [--skip-https]
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
                        "scgi_pass", "grpc_pass", "dav_methods")
REQUIRED_IN_PAYLOAD_LOCATION = (
    "limit_except GET { deny all; }",
    'add_header Access-Control-Allow-Origin "*" always;',
    'add_header Cache-Control "no-store, must-revalidate" always;',
)


def _blocks(text, keyword):
    """Yield the full text of `keyword ... { ... }` blocks, brace-matched."""
    for m in re.finditer(rf"(^|\n)\s*{keyword}\b[^{{]*{{", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        yield text[m.start():i]


def analyze_nginx(effective_text):
    """Pure analysis of the effective nginx config (Ruling 015.5). Returns a
    list of (check_name, ok, detail) tuples for the frozen hostname's server
    blocks."""
    out = []
    servers = [b for b in _blocks(effective_text, "server")
               if re.search(rf"server_name\s+[^;]*\b{re.escape(HOSTNAME)}\b",
                            b)]
    out.append((f"server block(s) for {HOSTNAME} present", bool(servers),
                f"{len(servers)} found"))
    payload_loc_seen = False
    for b in servers:
        for d in FORBIDDEN_DIRECTIVES:
            out.append((f"no {d} in {HOSTNAME} block",
                        not re.search(rf"\b{d}\b", b), ""))
        locations = list(_blocks(b, "location"))
        for loc in locations:
            header = loc.split("{", 1)[0].strip()
            if re.fullmatch(r"location\s+/", header):
                body = loc.split("{", 1)[1].rsplit("}", 1)[0]
                ok = re.fullmatch(r"\s*return\s+404;\s*", body) is not None
                out.append(("location / returns 404 and nothing else",
                            bool(ok), header))
            elif APPROVED_LOCATION_RE in loc.replace("\\\\", "\\"):
                payload_loc_seen = True
                for req in REQUIRED_IN_PAYLOAD_LOCATION:
                    out.append((f"payload location has: {req[:44]}",
                                req in loc, ""))
            else:
                out.append(("no unexpected location block", False, header))
        out.append(("root /var/www/arena in server block",
                    "root /var/www/arena;" in b, ""))
    out.append(("approved three-file payload location present",
                payload_loc_seen, APPROVED_LOCATION_RE))
    return out


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

    # 1. tree digests reproduce (and optionally match the issued digest)
    man = config.build_manifest()["combined"]
    issued = (arg("--engine-digest") or "").strip().lower()
    if issued:
        check("engine digest matches externally issued value", man == issued,
              man[:16] + "…")
    else:
        print(f"INFO  current engine digest: {man}")
        print(f"INFO  current site digest:   "
              f"{config.build_site_manifest()['combined']}")

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
