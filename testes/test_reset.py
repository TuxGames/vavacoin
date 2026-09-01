"""O reset como operação de verdade, não como SQL improvisado."""

from decimal import Decimal

from conftest import conservacao

from vavacoin.constantes import SAQUE_INICIAL, SUPPLY_INICIAL
from vavacoin.extensoes import db
from vavacoin.moeda import TIPO_RESET_RECOLHIMENTO, TIPO_RESET_REDISTRIBUICAO, mover
from vavacoin.modelos import Transacao
from vavacoin.operacoes import resetar_economia


def test_reset_devolve_tudo_e_redistribui(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    mover(ana, bia, "40.00", motivo="quebrou no caladinho")
    db.session.commit()
    assert (ana.saldo, bia.saldo) == (Decimal("10.00"), Decimal("90.00"))
    conservacao()

    quantos = resetar_economia(autoridade=bc)
    db.session.commit()

    assert quantos == 2
    assert ana.saldo == SAQUE_INICIAL
    assert bia.saldo == SAQUE_INICIAL
    assert bc.saldo == SUPPLY_INICIAL - 2 * SAQUE_INICIAL
    conservacao()


def test_reset_passa_pelo_ledger(app, bc, nova_pessoa):
    """Nenhum saldo muda sem uma linha explicando."""
    nova_pessoa(com_convite=True)
    conservacao()
    linhas_antes = db.session.query(Transacao).count()

    resetar_economia(autoridade=bc)
    db.session.commit()

    tipos = [
        t.tipo
        for t in db.session.query(Transacao)
        .order_by(Transacao.id)
        .offset(linhas_antes)
    ]
    assert tipos == [TIPO_RESET_RECOLHIMENTO, TIPO_RESET_REDISTRIBUICAO]
    conservacao()


def test_reset_nao_da_saque_a_quem_nunca_resgatou(app, bc, nova_pessoa):
    """O direito aos 50 é de quem tem convite resgatado, não de quem tem conta."""
    ana = nova_pessoa(com_convite=True)
    curioso = nova_pessoa(com_convite=False)
    conservacao()

    quantos = resetar_economia(autoridade=bc)
    db.session.commit()

    assert quantos == 1
    assert ana.saldo == SAQUE_INICIAL
    assert curioso.saldo == Decimal("0.00")
    conservacao()


def test_reset_e_idempotente_em_efeito(app, bc, nova_pessoa):
    """Resetar duas vezes seguidas deixa a economia no mesmo lugar."""
    nova_pessoa(com_convite=True)
    nova_pessoa(com_convite=True)
    resetar_economia(autoridade=bc)
    db.session.commit()
    conservacao()
    primeiro = {u.id: u.saldo for u in db.session.query(type(bc)).all()}

    resetar_economia(autoridade=bc)
    db.session.commit()

    assert {u.id: u.saldo for u in db.session.query(type(bc)).all()} == primeiro
    conservacao()


def test_reset_sem_ninguem_nao_quebra(app, bc):
    conservacao()
    assert resetar_economia(autoridade=bc) == 0
    db.session.commit()
    assert bc.saldo == SUPPLY_INICIAL
    conservacao()
