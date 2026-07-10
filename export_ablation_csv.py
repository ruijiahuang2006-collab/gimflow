from __future__ import annotations
import csv
import json
import os
import sys


DEFAULT_INPUT = os.path.join("data", "session_logs.jsonl")
DEFAULT_OUTPUT = os.path.join("data", "session_logs.csv")


def _va_from_sam(value):
    try:
        return (float(value) - 1.0) / 8.0
    except (TypeError, ValueError):
        return None


def _change(post_value, pre_value):
    if post_value is None or pre_value is None:
        return None
    return post_value - pre_value


def _read_jsonl(path):
    if not os.path.exists(path):
        return []

    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _row(record):
    pre = record.get("pre_emotion") or {}
    post = record.get("post_emotion") or {}
    pre_va_v = _va_from_sam(pre.get("sam_valence"))
    pre_va_a = _va_from_sam(pre.get("sam_arousal"))
    post_va_v = _va_from_sam(post.get("sam_valence"))
    post_va_a = _va_from_sam(post.get("sam_arousal"))

    return {
        "user_id": record.get("user_id"),
        "session_id": record.get("session_id"),
        "condition": record.get("condition"),
        "condition_order": "|".join(record.get("condition_order") or []),
        "session_number": record.get("session_number"),
        "pre_sam_valence": pre.get("sam_valence"),
        "pre_sam_arousal": pre.get("sam_arousal"),
        "pre_panas_pa": pre.get("panas_pa"),
        "pre_panas_na": pre.get("panas_na"),
        "post_sam_valence": post.get("sam_valence"),
        "post_sam_arousal": post.get("sam_arousal"),
        "post_panas_pa": post.get("panas_pa"),
        "post_panas_na": post.get("panas_na"),
        "pre_va_v": pre_va_v,
        "pre_va_a": pre_va_a,
        "post_va_v": post_va_v,
        "post_va_a": post_va_a,
        "pa_change": _change(post.get("panas_pa"), pre.get("panas_pa")),
        "na_change": _change(post.get("panas_na"), pre.get("panas_na")),
        "valence_shift": _change(post_va_v, pre_va_v),
        "arousal_shift": _change(post_va_a, pre_va_a),
        "sam_valence_change": _change(post.get("sam_valence"), pre.get("sam_valence")),
        "sam_arousal_change": _change(post.get("sam_arousal"), pre.get("sam_arousal")),
        "music_id": record.get("music_id"),
        "music_source": record.get("music_source"),
        "timestamp_start": record.get("timestamp_start"),
        "timestamp_end": record.get("timestamp_end"),
        "washout_start": record.get("washout_start"),
        "washout_end": record.get("washout_end"),
        "music_metadata": json.dumps(record.get("music_metadata") or {}, ensure_ascii=False),
    }


def export_jsonl_to_csv(input_path=DEFAULT_INPUT, output_path=DEFAULT_OUTPUT):
    records = _read_jsonl(input_path)
    rows = [_row(record) for record in records]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    fieldnames = list(_row({}).keys())
    with open(output_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


if __name__ == "__main__":
    input_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    print(export_jsonl_to_csv(input_path, output_path))

