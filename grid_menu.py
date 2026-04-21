import os
import pygame
import pygame._sdl2.video as video
from carousel import _load_custom_font

# Same colors as carousel
COL_BG           = (12, 12, 20, 255)
COL_TITLE        = (255, 255, 255, 255)
COL_ACCENT       = (90, 140, 255, 255)
COL_SUBMENU_BG   = (18, 18, 30, 230)
COL_SUBMENU_SEL  = (90, 140, 255, 255)
COL_SUBMENU_TXT  = (220, 220, 240, 255)

COVER_ASPECT = 9 / 16

class GridMenu:
    def __init__(self, renderer: video.Renderer, games: list[dict], width: int, height: int, font_dir: str = "font", show_inputs: bool = False):
        self.renderer = renderer
        self.games = games
        self.W = width
        self.H = height
        self.show_inputs = show_inputs
        
        # Grid settings
        self.cols = 8
        self.padding = 20
        self.cover_w = (self.W - self.padding * (self.cols + 1)) // self.cols
        self.cover_h = int(self.cover_w / COVER_ASPECT)
        
        # Scrolling
        self.scroll_y = 0.0
        self.target_scroll_y = 0.0
        self.max_scroll = 0.0
        
        self._calculate_max_scroll()
        
        # Sub-menu state
        self.submenu_open = False
        self.submenu_game_idx = -1
        self.submenu_scripts = []
        
        self.launch_request = None
        
        # Input state
        self.is_dragging = False
        self.drag_start_y = 0
        self.drag_start_scroll = 0
        self.drag_velocity = 0.0
        self.last_mouse_y = 0
        self.active_touches = []
        
        # Textures cache
        self.cover_textures: dict[int, video.Texture] = {}
        
        self._font_title = _load_custom_font(font_dir, int(height * 0.028))
        self._font_sub_sm = _load_custom_font(font_dir, int(height * 0.018))
        self._font_dir = font_dir
        
        self._touch_tex = None
        if self.show_inputs:
            self._init_touch_indicator()
        
        self._precache_covers()
        
    def _init_touch_indicator(self):
        size = 100
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, (255, 255, 255, 100), (size//2, size//2), size//2)
        pygame.draw.circle(surf, (255, 255, 255, 255), (size//2, size//2), size//2, 4)
        self._touch_tex = video.Texture.from_surface(self.renderer, surf)
        self._touch_tex.blend_mode = 1 # BLENDMODE_BLEND
        
    def _calculate_max_scroll(self):
        rows = (len(self.games) + self.cols - 1) // self.cols
        total_h = rows * (self.cover_h + self.padding) + self.padding
        self.max_scroll = max(0, total_h - self.H)

    def _precache_covers(self):
        print("[grid_menu] Pre-caching textures for grid...")
        for i, g in enumerate(self.games):
            path = g.get("bg_image_path")
            surf = None
            if path and os.path.isfile(path):
                try:
                    img = pygame.image.load(path).convert()
                    iw, ih = img.get_size()
                    # scale and center crop
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
        sy = y + self.scroll_y
        
        col = x // (self.cover_w + self.padding)
        # offset by padding
        if x < col * (self.cover_w + self.padding) + self.padding:
            return -1
        
        row = sy // (self.cover_h + self.padding)
        if sy < row * (self.cover_h + self.padding) + self.padding:
            return -1
            
        if col >= self.cols:
            return -1
            
        idx = int(row * self.cols + col)
        if 0 <= idx < len(self.games):
            return idx
        return -1

    def handle_mouse_event(self, event):
        """Handle mapped mouse events."""
        # Inverse mapping: Touch X = Main Y, Touch Y = 1080 - Main X
        mx, my = event.pos[0], event.pos[1]
        touch_x = my
        touch_y = 1080 - mx
        
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
            self.drag_start_y = touch_y
            self.drag_start_scroll = self.scroll_y
            self.drag_velocity = 0
            self.last_mouse_y = touch_y
            
        elif event.type == pygame.MOUSEMOTION:
            if self.is_dragging:
                dy = touch_y - self.last_mouse_y
                self.drag_velocity = -dy
                self.scroll_y += self.drag_velocity
                # clamp
                if self.scroll_y < 0: self.scroll_y = 0
                if self.scroll_y > self.max_scroll: self.scroll_y = self.max_scroll
                self.target_scroll_y = self.scroll_y
                self.last_mouse_y = touch_y
                
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.is_dragging = False
            # If the mouse barely moved, count it as a click
            if abs(touch_y - self.drag_start_y) < 10:
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

    def update(self, dt):
        if not self.is_dragging:
            # apply inertia
            if abs(self.drag_velocity) > 0.1:
                self.scroll_y += self.drag_velocity
                self.drag_velocity *= 0.9  # friction
                if self.scroll_y < 0:
                    self.scroll_y = 0
                    self.drag_velocity = 0
                elif self.scroll_y > self.max_scroll:
                    self.scroll_y = self.max_scroll
                    self.drag_velocity = 0
            self.target_scroll_y = self.scroll_y

    def draw(self):
        self.renderer.draw_color = COL_BG
        self.renderer.clear()
        
        # Draw grid
        start_row = int(self.scroll_y // (self.cover_h + self.padding))
        end_row = int((self.scroll_y + self.H) // (self.cover_h + self.padding)) + 1
        
        for row in range(start_row, end_row + 1):
            for col in range(self.cols):
                idx = row * self.cols + col
                if idx >= len(self.games):
                    break
                    
                x = self.padding + col * (self.cover_w + self.padding)
                y = self.padding + row * (self.cover_h + self.padding) - int(self.scroll_y)
                
                tex = self.cover_textures.get(idx)
                if tex:
                    tex.draw(dstrect=(x, y, self.cover_w, self.cover_h))
                    
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
