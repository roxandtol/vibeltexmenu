import pygame
import os

pygame.font.init()
font_path = "font/NotoSansJP-Regular.otf"
if os.path.exists(font_path):
    font = pygame.font.Font(font_path, 32)
    print(f"Font loaded: {font_path}")
    test_text = "Sound Voltex コナステ - Exceed Gear"
    try:
        surf = font.render(test_text, True, (255, 255, 255))
        print(f"Rendered surface size: {surf.get_size()}")
    except Exception as e:
        print(f"Render failed: {e}")
else:
    print(f"Font not found: {font_path}")
