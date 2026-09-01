"""O saque inicial: 50 da pessoa, sacados do que já existe."""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SUPPLY_INICIAL
from vavacoin.erros import (
    ConviteInvalido,
    ConviteJaResgatado,
    SupplyInsuficiente,
    UsuarioJaResgatou,
)
from vavacoin.extensoes import db
from vavacoin.modelos import Transacao
from vavacoin.operacoes import criar_convite, criar_usuario, resgatar_convite


def test_convite_sem_destinatario(app, bc):
    """O nome é rótulo, não requisito: dá para emitir código sem nome.

    Quem emite em série, para entregar depois, não sabe de antemão quem vai
    receber cada código.
    """
    conservacao()
    convite = criar_convite(autoridade=bc)
    db.session.commit()

    assert convite.codigo
    assert convite.destinatario is None
    assert convite.resgatado is False

    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    db.session.commit()
    resgatar_convite(ana, convite.codigo)
    db.session.commit()

    db.session.refresh(convite)
    assert convite.resgatado is True
    conservacao()


def test_convite_com_destinatario_guarda_o_rotulo(app, bc):
    convite = criar_convite(destinatario="Fulano", autoridade=bc)
    db.session.commit()
    assert convite.destinatario == "Fulano"


def test_resgate_nao_move_dinheiro(app, bc):
    """O convite dá entrada na economia, não valor.

    Este teste afirmava o contrário: que o resgate sacava 50 do Banco
    Central. O saque inicial acabou, e o teste passou a afirmar o novo
    comportamento com a mesma dureza — nada se move.
    """
    conservacao()
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()
    linhas_antes = db.session.query(Transacao).count()

    resgatar_convite(ana, convite.codigo)
    db.session.commit()

    assert ana.saldo == Decimal("0.00")
    assert bc.saldo == SUPPLY_INICIAL
    assert db.session.query(Transacao).count() == linhas_antes
    db.session.refresh(convite)
    assert convite.resgatado is True
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

    assert ana.saldo == Decimal("0.00")
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

    resgatados = [c for c in contas if c.convite]
    assert len(resgatados) == 1, "um código, uma conta"
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


def test_conta_repetida_nao_queima_o_convite(app, bc):
    """Se o cadastro falha, o código continua valendo — o savepoint garante.

    Era o teste do 101º aluno, que falhava por falta de saldo não emitido.
    Esse caso deixou de existir com o fim do saque inicial; a falha que
    sobrou, e que importa, é o nome de usuário já ocupado.
    """
    from sqlalchemy.exc import IntegrityError

    from vavacoin.operacoes import cadastrar_por_convite

    conservacao()
    criar_usuario("ana", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()

    with pytest.raises(IntegrityError):
        cadastrar_por_convite("ana", "senha-boa-123", convite.codigo)
        db.session.commit()
    db.session.rollback()

    db.session.refresh(convite)
    assert convite.resgatado is False
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
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
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
