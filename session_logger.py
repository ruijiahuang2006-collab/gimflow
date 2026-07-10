from __future__ import annotations
import copy
import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime


_SESSION_LOG_LOCK = threading.RLock()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def relative_existing_audio_path(*candidates):
    for candidate in candidates:
        if not candidate:
            continue
        path = str(candidate)
        check_path = path if os.path.isabs(path) else os.path.abspath(path)
        if os.path.isfile(check_path):
            return os.path.relpath(check_path).replace(os.sep, "/")
    return None


def compute_completion_metadata(session_data):
    post_session = session_data.get("post_session", {}) if isinstance(session_data, dict) else {}
    usability = session_data.get("usability", {}) if isinstance(session_data, dict) else {}
    post_assessments_done = bool(post_session.get("sam") and post_session.get("panas"))
    sus_done = usability.get("sus_responses") is not None
    therapy_done = usability.get("therapy_experience") is not None

    if post_assessments_done and sus_done and therapy_done:
        completion_status = "complete"
    elif post_assessments_done:
        completion_status = "post_session_complete"
    else:
        completion_status = "incomplete"

    timestamp_start = parse_timestamp(session_data.get("timestamp_start"))
    timestamp_end = parse_timestamp(session_data.get("timestamp_end"))
    duration_seconds = None
    if timestamp_start and timestamp_end:
        duration_seconds = max(0, int((timestamp_end - timestamp_start).total_seconds()))

    conversation_turns = sum(
        1
        for message in session_data.get("conversation_log", [])
        if isinstance(message, dict) and message.get("role") in {"user", "assistant"}
    )

    return {
        "completion_status": completion_status,
        "completed": completion_status == "complete",
        "session_duration_seconds": duration_seconds,
        "conversation_turns": conversation_turns,
    }


def normalize_va(va):
    if not isinstance(va, dict):
        return None

    v = va.get("v", va.get("valence"))
    a = va.get("a", va.get("arousal"))
    try:
        v = float(v)
        a = float(a)
    except (TypeError, ValueError):
        return None

    return {
        "v": max(-1.0, min(1.0, v)),
        "a": max(-1.0, min(1.0, a))
    }


def generate_waypoints(current_va, target_va, n_tracks=4):
    current_va = normalize_va(current_va)
    target_va = normalize_va(target_va)
    try:
        n_tracks = int(n_tracks)
    except (TypeError, ValueError):
        n_tracks = 4

    if not current_va or not target_va or n_tracks <= 0:
        return []

    if n_tracks == 1:
        return [{"index": 0, "v": round(current_va["v"], 3), "a": round(current_va["a"], 3)}]

    waypoints = []
    for index in range(n_tracks):
        t = index / (n_tracks - 1)
        v = current_va["v"] + t * (target_va["v"] - current_va["v"])
        a = current_va["a"] + t * (target_va["a"] - current_va["a"])
        waypoints.append({"index": index, "v": round(v, 3), "a": round(a, 3)})
    return waypoints


def euclidean_va_distance(va1, va2):
    va1 = normalize_va(va1)
    va2 = normalize_va(va2)
    if not va1 or not va2:
        return None
    return round(((va1["v"] - va2["v"]) ** 2 + (va1["a"] - va2["a"]) ** 2) ** 0.5, 3)


def assign_track_to_waypoint(track_va, waypoint_sequence, fallback_index=None):
    if not waypoint_sequence:
        return None, None, None

    normalized_track_va = normalize_va(track_va)
    if normalized_track_va:
        best_waypoint = min(
            waypoint_sequence,
            key=lambda waypoint: euclidean_va_distance(normalized_track_va, waypoint) or float("inf")
        )
        return best_waypoint.get("index"), copy.deepcopy(best_waypoint), euclidean_va_distance(normalized_track_va, best_waypoint)

    if fallback_index is not None and 0 <= fallback_index < len(waypoint_sequence):
        waypoint = waypoint_sequence[fallback_index]
        return waypoint.get("index"), copy.deepcopy(waypoint), None

    return None, None, None


def assign_generated_track_to_final_waypoint(track_va, waypoint_sequence):
    if not waypoint_sequence:
        return None, None, None

    normalized_track_va = normalize_va(track_va)
    final_waypoint = waypoint_sequence[-1]
    distance = euclidean_va_distance(normalized_track_va, final_waypoint)
    if distance == 0:
        return final_waypoint.get("index"), copy.deepcopy(final_waypoint), distance
    return None, None, None


