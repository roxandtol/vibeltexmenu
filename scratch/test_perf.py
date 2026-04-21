import os
import time
import pygame
import pygame._sdl2.video as video

os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
pygame.init()
screen = pygame.display.set_mode((1080, 1920))
window2 = video.Window("Secondary Window", size=(1920, 1080), position=(1080, 0))
renderer = video.Renderer(window2)

surf = pygame.Surface((1920, 1080))
surf.fill((0, 0, 255))
pygame.draw.circle(surf, (255, 255, 0), (100, 100), 50)

running = True
frames = 0
start_time = time.time()
while running and frames < 120:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    # Main screen logic
    screen.fill((255, 0, 0))
    pygame.display.flip()

    # Secondary screen logic
    renderer.clear()
    tex = video.Texture.from_surface(renderer, surf)
    tex.draw()
    renderer.present()
    
    # destroying the texture is automatic? 
    # tex is garbage collected

    frames += 1

elapsed = time.time() - start_time
print(f"Frames: {frames}, FPS: {frames/elapsed:.2f}")
pygame.quit()
