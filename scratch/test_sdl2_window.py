import os
import pygame
import pygame._sdl2.video as video

os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
pygame.init()
screen = pygame.display.set_mode((400, 400))
pygame.display.set_caption("Main Window")

window2 = video.Window("Secondary Window", size=(400, 400), position=(500, 100))
renderer = video.Renderer(window2)

surf = pygame.Surface((200, 200))
surf.fill((0, 0, 255))
pygame.draw.circle(surf, (255, 255, 0), (100, 100), 50)
tex = video.Texture.from_surface(renderer, surf)

running = True
ticks = 0
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
            
    if ticks > 60:
        running = False
    ticks += 1

    screen.fill((255, 0, 0))
    pygame.display.flip()

    renderer.draw_color = (0, 255, 0, 255)
    renderer.clear()
    tex.draw(dstrect=(100, 100))
    renderer.present()
    pygame.time.delay(16)

pygame.quit()
