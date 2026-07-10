from __future__ import annotations
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
        "v": max(-1.0, min(1.0, round(v, 3))),
        "a": max(-1.0, min(1.0, round(a, 3))),
    }


def va_to_mood(va):
    target_va = normalize_va(va) or {"v": 0.0, "a": 0.0}
    v = target_va["v"]
    a = target_va["a"]

    if v >= 0.25 and a <= -0.2:
        return ["restorative", "peaceful", "warm"]
    if v < 0 and a > 0.2:
        return ["tense", "anxious", "restless"]
    if v >= 0.25 and a > 0.2:
        return ["energetic", "uplifting", "hopeful"]
    if v < 0 and a <= -0.2:
        return ["melancholic", "reflective", "fragile"]
    return ["balanced", "reflective", "gentle"]


def mood_to_genre(moods):
    moods = moods or []
    mood_set = set(moods)

    if mood_set.intersection({"restorative", "peaceful", "warm"}):
        return "ambient classical"
    if mood_set.intersection({"energetic", "uplifting", "hopeful"}):
        return "cinematic orchestral"
    if "reflective" in mood_set:
        return "minimal piano"
    if mood_set.intersection({"tense", "anxious", "restless"}):
        return "minimal cinematic"
    if mood_set.intersection({"melancholic", "fragile"}):
        return "slow ambient"
    return "ambient instrumental"


def mood_to_instrumentation(moods):
    moods = moods or []
    mood_set = set(moods)

    if mood_set.intersection({"restorative", "peaceful", "warm"}):
        return ["piano", "strings", "soft pad"]
    if mood_set.intersection({"energetic", "uplifting", "hopeful"}):
        return ["strings", "woodwinds", "light percussion"]
    if "reflective" in mood_set:
        return ["solo piano", "cello"]
    if mood_set.intersection({"tense", "anxious", "restless"}):
        return ["low strings", "piano", "soft percussion"]
    if mood_set.intersection({"melancholic", "fragile"}):
        return ["solo piano", "cello", "soft pad"]
    return ["piano", "strings", "flute"]


def instrument_to_role(instrument):
    roles = {
        "piano": "main harmonic anchor",
        "solo piano": "main melodic and harmonic anchor",
        "strings": "sustained emotional support",
        "low strings": "grounding emotional support",
        "cello": "warm lyrical countervoice",
        "soft pad": "ambient continuity",
        "woodwinds": "gentle melodic color",
        "flute": "light melodic color",
        "light percussion": "subtle forward motion",
        "soft percussion": "contained rhythmic grounding",
    }
    return roles.get(instrument, "supporting texture")


def _normalize_waypoint_sequence(waypoint_sequence):
    normalized = []
    for index, waypoint in enumerate(waypoint_sequence or []):
        waypoint_va = normalize_va(waypoint)
        if not waypoint_va:
            continue
        if isinstance(waypoint, dict):
            waypoint_index = waypoint.get("index", index)
        else:
            waypoint_index = index
        normalized.append({
            "index": waypoint_index,
            "v": waypoint_va["v"],
            "a": waypoint_va["a"],
        })
    return normalized[:4]


def _four_section_structure(waypoint_sequence):
    waypoints = _normalize_waypoint_sequence(waypoint_sequence)
    section_specs = [
        ("section_0", 4, "orientation"),
        ("section_1", 4, "transition"),
        ("section_2", 4, "development"),
        ("section_3", 4, "target alignment"),
    ]
    structure = []
    for section_index, (name, bars, character) in enumerate(section_specs):
        waypoint = waypoints[section_index] if section_index < len(waypoints) else None
        section = {
            "name": name,
            "bars": bars,
            "character": character,
            "section_index": section_index,
            "waypoint_index": waypoint.get("index") if waypoint else section_index,
        }
        if waypoint:
            section["waypoint_va"] = {"v": waypoint["v"], "a": waypoint["a"]}
        structure.append(section)
    return structure


