import os
import math
import pygame
import pygame._sdl2.video as video
from carousel import _load_custom_font
from config import load_settings

# Same colors as carousel
COL_BG           = (12, 12, 20, 255)
COL_TITLE        = (255, 255, 255, 255)
COL_ACCENT       = (90, 140, 255, 255)
COL_SUBMENU_BG   = (18, 18, 30, 230)
COL_SUBMENU_SEL  = (90, 140, 255, 255)
COL_SUBMENU_TXT  = (220, 220, 240, 255)

COVER_ASPECT = 9 / 16

class GridMenu:
    def __init__(self, renderer: video.Renderer, games: list[dict], width: int, height: int,
                 font_dir: str = "font", show_inputs: bool = False,
                 base_covers: dict[int, pygame.Surface | None] | None = None):
        settings = load_settings()

        grid_menu = settings["grid_menu"]
        display = settings["display"]
        touchscreen = settings["touchscreen"]

        global COVER_ASPECT
        COVER_ASPECT = grid_menu["cover_aspect_ratio_width"] / grid_menu["cover_aspect_ratio_height"]
        
        global COL_TITLE
        COL_TITLE = tuple(grid_menu["title_color"])

        global COL_BG
        COL_BG = tuple(grid_menu["bg_color"])

        global COL_ACCENT
        COL_ACCENT = tuple(grid_menu["accent_color"])

        global COL_SUBMENU_BG
        COL_SUBMENU_BG = tuple(grid_menu["submenu_bg_color"])

        global COL_SUBMENU_SEL
        COL_SUBMENU_SEL = tuple(grid_menu["submenu_selected_color"])

        global COL_SUBMENU_TXT
        COL_SUBMENU_TXT = tuple(grid_menu["submenu_text_color"])

        # Window settings
        self._landscape = display["landscape"]
        self._touchscreen_height = touchscreen["height"]
        
        self.renderer = renderer
        self.games = games
        self.W = width
        self.H = height
        self.show_inputs = grid_menu.get("show_inputs", 1)
        self.selected_idx = 0
        
        # Grid settings (2 horizontal rows)
        self.rows = grid_menu.get("rows", 2)
        self.padding = 40
        self.cover_h = (self.H - self.padding * (self.rows + 1)) // self.rows
        self.cover_w = int(self.cover_h * COVER_ASPECT)
        
        # Scrolling (Horizontal)
        self.scroll_x = 0.0
        self.target_scroll_x = 0.0
        self.max_scroll = 0.0
        
        self._calculate_max_scroll()
        
        # Sub-menu state
        self.submenu_open = False
        self.submenu_game_idx = -1
        self.submenu_scripts = []
        
        self.launch_request = None
        
        # Input state
        self.is_dragging = False
        self.drag_start_x = 0
        self.drag_start_scroll = 0
        self.drag_velocity = 0.0
        self.last_mouse_x = 0
        self.active_touches = []
        
        # Textures cache
        self.cover_textures: dict[int, video.Texture] = {}
        
        self._font_title = _load_custom_font(font_dir, int(height * 0.028))
        self._font_sub_sm = _load_custom_font(font_dir, int(height * 0.018))
        self._font_dir = font_dir
        
        self._touch_tex = None
        if self.show_inputs:
            self._init_touch_indicator()
        
        self._precache_covers(base_covers)
        
    def _init_touch_indicator(self):
        size = 100
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, (255, 255, 255, 100), (size//2, size//2), size//2)
        pygame.draw.circle(surf, (255, 255, 255, 255), (size//2, size//2), size//2, 4)
        self._touch_tex = video.Texture.from_surface(self.renderer, surf)
        self._touch_tex.blend_mode = 1 # BLENDMODE_BLEND
        
    def _calculate_max_scroll(self):
        total_cols = (len(self.games) + self.rows - 1) // self.rows
        total_w = total_cols * (self.cover_w + self.padding) + self.padding
        self.max_scroll = max(0, total_w - self.W)

    def _precache_covers(self, base_covers: dict[int, pygame.Surface | None] | None = None):
        print("[grid_menu] Pre-caching textures for grid...")
        for i, g in enumerate(self.games):
            surf = None
            if base_covers and base_covers.get(i) is not None:
                # Fast downscale from carousel's 9:16 cover (~1.5ms per game)
                surf = pygame.transform.smoothscale(base_covers[i], (self.cover_w, self.cover_h))
            else:
                path = g.get("bg_image_path")
                if path and os.path.isfile(path):
                    try:
                        img = pygame.image.load(path).convert()
                        iw, ih = img.get_size()
                        import math
                        scale = max(self.cover_w / iw, self.cover_h / ih)
                        new_w, new_h = max(self.cover_w, math.ceil(iw * scale)), max(self.cover_h, math.ceil(ih * scale))
                        img = pygame.transform.smoothscale(img, (new_w, new_h))
                        cx = max(0, (new_w - self.cover_w) // 2)
                        cy = max(0, (new_h - self.cover_h) // 2)
                        surf = img.subsurface((cx, cy, self.cover_w, self.cover_h)).copy()
                    except Exception:
                        pass
            
            if not surf:
                surf = pygame.Surface((self.cover_w, self.cover_h))
                surf.fill((30, 30, 50))
                pygame.draw.rect(surf, (60, 90, 180), surf.get_rect(), 2)
            
            tex = video.Texture.from_surface(self.renderer, surf)
            self.cover_textures[i] = tex
            
    def _get_game_at_pos(self, x, y):
        # account for scroll
        sx = x + self.scroll_x
        
        col = sx // (self.cover_w + self.padding)
        # offset by padding
        if sx < col * (self.cover_w + self.padding) + self.padding:
            return -1
        
        row = y // (self.cover_h + self.padding)
        if y < row * (self.cover_h + self.padding) + self.padding:
            return -1
            
        if row >= self.rows:
            return -1
            
        idx = int(col * self.rows + row)
        if 0 <= idx < len(self.games):
            return idx
        return -1

    def handle_mouse_event(self, event):
        """Handle mapped mouse events."""
        # Inverse mapping: Touch X = Main Y, Touch Y = 1080 - Main X
        mx, my = event.pos[0], event.pos[1]
        touch_x = mx
        touch_y = my if self._landscape else self._touchscreen_height - mx
        
        if self.submenu_open:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # Basic check for clicking submenu items
                self._handle_submenu_click(touch_x, touch_y)
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.show_inputs:
                self.active_touches.append({
                    "x": touch_x,
                    "y": touch_y,
                    "time": pygame.time.get_ticks()
                })
            
            self.is_dragging = True
            self.drag_start_x = touch_x
            self.drag_start_scroll = self.scroll_x
            self.drag_velocity = 0
            self.last_mouse_x = touch_x
            
        elif event.type == pygame.MOUSEMOTION:
            if self.is_dragging:
                dx = touch_x - self.last_mouse_x
                self.drag_velocity = -dx
                self.scroll_x += self.drag_velocity
                # clamp
                if self.scroll_x < 0: self.scroll_x = 0
                if self.scroll_x > self.max_scroll: self.scroll_x = self.max_scroll
                self.target_scroll_x = self.scroll_x
                self.last_mouse_x = touch_x
                
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.is_dragging = False
            # If the mouse barely moved, count it as a click
            if abs(touch_x - self.drag_start_x) < 10:
                idx = self._get_game_at_pos(touch_x, touch_y)
                if idx != -1:
                    self._on_game_clicked(idx)
                    
    def _handle_submenu_click(self, x, y):
        # We need to calculate where the submenu elements are drawn to see what was clicked
        panel_w = int(self.W * 0.6)
        line_h = int(self.H * 0.045)
        panel_h = line_h * (len(self.submenu_scripts) + 2) + 60
        px = (self.W - panel_w) // 2
        py = (self.H - panel_h) // 2
        
        if not (px <= x <= px + panel_w and py <= y <= py + panel_h):
            # clicked outside, close submenu
            self.submenu_open = False
            return
            
        for i, script in enumerate(self.submenu_scripts):
            iy = py + line_h * (i + 2) + 20
            # Height is roughly line_h
            if iy <= y <= iy + line_h:
                self.launch_request = {"script": script}
                self.submenu_open = False
                return

    def _on_game_clicked(self, idx):
        game = self.games[idx]
        scripts = game.get("launch_scripts", [])
        if len(scripts) == 0:
            pass
        elif len(scripts) == 1:
            self.launch_request = {"script": scripts[0]}
        else:
            self.submenu_game_idx = idx
            self.submenu_scripts = scripts
            self.submenu_open = True

    def sync_selected(self, idx: int):
        """Keep grid selection and viewport smoothly synchronized with Carousel."""
        self.selected_idx = idx
        if 0 <= idx < len(self.games) and not self.is_dragging:
            col = idx // self.rows
            card_left = self.padding + col * (self.cover_w + self.padding)
            card_right = card_left + self.cover_w
            margin = self.cover_w // 2
            if card_left < self.scroll_x + margin:
                self.target_scroll_x = max(0, card_left - margin)
            elif card_right > self.scroll_x + self.W - margin:
                self.target_scroll_x = min(self.max_scroll, card_right + margin - self.W)

    def update(self, dt: float):
        if not self.is_dragging:
            # Apply smooth velocity decay (frame-rate independent)
            if abs(self.drag_velocity) > 0.05:
                self.scroll_x += self.drag_velocity * (dt * 60.0)
                decay = math.exp(-5.0 * dt)
                self.drag_velocity *= decay
            else:
                self.drag_velocity = 0.0

            # Rubber-band spring bounce when overscrolling edges
            if self.scroll_x < 0:
                spring = 1.0 - math.exp(-12.0 * dt)
                self.scroll_x += (0 - self.scroll_x) * spring
                self.drag_velocity *= 0.5
            elif self.scroll_x > self.max_scroll:
                spring = 1.0 - math.exp(-12.0 * dt)
                self.scroll_x += (self.max_scroll - self.scroll_x) * spring
                self.drag_velocity *= 0.5
            elif abs(self.target_scroll_x - self.scroll_x) > 0.5:
                # Smooth glide toward target scroll (e.g. from carousel navigation)
                glide = 1.0 - math.exp(-8.0 * dt)
                self.scroll_x += (self.target_scroll_x - self.scroll_x) * glide

    def draw(self):
        self.renderer.draw_color = COL_BG
        self.renderer.clear()
        
        # Draw grid
        start_col = max(0, int(self.scroll_x // (self.cover_w + self.padding)))
        end_col = int((self.scroll_x + self.W) // (self.cover_w + self.padding)) + 1
        
        for col in range(start_col, end_col + 1):
            for row in range(self.rows):
                idx = col * self.rows + row
                if idx >= len(self.games):
                    break
                    
                x = self.padding + col * (self.cover_w + self.padding) - int(self.scroll_x)
                y = self.padding + row * (self.cover_h + self.padding)
                
                tex = self.cover_textures.get(idx)
                if tex:
                    tex.draw(dstrect=(x, y, self.cover_w, self.cover_h))

                # Highlight the game currently selected on primary display
                if idx == self.selected_idx:
                    now = pygame.time.get_ticks()
                    pulse = 0.5 + 0.5 * math.sin(now * 0.005)
                    self.renderer.draw_color = (90, 140, 255, int(150 + pulse * 105))
                    for b in range(3):
                        self.renderer.draw_rect((x - b - 1, y - b - 1, self.cover_w + (b + 1) * 2, self.cover_h + (b + 1) * 2))
                    
        # Draw Submenu
        if self.submenu_open:
            self._draw_submenu()
            
        # Draw touches
        if self.show_inputs and self.active_touches:
            now = pygame.time.get_ticks()
            alive_touches = []
            for t in self.active_touches:
                life = (now - t["time"]) / 400.0  # 400ms duration
                if life < 1.0:
                    alive_touches.append(t)
                    size = int(60 + life * 60)
                    alpha = int(255 * (1.0 - life))
                    self._touch_tex.alpha = alpha
                    self._touch_tex.draw(dstrect=(int(t["x"] - size/2), int(t["y"] - size/2), size, size))
            self.active_touches = alive_touches
            
        self.renderer.present()

    def _draw_submenu(self):
        # Dim background
        dim_surf = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        dim_surf.fill((0, 0, 0, 160))
        
        game_name = self.games[self.submenu_game_idx]["name"]
        
        panel_w = int(self.W * 0.6)
        line_h = int(self.H * 0.045)
        panel_h = line_h * (len(self.submenu_scripts) + 2) + 60
        px = (self.W - panel_w) // 2
        py = (self.H - panel_h) // 2
        
        pygame.draw.rect(dim_surf, COL_SUBMENU_BG, (px, py, panel_w, panel_h), border_radius=16)
        pygame.draw.rect(dim_surf, COL_ACCENT, (px, py, panel_w, panel_h), width=2, border_radius=16)
        
        title = self._font_title.render(f"Launch: {game_name}", True, COL_ACCENT)
        dim_surf.blit(title, title.get_rect(center=(self.W // 2, py + line_h + 10)))
        
        for i, script in enumerate(self.submenu_scripts):
            y = py + line_h * (i + 2) + 20
            
            # Simple hover effect could be added here, but it's touch, so no hover
            label = self._font_sub_sm.render(script.get("name", "?"), True, COL_SUBMENU_TXT)
            dim_surf.blit(label, label.get_rect(midleft=(px + 36, y + line_h // 2 - 2)))
            
        # Draw the dim_surf as texture
        tex = video.Texture.from_surface(self.renderer, dim_surf)
        tex.draw()
        # Texture will be GC'd
