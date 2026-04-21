"""
carousel.py — Wii-style horizontal cover-flow carousel.

Renders:
  • A horizontal row of game cover images with the selected game centered
    and larger, neighbours receding to the sides with perspective tilt.
  • The game title displayed below the carousel.
  • Smooth slide animation when navigating.
  • A sub-menu overlay when confirming a game with multiple launch scripts.

All images are forced to 1080×1920 (or a cover-sized crop thereof).
"""

import os
import math
import pygame

# ── Colours ─────────────────────────────────────────────────────────────────

COL_BG           = (12, 12, 20)
COL_TITLE        = (255, 255, 255)
COL_ACCENT       = (90, 140, 255)
COL_ACCENT_DIM   = (60, 90, 180)
COL_SUBMENU_BG   = (18, 18, 30, 230)
COL_SUBMENU_SEL  = (90, 140, 255)
COL_SUBMENU_TXT  = (220, 220, 240)
COL_HINT         = (120, 120, 160)
COL_ARROW        = (180, 200, 255, 200)

# Cover aspect ratio: 9:16 (portrait), displayed as cover art
COVER_ASPECT = 9 / 16

def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


_FONT_CACHE = {}

def _load_custom_font(font_dir: str, size: int) -> pygame.font.Font:
    """
    Load a font from font_dir. Prioritizes Noto Sans JP if available.
    Falls back to common system fonts for Unicode support if needed.
    Caches results to avoid repetitive disk I/O.
    """
    cache_key = (font_dir, size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    font = None
    if os.path.isdir(font_dir):
        files = os.listdir(font_dir)
        # Prioritize Noto Sans JP
        for f in files:
            if "notosansjp" in f.lower() and f.lower().endswith((".ttf", ".otf")):
                try:
                    font = pygame.font.Font(os.path.join(font_dir, f), size)
                    break
                except Exception:
                    pass
        
        if not font:
            # Fallback to any other custom font in the dir
            for f in files:
                if f.lower().endswith((".ttf", ".otf")):
                    try:
                        font = pygame.font.Font(os.path.join(font_dir, f), size)
                        break
                    except Exception:
                        pass

    if not font:
        # System fallback: Try to find a font with good Unicode/CJK support
        fallbacks = ["notosansjp", "yugothic", "meiryo", "msgothic", "arialunicode", "segoeui"]
        for name in fallbacks:
            match = pygame.font.match_font(name)
            if match:
                try:
                    font = pygame.font.Font(match, size)
                    break
                except Exception:
                    pass

    if not font:
        font = pygame.font.SysFont(None, size)

    _FONT_CACHE[cache_key] = font
    return font


class Carousel:
    """Wii-style horizontal cover-flow game selector."""

    def __init__(self, games: list[dict], width: int, height: int, font_dir: str = "font"):
        self.games = games
        self.W = width
        self.H = height

        # Selection
        self._index = 0
        self._prev_index = 0

        # Smooth scroll: _scroll_pos is the floating-point "current position"
        # that lerps toward _index for smooth transitions
        self._scroll_pos = 0.0
        self._scroll_speed = 8.0     # lerp speed (higher = faster snap)

        # Sub-menu state
        self._submenu_open = False
        self._submenu_index = 0
        self._submenu_scripts: list[dict] = []

        # Fonts (from ./font directory)
        self._font_dir = font_dir
        self._font_title   = _load_custom_font(font_dir, int(height * 0.028))
        self._font_sub     = _load_custom_font(font_dir, int(height * 0.024))
        self._font_sub_sm  = _load_custom_font(font_dir, int(height * 0.018))
        self._font_hint    = _load_custom_font(font_dir, int(height * 0.013))

        # Pre-compute max title font size per game (so text fits on screen)
        self._title_max_size = int(height * 0.028)
        self._title_margin = int(width * 0.05)  # 5% margin on each side

        # Cover dimensions (for the center cover)
        self._cover_h = int(height * 0.50)
        self._cover_w = int(self._cover_h * COVER_ASPECT)

        # Pre-load, force to 1080×1920, then create cover thumbnails + screen-sized BGs
        self._bg_screen: dict[int, pygame.Surface | None] = {}   # pre-scaled to screen
        self._covers: dict[int, pygame.Surface | None] = {}      # cover thumbnails
        for i, g in enumerate(games):
            full = self._load_and_force_1080x1920(g["bg_image_path"])
            if full is not None:
                self._bg_screen[i] = pygame.transform.smoothscale(full, (width, height))
                self._covers[i] = pygame.transform.smoothscale(
                    full, (self._cover_w, self._cover_h))
            else:
                self._bg_screen[i] = None
                self._covers[i] = self._make_placeholder_cover(g["name"])

        # Pre-create the darkened overlay (reused every frame)
        self._overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        self._overlay.fill((0, 0, 0, 120))

        # ── Precompute ALL cover variants at boot ───────────────────────
        # For each game, at each quantized offset, pre-scale+rotate+darken
        # so the render loop does ZERO transforms per frame.
        self._offset_step = 0.1
        self._max_offset = 3.0
        self._precomputed: dict[tuple[int, float], tuple[pygame.Surface, int, int]] = {}
        print("[carousel] Precomputing cover variants...")
        self._precompute_all()
        print(f"[carousel] Precomputed {len(self._precomputed)} surfaces.")

        # Result (set by caller after sub-menu confirm)
        self.launch_request: dict | None = None

    # ── Image loading ───────────────────────────────────────────────────────

    def _load_and_force_1080x1920(self, path: str) -> pygame.Surface | None:
        """Load an image and force it to exactly 1080×1920 via crop+scale."""
        if not path or not os.path.isfile(path):
            # Try .jpg fallback if .png not found
            alt = path.rsplit(".", 1)[0] + ".jpg" if path else ""
            if alt and os.path.isfile(alt):
                path = alt
            else:
                return None
        try:
            img = pygame.image.load(path).convert()
        except Exception as e:
            print(f"[carousel] Failed to load: {path}: {e}")
            return None

        iw, ih = img.get_size()
        target_w, target_h = 1080, 1920

        # Scale to fill (cover), then center-crop
        scale_w = target_w / iw
        scale_h = target_h / ih
        scale = max(scale_w, scale_h)

        new_w = max(target_w, math.ceil(iw * scale))
        new_h = max(target_h, math.ceil(ih * scale))
        img = pygame.transform.smoothscale(img, (new_w, new_h))

        # Center crop to 1080×1920
        cx = max(0, (new_w - target_w) // 2)
        cy = max(0, (new_h - target_h) // 2)
        img = img.subsurface((cx, cy, target_w, target_h)).copy()
        return img

    def _make_placeholder_cover(self, name: str) -> pygame.Surface:
        """Generate a dark placeholder cover with the game name."""
        surf = pygame.Surface((self._cover_w, self._cover_h))
        surf.fill((30, 30, 50))
        pygame.draw.rect(surf, COL_ACCENT_DIM, surf.get_rect(), 2)
        # Render name centered
        font = self._font_hint
        lines = self._wrap_text(name, font, self._cover_w - 20)
        y = self._cover_h // 2 - len(lines) * font.get_height() // 2
        for line in lines:
            txt = font.render(line, True, COL_TITLE)
            surf.blit(txt, (self._cover_w // 2 - txt.get_width() // 2, y))
            y += font.get_height()
        return surf

    def _wrap_text(self, text: str, font: pygame.font.Font, max_w: int) -> list[str]:
        words = text.split()
        lines = []
        current = ""
        for w in words:
            test = f"{current} {w}".strip()
            if font.size(test)[0] <= max_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines or [text]

    def _render_fitted_text(self, text: str, color: tuple, max_w: int,
                            max_size: int | None = None) -> pygame.Surface:
        """Render text, auto-shrinking font size until it fits within max_w."""
        if max_size is None:
            max_size = self._title_max_size
        size = max_size
        min_size = max(8, max_size // 4)
        while size >= min_size:
            font = _load_custom_font(self._font_dir, size)
            surf = font.render(text, True, color)
            if surf.get_width() <= max_w:
                return surf
            size -= 2
        # Last resort: render at min size
        font = _load_custom_font(self._font_dir, min_size)
        return font.render(text, True, color)

    # ── Update ──────────────────────────────────────────────────────────────

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def submenu_open(self) -> bool:
        return self._submenu_open

    def update(self, dt: float, actions: set[str]):
        """Advance animation and handle input actions. *dt* in seconds."""
        self.launch_request = None
        n = len(self.games)

        # Smoothly lerp scroll position toward target index
        if n > 0:
            target = float(self._index)
            diff = target - self._scroll_pos
            # Handle wrapping (e.g., going from last to first)
            if abs(diff) > n / 2:
                if diff > 0:
                    diff -= n
                else:
                    diff += n
            self._scroll_pos += diff * min(1.0, dt * self._scroll_speed)
            # Keep scroll_pos in [0, n) range
            self._scroll_pos = self._scroll_pos % n

        if not self.games:
            return

        # ── Sub-menu mode ────────────────────────────────────────────────
        if self._submenu_open:
            if "launch_script_up" in actions or "navigate_up" in actions:
                self._submenu_index = (self._submenu_index - 1) % len(self._submenu_scripts)
            if "launch_script_down" in actions or "navigate_down" in actions:
                self._submenu_index = (self._submenu_index + 1) % len(self._submenu_scripts)
            if "confirm" in actions:
                script = self._submenu_scripts[self._submenu_index]
                self.launch_request = {"script": script}
                self._submenu_open = False
            if "back" in actions:
                self._submenu_open = False
            return

        # ── Carousel navigation (left/right) ────────────────────────────
        if "navigate_left" in actions or "navigate_up" in actions:
            self._prev_index = self._index
            self._index = (self._index - 1) % n
        elif "navigate_right" in actions or "navigate_down" in actions:
            self._prev_index = self._index
            self._index = (self._index + 1) % n

        # Confirm → open sub-menu or launch directly
        if "confirm" in actions:
            scripts = self.games[self._index].get("launch_scripts", [])
            if len(scripts) == 0:
                pass
            elif len(scripts) == 1:
                self.launch_request = {"script": scripts[0]}
            else:
                self._submenu_scripts = scripts
                self._submenu_index = 0
                self._submenu_open = True

    # ── Draw ────────────────────────────────────────────────────────────────

    def draw(self, screen: pygame.Surface):
        """Render the full carousel frame."""
        if not self.games:
            screen.fill(COL_BG)
            label = self._font_title.render("No games loaded", True, COL_TITLE)
            screen.blit(label, label.get_rect(center=(self.W // 2, self.H // 2)))
            return

        # Compute how close we are to settled (for BG crossfade)
        n = len(self.games)
        diff_to_target = self._index - self._scroll_pos
        if abs(diff_to_target) > n / 2:
            diff_to_target = diff_to_target - n if diff_to_target > 0 else diff_to_target + n
        t = 1.0 - min(1.0, abs(diff_to_target) * 2.0)  # 0=moving, 1=settled

        # ── Background (current game, crossfading) ──────────────────────
        self._draw_background(screen, t)

        # ── Darkened overlay for contrast (pre-created) ──────────────────
        screen.blit(self._overlay, (0, 0))

        # ── Cover flow ──────────────────────────────────────────────────
        self._draw_covers(screen, t)

        # ── Game title (auto-scaled to fit) ─────────────────────────────
        # Position title at the top of the screen
        title_y = int(self.H * 0.06)
        name = self.games[self._index]["name"]
        max_title_w = self.W - self._title_margin * 2
        title_surf = self._render_fitted_text(name, COL_TITLE, max_title_w)
        title_rect = title_surf.get_rect(center=(self.W // 2, title_y))
        screen.blit(title_surf, title_rect)

        # Script count hint
        n_scripts = len(self.games[self._index].get("launch_scripts", []))
        if n_scripts > 1:
            hint = self._font_hint.render(f"{n_scripts} launch options", True, COL_ACCENT)
            screen.blit(hint, hint.get_rect(center=(self.W // 2, title_y + title_surf.get_height() + 8)))

        # ── Navigation arrows ──────────────────────────────────────────
        self._draw_arrows(screen)

        # ── Bottom hints ────────────────────────────────────────────────
        hint_y = int(self.H * 0.95)
        hint_text = "<< >>  Navigate   |   A  Confirm   |   B  Back"
        max_hint_w = self.W - self._title_margin * 2
        hints = self._render_fitted_text(hint_text, COL_HINT, max_hint_w,
                                         max_size=int(self.H * 0.013))
        screen.blit(hints, hints.get_rect(center=(self.W // 2, hint_y)))

        # ── Sub-menu ────────────────────────────────────────────────────
        if self._submenu_open:
            self._draw_submenu(screen)

    # ── Background ──────────────────────────────────────────────────────────

    def _draw_background(self, screen: pygame.Surface, t: float):
        """Draw the fullscreen BG image with crossfade."""
        screen.fill(COL_BG)
        prev_bg = self._bg_screen.get(self._prev_index)
        curr_bg = self._bg_screen.get(self._index)

        if curr_bg and prev_bg and t < 1.0 and self._prev_index != self._index:
            # BGs are already screen-sized — just blit with alpha
            prev_bg.set_alpha(int(255 * (1 - t)))
            screen.blit(prev_bg, (0, 0))
            prev_bg.set_alpha(255)
            curr_bg.set_alpha(int(255 * t))
            screen.blit(curr_bg, (0, 0))
            curr_bg.set_alpha(255)
        elif curr_bg:
            screen.blit(curr_bg, (0, 0))

    # ── Cover Flow ──────────────────────────────────────────────────────────

    def _precompute_all(self):
        """Precompute scaled+rotated cover surfaces for every game at every offset step."""
        step = self._offset_step
        max_off = self._max_offset
        n = len(self.games)

        # Generate all quantized offsets: -3.0, -2.75, ..., 0, ..., 2.75, 3.0
        offsets = []
        q = -max_off
        while q <= max_off + 0.001:
            offsets.append(round(q, 2))
            q += step

        for gi in range(n):
            cover = self._covers.get(gi)
            if cover is None:
                continue

            for q in offsets:
                dist = abs(q)
                scale_factor = max(0.50, 1.0 - dist * 0.12)
                cw = int(self._cover_w * scale_factor)
                ch = int(self._cover_h * scale_factor)
                if cw < 10 or ch < 10:
                    continue

                # Scale (use smoothscale at boot, it's a one-time cost)
                scaled = pygame.transform.smoothscale(cover, (cw, ch))

                # Rotation — use rotozoom for anti-aliased (smooth) edges
                rotation_angle = -q * 8
                rotation_angle = max(-20, min(20, rotation_angle))
                if abs(rotation_angle) > 0.5:
                    alpha_surf = scaled.convert_alpha()
                    scaled = pygame.transform.rotozoom(alpha_surf, rotation_angle, 1.0)

                rw, rh = scaled.get_size()
                self._precomputed[(gi, q)] = (scaled, rw, rh)

    def _draw_covers(self, screen: pygame.Surface, t: float):
        """Draw covers using precomputed surfaces — zero transforms per frame."""
        center_x = self.W // 2
        center_y = self.H // 2
        spacing = int(self._cover_w * 0.72)

        visible_each_side = 3
        n = len(self.games)
        sp = self._scroll_pos
        step = self._offset_step

        # Build draw list
        draw_list = []
        for slot in range(-visible_each_side, visible_each_side + 1):
            gi = (round(sp) + slot) % n
            float_offset = (gi - sp) % n
            if float_offset > n / 2:
                float_offset -= n
            if abs(float_offset) <= visible_each_side + 0.5:
                draw_list.append((float_offset, gi))

        # De-duplicate and sort back-to-front
        seen = set()
        unique_draw = []
        for fo, gi in draw_list:
            if gi not in seen:
                seen.add(gi)
                unique_draw.append((fo, gi))
        unique_draw.sort(key=lambda x: -abs(x[0]))

        for float_offset, gi in unique_draw:
            dist = abs(float_offset)
            x = center_x + float_offset * spacing

            # Snap to nearest precomputed offset step
            q = round(round(float_offset / step) * step, 2)
            q = max(-self._max_offset, min(self._max_offset, q))

            key = (gi, q)
            entry = self._precomputed.get(key)
            if entry is None:
                continue

            surf, rw, rh = entry

            # Alpha for side covers
            if dist > 0.1:
                alpha = max(80, int(255 - dist * 60))
                surf.set_alpha(alpha)
                screen.blit(surf, (int(x) - rw // 2, center_y - rh // 2))
                surf.set_alpha(255)
            else:
                screen.blit(surf, (int(x) - rw // 2, center_y - rh // 2))

    # ── Arrows ──────────────────────────────────────────────────────────────

    def _draw_arrows(self, screen: pygame.Surface):
        """Draw left/right navigation arrows."""
        arrow_y = self.H // 2           # match cover center
        arrow_size = int(self.H * 0.025)
        margin = int(self.W * 0.04)

        # Left arrow ◄
        left_x = margin
        pts_l = [
            (left_x + arrow_size, arrow_y - arrow_size),
            (left_x, arrow_y),
            (left_x + arrow_size, arrow_y + arrow_size),
        ]
        arrow_surf = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        pygame.draw.polygon(arrow_surf, COL_ARROW, pts_l)
        screen.blit(arrow_surf, (0, 0))

        # Right arrow ►
        right_x = self.W - margin
        pts_r = [
            (right_x - arrow_size, arrow_y - arrow_size),
            (right_x, arrow_y),
            (right_x - arrow_size, arrow_y + arrow_size),
        ]
        pygame.draw.polygon(arrow_surf, COL_ARROW, pts_r)
        screen.blit(arrow_surf, (0, 0))

    # ── Sub-menu overlay ────────────────────────────────────────────────────

    def _draw_submenu(self, screen: pygame.Surface):
        """Draw the launch script selection sub-menu."""
        # Full-screen dim
        dim = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        screen.blit(dim, (0, 0))

        game_name = self.games[self._index]["name"]

        # Panel
        panel_w = int(self.W * 0.7)
        line_h = int(self.H * 0.045)
        panel_h = line_h * (len(self._submenu_scripts) + 2) + 60
        px = (self.W - panel_w) // 2
        py = (self.H - panel_h) // 2

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        pygame.draw.rect(panel, COL_SUBMENU_BG,
                         (0, 0, panel_w, panel_h), border_radius=16)
        pygame.draw.rect(panel, (*COL_ACCENT, 100),
                         (0, 0, panel_w, panel_h), width=2, border_radius=16)
        screen.blit(panel, (px, py))

        # Title
        max_sub_w = panel_w - 40
        title = self._render_fitted_text(f"Launch: {game_name}", COL_ACCENT,
                                         max_sub_w, max_size=int(self.H * 0.024))
        screen.blit(title, title.get_rect(center=(self.W // 2, py + line_h + 10)))

        # Script entries
        for i, script in enumerate(self._submenu_scripts):
            y = py + line_h * (i + 2) + 20
            is_sel = (i == self._submenu_index)

            if is_sel:
                sel_rect = pygame.Surface((panel_w - 32, line_h - 4), pygame.SRCALPHA)
                pygame.draw.rect(sel_rect, (*COL_ACCENT, 50),
                                 (0, 0, panel_w - 32, line_h - 4), border_radius=8)
                screen.blit(sel_rect, (px + 16, y))

                # Selection indicator
                pygame.draw.rect(screen, COL_ACCENT,
                                 (px + 20, y + 4, 3, line_h - 12), border_radius=2)

            color = COL_SUBMENU_SEL if is_sel else COL_SUBMENU_TXT
            label = self._font_sub_sm.render(script.get("name", "?"), True, color)
            screen.blit(label, label.get_rect(midleft=(px + 36, y + line_h // 2 - 2)))

        # Hint at bottom of panel
        hint_text = self._font_hint.render("↑↓ Select  │  A Confirm  │  B Back", True, COL_HINT)
        screen.blit(hint_text, hint_text.get_rect(
            center=(self.W // 2, py + panel_h - 20)))
