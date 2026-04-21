"""
config.py — Load and validate games.yaml and settings.yaml.
"""

import os
import sys
import yaml


# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "display": {
        "width": 1080,
        "height": 1920,
        "fullscreen": True,
        "monitor": 0,
        "secondary_monitor": 1,
        "fps": 60,
    },
    "audio": {
        "asio_device": "",
        "output_channels": [0, 1],
        "sample_rate": 44100,
        "crossfade_ms": 800,
        "volume": 0.7,
    },
    "keybinds": {
        "navigate_up":   {"type": "hat", "index": 0, "value": [0, 1]},
        "navigate_down": {"type": "hat", "index": 0, "value": [0, -1]},
        "navigate_left": {"type": "hat", "index": 0, "value": [-1, 0]},
        "navigate_right":{"type": "hat", "index": 0, "value": [1, 0]},
        "confirm":       {"type": "button", "index": 0},
        "back":          {"type": "button", "index": 1},
        "launch_script_up":   {"type": "hat", "index": 0, "value": [0, 1]},
        "launch_script_down": {"type": "hat", "index": 0, "value": [0, -1]},
    },
    "input": {
        "navigate_cooldown_ms": 250,
        "axis_deadzone": 0.5,
        "show_inputs": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (non-destructive)."""
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


# ── Public API ──────────────────────────────────────────────────────────────

def base_dir() -> str:
    """Return the directory that contains this script or the executable."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_settings(path: str | None = None) -> dict:
    """Load settings.yaml, falling back to defaults for missing keys."""
    if path is None:
        path = os.path.join(base_dir(), "settings.yaml")
    if not os.path.isfile(path):
        print(f"[config] settings.yaml not found at {path}; using defaults.")
        return DEFAULT_SETTINGS.copy()
    with open(path, "r", encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_SETTINGS, user)


def load_games(path: str | None = None) -> list[dict]:
    """
    Parse games.yaml into a list of game dicts.

    Each entry gets:
        name            – top-level key (game title)
        bg_image_path   – resolved absolute path to the background image
        sound_subfolder – subfolder name under sounds/
        bgm_path        – resolved absolute path to the BGM file
        start_sound_path– resolved absolute path to the start sound
        launch_scripts  – list of {name, path} dicts
    """
    if path is None:
        path = os.path.join(base_dir(), "games.yaml")
    if not os.path.isfile(path):
        print(f"[config] ERROR: games.yaml not found at {path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    root = base_dir()
    games: list[dict] = []

    for name, props in raw.items():
        bg_image = props.get("bg image", "")
        sound_sub = props.get("sound subfolder", "")
        bgm = props.get("bgm", "")
        start_sound = props.get("start sound", "")

        bg_image_path = os.path.join(root, "images", bg_image) if bg_image else ""
        bgm_path = os.path.join(root, "sounds", sound_sub, bgm) if (sound_sub and bgm) else ""
        start_sound_path = os.path.join(root, "sounds", sound_sub, start_sound) if (sound_sub and start_sound) else ""

        # Parse launch scripts (numbered dict → ordered list)
        raw_scripts = props.get("launch scripts", {}) or {}
        scripts = []
        for idx in sorted(raw_scripts.keys(), key=lambda k: int(k)):
            entry = raw_scripts[idx]
            scripts.append({
                "name": entry.get("name", f"Script {idx}"),
                "path": entry.get("path", ""),
            })

        game = {
            "name": name,
            "bg_image_path": bg_image_path,
            "sound_subfolder": sound_sub,
            "bgm_path": bgm_path,
            "start_sound_path": start_sound_path,
            "launch_scripts": scripts,
        }
        games.append(game)

        # Warn about missing assets
        if bg_image_path and not os.path.isfile(bg_image_path):
            print(f"[config] WARNING: missing bg image for '{name}': {bg_image_path}")
        if bgm_path and not os.path.isfile(bgm_path):
            print(f"[config] WARNING: missing bgm for '{name}': {bgm_path}")

    print(f"[config] Loaded {len(games)} games from {path}")
    return games