def clean_text(value, max_chars=None):
    if not isinstance(value, str):
        return None

    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None

    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def is_generic_focus_theme(value):
    text = clean_text(value)
    if not text:
        return True

    normalized = text.lower()
    generic_markers = [
        "\u6e29\u548c\u5730\u7ed3\u675f\u4f1a\u8bdd",
        "\u7ed3\u675f\u4f1a\u8bdd",
        "\u611f\u8c22\u53c2\u4e0e",
        "\u4f1a\u8bdd\u7ed3\u675f",
        "gentle closing",
        "end session",
        "finish session",
        "session ended",
        "thank you for participating",
        "exploring inner experiences",
    ]
    return any(marker in normalized for marker in generic_markers)


def is_takeaway_or_closing(value):
    text = clean_text(value)
    if not text:
        return True

    markers = [
        "\u5141\u8bb8\u81ea\u5df1",
        "\u653e\u8fc7\u6211\u81ea\u5df1",
        "\u653e\u8fc7\u81ea\u5df1",
        "\u5076\u5c14\u653e\u677e",
        "\u5076\u5c14\u4e5f\u8981",
        "\u6700\u8f9b\u82e6\u7684\u65f6\u5019",
        "\u4ee5\u540e",
        "\u672a\u6765",
        "\u4e0b\u6b21",
        "takeaway",
        "next time",
        "in the future",
        "allow myself",
    ]
    return is_generic_focus_theme(text) or any(marker in text.lower() for marker in markers)


def get_user_messages_by_phase(session_data, phase, limit=None):
    messages = []
    for message in session_data.get("conversation_log", []):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and message.get("phase") == phase:
            text = clean_text(message.get("content"), max_chars=120)
            if text:
                messages.append(text)
                if limit and len(messages) >= limit:
                    break
    return messages


def contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def synthesize_chinese_focus_theme(text):
    if not has_cjk(text):
        return None

    concerns = []
    if contains_any(text, ["\u5b66\u4e1a", "\u5b66\u4e60", "\u4f5c\u4e1a", "\u8bba\u6587", "\u8bfe\u4e1a"]):
        concerns.append("\u5b66\u4e1a\u538b\u529b")
    if contains_any(text, ["\u8003\u8bd5", "\u5907\u8003"]):
        concerns.append("\u8003\u8bd5\u7126\u8651")
    if contains_any(text, ["\u5de5\u4f5c", "\u804c\u573a", "\u9879\u76ee"]):
        concerns.append("\u5de5\u4f5c\u538b\u529b")
    if contains_any(text, ["\u4eba\u9645", "\u5173\u7cfb", "\u5bb6\u5ead"]):
        concerns.append("\u4eba\u9645\u6216\u5173\u7cfb\u538b\u529b")
    if not concerns and "\u538b\u529b" in text:
        concerns.append("\u538b\u529b")

    states = []
    if contains_any(text, ["\u7d2f", "\u75b2\u60eb", "\u75b2\u52b3", "\u75b2\u60eb"]):
        states.append("\u75b2\u60eb")
    if contains_any(text, ["\u7d27\u7ef7", "\u7d27\u5f20", "\u538b\u529b", "\u7ef7"]):
        states.append("\u7d27\u7ef7")
    if contains_any(text, ["\u7126\u8651", "\u62c5\u5fc3", "\u4e0d\u5b89"]):
        states.append("\u7126\u8651")
    if contains_any(text, ["\u96be\u8fc7", "\u4f4e\u843d", "\u6cae\u4e27"]):
        states.append("\u4f4e\u843d")

    intentions = []
    if contains_any(text, ["\u653e\u677e", "\u677e\u4e00\u4e0b"]):
        intentions.append("\u653e\u677e\u9700\u6c42")
    if contains_any(text, ["\u8c03\u8282", "\u7f13\u89e3", "\u5e73\u9759", "\u5e73\u590d"]):
        intentions.append("\u60c5\u7eea\u8c03\u8282\u9700\u6c42")
    if contains_any(text, ["\u4f11\u606f", "\u8c03\u6574"]):
        intentions.append("\u4f11\u606f\u4e0e\u8c03\u6574\u9700\u6c42")

    if "\u8003\u8bd5\u7126\u8651" in concerns:
        if "\u60c5\u7eea\u8c03\u8282\u9700\u6c42" not in intentions:
            intentions.append("\u60c5\u7eea\u8c03\u8282\u9700\u6c42")
        return "\u8003\u8bd5\u7126\u8651\u4e0e" + "\u3001".join(intentions)

    pieces = []
    if concerns:
        pieces.append("\u3001".join(dict.fromkeys(concerns)))
    if states:
        pieces.append("\u3001".join(dict.fromkeys(states)))
    if intentions:
        pieces.append("\u3001".join(dict.fromkeys(intentions)))
    if pieces:
        return "\u4e0b\u7684".join(pieces[:2]) + ("\u4e0e" + pieces[2] if len(pieces) > 2 else "")
    return None


