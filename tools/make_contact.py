from PIL import Image,ImageDraw
from pathlib import Path
fs=sorted(Path('output/iej_render').glob('page-*.png'))
ims=[]
for f in fs:
 im=Image.open(f).convert('RGB'); im.thumbnail((340,480)); ims.append((f.name,im.copy()))
out=Image.new('RGB',(340*4,510*((len(ims)+3)//4)),'white'); d=ImageDraw.Draw(out)
for i,(n,im) in enumerate(ims):
 x=(i%4)*340;y=(i//4)*510;out.paste(im,(x,y+25));d.text((x+8,y+5),n,fill='black')
out.save('output/iej_render/contact.png')
