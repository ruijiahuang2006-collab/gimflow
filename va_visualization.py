from __future__ import annotations
import argparse
import json
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


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


def load_session_json(session_json_path):
    with open(session_json_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_track_points(session_data):
    points = []
    for entry in session_data.get("music_sequence", []):
        track_va = normalize_va(entry.get("track_va"))
        if not track_va:
            continue
        points.append({
            "v": track_va["v"],
            "a": track_va["a"],
            "label": entry.get("track_title") or entry.get("title") or entry.get("filename") or "Track"
        })
    return points


def safe_output_name(session_json_path, session_data):
    session_id = session_data.get("session_id")
    if not session_id:
        session_id = os.path.splitext(os.path.basename(session_json_path))[0]
    safe_session_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(session_id))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"va_visualization_{safe_session_id}_{timestamp}.png"


def add_quadrant_background(ax):
    quadrants = [
        (-1, 0, 1, 1, "#f6c9b7", "High arousal\nNegative valence"),
        (0, 0, 1, 1, "#f7e5a5", "High arousal\nPositive valence"),
        (-1, -1, 1, 1, "#c8d9f0", "Low arousal\nNegative valence"),
        (0, -1, 1, 1, "#cfe8d6", "Low arousal\nPositive valence"),
    ]
    for x, y, width, height, color, label in quadrants:
        ax.add_patch(Rectangle((x, y), width, height, facecolor=color, edgecolor="none", alpha=0.28))
        ax.text(
            x + width / 2,
            y + height / 2,
            label,
            ha="center",
            va="center",
            fontsize=8,
            color="#666666",
            alpha=0.55
        )


def plot_va_data(session_data, output_path):
    current_va = normalize_va(session_data.get("current_state_va"))
    target_va = normalize_va(session_data.get("target_state_va"))
    waypoints = [normalize_va(item) for item in session_data.get("waypoint_sequence", [])]
    waypoints = [item for item in waypoints if item]
    track_points = get_track_points(session_data)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=100)
    add_quadrant_background(ax)

    ax.axhline(0, color="#444444", linewidth=0.8, alpha=0.7)
    ax.axvline(0, color="#444444", linewidth=0.8, alpha=0.7)
    ax.grid(True, color="#ffffff", linewidth=0.8, alpha=0.9)

    if waypoints:
        waypoint_v = [point["v"] for point in waypoints]
        waypoint_a = [point["a"] for point in waypoints]
        ax.plot(waypoint_v, waypoint_a, color="#5f6368", linewidth=1.8, marker="o", markersize=5, label="Waypoints")
        for index, point in enumerate(waypoints):
            ax.annotate(str(index), (point["v"], point["a"]), textcoords="offset points", xytext=(5, 5), fontsize=8)

    if current_va:
        ax.scatter(current_va["v"], current_va["a"], s=90, color="#1f77b4", edgecolor="white", linewidth=1.0, label="Current")
        ax.annotate("Current", (current_va["v"], current_va["a"]), textcoords="offset points", xytext=(7, -14), fontsize=9)

    if target_va:
        ax.scatter(target_va["v"], target_va["a"], s=120, color="#ff7f0e", marker="*", edgecolor="white", linewidth=1.0, label="Target")
        ax.annotate("Target", (target_va["v"], target_va["a"]), textcoords="offset points", xytext=(7, 7), fontsize=9)

    if track_points:
        track_v = [point["v"] for point in track_points]
        track_a = [point["a"] for point in track_points]
        ax.scatter(track_v, track_a, s=70, color="#2ca02c", marker="D", edgecolor="white", linewidth=0.8, label="Track VA")
        for index, point in enumerate(track_points, start=1):
            ax.annotate(f"T{index}", (point["v"], point["a"]), textcoords="offset points", xytext=(6, 6), fontsize=8)

    ax.set_xlim(-1.05, 1.05)
    ax.set_ylim(-1.05, 1.05)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Valence")
    ax.set_ylabel("Arousal")
    ax.set_title("Valence-Arousal Session Path")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=4, frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def generate_va_visualization(session_json_path: str, output_dir: str = "output/va_visualizations") -> str:
    session_data = load_session_json(session_json_path)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, safe_output_name(session_json_path, session_data))
    plot_va_data(session_data, output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate a V-A visualization PNG from a session JSON file.")
    parser.add_argument("session_json_path", help="Path to a session JSON file.")
    parser.add_argument("--output-dir", default="output/va_visualizations", help="Directory for generated PNG files.")
    args = parser.parse_args()

    output_path = generate_va_visualization(args.session_json_path, args.output_dir)
    print(output_path)


if __name__ == "__main__":
    main()