def synthesize_english_focus_theme(text):
    normalized = text.lower()
    concerns = []
    if contains_any(normalized, ["academic", "study", "school", "coursework", "thesis"]):
        concerns.append("academic stress")
    if contains_any(normalized, ["exam", "test"]):
        concerns.append("exam anxiety")
    if contains_any(normalized, ["work", "job", "project"]):
        concerns.append("work stress")
    if not concerns and "stress" in normalized:
        concerns.append("stress")

    states = []
    if contains_any(normalized, ["tired", "exhausted", "fatigue"]):
        states.append("fatigue")
    if contains_any(normalized, ["tense", "tension", "stressed"]):
        states.append("tension")
    if contains_any(normalized, ["anxious", "anxiety", "worried"]):
        states.append("anxiety")

    intentions = []
    if contains_any(normalized, ["relax", "rest", "calm"]):
        intentions.append("need for relaxation")
    if contains_any(normalized, ["regulate", "manage", "cope"]):
        intentions.append("emotional regulation need")

    pieces = concerns + states + intentions
    return ", ".join(dict.fromkeys(pieces)) if pieces else None


def extract_focus_theme(session_data, candidate=None):
    if not isinstance(session_data, dict):
        return None

    prelude_user_messages = [
        text for text in get_user_messages_by_phase(session_data, "prelude", limit=5)
        if not is_takeaway_or_closing(text)
    ]
    if prelude_user_messages:
        combined = " ".join(prelude_user_messages)
        synthesized = synthesize_chinese_focus_theme(combined) or synthesize_english_focus_theme(combined)
        if synthesized:
            return synthesized

    for value in (candidate, session_data.get("focus_theme")):
        text = clean_text(value, max_chars=120)
        if text and not is_takeaway_or_closing(text):
            return text

    return prelude_user_messages[0] if prelude_user_messages else None


def has_cjk(text):
    return isinstance(text, str) and re.search(r"[\u4e00-\u9fff]", text) is not None


def collect_postlude_user_texts(session_data):
    texts = []
    for message in session_data.get("conversation_log", []):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and message.get("phase") == "postlude":
            text = clean_text(message.get("content"), max_chars=140)
            if text and not is_admin_closing_text(text):
                texts.append(text)
    return texts


def collect_imagery_texts(session_data):
    texts = []
    for entry in session_data.get("music_sequence", []):
        if not isinstance(entry, dict):
            continue
        text = clean_text(entry.get("imagery_description"), max_chars=140)
        if text:
            texts.append(text)
    return texts


def is_admin_closing_text(text):
    text = clean_text(text)
    if not text:
        return True

    normalized = text.lower()
    useful_markers = [
        "\u6d77\u6ee9", "\u6d77\u9762", "\u6708\u5149", "\u6c99\u6ee9", "\u6d77\u6d6a", "\u68ee\u6797",
        "\u5e73\u9759", "\u5b81\u9759", "\u653e\u677e", "\u5b89\u7a33", "\u8f7b\u677e", "\u88ab\u652f\u6301",
        "\u51a5\u60f3", "\u7ed9\u81ea\u5df1\u65f6\u95f4", "\u653e\u8fc7", "\u6682\u505c", "\u547c\u5438",
        "\u4f11\u606f", "\u6700\u8f9b\u82e6", "\u538b\u529b\u5347\u8d77",
        "beach", "ocean", "moonlight", "peace", "calm", "relief", "meditate", "pause", "breathe", "rest"
    ]
    if contains_any(normalized, useful_markers):
        return False

    admin_markers = [
        "\u8c22\u8c22",
        "\u7ed3\u675f\u4e86\u5417",
        "\u7ed3\u675f\u5417",
        "\u597d\u7684",
        "\u6211\u542c\u597d\u4e86",
        "\u542c\u597d\u4e86",
        "\u518d\u89c1",
        "thank",
        "is it over",
        "are we done",
        "okay",
        "ok",
        "i finished listening",
    ]
    stripped = re.sub(r"[\s\uff0c\u3002\uff1f?!,.]", "", normalized)
    if stripped in {"\u597d", "\u597d\u7684", "ok", "okay"}:
        return True
    return any(marker in normalized for marker in admin_markers)


