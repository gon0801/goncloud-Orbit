"""Pruebas del resolvedor de rutas (portables: no tocan el host real)."""

from __future__ import annotations

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
    monkeypatch.setattr("tools.repo_root._bases", lambda: [tmp_path])
    with pytest.raises(FileNotFoundError):
        repo_path("x")
    assert worktree_path("x") == tmp_path / "wt" / "x"


def test_worktree_path_prefiere_env(monkeypatch, tmp_path):
    (tmp_path / "wt" / "sec5e").mkdir(parents=True)
    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    assert worktree_path("sec5e") == tmp_path / "wt" / "sec5e"


def test_env_va_primero_en_bases(monkeypatch, tmp_path):
    import tools.repo_root as rr

    monkeypatch.setenv("ORBIT_DEV", str(tmp_path))
    bases = rr._bases()
    assert bases[0] == tmp_path
    assert all(isinstance(b, type(tmp_path)) for b in bases)
