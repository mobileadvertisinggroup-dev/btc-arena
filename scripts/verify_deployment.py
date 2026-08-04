"""POST-DEPLOYMENT VERIFICATION (Mentor Ruling 014.6) — read-only, offline.

Run ON the server after any deployment. Proves the installed service and the
effective Nginx configuration correspond to the audited versions, and that
the runtime environment matches the pinned lock. Makes no network requests,
no model calls, no writes; prints PASS/FAIL per check and exits non-zero on
any failure. Never prints secrets (it never reads the env file's values).

usage: venv/bin/python scripts/verify_deployment.py [--engine-digest <64hex>]
With --engine-digest, the current tree is additionally verified against the
externally issued approved digest.
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

INSTALLED_UNIT = "/etc/systemd/system/arena-official.service"
NGINX_SITE = "/etc/nginx/sites-enabled/arena"

# Directives that MUST survive in the effective nginx config even after
# certbot rewrites the server block (content check, not byte equality):
REQUIRED_NGINX_LINES = [
    "server_name live.akraarena.online;",
    "root /var/www/arena;",
    "location ~ ^/(live_payload\\.js|live_payload\\.sha256|health\\.json)$",
    "limit_except GET { deny all; }",
    'add_header Access-Control-Allow-Origin "*" always;',
    'add_header Cache-Control "no-store, must-revalidate" always;',
]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def arg(name):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def check(results, name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail
                                                   else ""))


def main():
    from engine import config, official
    results = []

    # 1. tree digests reproduce (and optionally match the issued digest)
    man = config.build_manifest()["combined"]
    issued = (arg("--engine-digest") or "").strip().lower()
    if issued:
        check(results, "engine digest matches externally issued value",
              man == issued, man[:16] + "…")
    else:
        print(f"INFO  current engine digest: {man}")
        print(f"INFO  current site digest:   "
              f"{config.build_site_manifest()['combined']}")

    # 2. installed systemd unit is byte-identical to the audited template
    tmpl = os.path.join(ROOT, "deploy", "arena-official.service")
    ok = os.path.exists(INSTALLED_UNIT) and sha(INSTALLED_UNIT) == sha(tmpl)
    check(results, "installed systemd unit == audited template", ok,
          INSTALLED_UNIT)

    # 3. effective nginx config carries every audited directive
    try:
        eff = subprocess.run(["nginx", "-T"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        eff = open(NGINX_SITE).read() if os.path.exists(NGINX_SITE) else ""
    for line in REQUIRED_NGINX_LINES:
        check(results, f"nginx effective config contains: {line[:52]}",
              line in eff)

    # 4. running under the audited venv python with the pinned packages
    venv_py = os.path.join(ROOT, "venv", "bin", "python")
    check(results, "executed by the audited venv python",
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
        check(results, f"pinned {pkg}=={want}", have == want, f"have {have}")

    # 5. canonical endpoint constant is the frozen hostname
    check(results, "canonical endpoint constant",
          official.OFFICIAL_PAYLOAD_URL
          == "https://live.akraarena.online/live_payload.js")

    n_fail = results.count(False)
    print(f"\nDEPLOYMENT VERIFICATION: "
          f"{'PASS' if n_fail == 0 else f'FAIL ({n_fail} checks failed)'}")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
