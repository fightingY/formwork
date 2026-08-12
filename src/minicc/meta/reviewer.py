from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from minicc.core.provider import CompletionOptions, ModelProvider, ModelResponse


REVIEW_SEVERITIES = {"low", "medium", "high"}
REVIEW_AREAS = {"context", "memory", "policy", "tools", "loop", "verification", "other"}
META_REVIEW_SCHEMA_VERSION = 2


class MetaReviewError(RuntimeError):
    """Raised when review input, model output, or immutable evidence is invalid."""


@dataclass(frozen=True)
class MetaReviewResult:
    review_id: str
    output_dir: Path
    report: dict[str, Any]

    @property
    def used_model(self) -> bool:
        return bool(self.report.get("invocation", {}).get("used_model"))


class MetaReviewer:
    """Review a completed run without modifying any source-run artifact."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        model: str = "",
        implementation_commit: str = "",
        max_schema_retries: int = 2,
    ) -> None:
        self.provider = provider
        self.model = model
        self.implementation_commit = implementation_commit
        self.max_schema_retries = max(0, max_schema_retries)

    def review_run(
        self,
        run_dir: Path,
        *,
        output_root: Path,
        offline: bool = False,
    ) -> MetaReviewResult:
        run_dir = run_dir.resolve()
        if not run_dir.is_dir():
            raise MetaReviewError(f"run directory does not exist: {run_dir}")
        output_root = output_root.resolve()
        if output_root == run_dir or output_root.is_relative_to(run_dir):
            raise MetaReviewError("meta review output must be outside the source run directory")
        snapshot, before = _load_snapshot(run_dir)
        responses: list[ModelResponse] = []
        schema_retry_reasons: list[str] = []
        if offline:
            content = _offline_review(snapshot)
        else:
            if self.provider is None:
                raise MetaReviewError("a provider is required unless --offline is used")
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are miniCC's offline meta reviewer. Diagnose reusable harness-level "
                        "improvements from immutable run evidence. Return exactly one JSON object."
                    ),
                },
                {"role": "user", "content": _review_prompt(snapshot)},
            ]
            for schema_attempt in range(self.max_schema_retries + 1):
                response = self.provider.complete(
                    messages,
                    options=CompletionOptions(
                        temperature=0.0,
                        stream=False,
                        include_usage=True,
                        json_mode=True,
                        max_tokens=2_048,
                    ),
                )
                responses.append(response)
                try:
                    content = _parse_model_review(response.text, snapshot=snapshot)
                    break
                except MetaReviewError as exc:
                    if schema_attempt >= self.max_schema_retries:
                        raise
                    schema_retry_reasons.append(str(exc))
                    messages.extend(
                        [
                            {"role": "assistant", "content": response.text[:8_000]},
                            {"role": "user", "content": _schema_correction_prompt(str(exc))},
                        ]
                    )
        _, after = _load_snapshot(run_dir)
        if before != after:
            raise MetaReviewError("source run evidence changed during review")

        review_id = f"meta-{run_dir.name}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        report = _build_report(
            review_id=review_id,
            run_dir=run_dir,
            source=before,
            content=content,
            responses=responses,
            model=self.model,
            offline=offline,
            implementation_commit=self.implementation_commit,
            schema_retry_reasons=schema_retry_reasons,
        )
        output_dir = _write_bundle(report, output_root / review_id)
        return MetaReviewResult(review_id=review_id, output_dir=output_dir, report=report)


def load_meta_review(path: Path, *, verify_manifest: bool = True) -> dict[str, Any]:
    review_dir = path.resolve() if path.is_dir() else path.resolve().parent
    report_path = review_dir / "report.json"
    manifest_path = review_dir / "manifest.json"
    try:
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes)
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaReviewError(f"invalid meta review bundle: {review_dir}") from exc
    if not isinstance(report, dict) or report.get("entity_type") != "meta_review":
        raise MetaReviewError(f"not a meta review report: {report_path}")
    if verify_manifest:
        expected = manifest.get("artifacts", {}).get("report_json", {})
        if expected.get("sha256") != hashlib.sha256(report_bytes).hexdigest():
            raise MetaReviewError(f"meta review manifest hash mismatch: {review_dir}")
        if expected.get("bytes") != len(report_bytes):
            raise MetaReviewError(f"meta review manifest size mismatch: {review_dir}")
    report["_evidence_integrity_verified"] = bool(verify_manifest)
    report["_evidence_source_path"] = str(report_path)
    report["_evidence_report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    report["_evidence_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    return report


def verify_review_source(report: Mapping[str, Any]) -> bool:
    source = report.get("source")
    if not isinstance(source, Mapping):
        return False
    run_dir = Path(str(source.get("run_dir") or ""))
    artifacts = source.get("artifacts")
    if not run_dir.is_dir() or not isinstance(artifacts, Mapping):
        return False
    for relative, expected in artifacts.items():
        if not isinstance(expected, Mapping):
            return False
        path = run_dir / str(relative)
        if not path.is_file():
            return False
        data = path.read_bytes()
        if expected.get("sha256") != hashlib.sha256(data).hexdigest():
            return False
        if expected.get("bytes") != len(data):
            return False
    return source.get("bundle_sha256") == _bundle_sha256(artifacts)


def _load_snapshot(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    required = ("state.json", "metrics.json", "trace.jsonl", "run_report.json")
    artifacts: dict[str, dict[str, Any]] = {}
    raw: dict[str, bytes] = {}
    for relative in required:
        path = run_dir / relative
        if not path.is_file():
            raise MetaReviewError(f"required source evidence is missing: {path}")
        data = path.read_bytes()
        raw[relative] = data
        artifacts[relative] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    diff_path = run_dir / "artifacts" / "diff.patch"
    if diff_path.is_file():
        data = diff_path.read_bytes()
        raw["artifacts/diff.patch"] = data
        artifacts["artifacts/diff.patch"] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    source = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "artifacts": artifacts,
        "bundle_sha256": _bundle_sha256(artifacts),
    }
    snapshot = {
        "run_id": run_dir.name,
        "state": _json_object(raw["state.json"], "state.json"),
        "metrics": _json_object(raw["metrics.json"], "metrics.json"),
        "run_report": _json_object(raw["run_report.json"], "run_report.json"),
        "trace_tail": _trace_tail(raw["trace.jsonl"], 80),
        "diff_preview": raw.get("artifacts/diff.patch", b"").decode(
            "utf-8", errors="replace"
        )[:20_000],
    }
    return snapshot, source


def _review_prompt(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return f"""Review this completed miniCC run.