def is_imagery_text(text):
    text = clean_text(text)
    if not text:
        return False

    imagery_markers = [
        "\u6d77\u6ee9", "\u6d77\u9762", "\u6708\u5149", "\u6c99\u6ee9", "\u6d77\u6d6a", "\u68ee\u6797",
        "\u623f\u95f4", "\u5c0f\u8def", "\u5149", "\u989c\u8272", "\u6811", "\u6cb3", "\u6c34",
        "\u5c71", "\u5929\u7a7a", "\u4e91", "\u753b\u9762", "\u770b\u5230", "\u573a\u666f",
        "beach", "shore", "ocean", "sea", "moonlight", "moon", "sand", "wave",
        "forest", "room", "path", "light", "color", "river", "water", "sky", "scene", "saw"
    ]
    return contains_any(text.lower(), imagery_markers)


def is_action_text(text):
    text = clean_text(text)
    if not text:
        return False

    action_markers = [
        "\u51a5\u60f3", "\u7ed9\u81ea\u5df1\u65f6\u95f4", "\u7559\u51fa\u65f6\u95f4", "\u653e\u8fc7\u81ea\u5df1",
        "\u653e\u8fc7\u6211\u81ea\u5df1", "\u6682\u505c", "\u547c\u5438", "\u4f11\u606f", "\u8c03\u6574",
        "\u5141\u8bb8", "\u6700\u8f9b\u82e6\u7684\u65f6\u5019", "\u538b\u529b\u5347\u8d77",
        "meditate", "meditation", "give myself time", "make time", "allow myself",
        "pause", "breathe", "breathing", "rest", "adjust", "when stress rises"
    ]
    return contains_any(text.lower(), action_markers)


def is_emotion_text(text):
    text = clean_text(text)
    if not text or is_action_text(text) or is_imagery_text(text):
        return False

    emotion_markers = [
        "\u5e73\u9759", "\u5b81\u9759", "\u653e\u677e", "\u8f7b\u677e", "\u677e\u4e86\u4e00\u53e3\u6c14",
        "\u5b89\u7a33", "\u5b89\u5fc3", "\u91ca\u7136", "\u5b89\u5168", "\u6e29\u6696", "\u88ab\u652f\u6301",
        "\u4f11\u606f", "\u7f13\u548c", "\u6e34\u671b", "\u7126\u8651", "\u7d27\u5f20",
        "\u60b2\u4f24", "\u96be\u8fc7", "\u9ad8\u5174",
        "peace", "peaceful", "calm", "relief", "relaxed", "safe", "warm", "anxious", "sad", "happy"
    ]
    return contains_any(text.lower(), emotion_markers)


def describe_imagery(text):
    text = clean_text(text, max_chars=80)
    if not text:
        return None
    if has_cjk(text):
        if "\u6708\u5149" in text and ("\u6d77\u6ee9" in text or "\u6c99\u6ee9" in text):
            return "\u6708\u5149\u4e0b\u7684\u6d77\u6ee9"
        if "\u6708\u5149" in text and "\u6d77" in text:
            return "\u6708\u5149\u4e0b\u7684\u6d77\u9762"
        if "\u6d77\u6ee9" in text or "\u6c99\u6ee9" in text:
            return "\u6d77\u6ee9"
        if "\u6d77" in text:
            return "\u5e73\u9759\u6d77\u9762" if "\u5e73\u9759" in text else "\u6d77\u9762"
        return text
    return text


def infer_emotion(imagery_text, explicit_emotion=None):
    emotion = clean_text(explicit_emotion, max_chars=80)
    if emotion:
        if has_cjk(emotion) and "\u5e73\u9759" in emotion and "\u6e34\u671b" in emotion:
            return "\u6e34\u671b\u5df2\u4e45\u7684\u5e73\u9759"
        return emotion

    text = clean_text(imagery_text) or ""
    if has_cjk(text):
        if contains_any(text, ["\u5e73\u9759", "\u6d77", "\u6c34", "\u6708\u5149"]):
            return "\u5b81\u9759\u4e0e\u653e\u677e"
        return "\u5185\u5728\u611f\u53d7\u7684\u53d8\u5316"

    normalized = text.lower()
    if contains_any(normalized, ["calm", "sea", "ocean", "water", "moonlight"]):
        return "calm and relaxation"
    return "a shift in inner experience"


