from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

CRITERIA = [
    "emotional_alignment",
    "therapeutic_coherence",
    "music_emotion_fit",
    "engagement",
    "safety",
]

CONDITIONS = ["kimusic", "baseline"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate swapped AI evaluation outputs at participant level.")
    parser.add_argument("--raw-dir", required=True, help="Directory containing raw evaluation JSON outputs.")
    parser.add_argument("--output-json", required=True, help="Final analysis JSON path.")
    parser.add_argument("--expected-n", type=int, default=None, help="Expected participant count after grouping swaps.")
    parser.add_argument("--allow-n-mismatch", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"SKIP malformed JSON {path}: {exc}", file=sys.stderr)
        return None
    if isinstance(data, dict):
        data["_source_file"] = str(path)
        return data
    print(f"SKIP non-object JSON {path}", file=sys.stderr)
    return None


def raw_json_files(raw_dir: Path) -> list[Path]:
    return sorted(path for path in raw_dir.glob("*.json") if not path.name.startswith("_") and not path.name.endswith("_prompt.json"))


def root_hash(pair_hash: str) -> str:
    return re.sub(r"_pos[12]$", "", str(pair_hash))


def swap_position(pair_hash: str) -> str:
    m = re.search(r"_(pos[12])$", str(pair_hash))
    return m.group(1) if m else "unknown"


def validate_record(record: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not record.get("participant_hash"):
        missing.append("participant_hash")
    if record.get("winner_condition") not in {"kimusic", "baseline", "tie"}:
        missing.append("winner_condition")
    scores = record.get("scores_by_condition")
    if not isinstance(scores, dict):
        missing.append("scores_by_condition")
        return missing
    for condition in CONDITIONS:
        if not isinstance(scores.get(condition), dict):
            missing.append(f"scores_by_condition.{condition}")
            continue
        for criterion in CRITERIA:
            value = scores[condition].get(criterion)
            if not isinstance(value, int) or isinstance(value, bool):
                missing.append(f"scores_by_condition.{condition}.{criterion}")
    return missing


def load_valid_records(raw_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid = []
    skipped = []
    for path in raw_json_files(raw_dir):
        record = load_json(path)
        if record is None:
            skipped.append({"file": str(path), "reason": "malformed_or_non_object"})
            continue
        missing = validate_record(record)
        if missing:
            skipped.append({"file": str(path), "reason": "missing_expected_fields", "missing": missing})
            continue
        valid.append(record)
    return valid, skipped


def aggregate_winner(winners: list[str], confidences: list[float]) -> tuple[str, float, str]:
    w1, w2 = winners
    mean_conf = mean(confidences) if confidences else 0.0

    if w1 == w2:
        return w1, round(mean_conf, 4), "agree"

    if "tie" in winners:
        non_tie = [w for w in winners if w != "tie"]
        if non_tie:
            return non_tie[0], round(mean_conf * 0.5, 4), "one_tie_one_preference"
        return "tie", round(mean_conf, 4), "both_tie"

    return "tie", 0.0, "opposite_winners_position_sensitive"


def mean_scores(evals: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {condition: {} for condition in CONDITIONS}
    for condition in CONDITIONS:
        for criterion in CRITERIA:
            values = [ev["scores_by_condition"][condition][criterion] for ev in evals]
            out[condition][criterion] = round(mean(values), 4)
    return out


def aggregate_participant(root: str, evals: list[dict[str, Any]]) -> dict[str, Any]:
    evals_sorted = sorted(evals, key=lambda ev: swap_position(ev.get("participant_hash", "")))

    winners = [ev["winner_condition"] for ev in evals_sorted]
    confidences = [float(ev.get("confidence") or 0.0) for ev in evals_sorted]
    strengths = [int(ev.get("preference_strength") or 1) for ev in evals_sorted]

    winner, agg_confidence, agreement_type = aggregate_winner(winners, confidences)
    scores = mean_scores(evals_sorted)

    kimusic_overall = mean(scores["kimusic"][c] for c in CRITERIA)
    baseline_overall = mean(scores["baseline"][c] for c in CRITERIA)

    return {
        "participant_hash": root,
        "participant_winner": winner,
        "aggregated_confidence": agg_confidence,
        "aggregated_preference_strength": round(mean(strengths), 4),
        "swap_winners": winners,
        "swap_confidences": confidences,
        "swap_preference_strengths": strengths,
        "agreement_type": agreement_type,
        "scores_by_condition": scores,
        "kimusic_overall_mean": round(kimusic_overall, 4),
        "baseline_overall_mean": round(baseline_overall, 4),
        "diff_overall_mean": round(kimusic_overall - baseline_overall, 4),
        "source_files": [ev.get("_source_file") for ev in evals_sorted],
        "reasoning_by_swap": [ev.get("reasoning") for ev in evals_sorted],
    }


def criterion_summary(participants: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out = {}
    for criterion in CRITERIA:
        k_vals = [p["scores_by_condition"]["kimusic"][criterion] for p in participants]
        b_vals = [p["scores_by_condition"]["baseline"][criterion] for p in participants]
        k_mean = mean(k_vals) if k_vals else 0.0
        b_mean = mean(b_vals) if b_vals else 0.0
        out[criterion] = {
            "kimusic_mean": round(k_mean, 4),
            "baseline_mean": round(b_mean, 4),
            "mean_difference_kimusic_minus_baseline": round(k_mean - b_mean, 4),
        }
    return out


def build_analysis(records: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        pair_hash = str(record["participant_hash"])
        groups[root_hash(pair_hash)].append(record)

    participant_results = []
    group_warnings = []

    for root, evals in sorted(groups.items()):
        if len(evals) != 2:
            group_warnings.append({
                "participant_hash": root,
                "reason": f"expected_2_position_swaps_found_{len(evals)}",
                "pair_hashes": [ev.get("participant_hash") for ev in evals],
            })
            continue

        positions = {swap_position(ev.get("participant_hash", "")) for ev in evals}
        if positions != {"pos1", "pos2"}:
            group_warnings.append({
                "participant_hash": root,
                "reason": "missing_pos1_or_pos2_suffix",
                "positions": sorted(positions),
                "pair_hashes": [ev.get("participant_hash") for ev in evals],
            })
            continue

        participant_results.append(aggregate_participant(root, evals))

    total = len(participant_results)
    kimusic_wins = sum(1 for p in participant_results if p["participant_winner"] == "kimusic")
    baseline_wins = sum(1 for p in participant_results if p["participant_winner"] == "baseline")
    ties = sum(1 for p in participant_results if p["participant_winner"] == "tie")

    inconsistent = sum(1 for p in participant_results if p["swap_winners"][0] != p["swap_winners"][1])
    opposite = sum(1 for p in participant_results if set(p["swap_winners"]) == {"kimusic", "baseline"})

    k_overall = mean([p["kimusic_overall_mean"] for p in participant_results]) if participant_results else 0.0
    b_overall = mean([p["baseline_overall_mean"] for p in participant_results]) if participant_results else 0.0

    return {
        "participant_count": total,
        "raw_evaluation_files_loaded": len(records),
        "raw_evaluation_files_skipped": len(skipped),
        "skipped_files": skipped,
        "group_warnings": group_warnings,
        "winner_distribution": {
            "kimusic_wins": kimusic_wins,
            "baseline_wins": baseline_wins,
            "ties": ties,
            "kimusic_win_rate": round(kimusic_wins / total, 4) if total else 0.0,
            "baseline_win_rate": round(baseline_wins / total, 4) if total else 0.0,
            "tie_rate": round(ties / total, 4) if total else 0.0,
        },
        "position_swap_reliability": {
            "position_inconsistency_count": inconsistent,
            "position_flip_rate_any_disagreement": round(inconsistent / total, 4) if total else 0.0,
            "opposite_winner_count": opposite,
            "opposite_winner_rate": round(opposite / total, 4) if total else 0.0,
        },
        "criterion_level_summary": criterion_summary(participant_results),
        "overall_score_summary": {
            "kimusic_mean": round(k_overall, 4),
            "baseline_mean": round(b_overall, 4),
            "mean_difference_kimusic_minus_baseline": round(k_overall - b_overall, 4),
        },
        "criteria": CRITERIA,
        "participant_results": participant_results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, participants: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "participant_hash",
        "participant_winner",
        "aggregated_confidence",
        "aggregated_preference_strength",
        "agreement_type",
        "swap_winners",
        "kimusic_overall_mean",
        "baseline_overall_mean",
        "diff_overall_mean",
    ]

    for criterion in CRITERIA:
        fields.extend([f"kimusic_{criterion}", f"baseline_{criterion}", f"diff_{criterion}"])

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for p in participants:
            row = {key: p.get(key) for key in fields}
            row["swap_winners"] = "|".join(p.get("swap_winners", []))

            for criterion in CRITERIA:
                k = p["scores_by_condition"]["kimusic"][criterion]
                b = p["scores_by_condition"]["baseline"][criterion]
                row[f"kimusic_{criterion}"] = k
                row[f"baseline_{criterion}"] = b
                row[f"diff_{criterion}"] = round(k - b, 4)

            writer.writerow(row)


def write_report(path: Path, analysis: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    d = analysis["winner_distribution"]
    r = analysis["position_swap_reliability"]

    lines = [
        "# AI Evaluation Summary with Position Swaps",
        "",
        "## Overall Results",
        "",
        f"* Participant count: {analysis['participant_count']}",
        f"* Kimusic wins: {d['kimusic_wins']}",
        f"* Baseline wins: {d['baseline_wins']}",
        f"* Ties: {d['ties']}",
        f"* Kimusic win rate: {d['kimusic_win_rate']}",
        f"* Baseline win rate: {d['baseline_win_rate']}",
        "",
        "## Position-Swap Reliability",
        "",
        f"* Any disagreement count: {r['position_inconsistency_count']}",
        f"* Position flip rate, any disagreement: {r['position_flip_rate_any_disagreement']}",
        f"* Opposite winner count: {r['opposite_winner_count']}",
        f"* Opposite winner rate: {r['opposite_winner_rate']}",
        "",
        "## Criterion-Level Summary",
        "",
        "| Criterion | Kimusic Mean | Baseline Mean | Difference |",
        "|---|---:|---:|---:|",
    ]

    for criterion, values in analysis["criterion_level_summary"].items():
        lines.append(
            f"| {criterion} | {values['kimusic_mean']} | "
            f"{values['baseline_mean']} | {values['mean_difference_kimusic_minus_baseline']} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    raw_dir = Path(args.raw_dir)
    output_json = Path(args.output_json)

    if not raw_dir.exists():
        print(f"Raw dir not found: {raw_dir}", file=sys.stderr)
        return 1

    records, skipped = load_valid_records(raw_dir)
    analysis = build_analysis(records, skipped)

    actual_n = analysis["participant_count"]
    if args.expected_n is not None and actual_n != args.expected_n and not args.allow_n_mismatch:
        debug_path = output_json.with_name(output_json.stem + "_debug_n_mismatch.json")
        write_json(debug_path, analysis)
        print(f"ERROR: expected N={args.expected_n}, got N={actual_n}. Debug written to {debug_path}", file=sys.stderr)
        return 2

    write_json(output_json, analysis)
    write_csv(output_json.with_suffix(".csv"), analysis["participant_results"])
    write_report(output_json.with_name(output_json.stem + "_report.md"), analysis)

    d = analysis["winner_distribution"]
    r = analysis["position_swap_reliability"]

    print(f"Participant count: {actual_n}")
    print(f"Kimusic wins: {d['kimusic_wins']}")
    print(f"Baseline wins: {d['baseline_wins']}")
    print(f"Ties: {d['ties']}")
    print(f"Position flip rate, any disagreement: {r['position_flip_rate_any_disagreement']}")
    print(f"Opposite winner rate: {r['opposite_winner_rate']}")
    print(f"Final JSON written: {output_json}")
    print(f"Final CSV written: {output_json.with_suffix('.csv')}")
    print(f"Final report written: {output_json.with_name(output_json.stem + '_report.md')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

