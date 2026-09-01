"""O teto do supply: 10.000, e para aí.

O supply deixou de ser fixo quando o administrador ganhou o poder de cunhar
ao ajustar saldo — a disciplina passou a depender do juízo de quem
administra. Estes testes verificam a parte dela que voltou para o código.

Três coisas que **não** esbarram no teto, e cada uma tem teste: ajuste para
baixo, ajuste para cima que caiba no saldo não emitido do Banco Central, e
transferência entre pessoas. Só o que precisa cunhar é que conta.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SUPPLY_INICIAL, SUPPLY_MAXIMO
from vavacoin.erros import TetoDoSupply
from vavacoin.extensoes import db
from vavacoin.moeda import (
    TIPO_EMISSAO,
    cabe_emitir,
    mover,
    supply_emitido,
)
from vavacoin.modelos import Transacao
from vavacoin.operacoes import ajustar_saldo


def _esvaziar_o_banco_central(bc, alvo):
    """Manda todo o não emitido para ``alvo``: dali em diante, ajustar cunha."""
    mover(bc, alvo, bc.saldo, motivo="esvaziando para o teste")
    db.session.commit()
    assert bc.saldo == Decimal("0.00")


def test_teto_e_dez_mil(app):
    assert SUPPLY_MAXIMO == Decimal("10000.00")


def test_no_dia_zero_cabe_a_diferenca(app, bc):
    assert supply_emitido() == SUPPLY_INICIAL
    assert cabe_emitir() == SUPPLY_MAXIMO - SUPPLY_INICIAL


# --- fronteira --------------------------------------------------------------


def test_emitir_ate_exatamente_dez_mil_passa(app, bc):
    """O caminho que leva o supply de 5.000 a 10.000."""
    conservacao()
    mover(
        None,
        bc,
        "5000.00",
        tipo=TIPO_EMISSAO,
        motivo="segunda emissão, decidida pelo dono",
        ator=bc,
    )
    db.session.commit()

    assert supply_emitido() == SUPPLY_MAXIMO
    assert cabe_emitir() == Decimal("0.00")
    assert bc.saldo == SUPPLY_MAXIMO
    conservacao()


def test_um_centavo_acima_do_teto_e_recusado_e_nao_move_nada(app, bc):
    conservacao()
    saldo_antes = bc.saldo
    linhas_antes = db.session.query(Transacao).count()

    with pytest.raises(TetoDoSupply):
        mover(None, bc, "5000.01", tipo=TIPO_EMISSAO, motivo="passar do teto", ator=bc)
    db.session.rollback()

    db.session.expire_all()
    assert bc.saldo == saldo_antes
    assert supply_emitido() == SUPPLY_INICIAL
    assert db.session.query(Transacao).count() == linhas_antes
    conservacao()


def test_mensagem_diz_quanto_ainda_cabe(app, bc):
    with pytest.raises(TetoDoSupply, match="ainda cabem 5000.00"):
        mover(None, bc, "9999.00", tipo=TIPO_EMISSAO, motivo="demais", ator=bc)
    db.session.rollback()


def test_no_teto_nao_cabe_mais_nem_um_centavo(app, bc):
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="chegando ao teto", ator=bc)
    db.session.commit()
    conservacao()

    with pytest.raises(TetoDoSupply):
        mover(None, bc, "0.01", tipo=TIPO_EMISSAO, motivo="mais um", ator=bc)
    db.session.rollback()

    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


# --- o que não esbarra no teto ----------------------------------------------


def test_ajuste_para_baixo_e_livre_mesmo_no_teto(app, bc, nova_pessoa):
    """Devolver dinheiro ao Banco Central nunca esbarra no teto."""
    ana = nova_pessoa(com_convite=True, saldo="100.00")
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="ao teto", ator=bc)
    db.session.commit()
    assert cabe_emitir() == Decimal("0.00")

    ajustar_saldo(ana, "10.00", "corrigindo para baixo", autoridade=bc)
    db.session.commit()

    assert ana.saldo == Decimal("10.00")
    assert supply_emitido() == SUPPLY_MAXIMO, "devolver não cunha"
    conservacao()


def test_ajuste_para_cima_dentro_do_nao_emitido_nao_toca_no_teto(app, bc, nova_pessoa):
    """Gastar o que o Banco Central já tem não é emissão."""
    ana = nova_pessoa(com_convite=True)
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="ao teto", ator=bc)
    db.session.commit()
    assert cabe_emitir() == Decimal("0.00")
    assert bc.saldo == SUPPLY_MAXIMO

    ajustar_saldo(ana, "300.00", "veio do não emitido", autoridade=bc)
    db.session.commit()

    assert ana.saldo == Decimal("300.00")
    assert bc.saldo == SUPPLY_MAXIMO - Decimal("300.00")
    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


def test_transferencia_entre_pessoas_nao_toca_no_teto(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="100.00")
    bia = nova_pessoa(com_convite=True, saldo="100.00")
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="ao teto", ator=bc)
    db.session.commit()

    mover(ana, bia, "50.00", motivo="pagou o lanche")
    db.session.commit()

    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


# --- o caso que o dono vai encontrar ----------------------------------------


def test_ajuste_para_cima_que_precisa_cunhar_e_recusado_no_teto(app, bc, nova_pessoa):
    """O alerta que vale ele saber antes de esbarrar.

    Com o supply no teto e o Banco Central sem saldo não emitido, corrigir o
    saldo de alguém para cima é recusado. A saída passa a ser tirar de outra
    conta — que é exatamente o ponto de existir um teto.
    """
    ana = nova_pessoa(com_convite=True, saldo="100.00")
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="ao teto", ator=bc)
    db.session.commit()
    _esvaziar_o_banco_central(bc, ana)
    conservacao()

    saldo_antes = ana.saldo
    with pytest.raises(TetoDoSupply):
        ajustar_saldo(ana, saldo_antes + Decimal("1.00"), "mais um", autoridade=bc)
    db.session.rollback()

    db.session.expire_all()
    assert ana.saldo == saldo_antes, "recusada não pode mover nada"
    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


def test_com_o_teto_batendo_ainda_da_para_corrigir_tirando_de_outra_conta(
    app, bc, nova_pessoa
):
    """A saída que sobra, e que funciona."""
    ana = nova_pessoa(com_convite=True, saldo="100.00")
    bia = nova_pessoa(com_convite=True, saldo="100.00")
    mover(None, bc, "5000.00", tipo=TIPO_EMISSAO, motivo="ao teto", ator=bc)
    db.session.commit()
    _esvaziar_o_banco_central(bc, ana)
    conservacao()

    # Tira da ana (devolve ao BC) e põe na bia: nada é cunhado.
    ajustar_saldo(ana, ana.saldo - Decimal("50.00"), "estava errado", autoridade=bc)
    ajustar_saldo(bia, bia.saldo + Decimal("50.00"), "era dela", autoridade=bc)
    db.session.commit()

    assert bia.saldo == Decimal("150.00")
    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


# --- a gênese não passa pelo teto -------------------------------------------


def test_a_genese_nao_e_barrada_pelo_teto(app):
    """Ela escreve o saldo direto, antes de existir Banco Central."""
    from vavacoin.moeda import criar_genese

    bc = criar_genese()
    db.session.commit()
    assert bc.saldo == SUPPLY_INICIAL
    assert supply_emitido() == SUPPLY_INICIAL
    conservacao()
