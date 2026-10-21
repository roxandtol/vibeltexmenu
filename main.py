"""
main.py — VibeltexMenu entry point.

Fullscreen (1080×1920) Wii-style carousel game launcher with:
  • Background images per game (forced 1080×1920)
  • BGM via ASIO (sounddevice) with crossfade
  • Xbox 360 controller support with configurable keybinds
  • Sub-menu for selecting launch scripts
"""

import os
import sys
import subprocess

# Enable ASIO before any audio imports
os.environ["SD_ENABLE_ASIO"] = "1"

import pygame
import pygame._sdl2.video as video

from config import load_settings, load_games, base_dir
from audio_manager import AudioManager
from input_manager import InputManager
from carousel import Carousel, _load_custom_font
from grid_menu import GridMenu


def main():
    # ── Load configuration ──────────────────────────────────────────────
    settings = load_settings()
    games = load_games()

    disp = settings["display"]
    WIDTH = disp["width"]
    HEIGHT = disp["height"]
    LANDSCAPE = disp["landscape"]
    FPS = disp["fps"]
    FULLSCREEN = disp["fullscreen"]
    MONITOR = disp["monitor"]
    SEC_MONITOR = disp.get("secondary_monitor", 1)
    SHOW_INPUTS = settings.get("input", {}).get("show_inputs", False)

    # ── Pygame init ─────────────────────────────────────────────────────
    os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
    pygame.init()

    pygame.display.set_caption("VibeltexMenu")

    if LANDSCAPE:
        resolution = (WIDTH, HEIGHT)
    else:
        resolution = (HEIGHT, WIDTH)

    flags = 0
    if FULLSCREEN:
        flags |= pygame.FULLSCREEN | pygame.NOFRAME
        try:
            screen = pygame.display.set_mode(resolution, flags, display=MONITOR)
        except TypeError:
            screen = pygame.display.set_mode(resolution, flags)
    else:
        screen = pygame.display.set_mode(resolution, flags)

    clock = pygame.time.Clock()
    pygame.mouse.set_visible(True)
    
    # ── Secondary display (Grid Menu) ───────────────────────────────────
    # Landscape 1920x1080
    touchscreen = settings["touchscreen"]
    sec_w, sec_h = touchscreen["width"], touchscreen["height"]
    # Use SDL macro trick to center on specific monitor
    pos = video.WINDOWPOS_CENTERED | SEC_MONITOR
    window2 = video.Window("Secondary Window", size=(sec_w, sec_h), position=(pos, pos))

    # Select fastest modern hardware renderer (bypasses Direct3D 9 timeout, saves ~1.1s)
    def _create_fast_renderer(win: video.Window) -> video.Renderer:
        target_drivers = ["direct3d11", "opengl", "direct3d12"]
        available = {d.name: i for i, d in enumerate(video.get_drivers())}
        for preferred in target_drivers:
            if preferred in available:
                try:
                    return video.Renderer(win, index=available[preferred])
                except Exception:
                    pass
        return video.Renderer(win)

    renderer = _create_fast_renderer(window2)

    # ── Subsystems ──────────────────────────────────────────────────────
    audio = AudioManager(settings)
    inp = InputManager(settings)

    font_dir = os.path.join(base_dir(), "font")
    carousel = Carousel(games, WIDTH, HEIGHT, font_dir=font_dir)
    grid_menu = GridMenu(renderer, games, sec_w, sec_h, font_dir=font_dir, show_inputs=SHOW_INPUTS,
                         base_covers=carousel.get_cover_surfaces())
    
    input_font = _load_custom_font(font_dir, int(HEIGHT * 0.02))
    input_displays = []

    # Start initial BGM
    if games:
        _play_game_bgm(audio, games[carousel.current_index])

    last_bgm_index = carousel.current_index

    # ── Main loop ───────────────────────────────────────────────────────
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        events = pygame.event.get()
        new_inputs = []
        for event in events:
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
                grid_menu.handle_mouse_event(event)
            if event.type == pygame.MOUSEBUTTONDOWN:
                new_inputs.append(f"Touch {event.pos}")

        actions = inp.poll(events)
        
        if SHOW_INPUTS:
            for act in actions:
                if act != "quit":
                    new_inputs.append(act.replace('_', ' ').title())
            now = pygame.time.get_ticks()
            for text in new_inputs:
                input_displays.append({"text": text, "expires": now + 1500})
            
            # Remove expired
            input_displays = [d for d in input_displays if d["expires"] > now]

        if "quit" in actions:
            running = False
            continue

        if "back" in actions and not carousel.submenu_open:
            running = False
            continue

        carousel.update(dt, actions)
        grid_menu.sync_selected(carousel.current_index)
        grid_menu.update(dt)

        # ── Update SDVX Wing Lights from Background ─────────────────────
        inp.update_wing_lights(carousel.get_current_wing_colors())

        # ── BGM change on selection change ──────────────────────────────
        if carousel.current_index != last_bgm_index:
            game = games[carousel.current_index]
            bgm_path = game.get("bgm_path", "")
            if bgm_path and os.path.isfile(bgm_path):
                audio.crossfade_to(bgm_path)
            else:
                audio.stop()
            last_bgm_index = carousel.current_index

        # ── Launch request ──────────────────────────────────────────────
        launch_req = carousel.launch_request or grid_menu.launch_request
        if launch_req is not None:
            script = launch_req["script"]
            script_path = script.get("path", "")
            script_name = script.get("name", "?")

            # Play start sound
            game = games[carousel.current_index]
            start_snd = game.get("start_sound_path", "")
            if start_snd and os.path.isfile(start_snd):
                audio.play_sfx(start_snd)

            if script_path and os.path.isfile(script_path):
                script_dir = os.path.dirname(os.path.abspath(script_path))
                print(f"[main] Launching: {script_name} → {script_path} (cwd={script_dir})")
                try:
                    subprocess.Popen(
                        f'cmd /c "{script_path}"',
                        cwd=script_dir,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                except Exception as e:
                    print(f"[main] Launch failed: {e}")
                # Kill the launcher immediately
                inp.shutdown()
                audio.shutdown()
                pygame.quit()
                sys.exit(0)
            else:
                print(f"[main] Script path missing or not found: '{script_name}' → '{script_path}'")

        # ── Render ──────────────────────────────────────────────────────
        carousel.draw(screen)
        
        if SHOW_INPUTS:
            iy = HEIGHT - int(HEIGHT * 0.1)
            for d in reversed(input_displays):
                surf = input_font.render(d["text"], True, (0, 255, 120))
                screen.blit(surf, (20, iy))
                iy -= surf.get_height() + 5

        pygame.display.flip()
        
        grid_menu.draw()

    # ── Cleanup ─────────────────────────────────────────────────────────
    inp.shutdown()
    audio.shutdown()
    pygame.quit()
    print("[main] Goodbye.")


def _play_game_bgm(audio: AudioManager, game: dict):
    """Start playing a game's BGM if the file exists."""
    bgm = game.get("bgm_path", "")
    if bgm and os.path.isfile(bgm):
        audio.play_bgm(bgm)


if __name__ == "__main__":
    main()
