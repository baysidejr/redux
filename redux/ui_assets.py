"""Read optional local artwork; the UI also works with generated native graphics.

No vendor pack, executable, asset downloader or network request is required.
"""
from collections import OrderedDict
from pathlib import Path
import sys
import json

from PIL import Image,ImageOps,ImageTk,ImageDraw,ImageFilter

SYSTEM_ART={
 "psx":("psx","19_Collection_PS1U.png","#82616f"),
 "nes":("nes","7_Collection_NES.png","#aa514b"),
 "snes":("snes","9_Collection_SNES.png","#796b99"),
 "n64":("n64","29_Collection_n64.png","#568d60"),
 "genesis":("genesis","0_Collection_Genesis.png","#4a6d93"),
 "32x":("32x","22_Collection_32X.png","#7b6251"),
 "sega_cd":("scd","3_Collection_SCD.png","#475f95"),
 "saturn":("saturn","25_Collection_Saturn_U.png","#6b7583"),
 "pce":("tg16","12_Collection_TG16.png","#986d40"),
 "pce_cd":("tgcd","13_Collection_TGCD.png","#537f88"),
 "supergrafx":("sgx","16_Collection_SGX.png","#607f77"),
 "neo_geo_cd":("ngcd","18_Collection_NGCD.png","#6a6a50"),
 "atari2600":("a2600","Collection_A2600.png","#8e6847"),
 "atari7800":("a7800","Collection_A7800.png","#715c63"),
}


def project_root():
    if getattr(sys,"frozen",False):
        exe=Path(sys.executable).resolve()
        # A separately registered Xbox home layout shares the portable library,
        # firmware and cores with the original application folder.
        try:
            location=json.loads((exe.parent/"redux-launch.json").read_text(encoding="utf-8"))
            portable=Path(location["portable_root"])
            if portable.is_absolute() and portable.is_dir():return portable.resolve()
        except (OSError,ValueError,TypeError,KeyError):pass
        for parent in exe.parents:
            if (parent/"tools"/"emulators").is_dir():return parent
        return exe.parent
    return Path(__file__).resolve().parents[1]


class Artwork:
    def __init__(self,master,reference_root=None):
        self.master=master
        automatic=project_root()/"analysis/formats/decoded/img/retrobloxui"
        self.reference_root=Path(reference_root) if reference_root else automatic
        self.cache=OrderedDict()

    def resource(self,name):
        candidate=self.reference_root/name
        return candidate if candidate.is_file() else None

    def scrim(self,width,height,opacity=180):
        key=("scrim",int(width),int(height),opacity)
        if key not in self.cache:
            surface=Image.new("RGBA",(int(width),int(height)),(0,0,0,0))
            alpha=ImageOps.invert(Image.linear_gradient("L")).resize(surface.size).point(lambda value:round(value*opacity/255))
            surface.putalpha(alpha)
            self.cache[key]=ImageTk.PhotoImage(surface,master=self.master)
        return self.cache[key]

    def backdrop(self,width,height,lane=False):
        """A layered console backdrop, with an original offline fallback."""
        key=("backdrop",int(width),int(height),lane,str(self.reference_root))
        if key in self.cache:return self.cache[key]
        width,height=int(width),int(height)
        source=self.resource("nav-background.png")
        if source:
            with Image.open(source) as opened:surface=ImageOps.fit(opened.convert("RGB"),(width,height),method=Image.Resampling.LANCZOS)
        else:
            surface=Image.new("RGB",(width,height),(8,9,10));draw=ImageDraw.Draw(surface)
            for y in range(0,height,7):
                for x in range(0,width,7):
                    if (x/width+y/height)%1.1<.42:draw.ellipse((x,y,x+1,y+1),fill=(20,21,23))
            draw.polygon([(width*.08,0),(width*.42,0),(width*.85,height),(width*.46,height)],fill=(12,13,15))
        if lane:
            lane_width=round(width*0.275)
            panel=Image.new("RGBA",(lane_width,height));pixels=ImageDraw.Draw(panel)
            for x in range(lane_width):
                value=round(52-18*x/max(1,lane_width-1))
                pixels.line((x,0,x,height),fill=(value,value,value,240))
            surface.paste(panel,(0,0),panel)
            beam=Image.new("RGBA",surface.size);d=ImageDraw.Draw(beam)
            d.polygon([(0,0),(width*.33,0),(width*.19,height),(0,height)],fill=(255,255,255,7))
            surface=Image.alpha_composite(surface.convert("RGBA"),beam)
        photo=ImageTk.PhotoImage(surface,master=self.master);self.cache[key]=photo
        return photo

    def path(self,system,kind="hero",custom=None):
        if custom and Path(custom).is_file():return Path(custom)
        if system=="pc":
            # Original PC category art ships with the frontend, independent of
            # optional reference assets and the user's game artwork.
            bundle=getattr(sys,"_MEIPASS",None)
            roots=([Path(bundle)/"assets",Path(bundle)/"redux"/"assets"] if bundle else [])
            roots.append(Path(__file__).resolve().parent/"assets")
            for root in roots:
                candidate=root/"platforms"/"pc-games.png"
                if candidate.is_file():return candidate
        if not self.reference_root.is_dir():return None
        stem,tile,_=SYSTEM_ART.get(system,(system,"","#536079"))
        candidate=self.reference_root/(f"home/system_hero/{stem}_hero_color.png" if kind=="hero" else f"home/collectiontiles/{tile}")
        if candidate.is_file():return candidate
        # Some references use different system naming. Never guess another
        # system's artwork if an exact filename is unavailable.
        return None

    def image(self,path,width,height,shade=False,*,mode="cover",radius=0):
        if path is None or width<1 or height<1:return None
        path=Path(path)
        try:
            stat=path.stat()
            key=(str(path),stat.st_mtime_ns,int(width),int(height),shade,mode,radius)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if stat.st_size>40*1024*1024:return None
            with Image.open(path) as source:
                if source.width*source.height>32_000_000:return None
                if mode=="contain":
                    image=Image.new("RGBA",(int(width),int(height)))
                    contained=ImageOps.contain(source.convert("RGBA"),image.size,method=Image.Resampling.LANCZOS)
                    image.paste(contained,((image.width-contained.width)//2,(image.height-contained.height)//2),contained)
                else:image=ImageOps.fit(source.convert("RGBA"),(int(width),int(height)),method=Image.Resampling.LANCZOS)
            if shade:
                overlay=Image.new("RGBA",image.size,(5,6,8,255))
                alpha=Image.linear_gradient("L").rotate(90,expand=True).resize(image.size)
                # Text is legible at the left; the original image remains clear
                # at the right. This transform is performed only in UI memory.
                alpha=alpha.point(lambda p:int(28+p*0.78))
                image=Image.composite(overlay,image,alpha)
            if radius:
                mask=Image.new("L",image.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,image.width-1,image.height-1),radius=radius,fill=255)
                image.putalpha(mask)
            photo=ImageTk.PhotoImage(image,master=self.master)
            self.cache[key]=photo
            while len(self.cache)>80:self.cache.popitem(last=False)
            return photo
        except (OSError,ValueError,Image.DecompressionBombError):
            return None

    def clear(self):self.cache.clear()
