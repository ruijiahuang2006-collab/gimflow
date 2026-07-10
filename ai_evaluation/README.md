# AI Evaluation Pipeline

This directory contains the three-stage LLM-judge evaluation pipeline used for blinded pairwise comparison.

1. `build_pairwise_inputs_from_session_results.py` builds blinded pairwise inputs from session-level results.
2. `run_ai_evaluation.py` runs the judge model on each blinded pair.
3. `analyze_ai_evaluations_swapped.py` aggregates swapped-position judgments and reports summary statistics.

Raw pairwise inputs and raw judge outputs are not released because they may contain participant-level content or judge rationales referencing session text.
