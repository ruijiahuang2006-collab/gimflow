from __future__ import annotations
from typing import List
import json
import os
import random
import string
import time
import threading
import uuid
from datetime import datetime


DATA_DIR = "data"
ASSIGNMENTS_PATH = os.path.join(DATA_DIR, "condition_assignments.json")
SESSION_LOGS_PATH = os.path.join(DATA_DIR, "session_logs.jsonl")
WASHOUT_STATE_PATH = os.path.join(DATA_DIR, "washout_state.json")
CONDITIONS = ["kimusic", "baseline"]
WASHOUT_SECONDS = 5 * 60
_STATE_LOCK = threading.RLock()


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path, payload):
    _ensure_data_dir()
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def normalize_user_id(user_id):
    text = str(user_id or "").strip()
    return text


def generate_participant_id() -> str:
    with _STATE_LOCK:
        assignments = _read_json(ASSIGNMENTS_PATH, {})
        existing_ids = set(assignments.keys())
        existing_ids.update(record.get("user_id") for record in read_session_logs() if record.get("user_id"))

    for _ in range(100):
        token = "".join(random.SystemRandom().choices(string.ascii_uppercase + string.digits, k=6))
        participant_id = f"P{token}"
        if participant_id not in existing_ids:
            return participant_id

    return f"P{uuid.uuid4().hex[:8].upper()}"


def assign_condition_order(user_id: str) -> List[str]:
    user_id = normalize_user_id(user_id) or generate_participant_id()
    with _STATE_LOCK:
        assignments = _read_json(ASSIGNMENTS_PATH, {})
        if user_id in assignments and assignments[user_id] in (
            ["kimusic", "baseline"],
            ["baseline", "kimusic"],
        ):
            return assignments[user_id]

        order = list(CONDITIONS)
        random.SystemRandom().shuffle(order)
        assignments[user_id] = order
        _write_json(ASSIGNMENTS_PATH, assignments)
        return order


def read_session_logs(user_id: str = None) -> list[dict]:
    if not os.path.exists(SESSION_LOGS_PATH):
        return []

    user_id = normalize_user_id(user_id) if user_id is not None else None
    records = []
    with open(SESSION_LOGS_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if user_id is None or record.get("user_id") == user_id:
                records.append(record)
    return records


def get_next_session_info(user_id: str) -> dict:
    user_id = normalize_user_id(user_id) or generate_participant_id()
    condition_order = assign_condition_order(user_id)
    completed = read_session_logs(user_id)
    completed_count = min(len(completed), len(condition_order))
    session_number = min(completed_count + 1, len(condition_order))
    condition = condition_order[session_number - 1]
    washout = get_washout_status(user_id)
    return {
        "user_id": user_id,
        "condition_order": condition_order,
        "session_number": session_number,
        "condition": condition,
        "completed_sessions": completed_count,
        "all_sessions_complete": completed_count >= len(condition_order),
        "washout": washout,
        "washout_required": completed_count == 1 and washout.get("active", False),
    }


def init_session(user_id: str, condition: str) -> str:
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")
    if not normalize_user_id(user_id):
        raise ValueError("user_id is required")

    session_id = str(uuid.uuid4())
    _ensure_data_dir()
    return session_id


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def start_washout(user_id: str, seconds: int = WASHOUT_SECONDS) -> dict:
    user_id = normalize_user_id(user_id)
    if not user_id:
        raise ValueError("user_id is required for washout tracking")

    with _STATE_LOCK:
        state = _read_json(WASHOUT_STATE_PATH, {})
        start_epoch = time.time()
        end_epoch = start_epoch + int(seconds)
        payload = {
            "washout_start": datetime.fromtimestamp(start_epoch).isoformat(timespec="seconds"),
            "washout_end": datetime.fromtimestamp(end_epoch).isoformat(timespec="seconds"),
            "washout_start_epoch": start_epoch,
            "washout_end_epoch": end_epoch,
        }
        state[user_id] = payload
        _write_json(WASHOUT_STATE_PATH, state)
        return payload


def get_washout_status(user_id: str) -> dict:
    user_id = normalize_user_id(user_id)
    state = _read_json(WASHOUT_STATE_PATH, {})
    payload = state.get(user_id) or {}
    end_epoch = payload.get("washout_end_epoch")
    try:
        remaining_seconds = max(0, int(end_epoch - time.time()))
    except (TypeError, ValueError):
        remaining_seconds = 0

    return {
        **payload,
        "remaining_seconds": remaining_seconds,
        "active": remaining_seconds > 0,
    }

