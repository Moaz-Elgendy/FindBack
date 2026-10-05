"""Category names shared by processing, validation and mobile chips."""
import json
from enum import Enum
from pathlib import Path

CATEGORIES = tuple(json.loads(Path(__file__).with_suffix(".json").read_text()))
Category = Enum("Category", {name: name for name in CATEGORIES}, type=str)
