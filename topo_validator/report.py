#!/usr/bin/env python3

"""Format topology validation issues as text or JSON reports."""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Literal, TypedDict

from .model import Issue, errors_only

RuleStatus = Literal["PASS", "WARN", "FAIL"]

STRUCTURE_CLASS = "Structure"
POINT_RULES_CLASS = "Point Rules"
CURVE_RULES_CLASS = "Curve Rules"
SURFACE_RULES_CLASS = "Surface Rules"
SHELL_FACE_RULES_CLASS = "Shell / Face Rules"
SOLID_RULES_CLASS = "Solid Rules"
SOLID_RELATIONSHIP_RULES_CLASS = "Solid Relationship Rules"
CONTAINMENT_RULES_CLASS = "Containment Rules"


HTML_REPORT_STYLE = """
:root {
  --ok: #157347;
  --warn: #b58100;
  --fail: #b02a37;
  --border: #d0d7de;
  --muted: #57606a;
  --bg: #f6f8fa;
  --text: #24292f;
}

body {
  margin: 0;
  padding: 2rem;
  color: var(--text);
  background: #ffffff;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.5;
}

main {
  max-width: 1100px;
  margin: 0 auto;
}

h1, h2 {
  margin-bottom: 0.75rem;
}

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
  margin: 1rem 0 2rem;
}

.card {
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: var(--bg);
}

.card-label {
  color: var(--muted);
  font-size: 0.875rem;
}

.card-value {
  margin-top: 0.25rem;
  font-size: 1.5rem;
  font-weight: 700;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 2rem;
}

th, td {
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}

thead th {
  background: var(--bg);
}

.group-row th {
  background: #eaeef2;
  font-size: 1rem;
}

.status,
.severity {
  display: inline-block;
  min-width: 4.5rem;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  color: #ffffff;
  font-size: 0.8rem;
  font-weight: 700;
  text-align: center;
}

.pass {
  background: var(--ok);
}

.warn,
.warning {
  background: var(--warn);
}

.fail,
.error {
  background: var(--fail);
}

.empty-state {
  color: var(--muted);
  font-style: italic;
}

.source-file,
.generated-at {
  margin: 0.25rem 0;
  color: var(--muted);
  font-size: 0.875rem;
}
"""

# Run timestamp format: local date and time with the UTC offset, so a report
# archived from a machine in another timezone is still unambiguous.
GENERATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S %z"


class RuleCheck(TypedDict):
    """Rule-check definition used to group issue codes in reports."""

    id: str
    name: str
    conformanceClass: str
    codes: set[str]


class RuleResult(TypedDict):
    """Report result for one validation rule or structural check."""

    id: str
    name: str
    conformanceClass: str
    status: RuleStatus
    issueCount: int
    errorCount: int
    issues: list[Issue]


def rule_check(
    rule_id: str,
    name: str,
    conformance_class: str,
    codes: set[str],
) -> RuleCheck:
    """Create a typed rule-check definition."""
    return {
        "id": rule_id,
        "name": name,
        "conformanceClass": conformance_class,
        "codes": codes,
    }


