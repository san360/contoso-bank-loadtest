"""Scoped Azure VM Run Command; explicit exit proof and private diagnostic output."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import uuid

from .common import DemoError, azure


def execute(resources: dict, folder: Path, name: str, script: str, *, timeout=1800) -> str:
    vm_id = resources.get("gitlab_vm_id")
    prefix = f"/subscriptions/{resources['subscription_id']}/resourceGroups/{resources['resource_group']}/"
    if not vm_id or not vm_id.lower().startswith(prefix.lower()):
        raise DemoError("VM administration requires the discovered VM in the explicit demo subscription/group")
    marker = "DEMO_VM_" + uuid.uuid4().hex
    payload = folder / f"vm-{name}-command.sh"
    payload.write_text(
        "#!/bin/bash\nbash -c " + shlex.quote("set -euo pipefail\n" + script.replace("\r\n", "\n"))
        + "\nresult=$?\nprintf '\\n" + marker + "_EXIT=%s\\n' \"$result\"\nexit \"$result\"\n",
        encoding="utf-8",
    )
    payload.chmod(0o600)
    result = json.loads(azure([
        "vm", "run-command", "invoke", "--ids", vm_id, "--command-id", "RunShellScript",
        "--scripts", "@" + str(payload), "-o", "json",
    ], resources["subscription_id"], timeout=timeout))
    output = "\n".join(item.get("message", "") for item in result.get("value", []))
    log = folder / f"vm-{name}.log"
    log.write_text(output + "\n", encoding="utf-8")
    log.chmod(0o600)
    matched = re.search(re.escape(marker) + r"_EXIT=(\d+)", output)
    if not matched or int(matched.group(1)):
        code = matched.group(1) if matched else "unproven"
        raise DemoError(f"VM {name} did not succeed (exit={code}); private diagnostics: {log}")
    return output
