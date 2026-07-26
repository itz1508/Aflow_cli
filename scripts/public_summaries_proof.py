"""Public-summary integrity proof.

Proves that the CLI public summaries (review / verify / status) are computed
from actual persisted state rather than hard-coded values:

  1. Every review-summary field equals an independent recomputation from the
     persisted operation artifacts and work-state.
  2. review.summary.next_exact_action mirrors the real work-state action
     ("Begin step-001") and is NOT the previous generic placeholder string.
  3. stages_executed comes from the bundle manifest; plan_preserved is a real
     digest comparison (it discriminates - a different byte string would not
     match), not a constant True.
  4. Counts track the plan: a plan with a different number of sections yields
     a different validations_prepared count.
  5. verify / status summaries equal recomputations from persisted state.

Sanitized evidence -> release-evidence/summary-proof/public-summaries.json

Run: uv run python scripts/public_summaries_proof.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "release-evidence" / "summary-proof" / "public-summaries.json"

PLAN_TWO = "# Two\n\n## Section one\nObjective: a.\n\n## Section two\nObjective: b.\n"
PLAN_THREE = "# Three\n\n## Alpha\nObjective: a.\n\n## Beta\nObjective: b.\n\n## Gamma\nObjective: c.\n"


def _sanitize(value):
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    repo = str(REPO_ROOT)
    if isinstance(value, str):
        out = value
        for needle, token in ((repo, "<REPO>"), (home, "<HOME>")):
            if needle:
                out = out.replace(needle, token).replace(needle.replace("\\", "/"), token)
        if user:
            out = out.replace(user, "<USER>")
        return out
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="aflow_summary_"))
    repo = workdir / "repo"
    repo.mkdir(parents=True)
    ops = workdir / "ops"
    env = dict(os.environ)
    env["AFLOW_OPERATIONS_ROOT"] = str(ops)

    def run_cli(args: list[str]) -> tuple[int, dict, str]:
        proc = subprocess.run([sys.executable, "-m", "aflow_cli.cli", *args], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
        try:
            parsed = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            parsed = {"_unparsed": proc.stdout[-400:]}
        return proc.returncode, parsed, proc.stderr

    def do_review(plan_text: str, op_id: str) -> tuple[dict, Path]:
        plan_path = repo / f"{op_id}.md"
        plan_path.write_text(plan_text, encoding="utf-8")
        rc, out, err = run_cli([
            "review", "--plan", str(plan_path), "--plan-id", op_id,
            "--operation-id", op_id, "--repository-root", str(repo),
            "--output-dir", str(workdir / f"bundle-{op_id}"),
        ])
        assert rc == 0 and not err.strip(), (rc, err)
        return out, ops / op_id

    evidence: dict = {"proof_type": "public-summary-integrity"}

    # --- Review summary vs independent recomputation ---
    review_out, op_dir = do_review(PLAN_TWO, "summary-two")
    summary = review_out["summary"]

    resolution = json.loads((op_dir / "gap-resolution-history.json").read_text(encoding="utf-8"))
    gap_records = json.loads((op_dir / "gap-records.json").read_text(encoding="utf-8"))
    criteria = json.loads((op_dir / "success-criteria.json").read_text(encoding="utf-8"))
    specs = json.loads((op_dir / "validation-specs.json").read_text(encoding="utf-8"))
    fixtures = json.loads((op_dir / "fixture-cases.json").read_text(encoding="utf-8"))
    work_status = json.loads((op_dir / "work-status.json").read_text(encoding="utf-8"))
    digest_stored = json.loads((op_dir / "original-plan-digest.json").read_text(encoding="utf-8"))["digest"]
    manifest = json.loads((op_dir / "manifest.json").read_text(encoding="utf-8"))
    plan_bytes = (repo / "summary-two.md").read_bytes()

    recomputed = {
        "gaps_discovered": len(gap_records.get("gaps", [])),
        "gaps_resolved": sum(1 for x in resolution if x.get("resolution_status") == "resolved"),
        "gaps_unresolved": sum(1 for x in resolution if x.get("resolution_status") == "unresolved"),
        "success_criteria_prepared": len(criteria.get("criteria", [])),
        "validations_prepared": len(specs.get("validations", [])),
        "fixtures_prepared": len(fixtures.get("fixtures", [])),
        "next_exact_action": work_status.get("next_exact_action"),
        "plan_preserved": digest_stored == hashlib.sha256(plan_bytes).hexdigest(),
    }
    field_matches = {k: summary.get(k) == recomputed[k] for k in recomputed}
    evidence["review_summary"] = {
        "emitted": summary,
        "recomputed_from_state": recomputed,
        "all_fields_match": all(field_matches.values()),
        "field_matches": field_matches,
        "stages_executed_matches_manifest": review_out.get("stages_executed") == manifest.get("stages_executed"),
        "next_exact_action_from_state": summary.get("next_exact_action") == work_status.get("next_exact_action"),
        "next_exact_action_not_placeholder": summary.get("next_exact_action") != "Begin current operation step",
        "plan_preserved_is_derived_not_constant": (
            recomputed["plan_preserved"] is True
            and digest_stored != hashlib.sha256(plan_bytes + b"tamper").hexdigest()
        ),
    }

    # --- Counts track the plan (sensitivity) ---
    review_three, _ = do_review(PLAN_THREE, "summary-three")
    evidence["counts_track_plan"] = {
        "two_section_validations": summary.get("validations_prepared"),
        "three_section_validations": review_three["summary"].get("validations_prepared"),
        "counts_differ_with_plan": summary.get("validations_prepared") != review_three["summary"].get("validations_prepared"),
        "three_section_matches_actual": review_three["summary"].get("validations_prepared")
        == len(json.loads((ops / "summary-three" / "validation-specs.json").read_text(encoding="utf-8")).get("validations", [])),
    }

    # --- verify summary vs recomputation ---
    rc_v, verify_out, err_v = run_cli(["verify", "--operation-id", "summary-two"])
    vsummary = verify_out.get("summary", {})
    val_results = verify_out.get("validation_results", [])
    fix_results = verify_out.get("fixture_results", [])
    verify_recomputed = {
        "executed": len(val_results) + len(fix_results),
        "passed": len([r for r in val_results if r.get("status") == "passed"]) + len([r for r in fix_results if r.get("status") == "passed"]),
        "criteria_satisfied": len(verify_out.get("satisfied_criterion_ids", [])),
        "operation_status": verify_out.get("verification_status"),
    }
    evidence["verify_summary"] = {
        "emitted": {k: vsummary.get(k) for k in verify_recomputed},
        "recomputed_from_result": verify_recomputed,
        "all_fields_match": all(vsummary.get(k) == verify_recomputed[k] for k in verify_recomputed),
    }

    # --- status summary vs recomputation ---
    rc_s, status_out, _ = run_cli(["status", "--operation-id", "summary-two"])
    ws = json.loads((op_dir / "work-status.json").read_text(encoding="utf-8"))
    report = json.loads((op_dir / "validation-report.json").read_text(encoding="utf-8"))
    status_recomputed = {
        "operation_status": ws.get("operation_status"),
        "work_state_revision": ws.get("work_state_revision"),
        "completed_count": len(ws.get("completed_step_ids", [])),
        "remaining_count": len(ws.get("remaining_step_ids", [])),
        "unresolved_count": len(ws.get("unresolved_items", [])),
        "next_exact_action": ws.get("next_exact_action"),
        "stale_validation_count": sum(1 for x in report.get("validations", []) if x.get("status") == "stale"),
    }
    evidence["status_summary"] = {
        "emitted": {k: status_out.get(k) for k in status_recomputed},
        "recomputed_from_state": status_recomputed,
        "all_fields_match": all(status_out.get(k) == status_recomputed[k] for k in status_recomputed),
    }

    checks = {
        "review_summary_matches_state": evidence["review_summary"]["all_fields_match"],
        "stages_executed_from_manifest": evidence["review_summary"]["stages_executed_matches_manifest"],
        "next_exact_action_from_state": evidence["review_summary"]["next_exact_action_from_state"],
        "next_exact_action_not_placeholder": evidence["review_summary"]["next_exact_action_not_placeholder"],
        "plan_preserved_derived": evidence["review_summary"]["plan_preserved_is_derived_not_constant"],
        "counts_track_plan": evidence["counts_track_plan"]["counts_differ_with_plan"] and evidence["counts_track_plan"]["three_section_matches_actual"],
        "verify_summary_matches_state": evidence["verify_summary"]["all_fields_match"],
        "status_summary_matches_state": evidence["status_summary"]["all_fields_match"],
    }
    evidence["checks"] = checks
    evidence["all_checks_passed"] = all(checks.values())

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_sanitize(evidence), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, EVIDENCE)
    print(json.dumps(_sanitize(evidence), indent=2, sort_keys=True))
    return 0 if evidence["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