# Known structure and topology rule checks, grouped by report row.
RULE_CHECKS: tuple[RuleCheck, ...] = (
    rule_check(
        "Structure",
        "Required collections, ids, and field types",
        STRUCTURE_CLASS,
        {
            "MISSING_COLLECTION",
            "INVALID_COLLECTION_TYPE",
            "INVALID_OBJECT_TYPE",
            "MISSING_ID",
            "INVALID_ID_TYPE",
            "DUPLICATE_ID",
            "INVALID_COORDINATES",
            "INVALID_COORDINATE_VALUE",
            "INVALID_VERTICES",
            "INVALID_VERTEX_ID",
            "INVALID_RINGS",
            "INVALID_RING",
            "INVALID_RING_MEMBERS",
            "INVALID_RING_MEMBER",
            "INVALID_RING_MEMBER_REF",
            "INVALID_ORIENTATION",
            "INVALID_FACES",
            "INVALID_FACE_ID",
            "INVALID_VOLUME",
            "INVALID_LEVELS",
            "INVALID_RELATIONSHIP_ID",
            "INVALID_SHELLS",
            "INVALID_SHELL",
            "INVALID_SHELL_TYPE",
            "INVALID_SHELL_FACES",
            "INVALID_FACE_ORIENTATIONS",
            "INVALID_FACE_ORIENTATION",
            "INVALID_OBSERVATION_CURVES",
            "INVALID_OBSERVATION_CURVE",
            "INVALID_OBSERVATION_CURVE_REF",
            "INVALID_OBSERVATION_CURVE_SOURCE",
        },
    ),
    rule_check("TR-01", "UniquePoints", POINT_RULES_CLASS, {"DUPLICATE_POINT_PROXIMITY"}),
    rule_check("TR-11", "PointFabricConsistency", POINT_RULES_CLASS, {"UNKNOWN_POINT_REFERENCE"}),

    rule_check("TR-02", "CurveNoSelfIntersection", CURVE_RULES_CLASS, {"CURVE_SELF_INTERSECTION"}),
    rule_check("TR-03", "NoDanglingCurves", CURVE_RULES_CLASS, {"DANGLING_CURVE"}),
    rule_check("TR-12", "MinimumCurveLength", CURVE_RULES_CLASS, {"CURVE_BELOW_MINIMUM_LENGTH"}),
    rule_check("TR-13", "NoDuplicateCurves", CURVE_RULES_CLASS, {"DUPLICATE_CURVE"}),
    rule_check(
        "TR-14",
        "CurveIntersectionAtNodesOnly",
        CURVE_RULES_CLASS,
        {"CURVE_INTERSECTION_NOT_AT_NODE"},
    ),
    rule_check("TR-22", "CurveOrientation", CURVE_RULES_CLASS, {"CURVE_REPEATED_IN_RING"}),

    rule_check("TR-04", "SurfaceClosedRing", SURFACE_RULES_CLASS, {"SURFACE_RING_NOT_CLOSED"}),
    rule_check("TR-05", "SharedSurfaceEdges", SURFACE_RULES_CLASS, {"SHARED_EDGE_SAME_ORIENTATION"}),
    rule_check("TR-15", "NoSurfaceSelfIntersection", SURFACE_RULES_CLASS, {"SURFACE_SELF_INTERSECTION"}),
    rule_check("TR-16", "NoDuplicateSurfaces", SURFACE_RULES_CLASS, {"DUPLICATE_SURFACE"}),
    rule_check("TR-17", "SurfaceCurveConsistency", SURFACE_RULES_CLASS, {"UNKNOWN_CURVE_REFERENCE"}),
    rule_check("TR-23", "ConnectedInterior", SURFACE_RULES_CLASS, {"SURFACE_RING_REPEATED_VERTEX"}),

    rule_check("TR-06", "ClosedSolid", SHELL_FACE_RULES_CLASS, {"OPEN_SOLID_SHELL"}),
    rule_check("TR-18", "NoDanglingFaces", SHELL_FACE_RULES_CLASS, {"DANGLING_FACE"}),
    rule_check("TR-27", "ShellClosure", SHELL_FACE_RULES_CLASS, {"SHELL_NOT_CLOSED"}),

    rule_check("TR-07", "PositiveVolume", SOLID_RULES_CLASS, {"ZERO_OR_NEGATIVE_VOLUME"}),
    rule_check("TR-19", "MinimumSolidThickness", SOLID_RULES_CLASS, {"SOLID_BELOW_MINIMUM_THICKNESS"}),
    rule_check("TR-24", "SolidNonSelfIntersection", SOLID_RULES_CLASS, {"SOLID_SELF_INTERSECTION"}),
    rule_check(
        "TR-25",
        "ShellOrientation",
        SOLID_RULES_CLASS,
        {
            "SHELL_ORIENTATION_REVERSED",
            "INNER_SHELL_ORIENTATION_REVERSED",
        },
    ),
    rule_check(
        "TR-26",
        "DeclaredVolumeConsistency",
        SOLID_RULES_CLASS,
        {"DECLARED_VOLUME_MISMATCH"},
    ),

    rule_check("TR-08", "NoSolidOverlap", SOLID_RELATIONSHIP_RULES_CLASS, {"SOLID_OVERLAP"}),
    rule_check("TR-10", "SharedSolidFace", SOLID_RELATIONSHIP_RULES_CLASS, {"FACE_ADJACENCY_LIMIT_EXCEEDED"}),

    rule_check(
        "TR-09",
        "ParentContainment",
        CONTAINMENT_RULES_CLASS,
        {
            "UNKNOWN_PARENT_REFERENCE",
            "CHILD_NOT_CONTAINED_IN_PARENT",
        },
    ),
    rule_check(
        "TR-20",
        "EasementContainment",
        CONTAINMENT_RULES_CLASS,
        {
            "EASEMENT_MISSING_BURDENED",
            "UNKNOWN_BURDENED_REFERENCE",
            "EASEMENT_NOT_CONTAINED_IN_BURDENED",
        },
    ),
    rule_check(
        "TR-21",
        "ThematicHostRelationship",
        CONTAINMENT_RULES_CLASS,
        {
            "THEMATIC_SOLID_MISSING_HOST",
            "UNKNOWN_HOST_REFERENCE",
        },
    ),
    rule_check(
        "TR-28",
        "DeclaredParcelContainment",
        CONTAINMENT_RULES_CLASS,
        {
            "MISSING_PARCEL_CONTAINMENT_RELATIONSHIP",
            "MULTIPLE_PARCEL_CONTAINMENT_RELATIONSHIPS",
            "UNKNOWN_PARCEL_REFERENCE",
            "PARCEL_TYPE_MISMATCH",
            "SOLID_NOT_WITHIN_DECLARED_PARCEL",
        },
    ),
    rule_check(
        "TR-29",
        "DeclaredEasementBurden",
        CONTAINMENT_RULES_CLASS,
        {
            "MISSING_EASEMENT_BURDEN_RELATIONSHIP",
            "MULTIPLE_EASEMENT_BURDEN_RELATIONSHIPS",
            "UNKNOWN_BURDENED_PARCEL_REFERENCE",
            "BURDENED_PARCEL_TYPE_MISMATCH",
            "SOLID_NOT_WITHIN_BURDENED_PARCEL",
        },
    ),
)