def normalize_action_takeaway(action_texts):
    texts = [clean_text(text, max_chars=120) for text in action_texts]
    texts = [text for text in texts if text]
    if not texts:
        return None

    combined = " ".join(texts)
    if has_cjk(combined):
        has_meditation = "\u51a5\u60f3" in combined
        has_self_time = contains_any(combined, ["\u7ed9\u81ea\u5df1\u65f6\u95f4", "\u7559\u51fa\u65f6\u95f4"])
        has_self_kindness = contains_any(combined, ["\u653e\u8fc7", "\u5141\u8bb8"])
        has_pressure_pause = contains_any(combined, ["\u4f11\u606f", "\u6700\u8f9b\u82e6", "\u538b\u529b"])
        if has_meditation or has_self_time or has_self_kindness or has_pressure_pause:
            clauses = []
            if has_self_kindness:
                clauses.append("\u81ea\u5df1\u9700\u8981\u5076\u5c14\u653e\u8fc7\u81ea\u5df1")
            if has_meditation or has_self_time:
                clauses.append("\u8ba1\u5212\u6bcf\u5929\u7559\u51fa\u65f6\u95f4\u51a5\u60f3")
            if has_pressure_pause:
                clauses.append("\u5728\u538b\u529b\u5347\u8d77\u65f6\u7ed9\u81ea\u5df1\u77ed\u6682\u4f11\u606f\u548c\u8c03\u6574\u7684\u7a7a\u95f4")
            return "\uff0c\u5e76".join(clauses)
        return texts[-1]

    normalized = combined.lower()
    if contains_any(normalized, ["allow", "rest", "pause", "difficult", "hard"]):
        return "allowing themselves to pause and make room for rest during difficult moments"
    return texts[-1]


def extract_postlude_summary(session_data):
    if not isinstance(session_data, dict):
        return None

    existing_summary = session_data.get("postlude_summary")
    if isinstance(existing_summary, str) and existing_summary.strip():
        return existing_summary.strip()

    latest_assistant_postlude = None
    for message in session_data.get("conversation_log", []):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant" or message.get("phase") != "postlude":
            continue

        content = message.get("content")
        if isinstance(content, str) and content.strip():
            latest_assistant_postlude = content.strip()

    if not latest_assistant_postlude:
        return None

    summary_match = re.search(
        r"\[SUMMARY\]\s*(.*?)\s*\[/SUMMARY\]",
        latest_assistant_postlude,
        flags=re.DOTALL | re.IGNORECASE
    )
    if summary_match and summary_match.group(1).strip():
        return summary_match.group(1).strip()

    return latest_assistant_postlude


def generate_rule_based_postlude_summary(session_data):
    if not isinstance(session_data, dict):
        return None

    user_postlude_texts = collect_postlude_user_texts(session_data)
    music_imagery_texts = collect_imagery_texts(session_data)
    if not user_postlude_texts and not music_imagery_texts:
        return extract_postlude_summary(session_data)

    focus_theme = extract_focus_theme(session_data)
    user_imagery_texts = [text for text in user_postlude_texts if is_imagery_text(text)]
    emotion_texts = [text for text in user_postlude_texts if is_emotion_text(text)]
    action_texts = [text for text in user_postlude_texts if is_action_text(text)]

    imagery_text = (
        music_imagery_texts[-1]
        if music_imagery_texts
        else (user_imagery_texts[0] if user_imagery_texts else None)
    )
    if imagery_text is None and user_postlude_texts:
        imagery_text = user_postlude_texts[0]

    imagery_description = describe_imagery(imagery_text)
    emotion_description = infer_emotion(imagery_text, emotion_texts[-1] if emotion_texts else None)
    takeaway = normalize_action_takeaway(action_texts)
    use_chinese = has_cjk(" ".join([imagery_description or "", emotion_description or "", focus_theme or "", takeaway or ""]))

    if use_chinese:
        parts = []
        if focus_theme:
            parts.append("\u7528\u6237\u5e26\u7740" + focus_theme + "\u8fdb\u5165\u4f1a\u8bdd\u3002")
        if imagery_description:
            parts.append("\u5728\u97f3\u4e50\u4f53\u9a8c\u4e2d\uff0c\u51fa\u73b0\u4e86" + imagery_description + "\u610f\u8c61\u3002")
        if emotion_description:
            parts.append("\u7528\u6237\u611f\u53d7\u5230" + emotion_description + "\u3002")
        if takeaway:
            parts.append("\u7528\u6237\u610f\u8bc6\u5230" + takeaway + "\u3002")
        return "".join(parts)

    parts = []
    if focus_theme:
        parts.append(f"The user entered the session with {focus_theme}.")
    if imagery_description:
        parts.append(f"During the music experience, the key imagery was {imagery_description}.")
    if emotion_description:
        parts.append(f"The emotional experience was {emotion_description}.")
    if takeaway:
        parts.append(f"The takeaway was {takeaway}.")
    return " ".join(parts)


