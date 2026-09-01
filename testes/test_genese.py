"""A gênese: os 5.000 existem no dia zero, uma vez só."""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SUPPLY_TOTAL
from vavacoin.extensoes import db
from vavacoin.moeda import TIPO_GENESE, criar_genese
from vavacoin.modelos import Transacao, Usuario


def test_genese_poe_todo_o_supply_no_banco_central(app):
    bc = criar_genese()
    db.session.commit()
    assert bc.saldo == SUPPLY_TOTAL
    conservacao()


def test_genese_e_idempotente(app):
    """Rodar duas vezes não cria 10.000."""
    primeiro = criar_genese()
    db.session.commit()
    conservacao()

    segundo = criar_genese()
    db.session.commit()

    assert segundo.id == primeiro.id
    conservacao()
    assert db.session.query(Usuario).count() == 1


def test_genese_e_a_unica_transacao_sem_origem(app, bc):
    """Só a gênese cria dinheiro; toda outra linha tem origem."""
    linhas = db.session.query(Transacao).all()
    assert len(linhas) == 1
    assert linhas[0].tipo == TIPO_GENESE
    assert linhas[0].origem_id is None
    assert linhas[0].valor == SUPPLY_TOTAL


def test_banco_central_nao_autentica(app, bc):
    """Conta de tesouraria que loga é caixa que qualquer um esvazia."""
    assert bc.senha_hash is None
    assert bc.verificar_senha("qualquer") is False
    assert bc.is_active is False
    with pytest.raises(ValueError):
        bc.definir_senha("tentativa")


def test_saldo_negativo_e_recusado_pelo_banco(app, bc):
    """Última rede: o banco recusa saldo negativo mesmo por escrita direta."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.update(Usuario).where(Usuario.id == bc.id).values(saldo=Decimal("-1.00"))
        )
    db.session.rollback()
    conservacao()