def concept_to_blueprint(concept, target_va=None, waypoint_sequence=None):
    concept = concept or {}
    target_va = normalize_va(target_va) or normalize_va(concept.get("target_va")) or {"v": 0.0, "a": 0.0}
    moods = concept.get("moods") or concept.get("mood") or va_to_mood(target_va)
    mood_set = set(moods)
    instrumentation = concept.get("instrumentation") or mood_to_instrumentation(moods)

    blueprint = {
        "duration_seconds": int(concept.get("duration_seconds") or 90),
        "tempo_bpm": 60,
        "key": "C major",
        "time_signature": "4/4",
        "structure": _four_section_structure(waypoint_sequence),
        "harmonic_language": "simple diatonic harmony with gentle resolution",
        "melodic_motion": "mostly stepwise with a calm contour",
        "dynamic_curve": "soft with gentle swells",
        "texture": "sparse piano with supportive sustained tones",
    }

    if mood_set.intersection({"restorative", "peaceful", "warm"}):
        blueprint.update({
            "tempo_bpm": 58,
            "key": "C major",
            "harmonic_language": "simple diatonic harmony with slow harmonic rhythm",
            "melodic_motion": "mostly stepwise, gently descending",
            "dynamic_curve": "soft to softer",
            "texture": "sparse piano with sustained strings and soft pad",
        })
    elif mood_set.intersection({"tense", "anxious", "restless"}):
        blueprint.update({
            "tempo_bpm": 72,
            "key": "A minor",
            "harmonic_language": "minor-mode harmony resolving toward consonance",
            "melodic_motion": "narrow-range repeated figures gradually opening",
            "dynamic_curve": "moderate tension gradually softening",
            "texture": "low strings, piano pulses, and soft percussion",
        })
    elif mood_set.intersection({"melancholic", "reflective", "fragile"}):
        blueprint.update({
            "tempo_bpm": 54,
            "key": "D minor",
            "harmonic_language": "slow minor harmony with gentle modal color",
            "melodic_motion": "slow lyrical contour with small intervals",
            "dynamic_curve": "very soft with small swells",
            "texture": "solo piano and cello",
        })
    elif mood_set.intersection({"energetic", "uplifting", "hopeful"}):
        blueprint.update({
            "tempo_bpm": 84,
            "key": "G major",
            "harmonic_language": "bright diatonic harmony with forward motion",
            "melodic_motion": "ascending phrases and wider intervals",
            "dynamic_curve": "gentle crescendo toward hopefulness",
            "texture": "strings, woodwinds, and light percussion",
        })

    blueprint["instrument_roles"] = [
        {
            "instrument": instrument,
            "role": instrument_to_role(instrument),
        }
        for instrument in instrumentation
    ]
    return blueprint


def va_to_kimusic_concept(waypoint_va, duration_seconds=90, track_index=0, waypoint_sequence=None):
    target_va = normalize_va(waypoint_va) or {"v": 0.0, "a": 0.0}
    moods = va_to_mood(target_va)
    genre = mood_to_genre(moods)
    instrumentation = mood_to_instrumentation(moods)
    title = "Kimusic Proxy Waypoint"

    return {
        "title": f"{title} {track_index + 1}",
        "mood": moods,
        "moods": moods,
        "target_va": target_va,
        "duration_seconds": int(duration_seconds or 90),
        "genre": genre,
        "instrumentation": instrumentation,
        "waypoint_sequence": _normalize_waypoint_sequence(waypoint_sequence),
    }


def create_proxy_generated_track(waypoint_va, session_id, track_index, fallback_audio_path, waypoint_sequence=None):
    target_va = normalize_va(waypoint_va) or {"v": 0.0, "a": 0.0}
    concept = va_to_kimusic_concept(
        target_va,
        duration_seconds=90,
        track_index=track_index,
        waypoint_sequence=waypoint_sequence,
    )
    concept_summary = {
        "moods": concept["moods"],
        "genre": concept["genre"],
        "instrumentation": concept["instrumentation"],
    }
    blueprint = concept_to_blueprint(concept, target_va, waypoint_sequence=waypoint_sequence)
    track_id = f"generated_{session_id}_{track_index}"

    return {
        "track_id": track_id,
        "id": track_id,
        "source": "generated",
        "music_source": "generated",
        "filename": fallback_audio_path.split("\\")[-1].split("/")[-1] if fallback_audio_path else None,
        "title": concept["title"],
        "track_title": concept["title"],
        "file_path": fallback_audio_path,
        "full_path": fallback_audio_path,
        "track_va": target_va,
        "waypoint_va": target_va,
        "va_distance": 0.0,
        "generation_model": "kimusic_proxy",
        "generation_method": "proxy",
        "generation_params": {
            "concept": concept,
            "blueprint": blueprint,
            "fallback_audio_path": fallback_audio_path,
        },
        "concept": concept_summary,
        "blueprint": blueprint,
        "generated_music": {
            "generation_model": "kimusic_proxy",
            "generation_method": "proxy",
            "target_va": target_va,
            "concept": concept_summary,
            "blueprint": blueprint,
            "emotion_alignment_score": 1.0,
        },
    }