def create_empty_session(user_id="anonymous", condition="full"):
    return {
        "session_id": uuid.uuid4().hex,
        "user_id": user_id,
        "condition": condition,
        "timestamp_start": now_str(),
        "timestamp_end": None,
        "pre_session": {
            "sam": None,
            "panas": None
        },
        "conversation_log": [],
        "llm_estimated_va": {
            "current_state": None,
            "target_state": None
        },
        "current_state_va": None,
        "target_state_va": None,
        "focus_theme": None,
        "waypoint_sequence": [],
        "track_waypoint_mapping": [],
        "postlude_summary": None,
        "music_source": None,
        "generated_music": None,
        "music_sequence": [],
        "post_session": {
            "sam": None,
            "panas": None
        },
        "usability": {
            "sus_responses": None,
            "sus_score": None,
            "therapy_experience": None
        }
    }


def log_message(session_data, role, content, phase):
    if not isinstance(session_data, dict):
        return session_data

    session_data.setdefault("conversation_log", [])
    if content is None:
        content = ""

    session_data["conversation_log"].append({
        "role": role,
        "content": str(content),
        "phase": phase or "system",
        "timestamp": now_str()
    })
    return session_data


def save_llm_estimated_va(session_data, current_va, target_va):
    if not isinstance(session_data, dict):
        return session_data

    session_data.setdefault("llm_estimated_va", {})
    session_data["llm_estimated_va"]["current_state"] = copy.deepcopy(current_va) if current_va is not None else None
    session_data["llm_estimated_va"]["target_state"] = copy.deepcopy(target_va) if target_va is not None else None
    session_data["current_state_va"] = normalize_va(current_va)
    session_data["target_state_va"] = normalize_va(target_va)
    return session_data


def prepare_session_metadata(session_data, n_tracks=4, focus_theme=None, postlude_summary=None):
    if not isinstance(session_data, dict):
        return session_data

    estimated_va = session_data.get("llm_estimated_va", {})
    current_va = normalize_va(session_data.get("current_state_va")) or normalize_va(estimated_va.get("current_state"))
    target_va = normalize_va(session_data.get("target_state_va")) or normalize_va(estimated_va.get("target_state"))

    session_data["current_state_va"] = current_va
    session_data["target_state_va"] = target_va

    session_data["focus_theme"] = extract_focus_theme(session_data, candidate=focus_theme)

    if postlude_summary is not None:
        session_data["postlude_summary"] = postlude_summary
    else:
        session_data["postlude_summary"] = generate_rule_based_postlude_summary(session_data)

    waypoints = generate_waypoints(current_va, target_va, n_tracks=n_tracks)
    session_data["waypoint_sequence"] = waypoints

    mappings = []
    generated_music = None
    music_source = None
    for fallback_index, entry in enumerate(session_data.get("music_sequence", [])):
        track_va = normalize_va(entry.get("track_va"))
        assigned_index, waypoint_va, distance = assign_track_to_waypoint(track_va, waypoints, fallback_index=fallback_index)
        entry_source = entry.get("source") or entry.get("music_source") or "retrieved"
        if entry_source == "generated":
            generated_index, generated_waypoint_va, generated_distance = assign_generated_track_to_final_waypoint(track_va, waypoints)
            if generated_index is not None:
                assigned_index = generated_index
                waypoint_va = generated_waypoint_va
                distance = generated_distance
        entry["source"] = entry_source
        entry["music_source"] = entry_source
        entry["track_va"] = track_va
        entry["assigned_waypoint"] = assigned_index
        entry["waypoint_va"] = waypoint_va
        entry["va_distance"] = distance
        entry.setdefault("per_track_feedback_collected", False)
        if entry_source == "generated":
            entry_generated_music = copy.deepcopy(entry.get("generated_music") or {})
            playable_audio = relative_existing_audio_path(
                entry_generated_music.get("audio_file"),
                entry.get("file_path"),
                entry.get("full_path"),
                entry.get("filename"),
            )
            if playable_audio:
                entry_generated_music["audio_file"] = playable_audio
                entry["file_path"] = playable_audio
                entry["generated_music"] = entry_generated_music
        if entry_source == "generated" and entry.get("generated_music"):
            generated_music = copy.deepcopy(entry.get("generated_music"))
            music_source = "generated"
        elif music_source is None:
            music_source = entry_source

        mapping = {
            "track_index": entry.get("track_index"),
            "track_id": entry.get("track_id") or entry.get("filename"),
            "filename": entry.get("filename"),
            "track_title": entry.get("track_title") or entry.get("title"),
            "track_va": copy.deepcopy(track_va) if track_va is not None else None,
            "assigned_waypoint": assigned_index,
            "waypoint_va": copy.deepcopy(waypoint_va) if waypoint_va is not None else None,
            "va_distance": distance
        }
        mappings.append(mapping)

    session_data["track_waypoint_mapping"] = mappings
    session_data["music_source"] = music_source
    session_data["generated_music"] = generated_music
    session_data.update(compute_completion_metadata(session_data))
    return session_data


