"""Pruebas de tools/check_push_creds.py (frena un push sin credencial en vez de colgarse)."""

def test_check_push_creds_sin_git_falla_rapido(monkeypatch):
    """Sin binario de git no hay credential helper: 1 sin colgarse."""
    import subprocess as sp

    from tools.check_push_creds import main

    def roto(*a, **k):
        raise FileNotFoundError("sin git")

    monkeypatch.setattr(sp, "run", roto)
    assert main() == 1


def test_check_push_creds_timeout_falla(monkeypatch):
    """Un credential helper colgado no detiene el push eternamente."""
    import subprocess as sp

    from tools.check_push_creds import main

    def colgado(*a, **k):
        raise sp.TimeoutExpired(cmd="git", timeout=15)

    monkeypatch.setattr(sp, "run", colgado)
    assert main() == 1


def test_check_push_creds_con_credencial_pasa(monkeypatch):
    """Usuario+password del fill = via libre."""
    import subprocess as sp

    from tools.check_push_creds import main

    def con_cred(*a, **k):
        return sp.CompletedProcess(
            args="git", returncode=0, stdout="username=x\npassword=y\n", stderr=""
        )

    monkeypatch.setattr(sp, "run", con_cred)
    assert main() == 0
