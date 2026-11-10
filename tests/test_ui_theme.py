"""Rendering contracts without opening Tk or depending on a display device."""
import unittest

from redux.ui_theme import Painter, _rounded_surface


class Canvas:
    def __init__(self):self.items=[]
    def _add(self,kind,args,options):
        self.items.append((kind,args,options));return len(self.items)
    def create_image(self,*args,**options):return self._add("image",args,options)
    def create_line(self,*args,**options):return self._add("line",args,options)
    def create_oval(self,*args,**options):return self._add("oval",args,options)
    def create_polygon(self,*args,**options):return self._add("polygon",args,options)


class ThemeTests(unittest.TestCase):
    def test_rounded_surface_has_transparent_corners_and_covered_edges(self):
        image=_rounded_surface(80,40,12,"#ec407a","#ffffff",1)
        self.assertEqual(image.size,(80,40))
        self.assertEqual(image.getpixel((0,0))[3],0)
        self.assertEqual(image.getpixel((40,20)),(236,64,122,255))
        self.assertTrue(any(image.getchannel("A").histogram()[1:255]),"Edges should retain antialias coverage")

    def test_physical_transform_is_consistent_for_paths_and_surfaces(self):
        canvas=Canvas(); painter=Painter(canvas,scale=2,offset=(3,7),_photo_factory=lambda image:image)
        painter.rounded_rect(10,20,30,40,"#ffffff")
        self.assertEqual(canvas.items[0][1],(23,47))
        self.assertEqual(canvas.items[0][2]["image"].size,(60,80))
        painter.icon("plus",10,20,24,"#ffffff")
        self.assertEqual(canvas.items[1][1],(47,55,47,87))
        self.assertEqual(canvas.items[1][2]["width"],4)

    def test_reusing_surface_retains_photo_and_frame_lifetime(self):
        canvas=Canvas(); made=[]
        def factory(image):made.append(image);return image
        painter=Painter(canvas,_photo_factory=factory)
        painter.rounded_rect(0,0,60,30,"#ffffff")
        painter.rounded_rect(20,10,60,30,"#ffffff")
        self.assertEqual(len(made),1)
        self.assertEqual(len(painter._visible),2)
        painter.begin_frame(scale=.5,offset=(1,2))
        self.assertEqual(painter._visible,[])
        self.assertEqual(len(painter._cache),1)

    def test_invalid_transforms_or_icon_are_rejected_without_drawing(self):
        canvas=Canvas()
        with self.assertRaises(ValueError):Painter(canvas,scale=0)
        painter=Painter(canvas,_photo_factory=lambda image:image)
        with self.assertRaises(ValueError):painter.transform(offset=(float("nan"),0))
        with self.assertRaises(ValueError):painter.icon("unknown",0,0,24)
        self.assertEqual(canvas.items,[])


if __name__=="__main__":unittest.main()
