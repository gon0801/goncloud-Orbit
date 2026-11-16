"""Aceptacion externa: exige conducta correcta, no perpetua los defectos."""
import json
import pytest
import writes


@pytest.fixture(scope="session", autouse=True)
def metadata():
    print(json.dumps(writes.metadata(), ensure_ascii=False))


@pytest.mark.parametrize("mode,enabled", [("shadow", True), ("off", True), ("live", False), ("live", True)])
def test_01_reconciliacion_no_reenvia_bid_anterior(mode, enabled):
    result = writes.scenario01(mode, enabled)
    print(json.dumps(result, default=str))
    assert result["puts_recovery"] == 0
    assert len(result["ledger"]) == 1


@pytest.mark.parametrize("shape", ["missing", "truncated"])
def test_02_lista_incompleta_no_autoriza_repost(shape):
    result = writes.scenario02(shape)
    print(json.dumps(result))
    assert result["reposts"] == 0, result
    assert not result["confirmada"], result
    assert result["queue"] not in ("applied", "failed"), result


def test_03_harvest_y_reversa_no_archivan_keyword_ajena():
    result = writes.scenario03()
    print(json.dumps(result, default=str))
    assert not any("ajena" in b["keywordIdFilter"]["include"] for b in result["deletes_keyword"]), result
    assert not (result["external_ids"].get("keyword_id") == "ajena" and result["external_ids"].get("keyword_creada") is True), result


def test_10_negative_reconciliado_conserva_reversa():
    result = writes.scenario10()
    print(json.dumps(result, default=str))
    assert result["status"] == 200, result
    assert result["result"]["confirmada"] is True, result
    assert len(result["deletes"]) == 1, result


def test_11_reponer_no_recrea_tras_ack_y_readback_ambiguo():
    result = writes.scenario11()
    print(json.dumps(result, default=str))
    assert result["posts"] == 1, result