def add_music_track(session_data, track_index, track, waypoint_va=None):
    if not isinstance(session_data, dict):
        return session_data

    session_data.setdefault("music_sequence", [])
    track = track or {}
    track_va = normalize_va(track.get("track_va")) or normalize_va(track.get("valence_arousal")) or normalize_va(track.get("va"))
    waypoint_sequence = session_data.get("waypoint_sequence", [])
    source = track.get("source") or track.get("music_source") or "retrieved"
    music_source = track.get("music_source") or track.get("source") or "retrieved"
    fallback_index = track_index - 1 if isinstance(track_index, int) else None
    assigned_waypoint, assigned_waypoint_va, va_distance = assign_track_to_waypoint(
        track_va,
        waypoint_sequence,
        fallback_index=fallback_index
    )
    if source == "generated":
        generated_waypoint, generated_waypoint_va, generated_distance = assign_generated_track_to_final_waypoint(
            track_va,
            waypoint_sequence
        )
        if generated_waypoint is not None:
            assigned_waypoint = generated_waypoint
            assigned_waypoint_va = generated_waypoint_va
            va_distance = generated_distance
    waypoint_va = normalize_va(waypoint_va) or assigned_waypoint_va

    entry = {
        "track_index": track_index,
        "track_id": track.get("track_id") or track.get("id") or track.get("filename"),
        "source": source,
        "music_source": music_source,
        "track_title": track.get("title"),
        "title": track.get("title"),
        "filename": track.get("filename"),
        "file_path": track.get("file_path"),
        "full_path": track.get("full_path"),
        "track_va": copy.deepcopy(track_va) if track_va is not None else None,
        "waypoint_va": copy.deepcopy(waypoint_va) if waypoint_va is not None else None,
        "assigned_waypoint": assigned_waypoint,
        "va_distance": va_distance,
        "emotion_label": track.get("emotion_label"),
        "retrieval_keywords": copy.deepcopy(track.get("retrieval_keywords")) if track.get("retrieval_keywords") is not None else None,
        "avoid_keywords": copy.deepcopy(track.get("avoid_keywords")) if track.get("avoid_keywords") is not None else None,
        "generation_model": track.get("generation_model"),
        "generation_method": track.get("generation_method"),
        "generation_params": copy.deepcopy(track.get("generation_params")) if track.get("generation_params") is not None else None,
        "generated_music": copy.deepcopy(track.get("generated_music")) if track.get("generated_music") is not None else None,
        "user_sam_after": None,
        "imagery_vividness": None,
        "imagery_description": None,
        "per_track_feedback_collected": False
    }
    session_data["music_sequence"].append(entry)
    return session_data


def update_music_track_feedback(session_data, track_index, sam_valence, sam_arousal, vividness, imagery_text):
    if not isinstance(session_data, dict):
        return session_data

    music_sequence = session_data.setdefault("music_sequence", [])
    target_entry = None
    for entry in music_sequence:
        if entry.get("track_index") == track_index:
            target_entry = entry
            break

    if target_entry is None:
        return session_data

    if sam_valence is not None or sam_arousal is not None:
        target_entry["user_sam_after"] = {
            "valence": sam_valence,
            "arousal": sam_arousal
        }
    if vividness is not None:
        target_entry["imagery_vividness"] = vividness
    if imagery_text:
        target_entry["imagery_description"] = imagery_text
    return session_data


