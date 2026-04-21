"""
input_manager.py — Unified controller + keyboard input with configurable keybinds.

Supports Xbox 360 controller buttons, axes (with deadzone), and hats (D-pad).
Falls back to keyboard arrows / Enter / Escape when no controller is present.

Designed to minimize event processing overhead to avoid interfering with
the audio callback thread (which caused lag on previous implementation).
"""

import time
import pygame


# Keyboard fallback mapping: action → pygame key constant
_KB_MAP = {
    "navigate_up":    pygame.K_UP,
    "navigate_down":  pygame.K_DOWN,
    "navigate_left":  pygame.K_LEFT,
    "navigate_right": pygame.K_RIGHT,
    "confirm":        pygame.K_RETURN,
    "back":           pygame.K_ESCAPE,
    "launch_script_up":   pygame.K_UP,
    "launch_script_down": pygame.K_DOWN,
}


class InputManager:
    """
    Polls pygame events and exposes high-level logical actions.

    Usage::

        im = InputManager(settings)
        # inside main loop:
        actions = im.poll()   # → set of action names fired this frame
    """

    def __init__(self, settings: dict):
        keybinds = settings.get("keybinds", {})
        input_cfg = settings.get("input", {})

        self._bindings: dict[str, dict] = {}
        for action, bind in keybinds.items():
            self._bindings[action] = dict(bind)

        self._cooldown_ms = input_cfg.get("navigate_cooldown_ms", 250)
        self._deadzone = input_cfg.get("axis_deadzone", 0.5)

        # Timestamps of last trigger per action (for debounce)
        self._last_trigger: dict[str, float] = {}

        # Joystick reference
        self._joystick: pygame.joystick.JoystickType | None = None
        self._joystick_id: int | None = None  # instance id to track
        pygame.joystick.init()
        self._init_first_joystick()

        # Track events this frame
        self._button_downs: set[int] = set()
        self._hat_values: dict[int, tuple[int, int]] = {}
        self._key_downs: set[int] = set()

    def _init_first_joystick(self):
        """Connect to the first available joystick (one-time, no subsystem restart)."""
        try:
            count = pygame.joystick.get_count()
            if count > 0 and self._joystick is None:
                js = pygame.joystick.Joystick(0)
                js.init()
                self._joystick = js
                self._joystick_id = js.get_instance_id()
                print(f"[input] Controller: {js.get_name()}")
        except Exception:
            pass

    # ── public ──────────────────────────────────────────────────────────────

    def poll(self, events) -> set[str]:
        """
        Process this frame's pygame events and return the set of logical
        actions that were triggered.
        """
        self._button_downs.clear()
        self._key_downs.clear()
        self._hat_values.clear()

        for event in events:
            if event.type == pygame.QUIT:
                return {"quit"}
            elif event.type == pygame.JOYBUTTONDOWN:
                self._button_downs.add(event.button)
            elif event.type == pygame.JOYHATMOTION:
                self._hat_values[event.hat] = event.value
            elif event.type == pygame.KEYDOWN:
                self._key_downs.add(event.key)
            elif event.type == pygame.JOYDEVICEADDED:
                # Only connect if we don't already have one
                if self._joystick is None:
                    self._init_first_joystick()
            elif event.type == pygame.JOYDEVICEREMOVED:
                # Only disconnect if it matches our tracked joystick
                if hasattr(event, 'instance_id') and event.instance_id == self._joystick_id:
                    self._joystick = None
                    self._joystick_id = None

        # Poll current hat state for held directions
        if self._joystick is not None:
            try:
                for h in range(self._joystick.get_numhats()):
                    val = self._joystick.get_hat(h)
                    if val != (0, 0):
                        self._hat_values[h] = val
            except Exception:
                pass

        # Poll analog stick axes for navigation
        # Left stick: axis 0 = X (left/right), axis 1 = Y (up/down)
        axis_actions: set[str] = set()
        if self._joystick is not None:
            try:
                ax0 = self._joystick.get_axis(0)  # left stick X
                ax1 = self._joystick.get_axis(1)  # left stick Y
                if ax0 < -self._deadzone:
                    axis_actions.add("navigate_left")
                elif ax0 > self._deadzone:
                    axis_actions.add("navigate_right")
                if ax1 < -self._deadzone:
                    axis_actions.add("navigate_up")
                    axis_actions.add("launch_script_up")
                elif ax1 > self._deadzone:
                    axis_actions.add("navigate_down")
                    axis_actions.add("launch_script_down")
            except Exception:
                pass

        now = time.monotonic()
        triggered: set[str] = set()

        for action, bind in self._bindings.items():
            if self._check_bind(bind) or self._check_keyboard(action):
                # Apply cooldown for navigation-type actions
                if "navigate" in action or "launch_script" in action:
                    last = self._last_trigger.get(action, 0)
                    if (now - last) * 1000 < self._cooldown_ms:
                        continue
                self._last_trigger[action] = now
                triggered.add(action)

        # Add axis-triggered actions (with cooldown)
        for action in axis_actions:
            last = self._last_trigger.get(action, 0)
            if (now - last) * 1000 < self._cooldown_ms:
                continue
            self._last_trigger[action] = now
            triggered.add(action)

        # Keyboard Escape always maps to back
        if pygame.K_ESCAPE in self._key_downs:
            triggered.add("back")

        return triggered

    def refresh_joystick(self):
        """Re-scan for controllers."""
        self._init_first_joystick()

    # ── private ─────────────────────────────────────────────────────────────

    def _check_bind(self, bind: dict) -> bool:
        if self._joystick is None:
            return False
        btype = bind.get("type", "")
        idx = bind.get("index", 0)

        if btype == "button":
            return idx in self._button_downs

        elif btype == "hat":
            expected = tuple(bind.get("value", [0, 0]))
            actual = self._hat_values.get(idx)
            if actual is None:
                return False
            return actual == expected

        elif btype == "axis":
            try:
                val = self._joystick.get_axis(idx)
            except Exception:
                return False
            threshold = bind.get("threshold", self._deadzone)
            if threshold >= 0:
                return val > threshold
            else:
                return val < threshold

        return False

    def _check_keyboard(self, action: str) -> bool:
        key = _KB_MAP.get(action)
        if key is None:
            return False
        return key in self._key_downs
