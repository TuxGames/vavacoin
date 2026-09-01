"""A auditoria: conferir sem precisar confiar no núcleo.

O teste que importa aqui é o de sabotagem — escrever saldo por fora do
``mover()`` e mostrar que a auditoria acusa. Sem isso, "o ledger explica os
saldos" seria uma frase, não uma verificação.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.auditoria import (
    auditar,
    auditar_ou_falhar,
    conferir_ledger,
    estado_da_economia,
    linhas_extrato,
)
from vavacoin.constantes import SAQUE_INICIAL, SUPPLY_INICIAL
from vavacoin.erros import MassaViolada
from vavacoin.extensoes import db
from vavacoin.moeda import mover
from vavacoin.modelos import Transacao, Usuario


def test_estado_da_economia_separa_emitido_de_circulante(app, bc, nova_pessoa):
    conservacao()
    nova_pessoa(com_convite=True)
    nova_pessoa(com_convite=True)
    curioso = nova_pessoa(com_convite=False)  # noqa: F841
    db.session.commit()

    estado = estado_da_economia()

    assert estado["conservado"] is True
    assert estado["soma_dos_saldos"] == SUPPLY_INICIAL
    assert estado["diferenca"] == Decimal("0.00")
    assert estado["em_circulacao"] == 2 * SAQUE_INICIAL
    assert estado["nao_emitido"] == SUPPLY_INICIAL - 2 * SAQUE_INICIAL
    assert estado["contas"] == 3
    assert estado["participantes"] == 2
    conservacao()


def test_auditoria_passa_numa_economia_movimentada(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    for _ in range(20):
        mover(ana, bia, "0.07")
        mover(bia, ana, "0.03")
    db.session.commit()
    conservacao()

    relatorio = auditar()

    assert relatorio["ok"] is True
    assert relatorio["ledger"]["saldos_divergentes"] == []
    assert relatorio["ledger"]["linhas_inconsistentes"] == []
    assert relatorio["ledger"]["soma_pelo_ledger"] == SUPPLY_INICIAL


def test_auditoria_acusa_saldo_escrito_por_fora(app, bc, nova_pessoa):
    """UPDATE na mão: a massa até muda, e o ledger deixa de explicar o saldo."""
    ana = nova_pessoa(com_convite=True)
    db.session.commit()
    conservacao()

    # A sabotagem que a auditoria existe para pegar.
    db.session.execute(
        db.update(Usuario).where(Usuario.id == ana.id).values(saldo=Decimal("999.00"))
    )
    db.session.commit()

    relatorio = auditar()
    assert relatorio["ok"] is False
    divergencias = relatorio["ledger"]["saldos_divergentes"]
    assert [d["usuario"] for d in divergencias] == ["aluno1"]
    assert divergencias[0]["pelo_ledger"] == SAQUE_INICIAL
    assert divergencias[0]["diferenca"] == Decimal("949.00")

    with pytest.raises(MassaViolada):
        auditar_ou_falhar()


def test_auditoria_acusa_troca_disfarcada_que_conserva_a_massa(app, bc, nova_pessoa):
    """Tirar de um e pôr no outro por fora mantém a soma — e é pego assim mesmo.

    É o caso perverso: só somar saldos não veria nada. A reconstrução pelo
    ledger vê, porque nenhuma linha explica a mudança.
    """
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    db.session.commit()
    conservacao()

    db.session.execute(
        db.update(Usuario).where(Usuario.id == ana.id).values(saldo=Decimal("10.00"))
    )
    db.session.execute(
        db.update(Usuario).where(Usuario.id == bia.id).values(saldo=Decimal("90.00"))
    )
    db.session.commit()

    conservacao()  # a massa continua 5.000,00 — e ainda assim há fraude
    relatorio = auditar()
    assert relatorio["economia"]["conservado"] is True
    assert relatorio["ok"] is False
    assert len(relatorio["ledger"]["saldos_divergentes"]) == 2


def test_auditoria_acusa_linha_do_ledger_adulterada(app, bc, nova_pessoa):
    """Mexer no valor de uma transação antiga quebra a cadeia de saldos."""
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    transacao = mover(ana, bia, "10.00")
    db.session.commit()
    conservacao()

    db.session.execute(
        db.update(Transacao)
        .where(Transacao.id == transacao.id)
        .values(valor=Decimal("11.00"))
    )
    db.session.commit()

    ledger = conferir_ledger()
    assert ledger["ok"] is False
    assert ledger["linhas_inconsistentes"]


def test_extrato_traz_sinal_contraparte_e_saldo(app, bc, nova_pessoa):
    ana = nova_pessoa(nome="ana", com_convite=True)
    bia = nova_pessoa(nome="bia", com_convite=True)
    mover(ana, bia, "12.00", motivo="explicou a questão 3")
    db.session.commit()
    conservacao()

    linhas = linhas_extrato(ana)

    assert len(linhas) == 2
    saida, saque = linhas
    assert saida["valor_com_sinal"] == Decimal("-12.00")
    assert saida["contraparte"] == "bia"
    assert saida["saldo_depois"] == Decimal("38.00")
    assert saida["motivo"] == "explicou a questão 3"
    assert saque["valor_com_sinal"] == SAQUE_INICIAL
    assert saque["contraparte"] == "banco_central"
    assert saque["saldo_depois"] == SAQUE_INICIAL


def test_extrato_do_banco_central_mostra_a_genese(app, bc):
    """A gênese não tem contraparte: o dinheiro não veio de ninguém."""
    conservacao()
    linhas = linhas_extrato(bc)
    assert len(linhas) == 1
    assert linhas[0]["tipo"] == "genese"
    assert linhas[0]["valor_com_sinal"] == SUPPLY_INICIAL
    assert linhas[0]["contraparte"] == "—"


def test_extrato_respeita_o_limite(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    for _ in range(10):
        mover(ana, bia, "1.00")
    db.session.commit()
    conservacao()

    assert len(linhas_extrato(ana, limite=3)) == 3
    assert len(linhas_extrato(ana)) == 11  # 10 transferências + o saque