# Rule ids covered by `_run_2d_applicable_rules` (validator.py) -- these are
# the only ones that can legitimately appear tagged extra.dimensionality ==
# "2d", so the 2D-coverage section reuses exactly this subset of
# `RULE_CHECKS` rather than every rule (most of which -- volume, shell,
# solid-relationship, containment -- have no 2D analogue at all).
TWO_D_APPLICABLE_RULE_IDS = frozenset(
    {
        "TR-01", "TR-02", "TR-03", "TR-04", "TR-05", "TR-11", "TR-12",
        "TR-13", "TR-14", "TR-15", "TR-16", "TR-17", "TR-22", "TR-23",
    }
)

EXCLUDED_FROM_3D_VALIDATION_CODE = "EXCLUDED_FROM_3D_VALIDATION"


def _issues_for_rule(issues: list[Issue], issue_codes: set[str]) -> list[Issue]:
    """Return issues whose code belongs to a rule-check definition."""
    return [
        issue for issue in issues
        if issue.get("code") in issue_codes
    ]


def _rule_status(issues_for_rule: list[Issue], errors_for_rule: list[Issue]) -> RuleStatus:
    """Return the report status for a rule from its matching issues."""
    if errors_for_rule:
        return "FAIL"
    if issues_for_rule:
        return "WARN"
    return "PASS"


def _rule_result(check: RuleCheck, issues_for_rule: list[Issue]) -> RuleResult:
    """Build a report result for one validation rule."""
    errors_for_rule = errors_only(issues_for_rule)

    return {
        "id": check["id"],
        "name": check["name"],
        "conformanceClass": check["conformanceClass"],
        "status": _rule_status(issues_for_rule, errors_for_rule),
        "issueCount": len(issues_for_rule),
        "errorCount": len(errors_for_rule),
        "issues": issues_for_rule,
    }


def rule_results(issues: list[Issue]) -> list[RuleResult]:
    """Return pass/fail/warn results for every validation rule.

    Args:
        issues: Validation issues returned by the validator.

    Returns:
        One result dictionary per known structure/rule check.
    """
    results: list[RuleResult] = []

    for check in RULE_CHECKS:
        issues_for_rule = _issues_for_rule(issues, check["codes"])
        results.append(_rule_result(check, issues_for_rule))

    return results


