"""Render Phase 41's alarm catalog into the JSON Terraform reads.

    python ops/render_alarms.py            # writes infra/terraform/stack/alarms.generated.json
    python ops/render_alarms.py --check    # exits 1 if the committed file is stale

`api/services/alarms.ALARMS` is the single source of truth: every series has
exactly one alarm, each alarm names a runbook line, and `tests/test_alarms.py`
pins the catalog. Terraform does not get a second, hand-copied list -- it reads
this file with `jsondecode(file(...))` and `for_each`, and
`tests/test_terraform_policy.py` fails the build if the file and the catalog
disagree. A copy that can drift is the defect `tests/test_container_dependencies.py`
exists for, and this is the same shape one layer over.

The namespace is deliberately NOT rendered here. It is a Terraform variable,
because it is a deployment fact (which namespace the running sink publishes
under) and not a property of the catalog.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "infra/terraform/stack/alarms.generated.json"

#: Keys of the CloudWatch API shape that Terraform's aws_cloudwatch_metric_alarm
#: takes. The namespace is supplied by Terraform; ActionsEnabled is always true.
KEEP = (
    "AlarmName",
    "MetricName",
    "Statistic",
    "ComparisonOperator",
    "Threshold",
    "Period",
    "EvaluationPeriods",
    "DatapointsToAlarm",
    "TreatMissingData",
    "AlarmDescription",
    "Unit",
)


def render() -> str:
    os.environ.setdefault("APP_MODE", "private_operator")
    os.environ.setdefault("TELEMETRY_ENABLED", "false")
    os.environ.setdefault("TELEMETRY_REPORT_PATH", "/tmp/telemetry-report.md")
    sys.path.insert(0, str(ROOT))
    from api.services import alarms  # noqa: PLC0415

    rows = {}
    for spec in alarms.ALARMS:
        shape = spec.as_put_metric_alarm(namespace="__terraform__")
        rows[shape["AlarmName"]] = {key: shape[key] for key in KEEP}
    payload = {"_generated_by": "ops/render_alarms.py", "alarms": rows}
    body = json.dumps(payload, indent=2, sort_keys=True)
    return body + "\n"


def main(argv: list[str]) -> int:
    body = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != body:
            rel = OUT.relative_to(ROOT)
            print(f"{rel} is stale; run python ops/render_alarms.py", file=sys.stderr)
            return 1
        print("alarms.generated.json is current")
        return 0
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(json.loads(body)['alarms'])} alarms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
