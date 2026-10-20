"""Inspect a JMX execution tree without evaluating JMeter functions or scripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

MAX_BYTES = 10 * 1024 * 1024
REVIEW = {
    "ThroughputController": "Percentage execution control is not automatically a Locust task weight.",
    "TransactionController": "Preserve parent/child transaction naming and timing semantics.",
    "CSVDataSet": "Preserve sharing, iteration advancement, quoting, recycling and EOF behavior.",
    "CookieManager": "Match cookie/session lifetime and clearEachIteration behavior.",
    "CacheManager": "Locust does not automatically reproduce JMeter HTTP cache semantics.",
    "ConstantTimer": "Preserve timer scope: per sampler is different from Locust wait_time.",
    "UniformRandomTimer": "Preserve random timer distribution and scope.",
    "ConstantThroughputTimer": "A rate target is not a concurrency setting or a task weight.",
    "PreciseThroughputTimer": "Preserve the arrival model; a closed Locust loop is not equivalent.",
    "JSR223Sampler": "Custom script requires manual translation; do not execute during inspection.",
    "JSR223PreProcessor": "Custom preprocessor requires manual translation.",
    "JSR223PostProcessor": "Custom postprocessor requires manual translation.",
    "BeanShellSampler": "Custom code requires manual translation.",
    "BackendListener": "Recreate required telemetry explicitly; this is not a Locust built-in.",
}
KNOWN = {
    "TestPlan", "ThreadGroup", "SetupThreadGroup", "PostThreadGroup", "HTTPSamplerProxy",
    "ConfigTestElement", "HeaderManager", "Arguments", "ResponseAssertion", "DurationAssertion",
    "SizeAssertion", "JSONPathAssertion", "JSONPostProcessor", "RegexExtractor", "XPathExtractor",
    "XPath2Extractor", "BoundaryExtractor", "IfController", "LoopController", "OnceOnlyController",
    "GenericController", "InterleaveControl", "RandomController", "RandomOrderController",
    "ResultCollector", "DebugSampler", *REVIEW,
}


def safe_text(value: str) -> str:
    value = re.sub(r"(?i)([?&](?:sig|token|password|secret|api[-_]?key)=)[^&\s]*",
                   r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
    return value[:1000]


def properties(element: ElementTree.Element) -> dict[str, str]:
    return {child.attrib["name"]: child.text or ""
            for child in element.iter()
            if child.tag in {"stringProp", "boolProp", "intProp", "longProp"}
            and "name" in child.attrib}


def inspect(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("JMX exceeds the 10 MiB inspection limit.")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("JMX with DTD/entity declarations is not accepted.")
    root = ElementTree.fromstring(text)
    if root.tag != "jmeterTestPlan":
        raise ValueError("Expected a jmeterTestPlan XML root.")
    tree = root.find("hashTree")
    if tree is None:
        raise ValueError("The JMX has no root hashTree.")

    inventory: dict[str, Any] = {
        "schemaVersion": 1, "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "elements": [], "samplers": [], "threadGroups": [], "csvDataSets": [],
        "reviewRequired": [], "disabledSubtrees": [], "functions": [], "propertyReferences": [],
        "note": "Inventory only. Headers, bodies and literal credential values are deliberately not exported.",
    }
    counts: Counter[str] = Counter()
    functions: set[str] = set()
    references: set[str] = set()

    def walk(branch: ElementTree.Element, scope: list[str], depth: int = 0) -> None:
        if depth > 100:
            raise ValueError("JMX scope depth exceeds 100 levels.")
        children = list(branch)
        if len(children) % 2:
            raise ValueError("Malformed JMX: every test element must be followed by its hashTree.")
        for index in range(0, len(children), 2):
            element, nested = children[index:index + 2]
            if element.tag == "hashTree" or nested.tag != "hashTree":
                raise ValueError("Malformed JMX element/hashTree pairing.")
            name = safe_text(element.get("testname", element.tag))
            if element.get("enabled", "true").lower() == "false":
                inventory["disabledSubtrees"].append({"type": element.tag, "name": name, "scope": scope})
                continue
            values = properties(element)
            fragment = ElementTree.tostring(element, encoding="unicode")
            functions.update(re.findall(r"\$\{(__[A-Za-z0-9_]+)(?:\s*\(|\s*\})", fragment))
            references.update(re.findall(r"\$\{__P\(\s*([^,\s)]+)", fragment))
            counts[element.tag] += 1
            inventory["elements"].append({
                "type": element.tag, "name": name, "scope": scope,
                "propertyNames": sorted(values),
                "variableReferences": sorted(set(re.findall(r"\$\{([A-Za-z][A-Za-z0-9_.-]*)\}", fragment))),
            })
            if element.tag in REVIEW or element.tag not in KNOWN:
                inventory["reviewRequired"].append({
                    "type": element.tag, "name": name, "scope": scope,
                    "reason": REVIEW.get(element.tag, "Unknown/plugin element: translate and verify manually."),
                })
            if element.tag.endswith("ThreadGroup"):
                controller = element.find("./elementProp[@name='ThreadGroup.main_controller']")
                loop_values = properties(controller) if controller is not None else {}
                inventory["threadGroups"].append({
                    "name": name, "type": element.tag, "scope": scope,
                    "threads": safe_text(values.get("ThreadGroup.num_threads", "")),
                    "rampSeconds": safe_text(values.get("ThreadGroup.ramp_time", "")),
                    "durationSeconds": safe_text(values.get("ThreadGroup.duration", "")),
                    "startDelaySeconds": safe_text(values.get("ThreadGroup.delay", "")),
                    "scheduler": values.get("ThreadGroup.scheduler", ""),
                    "loops": safe_text(loop_values.get("LoopController.loops", "")),
                    "onSampleError": values.get("ThreadGroup.on_sample_error", ""),
                })
            if element.tag == "HTTPSamplerProxy":
                inventory["samplers"].append({
                    "name": name, "scope": scope, "method": values.get("HTTPSampler.method", "GET"),
                    "path": safe_text(values.get("HTTPSampler.path", "")),
                    "usesRawBody": values.get("HTTPSampler.postBodyRaw", "false"),
                    "followRedirects": values.get("HTTPSampler.follow_redirects", ""),
                    "connectTimeout": values.get("HTTPSampler.connect_timeout", "inherited"),
                    "readTimeout": values.get("HTTPSampler.response_timeout", "inherited"),
                    "argumentNames": [safe_text(item.text or "") for item in
                                      element.findall(".//stringProp[@name='Argument.name']")],
                })
            if element.tag == "CSVDataSet":
                inventory["csvDataSets"].append({
                    "name": name, "scope": scope,
                    **{key: safe_text(values.get(key, "")) for key in
                       ("filename", "variableNames", "delimiter", "quotedData", "ignoreFirstLine",
                        "recycle", "stopThread", "shareMode")},
                })
            walk(nested, [*scope, name], depth + 1)

    walk(tree, [])
    inventory["elementCounts"] = dict(sorted(counts.items()))
    inventory["functions"] = sorted(functions)
    inventory["propertyReferences"] = sorted(references)
    for function in sorted(functions & {"__BeanShell", "__groovy", "__javaScript"}):
        inventory["reviewRequired"].append({
            "type": "function", "name": function, "scope": [],
            "reason": "Embedded expression/code requires manual translation; it was not executed.",
        })
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jmx", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = json.dumps(inspect(args.jmx), indent=2, ensure_ascii=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result, encoding="utf-8")
            print(f"Wrote JMX inventory to {args.output}")
        else:
            print(result, end="")
    except (OSError, ValueError, ElementTree.ParseError) as exc:
        parser.exit(2, f"JMX inspection failed: {exc}\n")


if __name__ == "__main__":
    main()
