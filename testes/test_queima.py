"""Queima: o simétrico da emissão.

Antes, dinheiro só entrava no mundo — a linha sem origem. Agora também sai: a
linha sem destino. Existe porque o Banco Central é o único lado do mundo, e
baixar o saldo dele não tem para onde mandar o dinheiro sem mentir sobre o que
está em circulação.

E porque, com o teto de 10.000, sem queima o teto seria catraca de uma via:
chegando lá, nunca mais daria para emitir.

O invariante muda de forma, não de força: a soma dos saldos continua igual ao
supply reconstruído, que agora é **entradas menos saídas**.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SUPPLY_INICIAL, SUPPLY_MAXIMO
from vavacoin.erros import SaldoInsuficiente, SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.moeda import (
    TIPO_EMISSAO,
    TIPO_QUEIMA,
    cabe_emitir,
    mover,
    supply_emitido,
)
from vavacoin.modelos import Transacao
from vavacoin.operacoes import ajustar_saldo


def _queimar(bc, valor, motivo="tirando de circulação"):
    return mover(bc, None, valor, tipo=TIPO_QUEIMA, motivo=motivo, ator=bc)


# --- o básico ---------------------------------------------------------------


def test_queimar_baixa_o_saldo_e_o_supply(app, bc):
    conservacao()

    _queimar(bc, "1000.00")
    db.session.commit()

    assert bc.saldo == Decimal("4000.00")
    assert supply_emitido() == Decimal("4000.00")
    conservacao()


def test_a_queima_e_uma_linha_sem_destino(app, bc):
    transacao = _queimar(bc, "100.00", motivo="sobrou demais")
    db.session.commit()

    assert transacao.tipo == TIPO_QUEIMA
    assert transacao.origem_id == bc.id
    assert transacao.destino_id is None
    assert transacao.saldo_destino_depois is None
    assert transacao.ator_id == bc.id
    assert transacao.motivo == "sobrou demais"


def test_auditoria_fecha_depois_de_queimar(app, bc, nova_pessoa):
    from vavacoin.auditoria import auditar

    nova_pessoa(com_convite=True, saldo="50.00")
    _queimar(bc, "500.00")
    db.session.commit()

    relatorio = auditar()
    assert relatorio["ok"] is True
    assert relatorio["ledger"]["saldos_divergentes"] == []
    assert relatorio["ledger"]["linhas_inconsistentes"] == []
    conservacao()


def test_extrato_do_banco_central_mostra_a_queima(app, bc):
    from vavacoin.auditoria import linhas_extrato

    _queimar(bc, "100.00", motivo="queimando")
    db.session.commit()

    linha = linhas_extrato(bc)[0]
    assert linha["tipo"] == TIPO_QUEIMA
    assert linha["valor_com_sinal"] == Decimal("-100.00")
    assert linha["contraparte"] == "—", "queima não tem para quem"


# --- fronteira --------------------------------------------------------------


def test_queimar_ate_exatamente_zero_passa(app, bc):
    conservacao()

    _queimar(bc, bc.saldo)
    db.session.commit()

    assert bc.saldo == Decimal("0.00")
    assert supply_emitido() == Decimal("0.00")
    conservacao()


def test_queimar_um_centavo_a_mais_e_recusado(app, bc):
    conservacao()
    saldo = bc.saldo
    linhas_antes = db.session.query(Transacao).count()

    with pytest.raises(SaldoInsuficiente):
        _queimar(bc, saldo + Decimal("0.01"))
    db.session.rollback()

    db.session.expire_all()
    assert bc.saldo == saldo
    assert supply_emitido() == SUPPLY_INICIAL
    assert db.session.query(Transacao).count() == linhas_antes
    conservacao()


def test_queimar_o_que_esta_com_as_pessoas_e_recusado(app, bc, nova_pessoa):
    """A queima sai do saldo do BC, não do dinheiro que está na mão dos outros."""
    nova_pessoa(com_convite=True, saldo="4000.00")
    conservacao()

    with pytest.raises(SaldoInsuficiente):
        _queimar(bc, "2000.00")
    db.session.rollback()
    conservacao()


# --- só o Banco Central -----------------------------------------------------


def test_so_o_banco_central_queima(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(SemAutoridade):
        mover(ana, None, "10.00", tipo=TIPO_QUEIMA, motivo="quero sumir", ator=ana)
    db.session.rollback()

    with pytest.raises(SemAutoridade):
        mover(ana, None, "10.00", tipo=TIPO_QUEIMA, motivo="quero sumir", ator=bc)
    db.session.rollback()
    conservacao()


def test_queima_exige_motivo(app, bc):
    with pytest.raises(ValorInvalido):
        mover(bc, None, "10.00", tipo=TIPO_QUEIMA, ator=bc)
    db.session.rollback()
    conservacao()


def test_sem_destino_so_vale_para_queima(app, bc):
    """Uma transferência sem destino seria moeda sumindo sem ninguém decidir."""
    with pytest.raises(ValorInvalido):
        mover(bc, None, "10.00", tipo="transferencia", motivo="disfarce", ator=bc)
    db.session.rollback()
    conservacao()


def test_movimento_sem_origem_e_sem_destino(app, bc):
    with pytest.raises(ValorInvalido):
        mover(None, None, "10.00", tipo=TIPO_QUEIMA, motivo="nada", ator=bc)
    db.session.rollback()


# --- pelo ajuste do painel --------------------------------------------------


def test_baixar_o_saldo_do_bc_queima(app, bc):
    conservacao()

    ajustar_saldo(bc, "4000.00", "tirando de circulação", autoridade=bc)
    db.session.commit()

    assert bc.saldo == Decimal("4000.00")
    assert supply_emitido() == Decimal("4000.00")
    linha = db.session.query(Transacao).order_by(Transacao.id.desc()).first()
    assert linha.tipo == TIPO_QUEIMA
    conservacao()


def test_subir_o_saldo_do_bc_emite(app, bc):
    conservacao()

    ajustar_saldo(bc, "7000.00", "mais moeda", autoridade=bc)
    db.session.commit()

    assert bc.saldo == Decimal("7000.00")
    assert supply_emitido() == Decimal("7000.00")
    linha = db.session.query(Transacao).order_by(Transacao.id.desc()).first()
    assert linha.tipo == TIPO_EMISSAO
    conservacao()


def test_subir_o_bc_acima_do_teto_e_recusado(app, bc):
    from vavacoin.erros import TetoDoSupply

    with pytest.raises(TetoDoSupply):
        ajustar_saldo(bc, "10000.01", "demais", autoridade=bc)
    db.session.rollback()

    assert supply_emitido() == SUPPLY_INICIAL
    conservacao()


def test_queimar_devolve_espaco_debaixo_do_teto(app, bc):
    """A razão de a queima existir: sem ela o teto é catraca de uma via."""
    ajustar_saldo(bc, str(SUPPLY_MAXIMO), "até o teto", autoridade=bc)
    db.session.commit()
    assert cabe_emitir() == Decimal("0.00")

    ajustar_saldo(bc, "6000.00", "queimando o excesso", autoridade=bc)
    db.session.commit()

    assert supply_emitido() == Decimal("6000.00")
    assert cabe_emitir() == Decimal("4000.00")
    conservacao()

    # E dá para emitir de novo.
    ajustar_saldo(bc, "8000.00", "voltando a emitir", autoridade=bc)
    db.session.commit()
    assert supply_emitido() == Decimal("8000.00")
    conservacao()


def test_o_banco_pega_queima_disfarcada(app, bc):
    """Última rede: nem por INSERT direto some moeda sem ser queima."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.insert(Transacao).values(
                origem_id=bc.id,
                destino_id=None,
                valor=100,
                tipo="transferencia",
                motivo="disfarce",
                saldo_origem_depois=0,
                criado_em=db.func.now(),
            )
        )
    db.session.rollback()
