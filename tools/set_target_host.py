#!/usr/bin/env python3
"""
Point every load test artefact at a freshly deployed app.

`azd up` creates an App Service with a generated name such as
`app-contosobank-a1b2c3.azurewebsites.net`, but the load test configs, the JMX
plan, the Locust config and the URL-based test all ship with the placeholder
hostname `contoso-bank-api.azurewebsites.net`.

Running a test against the placeholder is the single most common way to lose
ten minutes at the start of a demo: the test runs, every request fails with a
DNS or 404 error, and the cause is not obvious from the results dashboard.

Usage
-----
    # After `azd up`:
    python tools/set_target_host.py app-contosobank-a1b2c3.azurewebsites.net

    # Or read it straight out of the azd environment:
    python tools/set_target_host.py --from-azd

    # See what would change without writing anything:
    python tools/set_target_host.py --from-azd --dry-run

    # Put the placeholder back (e.g. before committing):
    python tools/set_target_host.py --reset

The script is idempotent and only ever rewrites the hostname, so it is safe to
run repeatedly and safe to run against files you have edited by hand.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PLACEHOLDER = "contoso-bank-api.azurewebsites.net"

# Paths are relative to the repository root.
TARGET_FILES = [
    "loadtests/config/pr-gate.yaml",
    "loadtests/config/peak-load.yaml",
    "loadtests/config/peak-load-locust.yaml",
    "loadtests/config/secrets-demo.yaml",
    "loadtests/config/secrets-demo-locust.yaml",
    "loadtests/locust/locust.conf",
    "loadtests/locust/secrets.conf",
    "loadtests/url-tests/contoso-bank-basic.json",
    "loadtests/url-tests/contoso-bank-smoke.json",
    "loadtests/url-tests/contoso-bank-curl.json",
    "loadtests/url-tests/contoso-bank-correlated.json",
    "loadtests/url-tests/contoso-bank-false-green.json",
    "loadtests/url-tests/contoso-bank-csv.json",
]

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$", re.IGNORECASE)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def normalise(host: str) -> str:
    """Accept a full URL or a bare hostname; always return a bare hostname."""
    host = host.strip()
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE)
    return host.rstrip("/")


def host_from_azd() -> str:
    """Read SERVICE_API_HOSTNAME out of the current azd environment."""
    try:
        out = subprocess.run(
            ["azd", "env", "get-values"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root(),
        ).stdout
    except FileNotFoundError:
        sys.exit("azd is not on PATH. Pass the hostname explicitly instead.")
    except subprocess.CalledProcessError as exc:
        sys.exit(
            "Could not read the azd environment. Have you run `azd up`?\n"
            f"{exc.stderr.strip()}"
        )

    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')

    for key in ("SERVICE_API_HOSTNAME", "SERVICE_API_URI"):
        if values.get(key):
            return normalise(values[key])

    sys.exit(
        "No SERVICE_API_HOSTNAME in the azd environment.\n"
        "Run `azd up` first, or pass the hostname explicitly."
    )


def apply(old: str, new: str, dry_run: bool) -> int:
    root = repo_root()
    changed_files = 0
    total_hits = 0

    for rel in TARGET_FILES:
        path = root / rel
        if not path.exists():
            print(f"  ?  {rel}  (not found, skipped)")
            continue

        # newline="" on both read and write preserves the file's existing line
        # endings exactly, so this never shows up as a whole-file diff in git.
        with path.open("r", encoding="utf-8", newline="") as fh:
            original = fh.read()
        hits = original.count(old)

        if hits == 0:
            print(f"  =  {rel}")
            continue

        if not dry_run:
            with path.open("w", encoding="utf-8", newline="") as fh:
                fh.write(original.replace(old, new))

        verb = "would update" if dry_run else "updated"
        print(f"  ->  {rel}  ({hits} occurrence{'s' if hits != 1 else ''} {verb})")
        changed_files += 1
        total_hits += hits

    print()
    if total_hits == 0:
        print(f"Nothing to do - no occurrences of '{old}' found.")
        print("The files may already point at your target host.")
    else:
        action = "Would rewrite" if dry_run else "Rewrote"
        print(f"{action} {total_hits} occurrence(s) across {changed_files} file(s).")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Point the load test artefacts at a deployed App Service.",
    )
    p.add_argument(
        "hostname",
        nargs="?",
        help="Target hostname, e.g. app-contosobank-a1b2c3.azurewebsites.net "
        "(a full https:// URL is also accepted).",
    )
    p.add_argument(
        "--from-azd",
        action="store_true",
        help="Read the hostname from `azd env get-values`.",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Restore the placeholder hostname.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing.",
    )
    args = p.parse_args()

    if args.reset:
        if args.hostname or args.from_azd:
            p.error("--reset cannot be combined with a hostname or --from-azd.")
        # Find whatever host is currently in place and swap it back.
        current = detect_current_host()
        if current is None:
            print("No *.azurewebsites.net hostname found in the configs.")
            print("Nothing to reset - check the files by hand.")
            return 0
        if current == PLACEHOLDER:
            print(f"Already reset - '{PLACEHOLDER}' is in place.")
            return 0
        print(f"Restoring placeholder: {current} -> {PLACEHOLDER}\n")
        return apply(current, PLACEHOLDER, args.dry_run)

    if args.from_azd:
        new_host = host_from_azd()
    elif args.hostname:
        new_host = normalise(args.hostname)
    else:
        p.error("Provide a hostname, or use --from-azd, or --reset.")

    if not HOSTNAME_RE.match(new_host):
        p.error(
            f"'{new_host}' does not look like a hostname. "
            "Expected something like app-contosobank-a1b2c3.azurewebsites.net"
        )

    current = detect_current_host() or PLACEHOLDER
    if current == new_host:
        print(f"Already pointing at {new_host}. Nothing to do.")
        return 0

    print(f"Retargeting: {current} -> {new_host}\n")
    rc = apply(current, new_host, args.dry_run)

    if not args.dry_run and rc == 0:
        print()
        print("Next: verify the app is actually up before spending virtual user hours:")
        print(f"  curl https://{new_host}/health")
    return rc


def detect_current_host() -> str | None:
    """Find the *.azurewebsites.net host currently referenced by the configs."""
    root = repo_root()
    found: set[str] = set()
    pattern = re.compile(r"\b([a-z0-9][a-z0-9-]*\.azurewebsites\.net)\b", re.IGNORECASE)

    for rel in TARGET_FILES:
        path = root / rel
        if path.exists():
            found.update(pattern.findall(path.read_text(encoding="utf-8")))

    if not found:
        return None
    if len(found) > 1:
        # Mixed state - most likely a partially-applied previous run.
        print(
            "Warning: the configs reference more than one host "
            f"({', '.join(sorted(found))}).",
            file=sys.stderr,
        )
        print(
            "Pass the old hostname explicitly, or edit the files by hand.",
            file=sys.stderr,
        )
        # Prefer the placeholder if it is one of them, so --reset is a no-op.
        return PLACEHOLDER if PLACEHOLDER in found else sorted(found)[0]
    return found.pop()


if __name__ == "__main__":
    raise SystemExit(main())