def two_d_rule_results(two_dimensional_issues: list[Issue]) -> list[RuleResult]:
    """Return pass/fail/warn results for the 2D-applicable rule subset.

    Reuses the same `RuleCheck` definitions (same TR-xx ids, names, and
    codes) as the main 3D table -- a 2D finding is the same kind of defect
    under the same code, just found in the 2D view -- computed only from
    issues tagged `extra.dimensionality == "2d"`, not the full issue list.

    Args:
        two_dimensional_issues: The subset of validation issues tagged
            `extra.dimensionality == "2d"` (see
            `validator._run_2d_applicable_rules`).

    Returns:
        One result dictionary per 2D-applicable rule check.
    """
    return [
        _rule_result(check, _issues_for_rule(two_dimensional_issues, check["codes"]))
        for check in RULE_CHECKS
        if check["id"] in TWO_D_APPLICABLE_RULE_IDS
    ]


def _partition_issues_by_dimensionality(
    issues: list[Issue],
) -> tuple[list[Issue], list[Issue], list[Issue]]:
    """Split *issues* into (main, two_dimensional, excluded) for report grouping.

    - `excluded`: `EXCLUDED_FROM_3D_VALIDATION` notices -- rendered in their
      own small section, not the main rule-results table.
    - `two_dimensional`: issues tagged `extra.dimensionality == "2d"` --
      rendered in their own "2D topology" section instead of being folded
      into the same TR-xx row a 3D finding under the same code would occupy.
    - `main`: everything else, fed into the main rule-results table exactly
      as before this split existed.

    This only changes how issues are grouped for display -- the overall
    valid/issueCount/errorCount reported alongside these stays computed from
    the full, unfiltered issue list.
    """
    main: list[Issue] = []
    two_dimensional: list[Issue] = []
    excluded: list[Issue] = []

    for issue in issues:
        if issue.get("code") == EXCLUDED_FROM_3D_VALIDATION_CODE:
            excluded.append(issue)
        elif issue.get("extra", {}).get("dimensionality") == "2d":
            two_dimensional.append(issue)
        else:
            main.append(issue)

    return main, two_dimensional, excluded


def to_json_report(issues: list[Issue]) -> str:
    """Return a JSON report for validation issues.

    `valid`/`issueCount`/`errorCount` are computed from the full, unfiltered
    *issues* list, as always. `ruleResults` now reflects only the main
    (non-2D, non-exclusion-notice) issues -- a 2D-view finding under a TR-xx
    code no longer shows up in that code's main-table row; see
    `twoDimensionalRuleResults` and `excludedFromThreeDValidation` instead.
    `issues` is unchanged: every issue, unfiltered.

    Args:
        issues: Validation issues returned by the validator.

    Returns:
        Pretty-printed JSON report string.
    """
    main_issues, two_dimensional_issues, excluded_issues = (
        _partition_issues_by_dimensionality(issues)
    )

    return json.dumps(
        {
            "valid": len(errors_only(issues)) == 0,
            "issueCount": len(issues),
            "errorCount": len(errors_only(issues)),
            "ruleResults": rule_results(main_issues),
            "twoDimensionalRuleResults": two_d_rule_results(two_dimensional_issues),
            "excludedFromThreeDValidation": excluded_issues,
            "issues": issues,
        },
        indent=2,
    )


def _validation_status(issue_count: int, error_count: int) -> str:
    """Return the human-readable validation status for a report."""
    if error_count:
        return "failed"
    if issue_count:
        return "passed with warnings"
    return "passed"


def _text_report_summary(issue_count: int, error_count: int) -> list[str]:
    """Return the summary section for a text validation report."""
    return [
        f"Validation {_validation_status(issue_count, error_count)}",
        f"Issues: {issue_count}",
        f"Errors: {error_count}",
        "",
        "Rule results:",
    ]


def _text_rule_result_lines(results: list[RuleResult]) -> list[str]:
    """Return grouped rule-result lines for a text validation report."""
    lines: list[str] = []
    previous_conformance_class: str | None = None

    for result in results:
        conformance_class = result["conformanceClass"]

        if conformance_class != previous_conformance_class:
            lines.extend(["", conformance_class])
            previous_conformance_class = conformance_class

        lines.append(f"- {result['status']} {result['id']} {result['name']}")

    return lines


