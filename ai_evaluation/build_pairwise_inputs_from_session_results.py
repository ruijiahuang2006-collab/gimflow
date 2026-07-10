from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

STUDY1_EXCLUDE = set()  # Public release: study-specific exclusion IDs are not redistributed.
EXPECTED_BY_STUDY = {"study1": 23, "study2": 10}

LEAKAGE_TERMS = [
    "kimusic",
    "baseline",
    "condition",
    "blueprint",
    "retrieval_keywords",
    "kimusic_proxy",
    "generation_model",
    "music_source",
    "generated_music",
    "generated",
    "database",
    "ai-generated",
    "generated music",
    "music generation",
    "retrieval",
    "database track",
    "generated track",
    "file_path",
    "full_path",
    "audio_file",
    "mp3",
    "insighttimer",
    "insight timer",
]

SYSTEM_DEBUG_PREFIXES = (
    "State classification requested",
    "Music criteria requested",
    "Session state:",
)

STATUS_RANK = {
    "complete": 4,
    "completed": 4,
    "post_session_complete": 3,
    "post-session-complete": 3,
    "post_session": 2,
    "music_complete": 1,
    "unknown": 0,
    "": 0,
}

MAX_TRANSCRIPT_WORDS = 650
MAX_TRANSCRIPT_CHARS = 3500
MAX_TURN_CHARS_PHASE_FINAL = 450
MAX_TURNS_PER_PHASE_FINAL = 6
MAX_CONVERSATION_EVIDENCE_CHARS_FINAL = 6500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build blinded pairwise AI-eval inputs from session_results only.")
    parser.add_argument("--study", choices=["study1", "study2"], required=True)
    parser.add_argument("--session-results", required=True, help="Folder containing session_results JSON snapshots.")
    parser.add_argument("--output", required=True, help="Output folder for pairwise inputs.")
    parser.add_argument("--expected-n", type=int, default=None, help="Expected participant count after dedup/filter.")
    parser.add_argument("--allow-n-mismatch", action="store_true", help="Do not fail if expected N does not match.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"SKIP malformed JSON {path}: {exc}", file=sys.stderr)
        return None
    if isinstance(data, dict):
        data["_source_file"] = str(path)
        try:
            data["_source_mtime"] = path.stat().st_mtime
        except OSError:
            data["_source_mtime"] = 0
        return data
    print(f"SKIP non-object JSON {path}", file=sys.stderr)
    return None


def status_value(session: dict[str, Any]) -> str:
    for key in ("completion_status", "status", "current_phase", "phase"):
        value = session.get(key)
        if value is not None:
            return str(value).strip().lower()
    return "unknown"


def status_rank(session: dict[str, Any]) -> int:
    return STATUS_RANK.get(status_value(session), 0)


def session_id(session: dict[str, Any]) -> str:
    for key in ("session_id", "session_uuid", "id"):
        value = session.get(key)
        if value:
            return str(value).strip()
    raw = "|".join(str(session.get(k, "")) for k in ("user_id", "session_number", "condition", "_source_file"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def user_id(session: dict[str, Any]) -> str:
    for key in ("user_id", "participant_id", "participant", "uid"):
        value = session.get(key)
        if value:
            return str(value).strip()
    return ""


def session_number(session: dict[str, Any]) -> int | None:
    try:
        return int(session.get("session_number"))
    except (TypeError, ValueError):
        return None


def condition_value(session: dict[str, Any]) -> str:
    value = session.get("condition") or session.get("assigned_condition") or session.get("mode")
    text = str(value or "").strip().lower()
    if text in {"kimusic", "ki_music", "ki-music"}:
        return "kimusic"
    if text in {"baseline", "control", "retrieval"}:
        return "baseline"
    return text


def participant_hash(uid: str) -> str:
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:8]


def scrub_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for term in sorted(LEAKAGE_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(term), "[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:\\[^\s]+", "[redacted_path]", text)
    text = re.sub(r"/home/[^\s]+", "[redacted_path]", text)
    return text



def truncate_words(text: str, max_words: int = MAX_TRANSCRIPT_WORDS, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Truncate transcript by both characters and whitespace-delimited words.

    The character cap is essential for Chinese transcripts, where split()-based
    word counting can fail because long passages may contain few spaces.
    """
    text = str(text or "")

    if len(text) > max_chars:
        head_chars = max_chars // 3
        tail_chars = max_chars - head_chars
        text = text[:head_chars] + "\n[Transcript middle truncated]\n" + text[-tail_chars:]

    words = text.split()
    if len(words) <= max_words:
        return text

    first_count = min(220, max_words // 3)
    last_count = max_words - first_count
    return " ".join(words[:first_count]) + "\n[Transcript middle truncated]\n" + " ".join(words[-last_count:])



def classify_dialogue_phase(phase: str, content: str) -> str:
    text = f"{phase} {content[:200]}".lower()

    if any(key in text for key in ["prelude", "opening", "initial", "intake", "before music", "??", "??", "???"]):
        return "Prelude / Initial emotional exploration"

    if any(key in text for key in ["induction", "grounding", "breath", "relax", "??", "??", "??"]):
        return "Induction / Grounding"

    if any(key in text for key in ["music", "imagery", "image", "listen", "listening", "player", "??", "??", "??", "???"]):
        return "Music and imagery"

    if any(key in text for key in ["postlude", "reflection", "after music", "integrat", "summary", "??", "??", "???", "??"]):
        return "Postlude / Reflection"

    return "Other therapeutic dialogue"


def trim_turn_text(text: str, max_chars: int = MAX_TURN_CHARS_PHASE_FINAL) -> str:
    text = scrub_text(text).strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + " [turn truncated] " + text[-tail:]


def collect_dialogue_turns(session: dict[str, Any]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []

    for field in ("conversation_log", "conversation_history", "chat_history", "messages", "dialogue", "transcript"):
        value = session.get(field)
        if not value:
            continue

        if isinstance(value, str):
            clean = trim_turn_text(value, max_chars=MAX_CONVERSATION_EVIDENCE_CHARS_FINAL)
            if clean:
                turns.append({
                    "phase": "Transcript",
                    "bucket": "Other therapeutic dialogue",
                    "role": "Dialogue",
                    "content": clean,
                })
            break

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    role = str(item.get("role") or item.get("speaker") or "speaker").strip().title()
                    phase = str(item.get("phase") or "").strip()
                    raw = item.get("content") or item.get("message") or item.get("text") or ""
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    role = str(item[0]).strip().title()
                    phase = ""
                    raw = item[1]
                else:
                    continue

                raw_text = str(raw or "").strip()
                if not raw_text:
                    continue

                if role.lower() == "system" or phase.lower() == "system":
                    continue

                if any(raw_text.startswith(prefix) for prefix in SYSTEM_DEBUG_PREFIXES):
                    continue

                clean = trim_turn_text(raw_text)
                if not clean:
                    continue

                bucket = classify_dialogue_phase(phase, raw_text)
                turns.append({
                    "phase": phase,
                    "bucket": bucket,
                    "role": role,
                    "content": clean,
                })
            break

    return turns


def select_representative_turns(turns: list[dict[str, str]], max_per_phase: int = MAX_TURNS_PER_PHASE_FINAL) -> dict[str, list[dict[str, str]]]:
    preferred_order = [
        "Prelude / Initial emotional exploration",
        "Induction / Grounding",
        "Music and imagery",
        "Postlude / Reflection",
        "Other therapeutic dialogue",
    ]

    grouped: dict[str, list[dict[str, str]]] = {name: [] for name in preferred_order}
    for turn in turns:
        bucket = turn.get("bucket") or "Other therapeutic dialogue"
        if bucket not in grouped:
            grouped[bucket] = []
        grouped[bucket].append(turn)

    selected: dict[str, list[dict[str, str]]] = {}
    for bucket in preferred_order:
        items = grouped.get(bucket, [])
        if not items:
            continue

        if len(items) <= max_per_phase:
            chosen = items
        else:
            first_n = max_per_phase // 2
            last_n = max_per_phase - first_n
            chosen = items[:first_n] + items[-last_n:]

        # de-duplicate while preserving order
        seen = set()
        deduped = []
        for item in chosen:
            key = (item.get("role"), item.get("content"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        selected[bucket] = deduped

    return selected


def normalize_conversation(session: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    turns = collect_dialogue_turns(session)
    if not turns:
        return "[No conversation recorded]", {"conversation_missing": True, "conversation_excerpt_strategy": "phase_stratified_excerpts"}

    selected = select_representative_turns(turns)

    lines = [
        "Phase-stratified dialogue excerpts. These are representative user-assistant turns, not the full transcript."
    ]

    for phase, items in selected.items():
        lines.append("")
        lines.append(f"[{phase}]")
        for item in items:
            raw_phase = item.get("phase") or ""
            phase_label = f" ({raw_phase})" if raw_phase and raw_phase.lower() not in phase.lower() else ""
            lines.append(f"{item.get('role', 'Speaker')}{phase_label}: {item.get('content', '')}")

    result = "\n".join(lines).strip()

    if len(result) > MAX_CONVERSATION_EVIDENCE_CHARS_FINAL:
        head = MAX_CONVERSATION_EVIDENCE_CHARS_FINAL // 2
        tail = MAX_CONVERSATION_EVIDENCE_CHARS_FINAL - head
        result = result[:head] + "\n[Dialogue evidence truncated]\n" + result[-tail:]

    return result, {
        "conversation_missing": False,
        "conversation_excerpt_strategy": "phase_stratified_excerpts",
        "conversation_turns_available": len(turns),
    }


def emotion_from_session(session: dict[str, Any], phase: str) -> dict[str, Any]:
    payload = session.get(phase)
    if isinstance(payload, dict):
        sam = payload.get("sam") if isinstance(payload.get("sam"), dict) else {}
        panas = payload.get("panas") if isinstance(payload.get("panas"), dict) else {}
        out = {
            "sam_valence": sam.get("valence") or payload.get("sam_valence") or payload.get("valence"),
            "sam_arousal": sam.get("arousal") or payload.get("sam_arousal") or payload.get("arousal"),
            "panas_pa": panas.get("pa_score") or panas.get("positive_affect") or payload.get("panas_pa"),
            "panas_na": panas.get("na_score") or panas.get("negative_affect") or payload.get("panas_na"),
        }
        if any(v is not None for v in out.values()):
            return out
    fallback_key = "pre_emotion" if phase.startswith("pre") else "post_emotion"
    fallback = session.get(fallback_key)
    return fallback if isinstance(fallback, dict) else {}


def compute_deltas(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for out_key, key in [
        ("delta_sam_valence", "sam_valence"),
        ("delta_sam_arousal", "sam_arousal"),
        ("delta_panas_pa", "panas_pa"),
        ("delta_panas_na", "panas_na"),
    ]:
        try:
            out[out_key] = float(post[key]) - float(pre[key])
        except Exception:
            out[out_key] = None
    return out


def has_complete_emotion(session: dict[str, Any]) -> bool:
    pre = emotion_from_session(session, "pre_session")
    post = emotion_from_session(session, "post_session")
    required = ["sam_valence", "sam_arousal", "panas_pa", "panas_na"]
    return all(pre.get(k) is not None for k in required) and all(post.get(k) is not None for k in required)


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def va_to_9(value: Any) -> int | None:
    x = to_float(value)
    if x is None:
        return None
    if -1.0 <= x <= 1.0:
        return int(round(((x + 1.0) / 2.0) * 8.0 + 1.0))
    return int(round(max(1.0, min(9.0, x))))


def collect_va(session: dict[str, Any]) -> tuple[Any, Any]:
    candidates = []
    for key in ("target_state_va", "target_va", "current_state_va"):
        if isinstance(session.get(key), dict):
            candidates.append(session[key])
    generated = session.get("generated_music")
    if isinstance(generated, dict) and isinstance(generated.get("target_va"), dict):
        candidates.append(generated["target_va"])
    sequence = session.get("music_sequence")
    if isinstance(sequence, list):
        for track in sequence:
            if not isinstance(track, dict):
                continue
            for key in ("waypoint_va", "track_va", "target_va"):
                if isinstance(track.get(key), dict):
                    candidates.append(track[key])
            gen = track.get("generated_music")
            if isinstance(gen, dict) and isinstance(gen.get("target_va"), dict):
                candidates.append(gen["target_va"])
    for cand in candidates:
        v = cand.get("v", cand.get("valence"))
        a = cand.get("a", cand.get("arousal"))
        if v is not None or a is not None:
            return v, a
    return None, None


def collect_mood(session: dict[str, Any]) -> str:
    values = []
    for key in ("emotion_label", "mood", "focus_theme"):
        if session.get(key):
            values.append(session.get(key))
    sequence = session.get("music_sequence")
    if isinstance(sequence, list):
        for track in sequence:
            if isinstance(track, dict):
                for key in ("emotion_label", "mood"):
                    if track.get(key):
                        values.append(track.get(key))
                concept = track.get("concept")
                if isinstance(concept, dict) and isinstance(concept.get("moods"), list):
                    values.extend(concept.get("moods")[:3])
    generated = session.get("generated_music")
    if isinstance(generated, dict):
        concept = generated.get("concept")
        if isinstance(concept, dict) and isinstance(concept.get("moods"), list):
            values.extend(concept.get("moods")[:3])
    cleaned = []
    for value in values:
        text = scrub_text(value).strip()
        if text and text.lower() not in {"none", "unknown", "[redacted]"} and text not in cleaned:
            cleaned.append(text)
    return ", ".join(cleaned[:5]) if cleaned else "not available"


def count_music_segments(session: dict[str, Any]) -> int | str:
    sequence = session.get("music_sequence")
    if isinstance(sequence, list):
        return len(sequence)
    return "not available"


def format_music_profile(session: dict[str, Any]) -> str:
    v, a = collect_va(session)
    v9 = va_to_9(v)
    a9 = va_to_9(a)
    return "\n".join([
        "Music Profile",
        f"- Mood/affective descriptors: {collect_mood(session)}",
        f"- Emotional target/profile: Valence={v9 if v9 is not None else 'not available'}/9, Arousal={a9 if a9 is not None else 'not available'}/9",
        f"- Number of music segments: {count_music_segments(session)}",
    ])


def usability_from_session(session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    usability = session.get("usability") if isinstance(session.get("usability"), dict) else {}
    sus = {}
    if usability.get("sus_responses") is not None or usability.get("sus_score") is not None:
        sus = {
            "sus_responses": usability.get("sus_responses"),
            "sus_score": usability.get("sus_score"),
        }
    therapy = usability.get("therapy_experience") if isinstance(usability.get("therapy_experience"), dict) else {}
    return sus, therapy


def build_session_payload(session: dict[str, Any]) -> dict[str, Any]:
    conversation, flags = normalize_conversation(session)
    pre = emotion_from_session(session, "pre_session")
    post = emotion_from_session(session, "post_session")
    sus, therapy = usability_from_session(session)
    flags.update({
        "sus_results_missing": not bool(sus),
        "therapy_experience_results_missing": not bool(therapy),
        "source_status": status_value(session),
    })
    return {
        "session_number": session_number(session),
        "pre_emotion": pre,
        "post_emotion": post,
        "emotion_deltas": compute_deltas(pre, post),
        "conversation_summary": conversation,
        "focus_theme": scrub_text(session.get("focus_theme") or ""),
        "postlude_summary": scrub_text(session.get("postlude_summary") or ""),
        "music_profile": format_music_profile(session),
        "sus_results": sus,
        "therapy_experience_results": therapy,
        "field_flags": flags,
    }


def leakage_scan(payload: Any, path: str = "") -> list[str]:
    if path.startswith("_"):
        return []
    hits: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            child = f"{path}.{k}" if path else str(k)
            hits.extend(leakage_scan(v, child))
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            hits.extend(leakage_scan(v, f"{path}[{i}]"))
    elif isinstance(payload, str):
        lowered = payload.lower()
        for term in LEAKAGE_TERMS:
            if term.lower() in lowered:
                hits.append(f"{path}: {term}")
    return hits


def load_sessions(session_results_dir: Path) -> list[dict[str, Any]]:
    sessions = []
    for path in sorted(session_results_dir.glob("*.json")):
        item = load_json(path)
        if item is not None:
            sessions.append(item)
    return sessions


def dedup_sessions(sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for session in sessions:
        sid = session_id(session)
        old = by_id.get(sid)
        if old is None:
            by_id[sid] = session
            continue
        duplicates += 1
        old_key = (status_rank(old), float(old.get("_source_mtime") or 0))
        new_key = (status_rank(session), float(session.get("_source_mtime") or 0))
        if new_key >= old_key:
            by_id[sid] = session
    summary = {
        "raw_snapshot_count": len(sessions),
        "deduped_session_count": len(by_id),
        "duplicate_snapshots_removed": duplicates,
        "status_counts_after_dedup": dict(Counter(status_value(s) for s in by_id.values())),
    }
    return list(by_id.values()), summary


def eligible_participants(sessions: list[dict[str, Any]], study: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []
    excluded = STUDY1_EXCLUDE if study == "study1" else set()

    for session in sessions:
        uid = user_id(session)
        if not uid:
            skipped.append({"reason": "missing_user_id", "source_file": session.get("_source_file")})
            continue
        if uid in excluded:
            skipped.append({"user_id": uid, "reason": "study1_excluded_test_uid"})
            continue
        grouped[uid].append(session)

    eligible: dict[str, list[dict[str, Any]]] = {}
    for uid, items in sorted(grouped.items()):
        nums = [session_number(s) for s in items]
        conds = [condition_value(s) for s in items]
        if len(items) != 2:
            skipped.append({"user_id": uid, "reason": f"expected_2_deduped_sessions_found_{len(items)}", "session_numbers": nums, "conditions": conds})
            continue
        if set(nums) != {1, 2}:
            skipped.append({"user_id": uid, "reason": "session_numbers_not_1_and_2", "session_numbers": nums, "conditions": conds})
            continue
        if set(conds) != {"kimusic", "baseline"}:
            skipped.append({"user_id": uid, "reason": "conditions_not_kimusic_and_baseline", "session_numbers": nums, "conditions": conds})
            continue
        if not all(has_complete_emotion(s) for s in items):
            skipped.append({"user_id": uid, "reason": "missing_complete_pre_post_sam_panas", "session_numbers": nums, "conditions": conds})
            continue
        eligible[uid] = sorted(items, key=lambda s: session_number(s) or 0)
    return eligible, skipped


def build_pairwise_for_participant(uid: str, sessions: list[dict[str, Any]], study: str) -> list[dict[str, Any]]:
    root_hash = participant_hash(uid)
    by_condition = {condition_value(s): s for s in sessions}
    kimusic = by_condition["kimusic"]
    baseline = by_condition["baseline"]

    def one(position: str, a_session: dict[str, Any], b_session: dict[str, Any]) -> dict[str, Any]:
        pair_id = f"{root_hash}_{position}"
        payload = {
            "_participant_hash": pair_id,
            "_root_participant_hash": root_hash,
            "_study": study,
            "_pair_id": pair_id,
            "_swap_position": position,
            "_label_map": {
                "A": condition_value(a_session),
                "B": condition_value(b_session),
            },
            "session_a": build_session_payload(a_session),
            "session_b": build_session_payload(b_session),
        }
        hits = leakage_scan(payload)
        payload["_build_warnings"] = {"leakage_hits_outside_private_metadata": hits}
        return payload

    return [
        one("pos1", kimusic, baseline),
        one("pos2", baseline, kimusic),
    ]


def write_outputs(outputs: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.json"):
        old.unlink()
    for payload in outputs:
        path = output_dir / f"{payload['_pair_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    session_results_dir = Path(args.session_results)
    output_dir = Path(args.output)
    expected_n = args.expected_n if args.expected_n is not None else EXPECTED_BY_STUDY[args.study]

    if not session_results_dir.exists():
        print(f"Session results folder not found: {session_results_dir}", file=sys.stderr)
        return 1

    raw_sessions = load_sessions(session_results_dir)
    deduped, dedup_summary = dedup_sessions(raw_sessions)
    eligible, skipped = eligible_participants(deduped, args.study)

    actual_n = len(eligible)
    print(f"Study: {args.study}")
    print(f"Session results dir: {session_results_dir}")
    print(f"Raw JSON snapshots loaded: {dedup_summary['raw_snapshot_count']}")
    print(f"Deduped sessions: {dedup_summary['deduped_session_count']}")
    print(f"Duplicate snapshots removed: {dedup_summary['duplicate_snapshots_removed']}")
    print(f"Status counts after dedup: {dedup_summary['status_counts_after_dedup']}")
    print(f"Eligible participants after dedup/filter: {actual_n}")
    print(f"Expected participants: {expected_n}")
    print(f"Skipped records/participants: {len(skipped)}")

    if actual_n != expected_n and not args.allow_n_mismatch:
        debug_path = output_dir / "build_debug_skipped.json"
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps({"dedup_summary": dedup_summary, "skipped": skipped}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ERROR: expected N={expected_n}, got N={actual_n}. Debug written to {debug_path}", file=sys.stderr)
        return 2

    outputs: list[dict[str, Any]] = []
    for uid, sessions in eligible.items():
        outputs.extend(build_pairwise_for_participant(uid, sessions, args.study))

    write_outputs(outputs, output_dir)
    leakage_hits = sum(len(p.get("_build_warnings", {}).get("leakage_hits_outside_private_metadata", [])) for p in outputs)

    print(f"Pairwise files written: {len(outputs)}")
    print(f"Expected pairwise files: {expected_n * 2}")
    print(f"Leakage hits outside private metadata: {leakage_hits}")
    print(f"Output dir: {output_dir}")

    summary = {
        "study": args.study,
        "session_results_dir": str(session_results_dir),
        "output_dir": str(output_dir),
        "expected_participants": expected_n,
        "eligible_participants": actual_n,
        "pairwise_files_written": len(outputs),
        "dedup_summary": dedup_summary,
        "skipped": skipped,
        "leakage_hits_outside_private_metadata": leakage_hits,
    }
    (output_dir / "_build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if leakage_hits:
        print("ERROR: leakage hits found in evaluator-facing fields. Inspect _build_summary.json and pairwise inputs.", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


