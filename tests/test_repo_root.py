"""Pruebas del resolvedor de rutas (portables: no tocan el host real)."""

from __future__ import annotations

import os

import pytest

from tools.repo_root import repo_path, worktree_path


def test_repo_path_encuentra_checkout_en_base_tmp(monkeypatch, tmp_path):
    (tmp_path / "goncloud-Orbit").mkdir()
    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    assert repo_path("goncloud-Orbit") == tmp_path / "goncloud-Orbit"


def test_repo_path_falla_rapido_con_bases_probadas(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    monkeypatch.setattr("tools.repo_root._bases", lambda: [tmp_path])
    with pytest.raises(FileNotFoundError, match="probado"):
        repo_path("no-existe")


def test_repo_path_ignora_archivos_y_sigue(monkeypatch, tmp_path):
    (tmp_path / "x").write_text("soy archivo, no dir")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / "x").mkdir()
    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        repo_path("x")
    assert worktree_path("x") == tmp_path / "wt" / "x"


def test_worktree_path_prefiere_env(monkeypatch, tmp_path):
    (tmp_path / "wt" / "sec5e").mkdir(parents=True)
    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    assert worktree_path("sec5e") == tmp_path / "wt" / "sec5e"


def test_bases_no_dependen_del_os():
    import tools.repo_root as rr

    bases = [str(b) for b in rr._bases()]
    assert any(bases)
    assert os.name in ("nt", "posix")
