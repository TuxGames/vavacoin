"""O caminho único do dinheiro."""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.erros import MesmaConta, SaldoInsuficiente, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.moeda import TIPO_TRANSFERENCIA, mover
from vavacoin.modelos import Transacao


def test_mover_debita_e_credita_o_mesmo_valor(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    mover(ana, bia, "10.00", motivo="explicou a questão 3")
    db.session.commit()

    assert ana.saldo == Decimal("40.00")
    assert bia.saldo == Decimal("60.00")
    conservacao()


def test_mover_grava_a_linha_do_ledger(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    transacao = mover(ana, bia, "10.00", motivo="fila do bandejão")
    db.session.commit()

    lida = db.session.get(Transacao, transacao.id)
    assert lida.origem_id == ana.id
    assert lida.destino_id == bia.id
    assert lida.valor == Decimal("10.00")
    assert lida.tipo == TIPO_TRANSFERENCIA
    assert lida.motivo == "fila do bandejão"
    assert lida.saldo_origem_depois == Decimal("40.00")
    assert lida.saldo_destino_depois == Decimal("60.00")
    conservacao()


def test_saldo_insuficiente_nao_move_nada(app, bc, nova_pessoa):
    """Nem parcialmente: 50 não viram 49 na tentativa de mandar 60."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    antes = (ana.saldo, bia.saldo)
    linhas_antes = db.session.query(Transacao).count()

    with pytest.raises(SaldoInsuficiente):
        mover(ana, bia, "60.00")
    db.session.rollback()

    assert (ana.saldo, bia.saldo) == antes
    assert db.session.query(Transacao).count() == linhas_antes
    conservacao()


def test_transferencia_para_si_mesmo_e_recusada(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(MesmaConta):
        mover(ana, ana, "10.00")
    db.session.rollback()

    assert ana.saldo == Decimal("50.00")
    conservacao()


@pytest.mark.parametrize("valor", ["0.00", "-1.00", "-0.01"])
def test_valor_zero_ou_negativo_e_recusado(app, bc, nova_pessoa, valor):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        mover(ana, bia, valor)
    db.session.rollback()

    assert ana.saldo == Decimal("50.00")
    assert bia.saldo == Decimal("50.00")
    conservacao()


def test_valor_float_e_recusado(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        mover(ana, bia, 10.0)
    db.session.rollback()
    conservacao()


def test_valor_abaixo_do_centavo_e_recusado(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        mover(ana, bia, "0.001")
    db.session.rollback()
    conservacao()


def test_conta_inexistente_e_recusada(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        mover(ana, 99999, "1.00")
    db.session.rollback()
    conservacao()


def test_gastar_o_saldo_inteiro_e_permitido(app, bc, nova_pessoa):
    """O limite é o saldo, não menos que ele."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    mover(ana, bia, "50.00")
    db.session.commit()

    assert ana.saldo == Decimal("0.00")
    assert bia.saldo == Decimal("100.00")
    conservacao()


def test_muitos_movimentos_de_um_centavo_conservam_massa(app, bc, nova_pessoa):
    """Onde o float quebraria: 300 movimentos de 0,01 e volta ao mesmo lugar."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    for _ in range(300):
        mover(ana, bia, "0.01")
    for _ in range(300):
        mover(bia, ana, "0.01")
    db.session.commit()

    assert ana.saldo == Decimal("50.00")
    assert bia.saldo == Decimal("50.00")
    conservacao()
