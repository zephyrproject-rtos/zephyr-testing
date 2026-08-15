# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Findings reported by the API compatibility checks."""

from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass


class Severity(enum.Enum):
    """How much attention a finding deserves.

    ERROR marks something that violates a documented guarantee. WARNING marks
    something that is suspicious but permitted, typically because the API is
    not yet stable. NOTE is informational and never fails a run.
    """

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.NOTE: 2}


@dataclass
class Finding:
    """A single problem found in a change."""

    check: str
    severity: Severity
    title: str
    detail: str = ""
    file: str | None = None
    line: int | None = None
    symbol: str | None = None
    group: str | None = None
    lifecycle: str | None = None

    def location(self) -> str:
        if self.file is None:
            return "<tree>"
        if self.line is None:
            return self.file
        return f"{self.file}:{self.line}"

    def sort_key(self):
        return (_RANK[self.severity], self.file or "", self.line or 0, self.title)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: f.sort_key())


def format_text(findings: list[Finding], color: bool = False) -> str:
    """Render findings as a human readable report."""
    if not findings:
        return "No API compatibility problems found."

    def paint(text, code):
        return f"\033[{code}m{text}\033[0m" if color else text

    tint = {Severity.ERROR: "1;31", Severity.WARNING: "1;33", Severity.NOTE: "1;34"}

    lines = []
    for finding in sort_findings(findings):
        label = paint(finding.severity.value.upper(), tint[finding.severity])
        lines.append(f"{finding.location()}: {label}: {finding.title}")

        context = []
        if finding.symbol:
            context.append(f"symbol: {finding.symbol}")
        if finding.group:
            group = finding.group
            if finding.lifecycle:
                group += f" ({finding.lifecycle})"
            context.append(f"group: {group}")
        if context:
            lines.append(f"    {', '.join(context)}")

        for detail_line in finding.detail.splitlines():
            lines.append(f"    {detail_line}")
        lines.append("")

    counts = {sev: sum(1 for f in findings if f.severity is sev) for sev in Severity}
    summary = ", ".join(
        f"{counts[sev]} {sev.value}{'s' if counts[sev] != 1 else ''}"
        for sev in Severity
        if counts[sev]
    )
    lines.append(f"Summary: {summary}")
    return "\n".join(lines)


def _escape_data(text: str) -> str:
    """Escape an annotation message, which is a single line."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    """Escape an annotation property such as title or file.

    Properties are separated by commas and terminated by "::", so a value
    containing either has to be escaped or the annotation is silently
    truncated. Struct members are displayed as "parent::member", which is
    exactly the case that breaks it.
    """
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def format_github(findings: list[Finding]) -> str:
    """Render findings as GitHub Actions workflow annotations."""
    out = []
    for finding in sort_findings(findings):
        # GitHub only understands "error", "warning" and "notice".
        level = "notice" if finding.severity is Severity.NOTE else finding.severity.value
        attrs = []
        if finding.file:
            attrs.append(f"file={_escape_property(finding.file)}")
        if finding.line:
            attrs.append(f"line={finding.line}")
        attrs.append(f"title={_escape_property(finding.title)}")

        body = _escape_data(finding.detail) or _escape_data(finding.title)
        out.append(f"::{level} {','.join(attrs)}::{body}")
    return "\n".join(out)


def format_json(findings: list[Finding]) -> str:
    """Render findings as JSON, for consumption by other tooling."""
    payload = []
    for finding in sort_findings(findings):
        item = asdict(finding)
        item["severity"] = finding.severity.value
        payload.append(item)
    return json.dumps(payload, indent=2)


#: "html" is handled separately by report.py: it takes report metadata that the
#: other formatters have no use for, so it is not a plain callable here.
FORMATTERS = {
    "text": format_text,
    "github": format_github,
    "json": format_json,
}

#: Every value accepted by --format.
FORMAT_CHOICES = (*sorted(FORMATTERS), "html")
