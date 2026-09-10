"""The share card the page advertises as its og:image (site/card.png, 1200x630).

It is a designed asset, not a data product: the mark, the name and the
tagline on ink, drawn from card.html in the Social Atoms brand folder and
committed with the rest of the brand files. The refresh used to repaint it
here with an older layout and tagline, overwriting the committed card every
run; it no longer touches it. `build` stays so ssa.refresh's call site is
unchanged."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "card.png")


def build(data):
    """Keep the committed card. Returns True when it is present."""
    return os.path.exists(OUT)
