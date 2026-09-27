"""Reports are projections of canonical run records."""
import html
import json


def render(store, run):
    assessment = run["state"].get("assessment", {})
    lines = ["# Mathematical research assessment", "", run["request"]["question"], "",
             f"Assessment: {run['overall_assessment']}.", "",
             "This assessment applies only to the recorded source regions and obligations.",
             "Formal checks establish their encoded statements; correspondence to source claims must be established separately.", "",
             "| Claim | Assessment |", "|---|---|"]
    if run["request"].get("formal_target"):
        lines[6:6] = ["The overall verdict applies only to the caller-supplied encoded target below; natural-language claim components retain their individual assessments.", "",
                       "```json", json.dumps(run["request"]["formal_target"], indent=2), "```", ""]
    for claim in assessment.get("claim_components", []):
        statement = claim["statement"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {statement} | {claim['assessment']} |")
    lines.extend(["", "## Unresolved obligations", ""])
    lines.extend(f"- {gap}" for gap in assessment.get("unresolved_obligations", []))
    lines.extend(["", "## Proof attempts, counterexamples, and verification", "", "```json", json.dumps(assessment, indent=2, ensure_ascii=False), "```", "",
                  "## Provenance", "", f"Run: {run['run_id']}", f"Graph revision: {store.revision}", "", "Receipts:"])
    lines.extend(f"- {receipt_id}" for receipt_id in run["receipt_ids"])
    markdown = "\n".join(lines) + "\n"
    html_report = "<!doctype html><meta charset='utf-8'><title>Research assessment</title><pre>" + html.escape(markdown) + "</pre>"
    bibtex = "\n".join("@misc{source" + str(i) + ",\n  title = {" + source["name"].replace("{", "").replace("}", "").replace("\\", "") + "},\n  note = {Archived source SHA256 " + source["artifact_id"] + "}\n}" for i, source in enumerate(run["state"].get("sources", [])))
    return [store.artifact(markdown.encode()), store.artifact(html_report.encode()), store.artifact(bibtex.encode())]
