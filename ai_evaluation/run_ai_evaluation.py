"""
Step 2 for the Kimusic AI evaluation pipeline.

This script reads blinded pairwise inputs, builds the evaluator prompt, and
optionally calls the project's OpenAI-compatible LLM endpoint. It never writes
to the live experiment system, data/session_logs.jsonl, or session_results/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_EVALUATOR_MODEL = os.getenv("AI_EVAL_MODEL", "claude-sonnet-4-6")
DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_INPUT_DIR = Path("ai_evaluation/results/pairwise_inputs")
DEFAULT_OUTPUT_DIR = Path("ai_evaluation/results/raw_evaluations")
MAX_TOKENS = 2048
API_TIMEOUT_SECONDS = 120
CONNECTION_RETRIES = 2
CONNECTION_RETRY_DELAY_SECONDS = 5

CRITERIA = [
    "emotional_alignment",
    "therapeutic_coherence",
    "music_emotion_fit",
    "engagement",
    "safety",
]

LEAKAGE_TERMS = [
    "_label_map",
    "participant_id",
    "kimusic",
    "baseline",
    "generated",
    "database",
    "AI-generated",
    "generated music",
    "music generation",
    "retrieval",
    "database track",
    "generated track",
    "file_path",
    "audio_file",
]

SYSTEM_PROMPT = """You are an expert evaluator of music therapy sessions.

You will compare two blinded sessions from the same participant.

Return only one valid JSON object. The first character of your response must be { and the last character must be }.

Do not include markdown. Do not include a preamble. Do not think step by step. Do not explain your reasoning outside the JSON object.

Use the provided criteria and produce compact scores."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run blinded AI pairwise evaluation.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_DIR), help="Directory containing pairwise input JSON files.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="Directory for raw evaluation outputs.")
    parser.add_argument("--model", default=None, help="Evaluator model name.")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N participants.")
    parser.add_argument("--dry-run", action="store_true", help="Build and save prompts without calling the API.")
    parser.add_argument("--overwrite", action="store_true", help="Re-run participants even when successful output JSON exists.")
    parser.add_argument("--smoke-test", action="store_true", help="Send a minimal API request and exit.")
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def safe_value(value: Any, default: str = "not available") -> Any:
    if value is None or value == "":
        return default
    return value


