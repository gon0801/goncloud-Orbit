"""Cierre: dos fallos materiales y un control corregido; sin mock del motor."""
from decimal import Decimal
import optimizer as review


def test_12_la_meta_avanza_en_ciclos_live_con_ledger_rancio():
    print(review.metadata())
    with review.database() as conn:
        result = review.convergence_scenario(conn)
    assert result["initial_status"] == "done"
    initial = Decimal(result["initial"]["target_aplicado"])
    assert initial > Decimal("21.5"), result
    steps = result["steps"]
    assert all(s["status"] == "done" for s in steps), result
    assert all(s["target"]["motivo_abstencion"] == "ledger_rancio" for s in steps), result
    assert all(s["target"]["convergiendo_a_setting"] for s in steps), result
    expected = [initial - Decimal("0.5") * n for n in (1, 2, 3)]
    actual = [Decimal(s["target"]["target_aplicado"]) for s in steps]
    assert actual == expected, result
    assert [s["leaves"][0][0] for s in steps] == expected, result


def test_13_reobservar_un_termino_maduro_no_aborta_el_ciclo():
    print(review.metadata())
    with review.database() as conn:
        control = review.harvest_scenario(conn)
        assert control["status"] == "done", control
        assert set(control["kinds"]) == {"pause", "negative", "harvest"}, control
        for row in control["rows"]:
            assert review.replay.reproduce(row[9]) == (row[1], row[4], row[5])
    with review.database() as conn:
        result = review.harvest_scenario(conn, reobservado=True)
    assert result["status"] == "done", result
    assert {"pause", "negative"} <= set(result["kinds"]), result
    # No impone clamp frente a excluir la observacion posterior: ambas
    # soluciones deben preservar el ciclo y su contrato bitemporal.
    for row in result["rows"]:
        assert row[8] is None or row[8] <= review.seeds.DECIDED_AT
        assert review.replay.reproduce(row[9]) == (row[1], row[4], row[5])


def test_control_arreglos01_b_no_harvestea_keyword_exact_propia():
    with review.database() as conn:
        result = review.harvest_scenario(conn, keyword_propia=True)
    assert result["status"] == "done", result
    assert "harvest" not in result["kinds"], result
    assert set(result["kinds"]) == {"pause", "negative"}, result
    assert result["notes"]["skips"]["termino"]["termino_es_keyword_propia"] == 1
