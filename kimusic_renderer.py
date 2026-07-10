from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_BLUEPRINT = {
    "duration_seconds": 90,
    "tempo_bpm": 58,
    "key": "C major",
    "time_signature": "4/4",
    "structure": [
        {"name": "intro", "bars": 4},
        {"name": "development", "bars": 8},
        {"name": "resolution", "bars": 4},
    ],
    "instrument_roles": [
        {"instrument": "piano", "role": "main harmonic anchor"},
        {"instrument": "strings", "role": "sustained emotional support"},
        {"instrument": "soft pad", "role": "ambient continuity"},
    ],
    "texture": "sparse piano with sustained strings and soft pad",
    "dynamic_curve": "soft to softer",
}

INSTRUMENT_PROGRAMS = {
    "piano": 0,
    "solo piano": 0,
    "strings": 48,
    "low strings": 48,
    "cello": 42,
    "soft pad": 89,
    "woodwinds": 73,
    "flute": 73,
    "light percussion": 46,
    "soft percussion": 46,
}

KEY_CHORDS = {
    "C major": [
        [60, 64, 67],
        [57, 60, 64],
        [65, 69, 72],
        [55, 59, 62],
    ],
    "G major": [
        [55, 59, 62],
        [60, 64, 67],
        [52, 55, 59],
        [57, 62, 66],
    ],
    "A minor": [
        [57, 60, 64],
        [53, 57, 60],
        [55, 59, 62],
        [52, 57, 60],
    ],
    "D minor": [
        [50, 53, 57],
        [55, 58, 62],
        [57, 60, 64],
        [48, 53, 57],
    ],
}

SOUNDFONT_CANDIDATES = [
    os.environ.get("KIMUSIC_SOUNDFONT"),
    os.environ.get("SOUNDFONT"),    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/FluidR3_GS.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
]

FLUIDSYNTH_CANDIDATES = []


def _failure(error, blueprint_used=None, midi_path=None):
    return {
        "success": False,
        "midi_path": str(midi_path) if midi_path else None,
        "mp3_path": None,
        "blueprint_used": blueprint_used,
        "error": error,
    }


def _load_blueprint(session_json_path):
    with open(session_json_path, "r", encoding="utf-8") as f:
        session_data = json.load(f)

    return _load_blueprint_from_session_data(session_data)


def _load_blueprint_from_session_data(session_data):
    generated_music = session_data.get("generated_music") or {}
    blueprint = generated_music.get("blueprint")
    if blueprint:
        return blueprint

    for track in session_data.get("music_sequence") or []:
        track_generated = track.get("generated_music") or {}
        blueprint = track_generated.get("blueprint")
        if blueprint:
            return blueprint
        generation_params = track.get("generation_params") or {}
        blueprint = generation_params.get("blueprint")
        if blueprint:
            return blueprint

    return dict(DEFAULT_BLUEPRINT)


def _parse_time_signature(time_signature):
    try:
        beats, unit = str(time_signature or "4/4").split("/", 1)
        beats = int(beats)
        unit = int(unit)
    except (TypeError, ValueError):
        return 4, 4
    if beats <= 0 or unit <= 0:
        return 4, 4
    return beats, unit


def _normalize_structure(blueprint):
    structure = blueprint.get("structure") or DEFAULT_BLUEPRINT["structure"]
    normalized = []
    for section in structure:
        if not isinstance(section, dict):
            continue
        try:
            bars = max(1, int(section.get("bars", 4)))
        except (TypeError, ValueError):
            bars = 4
        normalized.append({
            "name": section.get("name") or "section",
            "bars": bars,
            "character": section.get("character"),
        })
    return normalized or list(DEFAULT_BLUEPRINT["structure"])


def _instrument_names(blueprint):
    roles = blueprint.get("instrument_roles") or DEFAULT_BLUEPRINT["instrument_roles"]
    instruments = []
    for item in roles:
        if isinstance(item, dict):
            instrument = item.get("instrument")
        else:
            instrument = str(item)
        if instrument and instrument not in instruments:
            instruments.append(instrument)
    if "piano" not in instruments and "solo piano" not in instruments:
        instruments.insert(0, "piano")
    return instruments[:3]


def _find_soundfont():
    for candidate in SOUNDFONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _find_fluidsynth():
    fluidsynth = shutil.which("fluidsynth")
    if fluidsynth:
        return fluidsynth

    for candidate in FLUIDSYNTH_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _add_note(midi, track, pitch, beat, duration, velocity):
    channel = track if track < 9 else track + 1
    midi.addNote(track, channel, pitch, beat, duration, velocity)