def format_mapping(value: Any) -> str:
    if value is None or value == {} or value == [] or value == "":
        return "not available"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def get_nested(mapping: Dict[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current



def compact_text(value: Any, max_chars: int = 700) -> str:
    text = format_mapping(value)
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n[truncated]\n" + text[-tail:]


def compact_feedback(value: Any, max_chars: int = 500) -> str:
    return compact_text(value, max_chars=max_chars)




def final_compact_text(value: Any, max_chars: int = 1200) -> str:
    text = format_mapping(value)
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n[truncated]\n" + text[-tail:]


def final_compact_sus(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("sus_score") is not None:
            return "SUS score: " + str(value.get("sus_score"))
    return final_compact_text(value, 350)


def final_compact_therapy(value: Any) -> str:
    if not isinstance(value, dict):
        return final_compact_text(value, 650)

    keep = {}
    for key, val in value.items():
        key_l = str(key).lower()
        if any(term in key_l for term in ["overall", "match", "help", "reuse", "comfort", "effective", "satisfaction", "rating", "score"]):
            keep[key] = val

    if not keep:
        keep = value

    return final_compact_text(keep, 800)


def build_user_prompt(pairwise: Dict[str, Any]) -> str:
    a = pairwise.get("session_a", {})
    b = pairwise.get("session_b", {})

    def session_block(label: str, s: Dict[str, Any]) -> str:
        return f"""SESSION {label}

Affective outcomes:
- Pre SAM Valence: {safe_value(get_nested(s, "pre_emotion", "sam_valence"))}/9
- Pre SAM Arousal: {safe_value(get_nested(s, "pre_emotion", "sam_arousal"))}/9
- Pre PANAS PA: {safe_value(get_nested(s, "pre_emotion", "panas_pa"))}/50
- Pre PANAS NA: {safe_value(get_nested(s, "pre_emotion", "panas_na"))}/50
- Post SAM Valence: {safe_value(get_nested(s, "post_emotion", "sam_valence"))}/9
- Post SAM Arousal: {safe_value(get_nested(s, "post_emotion", "sam_arousal"))}/9
- Post PANAS PA: {safe_value(get_nested(s, "post_emotion", "panas_pa"))}/50
- Post PANAS NA: {safe_value(get_nested(s, "post_emotion", "panas_na"))}/50
- ? SAM Valence: {safe_value(get_nested(s, "emotion_deltas", "delta_sam_valence"))}
- ? SAM Arousal: {safe_value(get_nested(s, "emotion_deltas", "delta_sam_arousal"))}
- ? PANAS PA: {safe_value(get_nested(s, "emotion_deltas", "delta_panas_pa"))}
- ? PANAS NA: {safe_value(get_nested(s, "emotion_deltas", "delta_panas_na"))}

Music profile:
{final_compact_text(s.get("music_profile"), 750)}

Participant feedback:
- {final_compact_sus(s.get("sus_results"))}
- Therapy experience: {final_compact_therapy(s.get("therapy_experience_results"))}

Dialogue evidence:
{final_compact_text(s.get("conversation_summary"), 3200)}
"""

    return f"""You are comparing two blinded music-therapy sessions completed by the same participant.

You receive structured affective outcomes, participant feedback, music-profile metadata, and phase-stratified dialogue excerpts. The excerpts are representative user-assistant turns, not full transcripts.

Do not infer which technical system produced either session. Judge only from the provided evidence.

{session_block("A", a)}

---

{session_block("B", b)}

Evaluate each session from 1 to 5 on:
1. emotional_alignment
2. therapeutic_coherence
3. music_emotion_fit
4. engagement
5. safety

Return exactly one valid JSON object and nothing else:
{{
  "winner": "A" | "B" | "tie",
  "confidence": <float 0.0-1.0>,
  "preference_strength": <int 1-5>,
  "scores": {{
    "session_a": {{
      "emotional_alignment": <int 1-5>,
      "therapeutic_coherence": <int 1-5>,
      "music_emotion_fit": <int 1-5>,
      "engagement": <int 1-5>,
      "safety": <int 1-5>
    }},
    "session_b": {{
      "emotional_alignment": <int 1-5>,
      "therapeutic_coherence": <int 1-5>,
      "music_emotion_fit": <int 1-5>,
      "engagement": <int 1-5>,
      "safety": <int 1-5>
    }}
  }},
  "reasoning": "<one concise sentence>"
}}

If winner is "tie", set preference_strength to 1. Output JSON only.
"""


def build_prompt(pairwise: Dict[str, Any]) -> Tuple[str, str]:
    return SYSTEM_PROMPT, build_user_prompt(pairwise)


def estimate_tokens(text: str) -> int:
    # Conservative enough for reporting without adding tokenizer dependencies.
    return max(1, int(len(text) / 4))


def prompt_leakage_hits(text: str) -> List[str]:
    lowered = text.lower()
    hits = []
    for term in LEAKAGE_TERMS:
        if term.lower() in lowered:
            hits.append(term)
    return sorted(set(hits), key=str.lower)


def strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def validate_scores(scores: Any) -> Dict[str, Dict[str, int]]:
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object")
    validated: Dict[str, Dict[str, int]] = {}
    for session_key in ("session_a", "session_b"):
        session_scores = scores.get(session_key)
        if not isinstance(session_scores, dict):
            raise ValueError(f"scores.{session_key} must be an object")
        missing = [criterion for criterion in CRITERIA if criterion not in session_scores]
        if missing:
            raise ValueError(f"scores.{session_key} missing criteria: {', '.join(missing)}")
        validated[session_key] = {}
        for criterion in CRITERIA:
            value = session_scores[criterion]
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise ValueError(f"scores.{session_key}.{criterion} must be an integer from 1 to 5")
            validated[session_key][criterion] = value
    return validated



def extract_first_json_object(text: str) -> str:
    """Extract the first balanced JSON object from a model response."""
    s = strip_markdown_fences(str(text or "").strip())
    if s.startswith("{") and s.endswith("}"):
        return s

    start = s.find("{")
    if start == -1:
        return s

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]

    return s[start:]


def parse_evaluation_response(raw_text: str) -> Dict[str, Any]:
    clean_text = extract_first_json_object(raw_text)
    data = json.loads(clean_text)
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")

    winner = data.get("winner")
    if winner not in {"A", "B", "tie"}:
        raise ValueError('winner must be one of "A", "B", or "tie"')

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a number from 0 to 1")

    preference_strength = data.get("preference_strength")
    if not isinstance(preference_strength, int) or isinstance(preference_strength, bool) or not 1 <= preference_strength <= 5:
        raise ValueError("preference_strength must be an integer from 1 to 5")
    if winner == "tie" and preference_strength != 1:
        raise ValueError('preference_strength must be 1 when winner is "tie"')

    scores = validate_scores(data.get("scores"))

    reasoning = data.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("reasoning must be a non-empty string")

    return {
        "winner": winner,
        "confidence": float(confidence),
        "preference_strength": preference_strength,
        "scores": scores,
        "reasoning": reasoning.strip(),
    }


def resolve_model(cli_model: str | None) -> str:
    return cli_model or DEFAULT_EVALUATOR_MODEL


def resolve_base_url() -> str:
    env_base_url = os.getenv("BASE_URL")
    env_openai_base_url = os.getenv("OPENAI_BASE_URL")
    raw_base_url = env_base_url or env_openai_base_url or DEFAULT_BASE_URL
    resolved = raw_base_url.strip().rstrip("/")
    if resolved.endswith("/chat/completions"):
        resolved = resolved[: -len("/chat/completions")]
    if resolved == "https://api.openai.com":
        resolved = "https://api.openai.com/v1"
    return resolved


def print_base_url_diagnostics() -> None:
    env_base_url = os.getenv("BASE_URL")
    env_openai_base_url = os.getenv("OPENAI_BASE_URL")
    override_source = "BASE_URL" if env_base_url else "OPENAI_BASE_URL" if env_openai_base_url else "none"
    print(f"[AI-EVAL] DEFAULT_BASE_URL: {DEFAULT_BASE_URL}")
    print(f"[AI-EVAL] env BASE_URL: {env_base_url or '[not set]'}")
    print(f"[AI-EVAL] env OPENAI_BASE_URL: {env_openai_base_url or '[not set]'}")
    print(f"[AI-EVAL] env override source: {override_source}")
    print(f"[AI-EVAL] final resolved base_url: {resolve_base_url()}")


def is_connection_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        class_name = current.__class__.__name__.lower()
        message = str(current).lower()
        if "connection" in class_name or "connect" in message or "timeout" in class_name or "timeout" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def extract_response_text(response: Any) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
            elif isinstance(part, dict) and part.get("text"):
                texts.append(part["text"])
        if texts:
            return "\n".join(texts)
    raise RuntimeError("API response did not contain evaluator text")




def decode_subprocess_output(data: object) -> str:
    """Decode PowerShell subprocess output robustly on Windows.

    Windows PowerShell may emit bytes in UTF-8, UTF-16, or local code pages.
    Capturing as bytes and decoding manually avoids UnicodeDecodeError crashes.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray)):
        return str(data)

    raw = bytes(data)
    for enc in ("utf-8-sig", "utf-16", "gb18030", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")



def call_evaluator_api(system_prompt: str, user_prompt: str, model: str) -> str:
    """Call OpenAI-compatible chat/completions through PowerShell Invoke-RestMethod.

    Retries with larger max_tokens when the provider returns empty content.
    Saves full provider response details in the raised error if all attempts fail.
    """
    import json
    import os
    import subprocess
    import tempfile

    api_key = DEFAULT_API_KEY
    base_url = resolve_base_url().rstrip("/")
    url = base_url + "/chat/completions"

    token_budgets = [2048, 2048, 2048]
    last_response_text = ""
    last_finish_reason = None
    last_usage = None
    last_error = None

    for max_tokens in token_budgets:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            payload_path = f.name

        payload_path_ps = payload_path.replace("\\", "/")

        ps_lines = [
            '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
            '$OutputEncoding = [System.Text.Encoding]::UTF8',
            '$ErrorActionPreference = "Stop"',
            '$body = Get-Content -Raw -LiteralPath "' + payload_path_ps + '"',
            '$headers = @{}',
            '$headers["Authorization"] = "Bearer " + $env:OPENAI_API_KEY',
            '$headers["Content-Type"] = "application/json; charset=utf-8"',
            '$resp = Invoke-RestMethod -Uri "' + url + '" -Method Post -Headers $headers -Body $body',
            '$resp | ConvertTo-Json -Depth 80 -Compress',
        ]
        ps_script = chr(10).join(ps_lines)

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True,
                timeout=300,
            )

            stdout_text = decode_subprocess_output(completed.stdout)
            stderr_text = decode_subprocess_output(completed.stderr)

            if completed.returncode != 0:
                last_error = "PowerShell API call failed with max_tokens=" + str(max_tokens) + chr(10) + stderr_text[:4000]
                continue

            last_response_text = stdout_text

            try:
                data = json.loads(stdout_text)
            except Exception as exc:
                last_error = "Could not parse provider JSON with max_tokens=" + str(max_tokens) + chr(10) + repr(exc) + chr(10) + stdout_text[:4000]
                continue

            choices = data.get("choices") or []
            choice = choices[0] if choices else {}
            message = choice.get("message") if isinstance(choice, dict) else {}
            if not isinstance(message, dict):
                message = {}

            last_finish_reason = choice.get("finish_reason")
            last_usage = data.get("usage")

            content = message.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        parts.append(str(part.get("text")))
                    elif hasattr(part, "text"):
                        parts.append(str(part.text))
                content = "\n".join(parts)

            content = str(content or "").strip()

            if content:
                return content

            last_error = (
                "Provider returned empty message.content with max_tokens="
                + str(max_tokens)
                + "; finish_reason="
                + str(last_finish_reason)
                + "; usage="
                + json.dumps(last_usage, ensure_ascii=False)
            )

        finally:
            try:
                os.remove(payload_path)
            except OSError:
                pass

    raise RuntimeError(
        "Evaluator API returned empty content after retries."
        + chr(10)
        + "last_finish_reason: "
        + str(last_finish_reason)
        + chr(10)
        + "last_usage: "
        + json.dumps(last_usage, ensure_ascii=False)
        + chr(10)
        + "last_error: "
        + str(last_error)
        + chr(10)
        + "last_provider_response_first_4000:"
        + chr(10)
        + str(last_response_text)[:4000]
    )



def diagnose_smoke_test_response(model: str) -> None:
    """Smoke-test via PowerShell Invoke-RestMethod."""
    import json
    import os
    import subprocess
    import tempfile

    api_key = DEFAULT_API_KEY
    base_url = resolve_base_url().rstrip("/")
    url = base_url + "/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Return only the word OK."}
        ],
        "temperature": 0,
        "max_tokens": 64,
    }

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        payload_path = f.name

    payload_path_ps = payload_path.replace("\\", "/")

    ps_lines = [
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
        '$OutputEncoding = [System.Text.Encoding]::UTF8',
        '$body = Get-Content -Raw -LiteralPath "' + payload_path_ps + '"',
        '$headers = @{}',
        '$headers["Authorization"] = "Bearer " + $env:OPENAI_API_KEY',
        '$headers["Content-Type"] = "application/json"',
        '$resp = Invoke-RestMethod -Uri "' + url + '" -Method Post -Headers $headers -Body $body',
        '$resp | ConvertTo-Json -Depth 50 -Compress',
    ]
    ps_script = chr(10).join(ps_lines)

    print("[AI-EVAL] smoke test API call starting via PowerShell Invoke-RestMethod")

    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            timeout=120,
        )

        if completed.returncode != 0:
            print("[AI-EVAL] smoke test failed")
            print("STDOUT:", decode_subprocess_output(completed.stdout)[:2000])
            print("STDERR:", decode_subprocess_output(completed.stderr)[:2000])
            raise RuntimeError("PowerShell smoke test failed")

        data = json.loads(decode_subprocess_output(completed.stdout))
        print("[AI-EVAL] smoke test succeeded")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])

    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass

def decode_scores_by_condition(scores: Dict[str, Dict[str, int]], label_map: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    for label, session_key in (("A", "session_a"), ("B", "session_b")):
        condition = label_map.get(label)
        if condition:
            result[condition] = scores[session_key]
    return result


def build_success_output(
    participant_hash: str,
    label_map: Dict[str, str],
    parsed: Dict[str, Any],
    model: str,
) -> Dict[str, Any]:
    winner_label = parsed["winner"]
    winner_condition = "tie" if winner_label == "tie" else label_map.get(winner_label, "unknown")
    return {
        "participant_hash": participant_hash,
        "label_map": label_map,
        "winner_label": winner_label,
        "winner_condition": winner_condition,
        "confidence": parsed["confidence"],
        "preference_strength": parsed["preference_strength"],
        "scores": parsed["scores"],
        "scores_by_condition": decode_scores_by_condition(parsed["scores"], label_map),
        "reasoning": parsed["reasoning"],
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "evaluator_model": model,
    }


def format_exception_details(exc: Exception) -> str:
    details = [
        f"exception_type: {type(exc).__module__}.{type(exc).__name__}",
        f"repr: {repr(exc)}",
        f"str: {str(exc)}",
    ]
    cause = getattr(exc, "__cause__", None)
    context = getattr(exc, "__context__", None)
    if cause is not None:
        details.extend(
            [
                f"cause_type: {type(cause).__module__}.{type(cause).__name__}",
                f"cause_repr: {repr(cause)}",
                f"cause_str: {str(cause)}",
            ]
        )
    if context is not None:
        details.extend(
            [
                f"context_type: {type(context).__module__}.{type(context).__name__}",
                f"context_repr: {repr(context)}",
                f"context_str: {str(context)}",
            ]
        )
    return "\n".join(details)


def write_failed_response(path: Path, raw_response: str, error_message: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"ERROR: {error_message}\n\nRAW RESPONSE:\n{raw_response}")


def validate_api_configuration(model: str) -> bool:
    if not DEFAULT_API_KEY:
        print("[AI-EVAL] early return: DEFAULT_API_KEY is empty")
        print(
            "Missing API key. Set DEFAULT_API_KEY in ai_evaluation/run_ai_evaluation.py before running without --dry-run.",
            file=sys.stderr,
        )
        return False
    if not model:
        print("[AI-EVAL] early return: evaluator model is not configured")
        print(
            "Missing evaluator model. Set DEFAULT_EVALUATOR_MODEL or pass --model before running without --dry-run.",
            file=sys.stderr,
        )
        return False
    return True


def run_smoke_test(model: str) -> int:
    print("[AI-EVAL] smoke test mode entered")
    print(f"[AI-EVAL] smoke test model: {model or '[not configured]'}")
    print_base_url_diagnostics()
    if not validate_api_configuration(model):
        return 1
    try:
        print("[AI-EVAL] smoke test API call starting")
        diagnose_smoke_test_response(model)
        print("[AI-EVAL] smoke test API response received")
        return 0
    except Exception as exc:
        print("[AI-EVAL] smoke test failed")
        print(format_exception_details(exc))
        return 1


def find_pairwise_files(input_dir: Path, limit: int | None) -> List[Path]:
    files = sorted(path for path in input_dir.glob("*.json") if not path.name.startswith("_"))
    if limit is not None:
        files = files[: max(0, limit)]
    return files


def print_cost_estimate(prompt_tokens: List[int]) -> None:
    if prompt_tokens:
        average_prompt_tokens = sum(prompt_tokens) / len(prompt_tokens)
    else:
        average_prompt_tokens = 0
    average_completion_tokens = MAX_TOKENS

    # Public pricing changes over time; these are explicit planning assumptions.
    input_price_per_million = 3.00
    output_price_per_million = 15.00

    def projected_cost(participants: int) -> float:
        return (
            participants * average_prompt_tokens * input_price_per_million / 1_000_000
            + participants * average_completion_tokens * output_price_per_million / 1_000_000
        )

    print("Token/cost estimate:")
    print(f"- Average prompt tokens: {average_prompt_tokens:.0f}")
    print(f"- Assumed average completion tokens: {average_completion_tokens}")
    print("- Pricing assumption: $3.00/M input tokens, $15.00/M output tokens")
    for count in (6, 20, 50):
        print(f"- Projected cost for {count} participants: ${projected_cost(count):.4f}")


def save_prompt(output_dir: Path, participant_hash: str, system_prompt: str, user_prompt: str) -> Path:
    prompt_path = output_dir / f"{participant_hash}_prompt.txt"
    with prompt_path.open("w", encoding="utf-8") as handle:
        handle.write("SYSTEM PROMPT\n")
        handle.write("=============\n")
        handle.write(system_prompt)
        handle.write("\n\nUSER PROMPT\n")
        handle.write("===========\n")
        handle.write(user_prompt)
    return prompt_path


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    print(f"[AI-EVAL] parsed args: {args}")
    print(f"[AI-EVAL] input directory: {input_dir}")
    print(f"[AI-EVAL] output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluator_model = resolve_model(args.model)

    if args.smoke_test:
        return run_smoke_test(evaluator_model)

    all_pairwise_files = sorted(path for path in input_dir.glob("*.json") if not path.name.startswith("_"))
    print(f"Pairwise input files found: {len(all_pairwise_files)}")
    pairwise_files = find_pairwise_files(input_dir, args.limit)
    print(f"[AI-EVAL] pairwise files after applying --limit: {len(pairwise_files)}")
    if not pairwise_files:
        print("[AI-EVAL] early return: no pairwise files selected")
        print(f"No pairwise input files found in {input_dir}")
        return 1
    if args.limit is not None:
        selected_hashes = [path.stem for path in pairwise_files]
        print(f"Participant hashes selected by --limit {args.limit}: {', '.join(selected_hashes)}")

    prompts: List[Tuple[Path, Dict[str, Any], str, str, int, List[str]]] = []
    for pairwise_path in pairwise_files:
        pairwise = load_json(pairwise_path)
        participant_hash = pairwise.get("_participant_hash") or pairwise_path.stem
        system_prompt, user_prompt = build_prompt(pairwise)
        combined_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompts.append(
            (
                pairwise_path,
                pairwise,
                system_prompt,
                user_prompt,
                estimate_tokens(combined_prompt),
                prompt_leakage_hits(combined_prompt),
            )
        )

    prompt_token_estimates = [item[4] for item in prompts]
    print(f"Pairwise files to evaluate: {len(prompts)}")
    print(f"Selected model: {evaluator_model or '[not configured]'}")
    print(f"Estimated prompt tokens by participant: {prompt_token_estimates}")
    print_cost_estimate(prompt_token_estimates)

    if not args.dry_run:
        print("[AI-EVAL] real API branch would be entered")
        if not validate_api_configuration(evaluator_model):
            return 1
        print_base_url_diagnostics()
    else:
        print("[AI-EVAL] dry-run branch entered; no real API call will be made")

    successes = 0
    failures = 0
    dry_run_prompts = 0

    for pairwise_path, pairwise, system_prompt, user_prompt, _tokens, leakage_hits in prompts:
        participant_hash = pairwise.get("_participant_hash") or pairwise_path.stem
        label_map = pairwise.get("_label_map", {})
        output_path = output_dir / f"{participant_hash}.json"
        print(f"Processing participant hash: {participant_hash}")
        if output_path.exists():
            if not args.overwrite:
                print(f"[AI-EVAL] skipping existing successful evaluation: {participant_hash}")
                continue
            print(f"Existing output file found at {output_path}; --overwrite enabled, evaluation will proceed.")
        else:
            print(f"No existing output found for {participant_hash}.")
        if leakage_hits:
            print(f"Warning: prompt for {participant_hash} contains leakage terms: {', '.join(leakage_hits)}")

        prompt_path = save_prompt(output_dir, participant_hash, system_prompt, user_prompt)
        print(f"Prompt path written: {prompt_path}")

        if args.dry_run:
            print(f"[AI-EVAL] dry-run complete for {participant_hash}; no API call made.")
            dry_run_prompts += 1
            continue

        raw_response = ""
        try:
            print(f"Starting real API call for {participant_hash}.")
            raw_response = call_evaluator_api(system_prompt, user_prompt, evaluator_model)
            print(f"API response received for {participant_hash}.")
            parsed = parse_evaluation_response(raw_response)
            output_payload = build_success_output(participant_hash, label_map, parsed, evaluator_model)
            dump_json(output_path, output_payload)
            print(f"Output path written: {output_path}")
            successes += 1
            time.sleep(2)
        except Exception as exc:
            failures += 1
            raw_text = locals().get("raw_response", "")
            failed_path = output_dir / f"{participant_hash}_failed.txt"
            error_details = format_exception_details(exc)
            write_failed_response(failed_path, raw_text, error_details)
            print(f"Evaluation failed for {participant_hash}: {repr(exc)}")
            print(f"Failure path written: {failed_path}")
            time.sleep(2)

    if args.dry_run:
        print(f"Dry run complete. Prompts saved: {dry_run_prompts}")
    else:
        print(f"Evaluation complete. Successes: {successes}; failures: {failures}")
    return 0


def main() -> int:
    print("[AI-EVAL] main() started")
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