def save_session_json(session_data, output_dir="session_results"):
    if not isinstance(session_data, dict):
        raise ValueError("session_data must be a dict")

    os.makedirs(output_dir, exist_ok=True)
    prepare_session_metadata(
        session_data,
        n_tracks=max(4, len(session_data.get("music_sequence", [])))
    )
    session_data["timestamp_end"] = now_str()
    session_data.update(compute_completion_metadata(session_data))
    session_id = session_data.get("session_id") or uuid.uuid4().hex
    session_data["session_id"] = session_id
    session_data["va_visualization_path"] = None
    safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id)) or uuid.uuid4().hex
    filename = f"session_results_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_session_id}.json"
    output_path = os.path.join(output_dir, filename)
    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(session_data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, output_path)
    try:
        from va_visualization import generate_va_visualization

        session_data["va_visualization_path"] = generate_va_visualization(output_path)
    except Exception:
        session_data["va_visualization_path"] = None

    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(session_data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, output_path)
    return output_path


def _score_panas(panas_record, item_keys, score_key):
    if not isinstance(panas_record, dict):
        return None
    items = panas_record.get("items")
    if isinstance(items, dict) and item_keys:
        return sum(int(items.get(item_key, 0) or 0) for item_key in item_keys)
    value = panas_record.get(score_key)
    return int(value) if value is not None else None


def _emotion_payload(session_data, phase_key, positive_items, negative_items):
    phase = session_data.get(phase_key, {}) if isinstance(session_data, dict) else {}
    sam = phase.get("sam") or {}
    panas = phase.get("panas") or {}
    return {
        "sam_valence": sam.get("valence"),
        "sam_arousal": sam.get("arousal"),
        "panas_pa": _score_panas(panas, positive_items, "pa_score"),
        "panas_na": _score_panas(panas, negative_items, "na_score"),
    }


def _music_log_payload(session_data):
    music_sequence = session_data.get("music_sequence", []) if isinstance(session_data, dict) else []
    track = music_sequence[0] if music_sequence else {}
    generated_music = track.get("generated_music") or session_data.get("generated_music") or {}
    music_source = track.get("music_source") or track.get("source") or session_data.get("music_source")

    if music_source == "generated":
        music_id = generated_music.get("audio_file") or track.get("file_path") or track.get("full_path")
        metadata = {
            "generation_model": generated_music.get("generation_model"),
            "generation_method": generated_music.get("generation_method"),
            "target_va": generated_music.get("target_va"),
            "concept": generated_music.get("concept"),
            "blueprint": generated_music.get("blueprint"),
        }
        if generated_music.get("audio_file") is not None:
            metadata["audio_file"] = generated_music.get("audio_file")
        if generated_music.get("render_error") is not None:
            metadata["render_error"] = generated_music.get("render_error")
        return music_id, "generated", metadata

    music_id = track.get("file_path") or track.get("full_path") or track.get("filename") or track.get("track_id")
    metadata = {
        "emotion_label": track.get("emotion_label"),
        "track_id": track.get("track_id"),
        "retrieval_keywords": track.get("retrieval_keywords") or [],
        "avoid_keywords": track.get("avoid_keywords") or [],
    }
    return music_id, "database" if music_source == "database" else music_source, metadata


def append_ablation_session_log(
    session_data,
    condition_order,
    session_number,
    positive_items,
    negative_items,
    output_path=os.path.join("data", "session_logs.jsonl"),
):
    if not isinstance(session_data, dict):
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        music_id, music_source, music_metadata = _music_log_payload(session_data)
        record = {
            "user_id": session_data.get("user_id"),
            "session_id": session_data.get("session_id"),
            "condition": session_data.get("condition"),
            "condition_order": list(condition_order or []),
            "session_number": session_number,
            "pre_emotion": _emotion_payload(session_data, "pre_session", positive_items, negative_items),
            "post_emotion": _emotion_payload(session_data, "post_session", positive_items, negative_items),
            "music_id": music_id,
            "music_source": music_source,
            "music_metadata": music_metadata,
            "timestamp_start": session_data.get("timestamp_start"),
            "timestamp_end": session_data.get("timestamp_end") or now_str(),
        }
        if session_data.get("washout_start") is not None:
            record["washout_start"] = session_data.get("washout_start")
        if session_data.get("washout_end") is not None:
            record["washout_end"] = session_data.get("washout_end")
        with _SESSION_LOG_LOCK:
            with open(output_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        print(f"Failed to append ablation session log: {exc}", file=sys.stderr)
        return False

