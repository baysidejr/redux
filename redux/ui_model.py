"""Deterministic spatial focus/navigation for the desktop and controller UI."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FocusTarget:
    key: str
    bounds: tuple[float,float,float,float]
    label: str = ""

    @property
    def center(self):
        x1,y1,x2,y2=self.bounds
        return (x1+x2)/2,(y1+y2)/2


def hit_test(targets,x,y):
    for target in reversed(targets):
        left,top,right,bottom=target.bounds
        if left<=x<=right and top<=y<=bottom:
            return target.key
    return None


def next_focus(targets,current,direction):
    """Choose the nearest target in a direction, preferring aligned controls."""
    targets=list(targets)
    if not targets:return None
    origin=next((t for t in targets if t.key==current),None)
    if origin is None:return targets[0].key
    if direction not in {"left","right","up","down"}:return current
    ox,oy=origin.center
    candidates=[]
    for target in targets:
        if target.key==current:continue
        x,y=target.center
        primary=(x-ox) if direction=="right" else (ox-x) if direction=="left" else (y-oy) if direction=="down" else (oy-y)
        perpendicular=abs(y-oy) if direction in {"left","right"} else abs(x-ox)
        if primary<=0.5:continue
        if direction in {"left","right"}:
            aligned=max(origin.bounds[1],target.bounds[1])<min(origin.bounds[3],target.bounds[3])
        else:
            aligned=max(origin.bounds[0],target.bounds[0])<min(origin.bounds[2],target.bounds[2])
        if not aligned and primary<perpendicular*0.5:
            continue
        # Give a clear row/column priority while allowing headers and footers to
        # remain reachable when the current row/column ends.
        score=(0 if aligned else 1,primary+perpendicular*2,math.hypot(primary,perpendicular),target.key)
        candidates.append((score,target.key))
    return min(candidates)[1] if candidates else current


class ScreenHistory:
    def __init__(self): self.stack=[]
    def push(self,state): self.stack.append(dict(state))
    def pop(self): return self.stack.pop() if self.stack else None
    def clear(self): self.stack.clear()


def page_window(count,selected,columns=5,rows=2):
    if columns<1 or rows<1:raise ValueError("Grid dimensions must be positive")
    if count<=0:return 0,0
    selected=max(0,min(count-1,selected))
    start_row=max(0,selected//columns-rows+1)
    start=start_row*columns
    end=min(count,start+columns*rows)
    if end==count:
        start=max(0,((max(0,count-1)//columns)-rows+1)*columns)
    return start,end