Focus only on reusable improvements to prompts, context, memory, policy, tools, loop control,
and verification. Do not change the run verdict. Do not propose task-specific product code.
Every finding must cite at least one evidence reference such as metrics.<field>,
trace_tail[index], run_report.<field>, or diff_preview.

Run evidence:
{payload}

Return ONLY this JSON shape:
{{
  "summary": "one concise paragraph",
  "findings": [
    {{
      "id": "F1",
      "severity": "low|medium|high",
      "area": "context|memory|policy|tools|loop|verification|other",
      "message": "reusable diagnosis",
      "evidence_refs": ["metrics.turns"]
    }}
  ],
  "suggested_changes": [
    {{
      "id": "S1",
      "finding_ids": ["F1"],
      "change": "bounded harness-level experiment",
      "expected_effect": "measurable expected outcome",
      "validation": "deterministic test or fixed A/B that would validate it"
    }}
  ]
}}
"""


def _parse_model_review(text: str, *, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    payload_text = text.strip()
    if not payload_text.startswith("{"):
        match = re.search(r"\{.*\}", payload_text, re.DOTALL)
        if match:
            payload_text = match.group()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise MetaReviewError("model meta review was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise MetaReviewError("model meta review must be a JSON object")
    return _validate_review_content(payload, snapshot=snapshot)


def _schema_correction_prompt(reason: str) -> str:
    return (
        "Your previous JSON failed validation: "
        + reason
        + ". Return the complete corrected JSON object only. Use canonical evidence paths without "
        "embedding values: state.<field>, metrics.<field>, run_report.<field>, "
        "trace_tail[<non-negative index>].<field>, or diff_preview. Every finding needs a unique F<n> "
        "id. Every suggested change needs a unique S<n> id, finding_ids, change, expected_effect, "
        "and validation. Every finding must be linked by at least one suggested change."
    )


def _validate_review_content(
    payload: Mapping[str, Any], *, snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise MetaReviewError("meta review summary is required")
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise MetaReviewError("meta review requires at least one finding")
    findings: list[dict[str, Any]] = []
    finding_ids: set[str] = set()
    normalized_messages: set[str] = set()
    for item in raw_findings:
        if not isinstance(item, Mapping):
            raise MetaReviewError("each meta review finding must be an object")
        finding_id = str(item.get("id") or "").strip()
        severity = str(item.get("severity") or "")
        area = str(item.get("area") or "")
        message = str(item.get("message") or "").strip()
        refs = item.get("evidence_refs")
        if (
            re.fullmatch(r"F[1-9]\d*", finding_id) is None
            or finding_id in finding_ids
            or severity not in REVIEW_SEVERITIES
            or area not in REVIEW_AREAS
            or not message
        ):
            raise MetaReviewError("meta review finding has invalid severity, area, or message")
        message_key = re.sub(r"\W+", " ", message.casefold()).strip()
        if message_key in normalized_messages:
            raise MetaReviewError("meta review findings must not duplicate a diagnosis")
        if not isinstance(refs, list) or not any(str(ref).strip() for ref in refs):
            raise MetaReviewError("each meta review finding requires evidence_refs")
        normalized_refs = [str(ref).strip()[:200] for ref in refs if str(ref).strip()]
        for ref in normalized_refs:
            resolve_evidence_ref(snapshot, ref)
        finding_ids.add(finding_id)
        normalized_messages.add(message_key)
        findings.append(
            {
                "id": finding_id,
                "severity": severity,
                "area": area,
                "message": message[:1_000],
                "evidence_refs": normalized_refs,
            }
        )
    changes = payload.get("suggested_changes")
    if not isinstance(changes, list) or not changes:
        raise MetaReviewError("suggested_changes must be a list")
    suggestions: list[dict[str, Any]] = []
    suggestion_ids: set[str] = set()
    linked_findings: set[str] = set()
    for item in changes:
        if not isinstance(item, Mapping):
            raise MetaReviewError("each suggested change must be an object")
        suggestion_id = str(item.get("id") or "").strip()
        links = item.get("finding_ids")
        change = str(item.get("change") or "").strip()
        expected_effect = str(item.get("expected_effect") or "").strip()
        validation = str(item.get("validation") or "").strip()
        if (
            re.fullmatch(r"S[1-9]\d*", suggestion_id) is None
            or suggestion_id in suggestion_ids
            or not isinstance(links, list)
            or not links
            or not change
            or not expected_effect
            or not validation
        ):
            raise MetaReviewError("suggested change has invalid id, links, or required text")
        normalized_links = [str(link).strip() for link in links if str(link).strip()]
        if not normalized_links or any(link not in finding_ids for link in normalized_links):
            raise MetaReviewError("suggested change references an unknown finding id")
        suggestion_ids.add(suggestion_id)
        linked_findings.update(normalized_links)
        suggestions.append(
            {
                "id": suggestion_id,
                "finding_ids": normalized_links,
                "change": change[:1_000],
                "expected_effect": expected_effect[:1_000],
                "validation": validation[:1_000],
            }
        )
    if linked_findings != finding_ids:
        missing = ", ".join(sorted(finding_ids - linked_findings))
        raise MetaReviewError(f"every finding must be linked to a suggested change: {missing}")
    return {
        "summary": summary[:2_000],
        "findings": findings,
        "suggested_changes": suggestions,
        "quality_audit": {
            "schema_version": META_REVIEW_SCHEMA_VERSION,
            "finding_count": len(findings),
            "suggestion_count": len(suggestions),
            "evidence_ref_count": sum(len(item["evidence_refs"]) for item in findings),
            "evidence_refs_resolved": True,
            "finding_ids_unique": True,
            "finding_messages_unique": True,
            "suggestion_ids_unique": True,
            "suggestion_links_valid": True,
            "all_findings_actionable": True,
            "quality_gate_passed": True,
        },
    }


def _offline_review(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = snapshot.get("state") if isinstance(snapshot.get("state"), Mapping) else {}
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), Mapping) else {}
    status = str(metrics.get("status") or state.get("status") or "unknown")
    turns = _int_value(metrics.get("turns"))
    failures = _int_value(metrics.get("command_failures"))
    severity = "high" if status not in {"completed", "waiting_approval"} else "low"
    return _validate_review_content(
        {
            "summary": f"Deterministic review: status={status}, turns={turns}, command_failures={failures}.",
            "findings": [
                {
                    "id": "F1",
                    "severity": severity,
                    "area": "loop" if severity == "high" else "other",
                    "message": (
                        "Inspect the terminal trace before changing strategy."
                        if severity == "high"
                        else "No deterministic harness-level failure pattern was detected."
                    ),
                    "evidence_refs": ["metrics.status", "metrics.turns"],
                }
            ],
            "suggested_changes": [
                {
                    "id": "S1",
                    "finding_ids": ["F1"],
                    "change": "Inspect the terminal trace before changing harness behavior.",
                    "expected_effect": "Avoid changing strategy without run-level evidence.",
                    "validation": "Re-run the deterministic review against the same immutable bundle.",
                }
            ],
        },
        snapshot=snapshot,
    )


def _build_report(
    *,
    review_id: str,
    run_dir: Path,
    source: Mapping[str, Any],
    content: Mapping[str, Any],
    responses: Sequence[ModelResponse],
    model: str,
    offline: bool,
    implementation_commit: str,
    schema_retry_reasons: Sequence[str],
) -> dict[str, Any]:
    used_model = bool(responses)
    prompt_tokens = _sum_usage(responses, "prompt_tokens")
    completion_tokens = _sum_usage(responses, "completion_tokens")
    total_tokens = _sum_usage(responses, "total_tokens")
    return {
        "schema_version": META_REVIEW_SCHEMA_VERSION,
        "entity_type": "meta_review",
        "review_id": review_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "implementation_commit": implementation_commit,
        "source": dict(source),
        "invocation": {
            "mode": "offline" if offline else "model",
            "used_model": used_model,
            "model": model if used_model else None,
            "model_call_count": len(responses),
            "schema_retry_count": len(schema_retry_reasons),
            "schema_retry_reasons": list(schema_retry_reasons),
            "latency_ms": sum(response.latency_ms for response in responses),
            "attempt_count": sum(response.attempt_count for response in responses),
            "retry_reasons": [
                reason for response in responses for reason in response.retry_reasons
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            if used_model
            else None,
        },
        "summary": content["summary"],
        "findings": content["findings"],
        "suggested_changes": content["suggested_changes"],
        "quality_audit": content["quality_audit"],
        "source_verified_after_review": True,
    }


def _write_bundle(report: Mapping[str, Any], output_dir: Path) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"meta review already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir()
    try:
        json_path = temporary / "report.json"
        md_path = temporary / "report.md"
        json_path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(_format_markdown(report), encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "entity_type": "meta_review_manifest",
            "review_id": report.get("review_id"),
            "source_run_id": report.get("source", {}).get("run_id"),
            "artifacts": {
                "report_json": _artifact_record(json_path),
                "report_markdown": _artifact_record(md_path),
            },
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output_dir


def _format_markdown(report: Mapping[str, Any]) -> str:
    invocation = report["invocation"]
    lines = [
        "# miniCC Meta Review",
        "",
        f"- Review: `{report['review_id']}`",
        f"- Source run: `{report['source']['run_id']}`",
        f"- Mode: `{invocation['mode']}`",
        f"- Used model: `{'true' if invocation['used_model'] else 'false'}`",
        f"- Source verified after review: `{'true' if report['source_verified_after_review'] else 'false'}`",
        "",
        "## Summary",
        "",
        str(report["summary"]),
        "",
        "## Findings",
        "",
    ]
    for finding in report["findings"]:
        refs = ", ".join(finding["evidence_refs"])
        lines.append(f"- {finding['id']} / {finding['severity']} / {finding['area']}: {finding['message']} (evidence: {refs})")
    lines.extend(["", "## Suggested Changes", ""])
    if report["suggested_changes"]:
        for change in report["suggested_changes"]:
            links = ", ".join(change["finding_ids"])
            lines.append(f"- {change['id']} ({links}): {change['change']}")
            lines.append(f"  expected: {change['expected_effect']}")
            lines.append(f"  validation: {change['validation']}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _artifact_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _bundle_sha256(artifacts: Mapping[str, Any]) -> str:
    payload = json.dumps(artifacts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise MetaReviewError(f"invalid JSON source evidence: {label}") from exc
    if not isinstance(value, dict):
        raise MetaReviewError(f"source evidence must be an object: {label}")
    return value


def _trace_tail(data: bytes, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events[-limit:]


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def resolve_evidence_ref(snapshot: Mapping[str, Any], value: str) -> Any:
    if value == "diff_preview":
        if "diff_preview" not in snapshot:
            raise MetaReviewError("evidence reference does not exist: diff_preview")
        return snapshot["diff_preview"]
    trace_match = re.fullmatch(r"trace_tail\[(\d+)\](?:\.([A-Za-z0-9_-]+))?", value)
    if trace_match:
        trace = snapshot.get("trace_tail")
        index = int(trace_match.group(1))
        if not isinstance(trace, list) or index >= len(trace):
            raise MetaReviewError(f"evidence reference does not exist: {value}")
        current: Any = trace[index]
        field = trace_match.group(2)
        if field:
            if not isinstance(current, Mapping) or field not in current:
                raise MetaReviewError(f"evidence reference does not exist: {value}")
            current = current[field]
        return current
    object_match = re.fullmatch(
        r"(state|metrics|run_report)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)", value
    )
    if not object_match:
        raise MetaReviewError(f"unsupported evidence reference: {value}")
    current = snapshot.get(object_match.group(1))
    for field in object_match.group(2).split("."):
        if not isinstance(current, Mapping) or field not in current:
            raise MetaReviewError(f"evidence reference does not exist: {value}")
        current = current[field]
    return current


def _sum_usage(responses: Sequence[ModelResponse], field: str) -> int | None:
    values = [getattr(response.usage, field) for response in responses]
    present = [int(value) for value in values if value is not None]
    return sum(present) if present else None
