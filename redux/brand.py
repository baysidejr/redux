"""Shared three-color Fuji geometry for the interface and Windows icons."""

COLORS = ("#ec407a", "#b5d742", "#40bfd0")


def _curve(start, control1, control2, end, steps=20):
    points = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        points.append(tuple(u**3 * start[i] + 3*u*u*t*control1[i]
                            + 3*u*t*t*control2[i] + t**3*end[i] for i in (0, 1)))
    return points


def fuji_shapes():
    """Return colored polygons in a 100 by 100 coordinate space."""
    left = [(28, 4), (39, 4), (39, 47)]
    left += _curve((39, 47), (39, 76), (26, 92), (3, 98))
    left += [(3, 85)]
    left += _curve((3, 85), (20, 80), (28, 67), (28, 47))
    center = [(44, 4), (56, 4), (56, 98), (44, 98)]
    right = [(100-x, y) for x, y in left]
    return tuple(zip(COLORS, (left, center, right)))
