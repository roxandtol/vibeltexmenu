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
import colorsys
import threading
import concurrent.futures
import pygame
from config import load_settings

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
        settings = load_settings()
        carousel = settings["carousel"]

        global COVER_ASPECT
        COVER_ASPECT = carousel["cover_aspect_ratio_width"] / carousel["cover_aspect_ratio_height"]
        
        global COL_TITLE
        COL_TITLE = tuple(carousel["title_color"])

        global COL_BG
        COL_BG = tuple(carousel["bg_color"])

        global COL_ACCENT
        COL_ACCENT = tuple(carousel["accent_color"])

        global COL_ACCENT_DIM
        COL_ACCENT_DIM = tuple(carousel["accent_dim_color"])

        global COL_SUBMENU_BG
        COL_SUBMENU_BG = tuple(carousel["submenu_bg_color"])

        global COL_SUBMENU_SEL
        COL_SUBMENU_SEL = tuple(carousel["submenu_selected_color"])

        global COL_SUBMENU_TXT
        COL_SUBMENU_TXT = tuple(carousel["submenu_text_color"])

        global COL_HINT
        COL_HINT = tuple(carousel["hint_color"])

        global COL_ARROW
        COL_ARROW = tuple(carousel["arrow_color"])
        
        self.games = games
        self.W = width
        self.H = height

        # Selection
        self._index = 0
        self._prev_index = 0

        # Smooth scroll: _scroll_pos is the floating-point "current position"
        # that lerps toward _index for smooth transitions
        self._scroll_pos = 0.0
        self._scroll_speed = 9.5     # fluid, responsive arcade glide

        # Sub-menu state & smooth animations
        self._submenu_open = False
        self._submenu_anim = 0.0
        self._submenu_sel_y = 0.0
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

        # Threaded pre-load and deduplication of background images
        self._bg_screen: dict[int, pygame.Surface | None] = {}   # pre-scaled to screen
        self._covers: dict[int, pygame.Surface | None] = {}      # cover thumbnails
        self._wing_colors: dict[int, dict] = {}                  # theme colors for wing lighting

        unique_paths = list({g.get("bg_image_path") for g in games if g.get("bg_image_path")})

        def _load_asset(p: str):
            full = self._load_and_force_1080x1920(p)
            if full is None:
                return p, (None, None, self._default_wing_colors())
            if full.get_size() == (width, height):
                bg = full
            else:
                bg = pygame.transform.smoothscale(full, (width, height))
            cov = pygame.transform.smoothscale(full, (self._cover_w, self._cover_h))
            wc = self._extract_wing_colors(full)
            return p, (bg, cov, wc)

        asset_cache = {}
        if unique_paths:
            workers = min(4, len(unique_paths))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for p, assets in executor.map(_load_asset, unique_paths):
                    asset_cache[p] = assets

        for i, g in enumerate(games):
            p = g.get("bg_image_path")
            if p and p in asset_cache and asset_cache[p][0] is not None:
                bg, cov, wc = asset_cache[p]
                self._bg_screen[i] = bg
                self._covers[i] = cov
                self._wing_colors[i] = wc
            else:
                self._bg_screen[i] = None
                self._covers[i] = self._make_placeholder_cover(g["name"])
                self._wing_colors[i] = self._default_wing_colors()

        # Pre-create the darkened overlay (reused every frame)
        self._overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        self._overlay.fill((0, 0, 0, 120))

        # ── Precompute cover variants in parallel across CPU cores ──────
        self._offset_step = 0.1
        self._max_offset = 3.0
        self._precomputed: dict[tuple[int, float], tuple[pygame.Surface, int, int]] = {}
        self._precompute_all()

        # Result (set by caller after sub-menu confirm)
        self.launch_request: dict | None = None

    def get_cover_surfaces(self) -> dict[int, pygame.Surface | None]:
        """Return pre-scaled 9:16 cover surfaces for use by secondary screen (GridMenu)."""
        return self._covers

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
            img = pygame.image.load(path)
            if pygame.display.get_surface() is not None:
                img = img.convert()
        except Exception as e:
            print(f"[carousel] Failed to load: {path}: {e}")
            return None

        iw, ih = img.get_size()
        target_w, target_h = 1080, 1920

        # Fast path if image is already target size
        if iw == target_w and ih == target_h:
            return img

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

    # ── Wing Lighting ───────────────────────────────────────────────────────

    @staticmethod
    def _default_wing_colors() -> dict:
        return {
            "upper_left": (90, 140, 255),
            "lower_left": (90, 140, 255),
            "upper_right": (90, 140, 255),
            "lower_right": (90, 140, 255),
            "woofer": (60, 90, 180),
            "controller": (90, 140, 255),
        }

    def _extract_wing_colors(self, surf: pygame.Surface) -> dict:
        w, h = surf.get_size()
        quads = {
            "upper_left": (0, 0, w // 2, h // 2),
            "lower_left": (0, h // 2, w // 2, h // 2),
            "upper_right": (w // 2, 0, w // 2, h // 2),
            "lower_right": (w // 2, h // 2, w // 2, h // 2),
            "controller": (int(w * 0.25), int(h * 0.5), int(w * 0.5), int(h * 0.5)),
        }
        results = {}
        for name, rect in quads.items():
            sub = surf.subsurface(rect)
            thumb = pygame.transform.smoothscale(sub, (24, 24))
            weighted_r, weighted_g, weighted_b, total_weight = 0.0, 0.0, 0.0, 0.0
            for y in range(24):
                for x in range(24):
                    r, g, b, _ = thumb.get_at((x, y))
                    h_val, s_val, v_val = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                    weight = (s_val ** 1.3) * (v_val ** 1.2) + 0.01
                    weighted_r += r * weight
                    weighted_g += g * weight
                    weighted_b += b * weight
                    total_weight += weight
            if total_weight > 0:
                r = weighted_r / total_weight
                g = weighted_g / total_weight
                b = weighted_b / total_weight
                mx = max(r, g, b, 1.0)
                scale = min(230.0 / mx, 3.5)
                results[name] = (min(255, int(r * scale)), min(255, int(g * scale)), min(255, int(b * scale)))
            else:
                results[name] = (90, 140, 255)
        results["woofer"] = (
            int(results["lower_left"][0] * 0.5 + results["lower_right"][0] * 0.5),
            int(results["lower_left"][1] * 0.5 + results["lower_right"][1] * 0.5),
            int(results["lower_left"][2] * 0.5 + results["lower_right"][2] * 0.5),
        )
        return results

    def get_current_wing_colors(self) -> dict:
        """Return smoothly interpolated wing colors matching current carousel scroll position."""
        n = len(self.games)
        if n == 0 or not self._wing_colors:
            return self._default_wing_colors()

        sp = self._scroll_pos % n
        idx0 = int(math.floor(sp)) % n
        idx1 = (idx0 + 1) % n
        t = sp - math.floor(sp)

        c0 = self._wing_colors.get(idx0, self._default_wing_colors())
        c1 = self._wing_colors.get(idx1, self._default_wing_colors())

        def lerp(k):
            a = c0[k]
            b = c1[k]
            return (
                int(a[0] + (b[0] - a[0]) * t),
                int(a[1] + (b[1] - a[1]) * t),
                int(a[2] + (b[2] - a[2]) * t),
            )

        return {
            "upper_left": lerp("upper_left"),
            "lower_left": lerp("lower_left"),
            "upper_right": lerp("upper_right"),
            "lower_right": lerp("lower_right"),
            "woofer": lerp("woofer"),
            "controller": lerp("controller"),
        }

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

        # Smoothly glide scroll position toward target index
        if n > 0:
            target = float(self._index)
            diff = target - self._scroll_pos
            # Handle wrapping (e.g., going from last to first)
            if abs(diff) > n / 2:
                if diff > 0:
                    diff -= n
                else:
                    diff += n
            
            # Frame-rate independent exponential smoothing
            decay = 1.0 - math.exp(-self._scroll_speed * dt)
            self._scroll_pos += diff * decay
            if abs(diff) < 0.001:
                self._scroll_pos = target
            self._scroll_pos = self._scroll_pos % n

        # Smoothly animate sub-menu in/out
        target_anim = 1.0 if self._submenu_open else 0.0
        anim_speed = 14.0
        self._submenu_anim += (target_anim - self._submenu_anim) * (1.0 - math.exp(-anim_speed * dt))
        if abs(target_anim - self._submenu_anim) < 0.005:
            self._submenu_anim = target_anim

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
        """Render the full carousel frame with buttery smooth transitions."""
        if not self.games:
            screen.fill(COL_BG)
            label = self._font_title.render("No games loaded", True, COL_TITLE)
            screen.blit(label, label.get_rect(center=(self.W // 2, self.H // 2)))
            return

        # Compute how close we are to settled
        n = len(self.games)
        diff_to_target = self._index - self._scroll_pos
        if abs(diff_to_target) > n / 2:
            diff_to_target = diff_to_target - n if diff_to_target > 0 else diff_to_target + n
        t = 1.0 - min(1.0, abs(diff_to_target) * 2.0)

        # ── Background (continuous cinematic crossfade) ─────────────────
        self._draw_background(screen, t)

        # ── Darkened overlay for contrast (pre-created) ──────────────────
        screen.blit(self._overlay, (0, 0))

        # ── Cover flow ──────────────────────────────────────────────────
        self._draw_covers(screen, t)

        # ── Game title (smooth crossfade & drift matching scroll_pos) ──
        title_y = int(self.H * 0.06)
        max_title_w = self.W - self._title_margin * 2

        sp = self._scroll_pos % n
        idx0 = int(math.floor(sp)) % n
        idx1 = (idx0 + 1) % n
        blend = sp - math.floor(sp)
        smooth_b = blend * blend * (3.0 - 2.0 * blend)

        name0 = self.games[idx0]["name"]
        title0 = self._render_fitted_text(name0, COL_TITLE, max_title_w)

        if idx0 == idx1 or smooth_b < 0.005:
            screen.blit(title0, title0.get_rect(center=(self.W // 2, title_y)))
        elif smooth_b > 0.995:
            name1 = self.games[idx1]["name"]
            title1 = self._render_fitted_text(name1, COL_TITLE, max_title_w)
            screen.blit(title1, title1.get_rect(center=(self.W // 2, title_y)))
        else:
            name1 = self.games[idx1]["name"]
            title1 = self._render_fitted_text(name1, COL_TITLE, max_title_w)

            drift = int(smooth_b * 24)
            title0.set_alpha(int(255 * (1.0 - smooth_b)))
            screen.blit(title0, title0.get_rect(center=(self.W // 2 - drift, title_y)))
            title0.set_alpha(255)

            title1.set_alpha(int(255 * smooth_b))
            screen.blit(title1, title1.get_rect(center=(self.W // 2 + 24 - drift, title_y)))
            title1.set_alpha(255)

        # Script count hint
        n_scripts = len(self.games[self._index].get("launch_scripts", []))
        if n_scripts > 1:
            hint = self._font_hint.render(f"{n_scripts} launch options", True, COL_ACCENT)
            screen.blit(hint, hint.get_rect(center=(self.W // 2, title_y + title0.get_height() + 8)))

        # ── Navigation arrows (floating & pulsing) ─────────────────────
        self._draw_arrows(screen)

        # ── Bottom hints ────────────────────────────────────────────────
        hint_y = int(self.H * 0.95)
        hint_text = "<< >>  Navigate   |   A  Confirm   |   B  Back"
        max_hint_w = self.W - self._title_margin * 2
        hints = self._render_fitted_text(hint_text, COL_HINT, max_hint_w,
                                         max_size=int(self.H * 0.013))
        screen.blit(hints, hints.get_rect(center=(self.W // 2, hint_y)))

        # ── Sub-menu (smooth pop-in & sliding pill) ────────────────────
        if self._submenu_open or self._submenu_anim > 0.001:
            self._draw_submenu(screen)

    # ── Background ──────────────────────────────────────────────────────────

    def _draw_background(self, screen: pygame.Surface, t: float):
        """Draw fullscreen BG with continuous smooth crossfade matching scroll_pos."""
        n = len(self.games)
        if n == 0:
            screen.fill(COL_BG)
            return

        sp = self._scroll_pos % n
        idx0 = int(math.floor(sp)) % n
        idx1 = (idx0 + 1) % n
        blend = sp - math.floor(sp)

        smooth_b = blend * blend * (3.0 - 2.0 * blend)

        bg0 = self._bg_screen.get(idx0)
        bg1 = self._bg_screen.get(idx1)

        screen.fill(COL_BG)
        if bg0 and (smooth_b < 0.999 or not bg1):
            screen.blit(bg0, (0, 0))
        if bg1 and smooth_b > 0.001 and idx0 != idx1:
            bg1.set_alpha(int(255 * smooth_b))
            screen.blit(bg1, (0, 0))
            bg1.set_alpha(255)

    # ── Cover Flow ──────────────────────────────────────────────────────────

    def _precompute_all(self):
        """Precompute scaled+rotated cover surfaces in parallel across CPU cores at boot."""
        step = self._offset_step
        max_off = self._max_offset
        n = len(self.games)

        offsets = []
        q = -max_off
        while q <= max_off + 0.001:
            offsets.append(round(q, 2))
            q += step

        def _worker(gi: int):
            cover = self._covers.get(gi)
            if cover is None:
                return gi, {}
            game_dict = {}
            for q_val in offsets:
                if q_val == 0.0:
                    game_dict[0.0] = (cover.copy(), self._cover_w, self._cover_h)
                    continue
                dist = abs(q_val)
                sf = max(0.50, 1.0 - dist * 0.12)
                cw = int(self._cover_w * sf)
                ch = int(self._cover_h * sf)
                if cw < 10 or ch < 10:
                    continue
                scaled = pygame.transform.smoothscale(cover, (cw, ch))
                rotation_angle = -q_val * 8
                rotation_angle = max(-20, min(20, rotation_angle))
                if abs(rotation_angle) > 0.5:
                    try:
                        alpha_surf = scaled.convert_alpha()
                    except pygame.error:
                        alpha_surf = scaled
                    scaled = pygame.transform.rotozoom(alpha_surf, rotation_angle, 1.0)
                game_dict[q_val] = (scaled, scaled.get_width(), scaled.get_height())
            return gi, game_dict

        workers = min(8, max(1, n))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for gi, game_dict in executor.map(_worker, range(n)):
                for q_val, entry in game_dict.items():
                    self._precomputed[(gi, q_val)] = entry

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
                # Fallback to center cover if out of quantized range
                cover = self._covers.get(gi)
                if cover is not None:
                    entry = (cover, self._cover_w, self._cover_h)
                else:
                    continue

            surf, rw, rh = entry

            # Breathing neon aura around settled center cover
            if dist < 0.15:
                now = pygame.time.get_ticks()
                pulse = 0.5 + 0.5 * math.sin(now * 0.004)
                aura_alpha = int((35 + pulse * 55) * (1.0 - dist / 0.15))
                aura_margin = 8 + int(pulse * 4)

                aura_rect = pygame.Rect(
                    int(x) - rw // 2 - aura_margin,
                    center_y - rh // 2 - aura_margin,
                    rw + aura_margin * 2,
                    rh + aura_margin * 2
                )
                aura_surf = pygame.Surface((aura_rect.width, aura_rect.height), pygame.SRCALPHA)
                pygame.draw.rect(aura_surf, (*COL_ACCENT, aura_alpha),
                                 (0, 0, aura_rect.width, aura_rect.height), width=3, border_radius=14)
                screen.blit(aura_surf, (aura_rect.x, aura_rect.y))

            # Alpha for side covers (smooth continuous fade)
            if dist > 0.1:
                alpha = max(70, int(255 - dist * 55))
                surf.set_alpha(alpha)
                screen.blit(surf, (int(x) - rw // 2, center_y - rh // 2))
                surf.set_alpha(255)
            else:
                screen.blit(surf, (int(x) - rw // 2, center_y - rh // 2))

    # ── Arrows ──────────────────────────────────────────────────────────────

    def _draw_arrows(self, screen: pygame.Surface):
        """Draw floating, pulsing navigation arrows."""
        now = pygame.time.get_ticks()
        float_offset = math.sin(now * 0.004) * (self.W * 0.006)
        pulse = 0.75 + 0.25 * math.sin(now * 0.005)
        arrow_col = (
            min(255, int(COL_ARROW[0] * pulse)),
            min(255, int(COL_ARROW[1] * pulse)),
            min(255, int(COL_ARROW[2] * pulse)),
            int(COL_ARROW[3] * pulse),
        )

        arrow_y = self.H // 2
        arrow_size = int(self.H * 0.025)
        margin = int(self.W * 0.04)

        # Left arrow ◄ with floating motion
        left_x = margin + float_offset
        pts_l = [
            (left_x + arrow_size, arrow_y - arrow_size),
            (left_x, arrow_y),
            (left_x + arrow_size, arrow_y + arrow_size),
        ]
        arrow_surf = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        pygame.draw.polygon(arrow_surf, arrow_col, pts_l)

        # Right arrow ► with floating motion
        right_x = self.W - margin - float_offset
        pts_r = [
            (right_x - arrow_size, arrow_y - arrow_size),
            (right_x, arrow_y),
            (right_x - arrow_size, arrow_y + arrow_size),
        ]
        pygame.draw.polygon(arrow_surf, arrow_col, pts_r)
        screen.blit(arrow_surf, (0, 0))

    # ── Sub-menu overlay ────────────────────────────────────────────────────

    def _draw_submenu(self, screen: pygame.Surface):
        """Draw the launch script selection sub-menu with smooth pop-in and sliding selector."""
        if self._submenu_anim <= 0.001:
            return

        t = _ease_out_cubic(self._submenu_anim)

        # Full-screen dim with smooth fade-in
        dim = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, int(160 * t)))
        screen.blit(dim, (0, 0))

        game_name = self.games[self._index]["name"]

        # Panel dimensions with subtle scale-in animation (0.92 -> 1.0)
        base_w = int(self.W * 0.7)
        line_h = int(self.H * 0.045)
        base_h = line_h * (len(self._submenu_scripts) + 2) + 60

        scale = 0.92 + 0.08 * t
        panel_w = int(base_w * scale)
        panel_h = int(base_h * scale)
        px = (self.W - panel_w) // 2
        py = (self.H - panel_h) // 2

        alpha_byte = int(255 * t)
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        bg_col = (COL_SUBMENU_BG[0], COL_SUBMENU_BG[1], COL_SUBMENU_BG[2], int(COL_SUBMENU_BG[3] * t))
        pygame.draw.rect(panel, bg_col, (0, 0, panel_w, panel_h), border_radius=16)
        border_col = (*COL_ACCENT, int(140 * t))
        pygame.draw.rect(panel, border_col, (0, 0, panel_w, panel_h), width=2, border_radius=16)
        screen.blit(panel, (px, py))

        # Title
        max_sub_w = panel_w - 40
        title = self._render_fitted_text(f"Launch: {game_name}", COL_ACCENT,
                                         max_sub_w, max_size=int(self.H * 0.024))
        title.set_alpha(alpha_byte)
        screen.blit(title, title.get_rect(center=(self.W // 2, py + line_h + 10)))

        # Update smooth selector Y position
        target_sel_y = py + line_h * (self._submenu_index + 2) + 20
        if self._submenu_sel_y == 0.0:
            self._submenu_sel_y = target_sel_y
        else:
            self._submenu_sel_y += (target_sel_y - self._submenu_sel_y) * (1.0 - math.exp(-20.0 * (1.0 / 60.0)))

        # Draw sliding selection pill
        sel_rect = pygame.Surface((panel_w - 32, line_h - 4), pygame.SRCALPHA)
        pygame.draw.rect(sel_rect, (*COL_ACCENT, int(55 * t)),
                         (0, 0, panel_w - 32, line_h - 4), border_radius=8)
        pygame.draw.rect(sel_rect, (*COL_ACCENT, int(160 * t)),
                         (0, 0, panel_w - 32, line_h - 4), width=1, border_radius=8)
        screen.blit(sel_rect, (px + 16, int(self._submenu_sel_y)))

        # Left indicator bar
        bar_surf = pygame.Surface((4, line_h - 12), pygame.SRCALPHA)
        bar_surf.fill((*COL_ACCENT, int(230 * t)))
        screen.blit(bar_surf, (px + 22, int(self._submenu_sel_y) + 4))

        # Script entries
        for i, script in enumerate(self._submenu_scripts):
            y = py + line_h * (i + 2) + 20
            is_sel = (i == self._submenu_index)
            color = COL_SUBMENU_SEL if is_sel else COL_SUBMENU_TXT

            label = self._font_sub_sm.render(script.get("name", "?"), True, color)
            label.set_alpha(alpha_byte)
            screen.blit(label, label.get_rect(midleft=(px + 36, y + line_h // 2 - 2)))

        # Hint at bottom of panel
        hint_text = self._font_hint.render("↑↓ Select  │  A Confirm  │  B Back", True, COL_HINT)
        hint_text.set_alpha(alpha_byte)
        screen.blit(hint_text, hint_text.get_rect(
            center=(self.W // 2, py + panel_h - 20)))
