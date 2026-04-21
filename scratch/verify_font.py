import os
import pygame

# Initialize pygame font module
pygame.font.init()

def get_font(font_dir, size):
    # Same logic as in carousel.py
    if os.path.isdir(font_dir):
        files = os.listdir(font_dir)
        for f in files:
            if "notosansjp" in f.lower() and f.lower().endswith((".ttf", ".otf")):
                return pygame.font.Font(os.path.join(font_dir, f), size)
        for f in files:
            if f.lower().endswith((".ttf", ".otf")):
                return pygame.font.Font(os.path.join(font_dir, f), size)
    
    fallbacks = ["notosansjp", "yugothic", "meiryo", "msgothic", "arialunicode", "segoeui"]
    for name in fallbacks:
        match = pygame.font.match_font(name)
        if match:
            print(f"Matched system font: {name} -> {match}")
            return pygame.font.Font(match, size)
    return pygame.font.SysFont(None, size)

test_chars = "∇コナステ"
font_dir = "font"
font = get_font(font_dir, 32)

print(f"Testing with font: {font}")
for char in test_chars:
    # Check if font can render character
    # Note: pygame.font doesn't have a direct "has_glyph" but we can check the size
    # and maybe try to render it to see if it's tofu (often difficult to detect programmatically without OCR)
    # But we can at least print the font path used.
    pass

# Most fonts that support Japanese will have U+3000 block.
# We'll just print which font is being used for now.
try:
    # For some fonts, we can get the filename
    pass
except:
    pass

print(f"Font being used: {font}")