def _text_two_d_section_lines(two_dimensional_issues: list[Issue]) -> list[str]:
    """Return the "2D topology" section for a text report, or [] when empty.

    Only rendered when there's at least one 2D-tagged issue to show -- a
    fully clean 2D dataset produces none, so this can't distinguish "no 2D
    content" from "2D content, no findings" (the report only ever sees the
    issue list, not the source topology).
    """
    if not two_dimensional_issues:
        return []

    lines = ["", "2D topology (partial coverage):"]
    lines.extend(_text_rule_result_lines(two_d_rule_results(two_dimensional_issues)))
    return lines


def _text_excluded_section_lines(excluded_issues: list[Issue]) -> list[str]:
    """Return the "excluded from 3D validation" section, or [] when empty."""
    if not excluded_issues:
        return []

    lines = ["", "Excluded from 3D validation:"]
    lines.extend(f"- {issue['message']}" for issue in excluded_issues)
    return lines


def _text_issue_detail_lines(issues: list[Issue]) -> list[str]:
    """Return issue-detail lines for a text validation report."""
    if not issues:
        return []

    lines = ["", "Issue details:"]

    for issue in issues:
        object_id = issue.get("object_id")
        issue_target = f" [{object_id}]" if object_id else ""
        lines.append(
            f"- {issue['severity'].upper()} {issue['code']}{issue_target}: "
            f"{issue['message']}"
        )

    return lines


def to_text_report(issues: list[Issue]) -> str:
    """Return a human-readable text report for validation issues.

    The summary counts and "Issue details" list are computed from the full,
    unfiltered *issues*, as always. The "Rule results" table now reflects
    only the main (non-2D, non-exclusion-notice) issues; a 2D-view finding
    and any solids-excluded-from-3D-validation notice get their own sections
    instead of being folded into a main-table row.

    Args:
        issues: Validation issues returned by the validator.

    Returns:
        Multi-line validation report.
    """
    main_issues, two_dimensional_issues, excluded_issues = (
        _partition_issues_by_dimensionality(issues)
    )

    error_count = len(errors_only(issues))
    lines = _text_report_summary(len(issues), error_count)
    lines.extend(_text_rule_result_lines(rule_results(main_issues)))
    lines.extend(_text_two_d_section_lines(two_dimensional_issues))
    lines.extend(_text_excluded_section_lines(excluded_issues))
    lines.extend(_text_issue_detail_lines(issues))

    return "\n".join(lines)


def _status_class(status: RuleStatus) -> str:
    """Return a CSS class name for a rule status."""
    return status.lower()


def _format_issue_target(issue: Issue) -> str:
    """Return a human-readable issue target string."""
    object_id = issue.get("object_id")
    if object_id:
        return str(object_id)

    path = issue.get("path")
    if path:
        return str(path)

    return ""


def _html_rule_rows(results: list[RuleResult]) -> str:
    """Return HTML table rows for rule results grouped by conformance class."""
    rows: list[str] = []
    current_conformance_class: str | None = None

    for result in results:
        conformance_class = result["conformanceClass"]

        if conformance_class != current_conformance_class:
            rows.append(
                '<tr class="group-row">'
                f"<th colspan=\"5\">{html.escape(conformance_class)}</th>"
                "</tr>"
            )
            current_conformance_class = conformance_class

        status = result["status"]
        rows.append(
            "<tr>"
            f"<td><span class=\"status {_status_class(status)}\">"
            f"{html.escape(status)}</span></td>"
            f"<td>{html.escape(result['id'])}</td>"
            f"<td>{html.escape(result['name'])}</td>"
            f"<td>{result['issueCount']}</td>"
            f"<td>{result['errorCount']}</td>"
            "</tr>"
        )

    return "".join(rows)


def _html_issue_rows(issues: list[Issue]) -> str:
    """Return HTML table rows for validation issue details."""
    rows: list[str] = []

    for issue in issues:
        severity = str(issue.get("severity", "error")).upper()
        code = str(issue.get("code", ""))
        target = _format_issue_target(issue)
        message = str(issue.get("message", ""))

        rows.append(
            "<tr>"
            f"<td><span class=\"severity {html.escape(severity.lower())}\">"
            f"{html.escape(severity)}</span></td>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{html.escape(target)}</td>"
            f"<td>{html.escape(message)}</td>"
            "</tr>"
        )

    return "\n".join(rows)


