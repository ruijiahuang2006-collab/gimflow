# Appendix D2: Kimusic Blueprint Construction Interface

This document describes the public-release Kimusic blueprint construction interface used by the GIMFlow condition.

In the released code, the Kimusic music blueprint is constructed from the target valence-arousal (VA) state and waypoint sequence through deterministic helper functions in `kimusic_client.py`, rather than being exposed as a standalone prompt template.

The relevant implementation entry points are:

- `va_to_kimusic_concept(...)`
- `concept_to_blueprint(...)`
- `create_proxy_generated_track(...)`

The generated blueprint is then consumed by the procedural renderer in `kimusic_renderer.py`.

## Inputs

The blueprint construction interface receives:

- `target_va`: the target valence-arousal coordinate.
- `waypoint_sequence`: the four-step VA trajectory planned from the participant's current state to the target state.
- `duration_seconds`: the intended music duration, set to 90 seconds in the demo configuration.
- `track_index` and `session_id`: identifiers used for output organization and logging.

## Concept Representation

`va_to_kimusic_concept(...)` maps the target VA coordinate to an intermediate concept object containing fields such as:

```json
{
  "title": "Kimusic Proxy Waypoint",
  "duration_seconds": 90,
  "moods": ["calm", "grounding"],
  "instrumentation": ["piano", "strings", "pad"],
  "waypoint_sequence": [
    {"index": 0, "v": -0.25, "a": 0.35},
    {"index": 1, "v": -0.05, "a": 0.20},
    {"index": 2, "v": 0.20, "a": 0.05},
    {"index": 3, "v": 0.45, "a": -0.10}
  ]
}
```

The exact mood and instrumentation choices are rule-derived from the target VA state.

## Blueprint Schema

`concept_to_blueprint(...)` converts the concept into a structured blueprint consumed by the renderer. The blueprint includes:

```json
{
  "duration_seconds": 90,
  "tempo": 72,
  "key": "C major",
  "mode": "major",
  "moods": ["calm", "grounding"],
  "texture": "soft layered accompaniment",
  "harmonic_language": "simple consonant progressions",
  "melodic_motion": "stepwise and gently rising",
  "dynamic_curve": "gradual softening",
  "structure": [
    {
      "section_index": 0,
      "label": "opening",
      "duration_seconds": 20,
      "waypoint_index": 0,
      "waypoint_va": {"v": -0.25, "a": 0.35}
    },
    {
      "section_index": 1,
      "label": "development",
      "duration_seconds": 20,
      "waypoint_index": 1,
      "waypoint_va": {"v": -0.05, "a": 0.20}
    },
    {
      "section_index": 2,
      "label": "grounding",
      "duration_seconds": 25,
      "waypoint_index": 2,
      "waypoint_va": {"v": 0.20, "a": 0.05}
    },
    {
      "section_index": 3,
      "label": "resolution",
      "duration_seconds": 25,
      "waypoint_index": 3,
      "waypoint_va": {"v": 0.45, "a": -0.10}
    }
  ],
  "instrument_roles": [
    {"instrument": "piano", "role": "main harmonic support"},
    {"instrument": "strings", "role": "sustained background texture"},
    {"instrument": "pad", "role": "ambient continuity"}
  ]
}
```

## Relationship to Waypoints

The four-section structure records a one-to-one mapping from musical sections to planned VA waypoints. This makes the intended affective trajectory inspectable in the session log and allows the renderer to shape the music as a single internally structured piece rather than four separate tracks.

## Renderer Interface

The procedural renderer reads the blueprint and produces MIDI/audio using:

- overall duration;
- tempo and key;
- section structure;
- instrument roles;
- section-level waypoint metadata; and
- dynamic/texture settings.

The released demo audio in `demo_audio/` is an example rendered output. The SoundFont license text is provided in `soundfonts/FluidR3_GM_LICENSE.txt`.