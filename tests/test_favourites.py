"""Tests for the SQLite favourites store, against a temp DB."""

from __future__ import annotations

import pytest

import favourites


def test_add_list_delete(tmp_path):
    db = tmp_path / "favs.db"
    fid = favourites.add("Warm red", {"MA_304": 55, "VC_219": 45}, path=db)
    rows = favourites.list_all(path=db)
    assert len(rows) == 1
    assert rows[0]["name"] == "Warm red"
    assert rows[0]["recipe"] == {"MA_304": 55, "VC_219": 45}
    assert rows[0]["id"] == fid

    favourites.delete(fid, path=db)
    assert favourites.list_all(path=db) == []


def test_add_rejects_bad_recipes(tmp_path):
    db = tmp_path / "favs.db"
    with pytest.raises(ValueError, match="name"):
        favourites.add("", {"MA_304": 100}, path=db)
    with pytest.raises(ValueError, match="recipe"):
        favourites.add("x", {}, path=db)
    with pytest.raises(ValueError, match="recipe"):
        favourites.add("x", {"MA_304": 0}, path=db)  # non-positive share
