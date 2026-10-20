"""Dependency-free localhost UI. Start with: python -m tools.cli_demo"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .commands import (
    ACTION_BY_ID, ROOT, Command, Config, build_commands, csv_export, metric_rows,
    parse_test_list_tsv, public_actions, redact, render_test_config, run_verdict, script_for, tsv_export,
)

LOG = logging.getLogger("cli_demo")
STATIC = Path(__file__).parent / "static"
DEFAULT_PORT = 8769
MAX_BODY = 32 * 1024
MAX_PLANS = 256
PLAN_LIFETIME = 30 * 60
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
TERMINAL = {"DONE", "FAILED", "CANCELLED", "CANCELED", "STOPPED", "AUTOSTOPPED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def azure_launcher() -> list[str]:
    executable = shutil.which("az")
    if not executable:
        raise FileNotFoundError("Azure CLI is not installed or not on PATH. Install it, then restart this app.")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat"}:
        python = path.parent.parent / "python.exe"
        if not python.is_file():
            raise FileNotFoundError(
                "Cannot find Azure CLI's bundled python.exe next to its Windows launcher. "
                "Repair the Azure CLI MSI installation; this app will not invoke cmd.exe."
            )
        # Bypass az.cmd: passing user-controlled arguments to a batch file can invoke cmd parsing.
        return [str(python), "-IBm", "azure.cli"]
    return [str(path)]


class CliRunner:
    def __call__(self, command: Command) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, AZURE_EXTENSION_USE_DYNAMIC_INSTALL="no",
                   AZURE_CORE_NO_COLOR="true", AZURE_CORE_COLLECT_TELEMETRY="no",
                   PYTHONIOENCODING="utf-8")
        launcher = azure_launcher()

        def invoke(args: tuple[str, ...] | list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*launcher, *args], cwd=ROOT, env=env, shell=False,
                input="", capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )

        if command.args[0] == "load":
            selected = command.args[command.args.index("--subscription") + 1]
            current = invoke(["account", "show", "--query", "id", "--output", "json", "--only-show-errors"], 45)
            if current.returncode:
                raise ValueError("Could not read the active CLI context. Run az login in a terminal. "
                                 + str(redact(current.stderr or ""))[-2000:])
            try:
                active = json.loads(current.stdout)
            except json.JSONDecodeError as exc:
                raise ValueError("The active CLI subscription could not be determined; no load command was run.") from exc
            if not isinstance(active, str) or active.lower() != selected.lower():
                raise ValueError(
                    f"CLI context mismatch: configured subscription {selected}, active subscription {active}. "
                    "The load extension can ignore --subscription for data-plane operations. "
                    "Use the confirmed Select CLI subscription step, or start this app with a separately "
                    "authenticated AZURE_CONFIG_DIR. No load command was run."
                )
        return invoke(command.args, 600 if command.download else 180)


@dataclass
class Plan:
    id: str
    action: str
    config: Config
    commands: list[Command]
    directory: Path
    test_config: dict[str, str] | None = None
    created: float = field(default_factory=time.monotonic)
    started: bool = False

    def public(self) -> dict[str, Any]:
        action = ACTION_BY_ID[self.action]
        note = ""
        if self.action == "collect-evidence":
            note = ("Server metric values are included."
                    if self.config.serverResourceId and self.config.serverMetric else
                    "Server metric values are NOT included. Configure an application resource, "
                    "a server metric, and the run timestamps to include them.")
        return {"id": self.id, "action": self.action, "label": action.label,
                "description": action.description, "confirmation": action.confirmation,
                "commands": [command.public() for command in self.commands],
                "testConfig": self.test_config,
                "artifactDirectory": str(self.directory), "note": note}


class App:
    def __init__(self, data_dir: Path, runner: Callable[[Command], subprocess.CompletedProcess[str]] | None = None):
        self.data_dir = data_dir.resolve()
        self.artifacts = self.data_dir / "artifacts"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.runner = runner or CliRunner()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.plans: dict[str, Plan] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="azure-cli")

    def preview(self, action: str, raw_config: Any) -> dict[str, Any]:
        config = Config.parse(raw_config)
        id_ = uuid.uuid4().hex
        directory = self.artifacts / id_
        commands = build_commands(action, config, directory)
        with self.lock:
            expired = [key for key, item in self.plans.items()
                       if time.monotonic() - item.created > PLAN_LIFETIME]
            for key in expired:
                del self.plans[key]
            if len(self.plans) >= MAX_PLANS:
                completed = [key for key in self.plans
                             if key in self.jobs and self.jobs[key]["state"] in {"succeeded", "failed"}]
                if not completed:
                    raise ValueError("Too many open previews. Wait for them to expire or restart the app.")
                del self.plans[completed[0]]
            test_config = None
            if action == "create-test":
                template = ROOT / "loadtests" / "cli-demo" / "smoke.yaml"
                try:
                    content = template.read_text(encoding="utf-8")
                except OSError as exc:
                    raise ValueError(f"Could not read the demo YAML configuration ({template}): {exc}") from exc
                content = render_test_config(content, config)
                args = commands[0].args
                config_file = Path(args[args.index("--load-test-config-file") + 1])
                if not config_file.is_absolute():
                    config_file = ROOT / config_file
                try:
                    config_file.parent.mkdir(parents=True)
                    shutil.copyfile(template.with_suffix(".jmx"), config_file.with_suffix(".jmx"))
                    config_file.write_text(content, encoding="utf-8")
                except OSError as exc:
                    raise ValueError(f"Could not prepare the demo YAML and JMX files ({config_file}): {exc}") from exc
                test_config = {"path": args[args.index("--load-test-config-file") + 1],
                               "sourcePath": str(template.relative_to(ROOT)), "content": content}
            plan = Plan(id_, action, config, commands, directory, test_config=test_config)
            self.plans[id_] = plan
        return plan.public()

    def get_plan(self, id_: str) -> Plan:
        with self.lock:
            plan = self.plans.get(id_)
            if not plan or time.monotonic() - plan.created > PLAN_LIFETIME:
                raise ValueError("Command preview expired. Preview the command again.")
            return plan

    def start(self, id_: str, confirmed: bool) -> dict[str, Any]:
        with self.lock:
            plan = self.get_plan(id_)
            if plan.started:
                raise ValueError("This preview was already executed. Preview again to avoid duplicate runs.")
            if ACTION_BY_ID[plan.action].confirmation and confirmed is not True:
                raise ValueError("Confirm the target and operation before executing this command.")
            if sum(job["state"] in {"queued", "running"} for job in self.jobs.values()) >= 4:
                raise ValueError("Four commands are already active. Wait before starting another.")
            plan.started = True
            self.jobs[id_] = {"id": id_, "action": plan.action, "state": "queued",
                              "createdAt": now(), "commands": [], "warnings": [], "error": ""}
            self.pool.submit(self._execute, plan)
            return {"id": id_, "state": "queued"}

    def _persist(self, plan: Plan) -> None:
        with self.lock:
            job = self.jobs[plan.id]
            manifest = {
                "schemaVersion": 1, "id": plan.id, "action": plan.action,
                "state": job["state"], "createdAt": job["createdAt"],
                "finishedAt": job.get("finishedAt"), "error": job["error"],
                "warnings": list(job["warnings"]), "config": redact(asdict(plan.config)),
                "commands": [{key: value for key, value in result.items() if key != "data"}
                             for result in job["commands"]],
            }
        temporary = plan.directory / "manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        for attempt in range(7):
            try:
                temporary.replace(plan.directory / "manifest.json")
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 6:
                    raise
                # Windows scanners can briefly lock a closed manifest. Never retry the CLI operation.
                if attempt == 0:
                    LOG.warning("Retrying local artifact publication after a Windows file lock (%s).", plan.id)
                time.sleep(.01 * 2 ** attempt)

    def _execute(self, plan: Plan) -> None:
        job = self.jobs[plan.id]
        terminal_state = "failed"
        client_definitions = None
        try:
            plan.directory.mkdir(parents=True, exist_ok=True)
            with self.lock:
                job["state"] = "running"
            self._persist(plan)
            for command in plan.commands:
                existing_downloads = ({path for path in (plan.directory / "files").rglob("*") if path.is_file()}
                                      if command.download else set())
                started = time.monotonic()
                with self.lock:
                    job["currentCommand"] = command.public()["powershell"]
                result = self.runner(command)
                stdout = str(redact(result.stdout or ""))
                stderr = str(redact(result.stderr or ""))
                entry: dict[str, Any] = {
                    "command": command.public()["powershell"], "exitCode": result.returncode,
                    "durationSeconds": round(time.monotonic() - started, 2),
                    "artifact": command.artifact, "stdout": stdout[-24000:], "stderr": stderr[-16000:],
                    "data": None,
                }
                with self.lock:
                    job["commands"].append(entry)
                if result.returncode:
                    raise ValueError(f"Azure CLI exited with code {result.returncode}. "
                                     "See the command's stderr below. No automatic retry was performed.")
                if command.download and not any(
                    path.is_file() and path not in existing_downloads and path.stat().st_size > 0
                    for path in (plan.directory / "files").rglob("*")
                ):
                    raise ValueError("No new artifact files were downloaded, despite the CLI's zero exit code. "
                                     "Check artifact availability and ingestion delay; inspect stdout below.")
                if command.artifact == "tests.tsv":
                    table, content = parse_test_list_tsv(stdout)
                    (plan.directory / command.artifact).write_text(content, encoding="utf-8", newline="")
                    entry["table"] = table
                elif command.artifact:
                    try:
                        data = redact(json.loads(result.stdout)) if (result.stdout or "").strip() else None
                    except json.JSONDecodeError as exc:
                        raise ValueError("Azure CLI returned non-JSON output. Inspect stdout; "
                                         "no result data was invented.") from exc
                    entry["data"] = data
                    entry["format"] = "json"
                    entry["stdout"] = json.dumps(data, indent=2, ensure_ascii=True)[-24000:]
                    (plan.directory / command.artifact).write_text(
                        json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
                    if command.artifact == "client-definitions.json":
                        client_definitions = data
                    if command.metric or command.namespace:
                        definitions = client_definitions if command.namespace == "LoadTestRunMetrics" else None
                        entry["metricRows"] = metric_rows(data, command.metric, command.namespace, definitions)
                        for suffix, exporter in ((".csv", csv_export), (".tsv", tsv_export)):
                            (plan.directory / Path(command.artifact).with_suffix(suffix)).write_text(
                                exporter(data, command.metric, command.namespace, definitions),
                                encoding="utf-8", newline="")
                        if not entry["metricRows"]:
                            with self.lock:
                                job["warnings"].append(
                                    f"{command.artifact}: no metric datapoints were returned. "
                                    "Check ingestion delay, run times, metric name, permissions and retention.")
                    if command.artifact == "run.json" and isinstance(data, dict):
                        entry["verdict"] = run_verdict(data)
                        if plan.action == "all-client-metrics" and data.get("testId") != plan.config.testId:
                            raise ValueError("The selected run does not belong to the configured test ID. "
                                             "Load runs for that test and select the matching execution.")
                        if plan.action == "collect-evidence" and str(data.get("status", "")).upper() not in TERMINAL:
                            raise ValueError("Run is not terminal yet. Wait for completion before collecting artifacts.")
                        if (plan.action == "collect-evidence" and plan.config.serverResourceId
                                and plan.config.serverMetric and data.get("startDateTime") and data.get("endDateTime")):
                            selected_times = [datetime.fromisoformat(value.replace("Z", "+00:00"))
                                              for value in (plan.config.startTime, plan.config.endTime)]
                            actual_times = [datetime.fromisoformat(str(data[key]).replace("Z", "+00:00"))
                                            for key in ("startDateTime", "endDateTime")]
                            if selected_times != actual_times:
                                raise ValueError("Azure's run timestamps changed during result processing. "
                                                 "Inspect the run and re-preview collection to align server metrics "
                                                 "with the final run window. No hidden command changes were made.")
                self._persist(plan)
            terminal_state = "succeeded"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            terminal_state = "failed"
            with self.lock:
                job["error"] = str(redact(str(exc)))
            LOG.error("%s (%s): %s", plan.action, plan.id, job["error"])
        finally:
            with self.lock:
                if terminal_state == "failed" and not job["error"]:
                    job["error"] = "Unexpected local processing error. Inspect the server terminal before retrying."
                    LOG.error("Unexpected failure in %s (%s).", plan.action, plan.id, exc_info=True)
                job["state"] = terminal_state
                job["finishedAt"] = now()
                job.pop("currentCommand", None)
                if plan.directory.is_dir():
                    try:
                        self._persist(plan)
                    except OSError as exc:
                        job["state"] = "failed"
                        job["error"] = (f"Could not persist artifacts: {exc}. Azure operations were not rolled back; "
                                        "inspect Azure before repeating a write or run.")
                        LOG.error("Persisting %s: %s", plan.id, exc)

    def files(self, id_: str) -> list[dict[str, Any]]:
        directory = self.artifact_directory(id_)
        if not directory.is_dir():
            return []
        files = []
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix != ".tmp":
                if not path.resolve().is_relative_to(directory.resolve()):
                    continue
                name = path.relative_to(directory).as_posix()
                files.append({"name": name, "bytes": path.stat().st_size,
                              "url": f"/downloads/{id_}/{quote(name, safe='/')}"})
        return files

    def artifact_directory(self, id_: str) -> Path:
        if not JOB_ID.fullmatch(id_):
            raise ValueError("Invalid artifact ID.")
        directory = self.artifacts / id_
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.artifacts):
            raise ValueError("Invalid artifact directory.")
        return directory

    def job(self, id_: str) -> dict[str, Any]:
        with self.lock:
            if id_ in self.jobs:
                result = json.loads(json.dumps(self.jobs[id_]))
            else:
                path = self.artifact_directory(id_) / "manifest.json"
                if not path.is_file():
                    raise FileNotFoundError("Job not found.")
                result = json.loads(path.read_text(encoding="utf-8"))
                if result["state"] in {"running", "queued"}:
                    result["state"] = "failed"
                    result["error"] = ("The local app restarted during this command. The Azure run may still "
                                       "be running; inspect it before retrying.")
        result["files"] = self.files(id_)
        result["bundleUrl"] = f"/downloads/{id_}/bundle.zip"
        result["artifactDirectory"] = str(self.artifact_directory(id_))
        return result

    def saved_artifacts(self) -> list[dict[str, Any]]:
        manifests = sorted(self.artifacts.glob("*/manifest.json"),
                           key=lambda path: path.stat().st_mtime, reverse=True)[:40]
        return [self.job(path.parent.name) for path in manifests if JOB_ID.fullmatch(path.parent.name)]

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int = DEFAULT_PORT):
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: DemoServer
    server_version = "LoadTestCliDemo"

    def log_message(self, format_: str, *args: Any) -> None:
        LOG.info("%s - %s", self.client_address[0], format_ % args)

    def _headers(self, status: int, content_type: str, length: int | None = None,
                 filename: str = "", cookie: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if cookie:
            self.send_header("Set-Cookie",
                             f"cli_demo={self.server.app.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=True).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _trusted(self, authenticated: bool = True, write: bool = False) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origins = {f"http://{host}" for host in hosts}
        if self.headers.get("Host") not in hosts:
            self._json({"error": "Only this app's localhost host is accepted."}, 403)
            return False
        landing_page = not authenticated and self.command == "GET" and self.path == "/"
        if not landing_page and self.headers.get("Origin") and self.headers["Origin"] not in origins:
            self._json({"error": "Cross-origin requests are not allowed."}, 403)
            return False
        if not landing_page and self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json({"error": "Cross-site requests are not allowed."}, 403)
            return False
        if authenticated:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                self._json({"error": "Invalid session cookie. Reload the app."}, 403)
                return False
            received = cookie["cli_demo"].value if "cli_demo" in cookie else ""
            if not hmac.compare_digest(received.encode("utf-8"), self.server.app.token.encode("ascii")):
                self._json({"error": "Open the local app first to establish a session."}, 403)
                return False
        if write and not hmac.compare_digest(self.headers.get("X-Demo-Token", "").encode("utf-8"),
                                             self.server.app.token.encode("ascii")):
            self._json({"error": "Missing session token. Reload the app."}, 403)
            return False
        return True

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        public = path in {"/", "/app.js", "/styles.css", "/api/health"}
        if not self._trusted(authenticated=not public):
            return
        try:
            if path == "/api/health":
                self._json({"status": "ready"})
            elif path in {"/", "/app.js", "/styles.css"}:
                file = STATIC / ("index.html" if path == "/" else path[1:])
                body = file.read_bytes()
                content_type = {".html": "text/html", ".js": "text/javascript",
                                ".css": "text/css"}[file.suffix] + "; charset=utf-8"
                self._headers(200, content_type, len(body), cookie=path == "/")
                self.wfile.write(body)
            elif path == "/api/bootstrap":
                defaults = asdict(Config(
                    tenant=os.getenv("AZURE_TENANT_ID", ""),
                    subscription=os.getenv("AZURE_SUBSCRIPTION_ID", ""),
                    resourceGroup=os.getenv("AZURE_RESOURCE_GROUP_NAME", os.getenv("AZURE_RESOURCE_GROUP", "")),
                    loadTestResource=os.getenv("AZURE_LOAD_TEST_RESOURCE_NAME", ""),
                    targetUrl=("https://" + os.environ["SERVICE_API_HOSTNAME"] + "/health"
                               if os.getenv("SERVICE_API_HOSTNAME") else ""),
                ))
                self._json({"token": self.server.app.token, "actions": public_actions(),
                            "defaults": defaults, "cliInstalled": shutil.which("az") is not None,
                            "dataDirectory": str(self.server.app.data_dir)})
            elif path == "/api/artifacts":
                self._json(self.server.app.saved_artifacts())
            elif path.startswith("/api/jobs/"):
                self._json(self.server.app.job(path.removeprefix("/api/jobs/")))
            elif path.startswith("/api/plans/") and path.endswith("/script"):
                id_ = path.removeprefix("/api/plans/").removesuffix("/script")
                plan = self.server.app.get_plan(id_)
                shell = parse_qs(urlsplit(self.path).query).get("shell", ["powershell"])[0]
                if shell not in {"powershell", "bash"}:
                    raise ValueError("Choose powershell or bash.")
                body = script_for(plan.commands, shell, plan.test_config).encode("utf-8")
                self._headers(200, "text/plain; charset=utf-8", len(body),
                              filename="load-test-step." + ("sh" if shell == "bash" else "ps1"))
                self.wfile.write(body)
            elif path.startswith("/downloads/"):
                self._download(path)
            else:
                self._json({"error": "Not found."}, 404)
        except FileNotFoundError as exc:
            self._json({"error": str(exc)}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except OSError as exc:
            LOG.error("Reading %s: %s", path, exc)
            self._json({"error": "Could not read the local artifact. See the server terminal."}, 500)

    def _download(self, path: str) -> None:
        parts = path.split("/", 3)
        if len(parts) != 4:
            raise ValueError("Invalid download path.")
        id_, relative = parts[2:]
        directory = self.server.app.artifact_directory(id_)
        if not directory.is_dir():
            raise FileNotFoundError("Artifact directory not found.")
        if self.server.app.job(id_)["state"] in {"queued", "running"}:
            raise ValueError("Artifacts are still being written. Wait for this command to finish.")
        if relative == "bundle.zip":
            with tempfile.TemporaryFile() as handle:
                with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    for item in self.server.app.files(id_):
                        bundle.write(directory / item["name"], item["name"])
                length = handle.tell()
                handle.seek(0)
                self._headers(200, "application/zip", length, f"load-test-evidence-{id_[:8]}.zip")
                shutil.copyfileobj(handle, self.wfile)
            return
        if "\\" in relative or ":" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise ValueError("Invalid download path.")
        file = directory / relative
        if (file.is_symlink() or not file.resolve().is_relative_to(directory.resolve())
                or not file.is_file()):
            raise FileNotFoundError("Artifact not found.")
        with file.open("rb") as handle:
            content_type = ("text/tab-separated-values; charset=utf-8" if file.suffix.lower() == ".tsv"
                            else mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self._headers(200, content_type,
                          file.stat().st_size, re.sub(r"[^A-Za-z0-9._ -]", "_", file.name))
            shutil.copyfileobj(handle, self.wfile)

    def do_POST(self) -> None:
        if not self._trusted(write=True):
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "Use application/json."}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                self._json({"error": "Request body must be 1-32768 bytes."}, 413)
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request must be a JSON object.")
            if self.path == "/api/preview":
                if not isinstance(data.get("action"), str):
                    raise ValueError("Select a demo action.")
                self._json(self.server.app.preview(data["action"], data.get("config", {})))
            elif self.path == "/api/jobs":
                if not isinstance(data.get("planId"), str):
                    raise ValueError("Preview the command first.")
                self._json(self.server.app.start(data["planId"], data.get("confirmed") is True), 202)
            else:
                self._json({"error": "Not found."}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Local HTTP port (default: %(default)s).")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".loadtest-demo",
                        help="Local output directory; may contain sensitive downloaded artifacts.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = App(args.data_dir)
    try:
        server = DemoServer(app, args.port)
    except OSError:
        app.close()
        raise
    print(f"Azure Load Testing CLI demo: http://127.0.0.1:{args.port}", flush=True)
    print("Local CLI credentials are used. Never expose this server through a tunnel or reverse proxy.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Closing local UI. This does not cancel an Azure load test run.")
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