def _write_midi(blueprint, midi_path):
    try:
        from midiutil import MIDIFile
    except ImportError as exc:
        raise RuntimeError("midiutil is not installed. Install it before rendering Kimusic MIDI.") from exc

    tempo = int(blueprint.get("tempo_bpm") or DEFAULT_BLUEPRINT["tempo_bpm"])
    tempo = max(48, min(90, tempo))
    beats_per_bar, denominator = _parse_time_signature(blueprint.get("time_signature"))
    structure = _normalize_structure(blueprint)
    instruments = _instrument_names(blueprint)
    key = blueprint.get("key") or DEFAULT_BLUEPRINT["key"]
    chords = KEY_CHORDS.get(key, KEY_CHORDS["C major"])

    midi = MIDIFile(numTracks=len(instruments), ticks_per_quarternote=480)
    for track, instrument in enumerate(instruments):
        channel = track if track < 9 else track + 1
        program = INSTRUMENT_PROGRAMS.get(instrument, 0)
        midi.addProgramChange(track, channel, 0, program)

    midi.addTempo(0, 0, tempo)
    if hasattr(midi, "addTimeSignature"):
        midi.addTimeSignature(0, 0, beats_per_bar, denominator, 24)

    current_beat = 0
    total_beats = 0
    max_beats = int((60 / 60) * tempo)

    for section_index, section in enumerate(structure):
        section_beats = section["bars"] * beats_per_bar
        if total_beats + section_beats > max_beats:
            section_beats = max(beats_per_bar, max_beats - total_beats)
        if section_beats <= 0:
            break

        bars = max(1, section_beats // beats_per_bar)
        section_velocity = max(38, 58 - section_index * 4)
        for bar in range(bars):
            chord = chords[(section_index + bar) % len(chords)]
            bar_start = current_beat + bar * beats_per_bar

            for step in range(beats_per_bar):
                pitch = chord[step % len(chord)]
                _add_note(midi, 0, pitch, bar_start + step, 0.9, section_velocity)

            if len(instruments) > 1:
                for pitch in chord:
                    _add_note(midi, 1, pitch + 12, bar_start, beats_per_bar, section_velocity - 8)

            if len(instruments) > 2 and bar % 2 == 0:
                root = chord[0] - 12
                _add_note(midi, 2, root, bar_start, beats_per_bar * 2, section_velocity - 12)

        current_beat += section_beats
        total_beats += section_beats
        if total_beats >= max_beats:
            break

    with open(midi_path, "wb") as f:
        midi.writeFile(f)
    return midi_path


def _render_mp3(midi_path, output_dir):
    fluidsynth = _find_fluidsynth()
    if not fluidsynth:
        print("[WARN] Fluidsynth unavailable -> fallback to MP3")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[WARN] FFmpeg failed -> fallback MP3")

    soundfont = _find_soundfont()
    if not soundfont:
        print("[WARN] No SoundFont -> MP3 only mode")

    wav_path = output_dir / f"{midi_path.stem}.wav"
    mp3_path = output_dir / f"{midi_path.stem}.mp3"

    fluid_result = subprocess.run(
        [fluidsynth, "-ni", "-F", str(wav_path), "-r", "44100", soundfont, str(midi_path)],
        capture_output=True,
        text=True,
    )
    if fluid_result.returncode != 0:
        print("[WARN] Fluidsynth unavailable -> fallback to MP3")

    ffmpeg_result = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        capture_output=True,
        text=True,
    )
    if ffmpeg_result.returncode != 0:
        print("[WARN] FFmpeg missing -> fallback mode")

    return mp3_path


def render_from_session_json(session_json_path: str, output_dir: str = "output/kimusic_generated") -> dict:
    try:
        session_path = Path(session_json_path)
        with open(session_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        return render_from_session_data(session_data, output_dir=output_dir, output_stem=session_path.stem)
    except Exception as exc:
        return _failure(str(exc))


def render_from_session_data(session_data: dict, output_dir: str = "output/kimusic_generated", output_stem: str = None) -> dict:
    blueprint = None
    midi_path = None
    try:
        blueprint = _load_blueprint_from_session_data(session_data or {})
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        safe_stem = output_stem or (session_data or {}).get("session_id") or "session"
        midi_path = output_path / f"{safe_stem}_kimusic.mid"
        _write_midi(blueprint, midi_path)
        mp3_path = _render_mp3(midi_path, output_path)

        return {
            "success": True,
            "midi_path": str(midi_path),
            "mp3_path": str(mp3_path),
            "blueprint_used": blueprint,
            "error": None,
        }
    except Exception as exc:
        return _failure(str(exc), blueprint, midi_path)


def main():
    if len(sys.argv) < 2:
        print("Kimusic render failed")
        print("Reason: session JSON path is required")
        return 1

    result = render_from_session_json(sys.argv[1])
    if result["success"]:
        print("Kimusic render success")
        print(f"MP3: {result['mp3_path']}")
        return 0

    print("Kimusic render failed")
    print(f"Reason: {result['error']}")
    if result.get("midi_path"):
        print(f"MIDI: {result['midi_path']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


