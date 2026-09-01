"""O saque inicial: 50 da pessoa, sacados do que já existe."""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import CAPACIDADE, SAQUE_INICIAL, SUPPLY_INICIAL
from vavacoin.erros import (
    ConviteInvalido,
    ConviteJaResgatado,
    SupplyInsuficiente,
    UsuarioJaResgatou,
)
from vavacoin.extensoes import db
from vavacoin.moeda import TIPO_SAQUE_INICIAL
from vavacoin.modelos import Transacao
from vavacoin.operacoes import criar_convite, criar_usuario, resgatar_convite


def test_saque_sai_do_banco_central(app, bc):
    """Os 50 não são criados: o Banco Central fica com 50 a menos."""
    conservacao()
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()

    transacao = resgatar_convite(ana, convite.codigo)
    db.session.commit()

    assert ana.saldo == SAQUE_INICIAL
    assert bc.saldo == SUPPLY_INICIAL - SAQUE_INICIAL
    assert transacao.tipo == TIPO_SAQUE_INICIAL
    assert transacao.origem_id == bc.id
    conservacao()


def test_mesmo_codigo_duas_vezes_nao_saca_duas_vezes(app, bc):
    """A segunda execução do resgate do mesmo código não move nada."""
    conservacao()
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()
    resgatar_convite(ana, convite.codigo)
    db.session.commit()
    conservacao()

    bia = criar_usuario("bia", "senha-boa-123", autoridade=bc)
    db.session.commit()
    with pytest.raises(ConviteJaResgatado):
        resgatar_convite(bia, convite.codigo)
    db.session.rollback()

    assert ana.saldo == SAQUE_INICIAL
    assert bia.saldo == Decimal("0.00")
    conservacao()


def test_dez_contas_da_mesma_pessoa_nao_viram_500(app, bc):
    """Os 50 são da pessoa: sem um convite novo, nenhuma conta saca."""
    conservacao()
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    contas = [criar_usuario(f"ana{i}", "senha-boa-123", autoridade=bc) for i in range(10)]
    db.session.commit()

    resgatar_convite(contas[0], convite.codigo)
    db.session.commit()

    for conta in contas[1:]:
        with pytest.raises(ConviteJaResgatado):
            resgatar_convite(conta, convite.codigo)
        db.session.rollback()

    emitido = sum((c.saldo for c in contas), Decimal("0.00"))
    assert emitido == SAQUE_INICIAL
    conservacao()


def test_uma_conta_nao_resgata_dois_codigos(app, bc):
    conservacao()
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    primeiro = criar_convite(destinatario="Ana", autoridade=bc)
    segundo = criar_convite(destinatario="Ana de novo", autoridade=bc)
    db.session.commit()

    resgatar_convite(ana, primeiro.codigo)
    db.session.commit()

    with pytest.raises(UsuarioJaResgatou):
        resgatar_convite(ana, segundo.codigo)
    db.session.rollback()

    assert ana.saldo == SAQUE_INICIAL
    conservacao()


def test_codigo_inexistente(app, bc):
    conservacao()
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    db.session.commit()

    with pytest.raises(ConviteInvalido):
        resgatar_convite(ana, "nao-existe")
    db.session.rollback()

    assert ana.saldo == Decimal("0.00")
    conservacao()


def test_supply_comporta_exatamente_cem_pessoas(app, bc, nova_pessoa):
    """A centésima entra; a centésima primeira não, e nada é cunhado."""
    conservacao()
    for _ in range(CAPACIDADE):
        nova_pessoa(com_convite=True)
    conservacao()
    assert bc.saldo == Decimal("0.00")

    excedente = criar_usuario("aluno101", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="o 101", autoridade=bc)
    db.session.commit()
    with pytest.raises(SupplyInsuficiente):
        resgatar_convite(excedente, convite.codigo)
    db.session.rollback()

    assert excedente.saldo == Decimal("0.00")
    conservacao()


def test_resgate_falho_nao_queima_o_convite(app, bc, nova_pessoa):
    """Se o saque falha, o código continua valendo — o savepoint garante."""
    conservacao()
    for _ in range(CAPACIDADE):
        nova_pessoa(com_convite=True)

    tarde = criar_usuario("atrasado", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Atrasado", autoridade=bc)
    db.session.commit()
    with pytest.raises(SupplyInsuficiente):
        resgatar_convite(tarde, convite.codigo)
    db.session.commit()  # quem chamou nem deu rollback

    db.session.expire_all()
    assert convite.resgatado is False
    assert tarde.saldo == Decimal("0.00")
    conservacao()


def test_senha_e_guardada_com_hash(app, bc):
    """Senha em texto puro é o que deixou o Benbals vulnerável."""
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    db.session.commit()

    assert ana.senha_hash != "senha-boa-123"
    assert "senha-boa-123" not in ana.senha_hash
    assert ana.senha_hash.startswith("$2")
    assert ana.verificar_senha("senha-boa-123")
    assert not ana.verificar_senha("senha-errada")


def test_ledger_explica_cada_centavo(app, bc, nova_pessoa):
    """Somando o ledger dá para reconstruir todo saldo a partir do zero."""
    ana = nova_pessoa(com_convite=True)
    bia = nova_pessoa(com_convite=True)
    from vavacoin.moeda import mover

    mover(ana, bia, "13.37")
    db.session.commit()
    conservacao()

    saldos = {}
    for linha in db.session.query(Transacao).order_by(Transacao.id):
        if linha.origem_id is not None:
            saldos[linha.origem_id] = saldos.get(linha.origem_id, Decimal("0.00")) - linha.valor
        saldos[linha.destino_id] = saldos.get(linha.destino_id, Decimal("0.00")) + linha.valor

    assert saldos[ana.id] == ana.saldo
    assert saldos[bia.id] == bia.saldo
    assert saldos[bc.id] == bc.saldo
    assert sum(saldos.values(), Decimal("0.00")) == SUPPLY_INICIAL
