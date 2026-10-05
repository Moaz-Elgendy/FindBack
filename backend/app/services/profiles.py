"""Content-specific extraction profiles (Phase 10).

Exactly five profiles exist, and nothing else may be added:

    list      -> structured_data.items[]
    recipe    -> ingredients, steps, time, temperature
    product   -> product_name, price, specifications, pros, cons, use_case
    tutorial  -> goal, prerequisites, steps, tools, commands
    general   -> everything else

A profile decides two things: which keys go inside the brief's
`structured_data`, and what the extraction prompt asks for. The brief's nine
top-level fields (Phase 9) are identical for every profile, so a brief is still
valid against the Phase 9 schema whichever profile produced it.

The profile is picked locally from the URL and text before the model is called.
That keeps the cost at one model call and makes the choice auditable: a brief
always records the profile it came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re


@dataclass(frozen=True)
class Profile:
    name: str
    # Keys the profile owns inside structured_data. `content_type` is always
    # written alongside them.
    keys: tuple[str, ...]
    instructions: str
    # Value each key defaults to when the model omits it or answers null.
    defaults: dict[str, Any] = field(default_factory=dict)

    def empty(self) -> dict[str, Any]:
        out = {"content_type": self.name}
        out.update({key: self.defaults.get(key, []) for key in self.keys})
        return out


LIST = Profile(
    name="list",
    keys=("items",),
    instructions=(
        "This content is a list of things. Put every entry in "
        "structured_data.items as one string per entry, in the order it appears. "
        "Do not summarise the list away: a list of five is five items. If an "
        "entry has a timing marker, append it as \" - 01:12\"."
    ),
)

RECIPE = Profile(
    name="recipe",
    keys=("ingredients", "steps", "time", "temperature"),
    instructions=(
        "This content is a recipe. structured_data.ingredients is one string "
        "per ingredient with its quantity. structured_data.steps is one string "
        "per step, in order. time is the total time as written "
        '(e.g. "40 minutes"), and temperature is the oven or cooking '
        'temperature as written (e.g. "180C"). Leave a value empty if the '
        "content does not state it."
    ),
    defaults={"time": "", "temperature": ""},
)

PRODUCT = Profile(
    name="product",
    keys=("product_name", "price", "specifications", "pros", "cons",
          "use_case"),
    instructions=(
        "This content is about a product. structured_data.product_name is the "
        "exact name, price is the price as written (e.g. \"$349\" or "
        '"from $299"). specifications is one string per spec '
        "(size, weight, capacity, model number...). pros and cons are one "
        "string each, drawn only from what the content says. use_case is who "
        "the product is for or what it is best used for. Do not invent a "
        "pros or a con."
    ),
    defaults={"product_name": "", "price": "", "use_case": ""},
)

TUTORIAL = Profile(
    name="tutorial",
    keys=("goal", "prerequisites", "steps", "tools", "commands"),
    instructions=(
        "This content is a tutorial. structured_data.goal is what the reader "
        "achieves at the end. prerequisites is one string per thing needed "
        "before starting. steps is one string per step, in order. tools is one "
        "string per tool used. commands is one string per literal command, "
        "copyable and exactly as written. Do not paraphrase a command."
    ),
    defaults={"goal": ""},
)

GENERAL = Profile(
    name="general",
    keys=(),
    instructions=(
        "This content is general. Leave structured_data empty apart from the "
        "content_type key; put anything worth keeping in the top-level fields."
    ),
)

# The five profiles, and nothing else. Lookup by name is deliberately closed so
# a typo or an invented type fails loudly rather than silently falling back.
PROFILES = {p.name: p for p in (LIST, RECIPE, PRODUCT, TUTORIAL, GENERAL)}

# Order matters: the first match wins, so the more specific signals come first.
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recipe", ("recipe", "ingredients", "preheat", "bake", "whisk",
                "tablespoon", "teaspoon", "simmer", "bake time", "serves",
                "cook time")),
    ("product", ("in stock", "price", "buy", "cart", "shipping",
                 "specifications", "battery life", "warranty", "add to",
                 "our verdict", "review of")),
    ("tutorial", ("tutorial", "how to", "step 1", "prerequisite", "you will",
                  "in this guide", "getting started", "install", "run the")),
    ("list", ("top 5", "top 10", "5 skills", "claude skills", "list of",
              "here are", "checklist", "the following", "compilation",
              "things worth", "tools worth", "worth saving")),
)

# A title that opens with a count ("5 Claude skills", "10 tools I use") is the
# clearest list signal there is, and no fixed marker list catches every wording.
_COUNT_TITLE = re.compile(r"^\s*\d{1,3}\s+\S")


def classify(url: str = "", text: str = "", title: str = "") -> Profile:
    """Pick the profile from what the content looks like, before calling a model.

    Deliberately local and cheap, so the choice is auditable and costs nothing.
    Video origin does not determine the extraction profile.
    """
    haystack = f"{title}\n{text[:4000]}".lower()

    if _COUNT_TITLE.match(title or ""):
        return LIST

    for name, markers in _SIGNALS:
        if any(marker in haystack for marker in markers):
            return PROFILES[name]
    if len(re.findall(r"(?:^|\s)\d+[.)]\s+", text or "")) >= 2:
        return LIST
    return GENERAL


def get_profile(name: str) -> Profile:
    """Look up a profile by name. Only the five profiles exist."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown extraction profile: {name!r}; "
                       f"expected one of {sorted(PROFILES)}") from None