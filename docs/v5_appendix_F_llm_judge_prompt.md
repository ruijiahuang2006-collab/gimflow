# Appendix F: LLM Judge Prompt

Note: This document reproduces the evaluator prompt used in the reported experiments. Some wording reflects the deployed evaluation terminology; the released system prompts use non-clinical wording, as described in the README and the paper.

This document provides the prompt template used by the LLM judge in the blinded pairwise AI-evaluation protocol reported in Section 5.4 of the paper.

The evaluator prompt has two parts:

1. a system prompt, defined as `SYSTEM_PROMPT` in `ai_evaluation/run_ai_evaluation.py`; and
2. a pair-specific user prompt, constructed by `build_user_prompt(pairwise)` from blinded pairwise JSON inputs.

Raw LLM-judge outputs are not released because rationales may contain session-specific details. Aggregate results are reported in the paper and in `data/aggregate_analysis/`.

## System Prompt

```text
You are an expert evaluator of music therapy sessions.

You will compare two blinded sessions from the same participant.

Return only one valid JSON object. The first character of your response must be { and the last character must be }.

Do not include markdown. Do not include a preamble. Do not think step by step. Do not explain your reasoning outside the JSON object.

Use the provided criteria and produce compact scores.
```

## Pair-Specific User Prompt Builder

```python
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
- Delta SAM Valence: {safe_value(get_nested(s, "emotion_deltas", "delta_sam_valence"))}
- Delta SAM Arousal: {safe_value(get_nested(s, "emotion_deltas", "delta_sam_arousal"))}
- Delta PANAS PA: {safe_value(get_nested(s, "emotion_deltas", "delta_panas_pa"))}
- Delta PANAS NA: {safe_value(get_nested(s, "emotion_deltas", "delta_panas_na"))}

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
```
