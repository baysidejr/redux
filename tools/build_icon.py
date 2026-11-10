"""Render Redux's three-color Fuji mark to Windows icon sizes."""
from pathlib import Path
import sys
from PIL import Image,ImageDraw

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from redux.brand import fuji_shapes
image=Image.new("RGBA",(1024,1024))
draw=ImageDraw.Draw(image)
draw.rounded_rectangle((0,0,1023,1023),radius=192,fill="#19151e")
for color,points in fuji_shapes():
    draw.polygon([(152+x*7.2,145+y*7.2) for x,y in points],fill=color)
svg=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">',
     '<rect width="256" height="256" rx="48" fill="#19151e"/>']
for color,points in fuji_shapes():
    coordinates=' '.join(f'{38+x*1.8:.3f},{36.25+y*1.8:.3f}' for x,y in points)
    svg.append(f'<polygon points="{coordinates}" fill="{color}"/>')
svg.append('</svg>')
(root/'redux/assets/redux.svg').write_text('\n'.join(svg)+'\n',encoding='utf-8')
image=image.resize((256,256),Image.Resampling.LANCZOS)
image.save(root/"redux/assets/redux.png")
image.save(root/"redux/assets/redux.ico",sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