def _html_two_d_section(two_dimensional_issues: list[Issue]) -> str:
    """Return the "2D topology" section, or "" when there's nothing to show.

    Only rendered when there's at least one 2D-tagged issue -- see
    `_text_two_d_section_lines` for why a clean 2D dataset can't be
    distinguished from no 2D content at all, at this layer.
    """
    if not two_dimensional_issues:
        return ""

    return f"""
        <section>
          <h2>2D topology (partial coverage)</h2>
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Rule</th>
                <th>Name</th>
                <th>Issues</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {_html_rule_rows(two_d_rule_results(two_dimensional_issues))}
            </tbody>
          </table>
        </section>
        """


def _html_excluded_section(excluded_issues: list[Issue]) -> str:
    """Return the "excluded from 3D validation" section, or "" when empty."""
    if not excluded_issues:
        return ""

    rows = "".join(
        f"<tr><td>{html.escape(issue['message'])}</td></tr>"
        for issue in excluded_issues
    )

    return f"""
        <section>
          <h2>Excluded from 3D validation</h2>
          <table>
            <tbody>
              {rows}
            </tbody>
          </table>
        </section>
        """


def _html_issue_details(issues: list[Issue]) -> str:
    """Return the issue details section, including an empty state when needed."""
    if not issues:
        return """
        <section>
          <h2>Issue details</h2>
          <p class="empty-state">No issues found.</p>
        </section>
        """

    return f"""
        <section>
          <h2>Issue details</h2>
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Code</th>
                <th>Target</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {_html_issue_rows(issues)}
            </tbody>
          </table>
        </section>
        """


def to_html_report(
    issues: list[Issue],
    source_name: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Return a human-readable HTML report for validation issues.

    Args:
        issues: Validation issues returned by the validator.
        source_name: Optional name of the validated input file to display in the report.
        generated_at: Timestamp shown as the run time, defaulting to now in the
            local timezone. Pass an explicit value to make the output
            reproducible — a report regenerated for comparison, or a test
            asserting on the HTML, otherwise differs on every run.

    Returns:
        Complete standalone HTML report string.
    """
    main_issues, two_dimensional_issues, excluded_issues = (
        _partition_issues_by_dimensionality(issues)
    )

    issue_count = len(issues)
    error_count = len(errors_only(issues))
    valid = error_count == 0
    validation_status = _validation_status(issue_count, error_count)
    source_heading = (
        f'\n    <p class="source-file">Test file: '
        f"<strong>{html.escape(source_name)}</strong></p>"
        if source_name
        else ""
    )

    # astimezone() attaches the local offset to an otherwise naive now(), so
    # %z renders rather than coming out empty.
    run_time = (generated_at or datetime.now().astimezone()).strftime(
        GENERATED_AT_FORMAT
    )
    generated_heading = (
        f'\n    <p class="generated-at">Validated: '
        f"<strong>{html.escape(run_time)}</strong></p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Topology Validation Report</title>
  <style>
{HTML_REPORT_STYLE}
  </style>
</head>
<body>
  <main>
    <h1>Topology Validation Report</h1>{source_heading}{generated_heading}

    <section class="summary">
      <div class="card">
        <div class="card-label">Validation</div>
        <div class="card-value">{html.escape(validation_status.title())}</div>
      </div>
      <div class="card">
        <div class="card-label">Valid</div>
        <div class="card-value">{str(valid)}</div>
      </div>
      <div class="card">
        <div class="card-label">Issues</div>
        <div class="card-value">{issue_count}</div>
      </div>
      <div class="card">
        <div class="card-label">Errors</div>
        <div class="card-value">{error_count}</div>
      </div>
    </section>

    <section>
      <h2>Rule results</h2>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Rule</th>
            <th>Name</th>
            <th>Issues</th>
            <th>Errors</th>
          </tr>
        </thead>
        <tbody>
          {_html_rule_rows(rule_results(main_issues))}
        </tbody>
      </table>
    </section>

    {_html_two_d_section(two_dimensional_issues)}
    {_html_excluded_section(excluded_issues)}
    {_html_issue_details(issues)}
  </main>
</body>
</html>
"""
