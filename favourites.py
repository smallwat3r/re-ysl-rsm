"""Favourite colours, persisted in SQLite.

A favourite is a name plus a recipe: cartridge CODE -> share (a proportion, the
amount is chosen at dispense time), e.g. {"MA_304": 55, "VC_219": 45}. Keyed by
code, not slot, so a favourite is dispensable whenever those exact cartridges are
loaded, whatever their slots. The web app checks that and reuses the normal
/dispense path to pour one.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "favourites.db"


def _conn(path: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS favourites "
        "(id INTEGER PRIMARY KEY, name TEXT NOT NULL, recipe TEXT NOT NULL)"
    )
    return con


def list_all(path: Path = DB) -> list[dict]:
    with _conn(path) as con:
        rows = con.execute(
            "SELECT id, name, recipe FROM favourites ORDER BY name"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "recipe": json.loads(r[2])} for r in rows]


def add(name: str, recipe: dict[str, int], path: Path = DB) -> int:
    """Store a favourite. Shares are proportions, so no cap, /dispense enforces it."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    if not recipe or not all(isinstance(v, int) and v > 0 for v in recipe.values()):
        raise ValueError("recipe must be {code: positive share}")
    with _conn(path) as con:
        cur = con.execute(
            "INSERT INTO favourites (name, recipe) VALUES (?, ?)",
            (name, json.dumps(recipe)),
        )
        return cur.lastrowid


def delete(fav_id: int, path: Path = DB) -> None:
    with _conn(path) as con:
        con.execute("DELETE FROM favourites WHERE id = ?", (fav_id,))
