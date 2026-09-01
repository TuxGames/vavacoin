"""O Banco Central não tem porta de entrada.

O BC é conta de dinheiro e poder administrativo ao mesmo tempo: quem entrar
nele é dono de tudo. No Benbals as contas de sistema autenticam com senha em
texto puro e dá para esvaziar o caixa de uma empresa. Aqui cada camada que
poderia virar porta é testada fechada — barato agora, caro depois.
"""

from decimal import Decimal

import pytest
from conftest import conservacao
from flask_login import login_user

from vavacoin.autoridade import exigir_banco_central
from vavacoin.erros import BancoCentralNaoAutentica, SemAutoridade
from vavacoin.extensoes import db
from vavacoin.modelos import Usuario
from vavacoin.operacoes import criar_convite, criar_usuario, resetar_economia


# --- o BC não autentica -----------------------------------------------------


def test_banco_central_nao_tem_senha(app, bc):
    assert bc.senha_hash is None
    assert bc.verificar_senha("") is False
    assert bc.verificar_senha("banco_central") is False


def test_nao_da_para_definir_senha_no_banco_central(app, bc):
    with pytest.raises(ValueError):
        bc.definir_senha("qualquer-senha")
    db.session.rollback()
    assert bc.senha_hash is None


def test_banco_nao_aceita_hash_no_banco_central(app, bc):
    """Última rede: nem por UPDATE direto o BC ganha senha."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.update(Usuario).where(Usuario.id == bc.id).values(senha_hash="$2b$x")
        )
    db.session.rollback()
    conservacao()


def test_promover_conta_com_senha_a_banco_central_e_recusado(app, bc, nova_pessoa):
    """Não dá para transformar uma conta que loga no Banco Central."""
    from sqlalchemy.exc import IntegrityError

    ana = nova_pessoa()
    with pytest.raises(IntegrityError):
        db.session.execute(
            db.update(Usuario).where(Usuario.id == ana.id).values(eh_banco_central=True)
        )
    db.session.rollback()
    conservacao()


def test_banco_central_nao_e_is_active(app, bc):
    assert bc.is_active is False


def test_get_id_do_banco_central_estoura(app, bc):
    """A trava mais interna: `login_user(bc, force=True)` também não passa."""
    with pytest.raises(BancoCentralNaoAutentica):
        bc.get_id()


def test_login_user_recusa_o_banco_central(app, bc, nova_pessoa):
    """Nem pela via normal, nem forçado."""
    ana = nova_pessoa()
    with app.test_request_context():
        assert login_user(ana) is True

    with app.test_request_context():
        assert login_user(bc) is False

    with app.test_request_context():
        with pytest.raises(BancoCentralNaoAutentica):
            login_user(bc, force=True)


def test_user_loader_nunca_devolve_o_banco_central(app, bc, nova_pessoa):
    """Mesmo que um cookie de sessão traga o id do BC, ninguém entra."""
    ana = nova_pessoa()
    carregar = app.login_manager._user_callback
    with app.test_request_context():
        assert carregar(str(bc.id)) is None
        assert carregar(str(ana.id)).id == ana.id


# --- os poderes do BC são pedidos explicitamente ----------------------------


def test_operacoes_privilegiadas_recusam_ausencia_de_autoridade(app, bc):
    """Conseguir chamar a função não é o mesmo que poder chamá-la."""
    conservacao()
    with pytest.raises(SemAutoridade):
        criar_usuario("ninguem", "senha-boa-123")
    db.session.rollback()
    with pytest.raises(SemAutoridade):
        criar_convite(destinatario="Ninguém")
    db.session.rollback()
    with pytest.raises(SemAutoridade):
        resetar_economia()
    db.session.rollback()
    conservacao()


def test_jogador_nao_exerce_poder_do_banco_central(app, bc, nova_pessoa):
    """Um aluno com conta não emite convite nem reseta a economia."""
    ana = nova_pessoa(com_convite=True)
    conservacao()

    with pytest.raises(SemAutoridade):
        criar_convite(destinatario="pra mim mesmo", autoridade=ana)
    db.session.rollback()
    with pytest.raises(SemAutoridade):
        criar_usuario("laranja", "senha-boa-123", autoridade=ana)
    db.session.rollback()
    with pytest.raises(SemAutoridade):
        resetar_economia(autoridade=ana)
    db.session.rollback()

    assert ana.saldo == Decimal("50.00")
    conservacao()


def test_exigir_banco_central_aceita_id_e_objeto(app, bc):
    assert exigir_banco_central(bc).id == bc.id
    assert exigir_banco_central(bc.id).id == bc.id
    with pytest.raises(SemAutoridade):
        exigir_banco_central("banco_central")


def test_banco_central_nao_resgata_convite(app, bc):
    """O BC não é jogador: não saca os 50 para si."""
    conservacao()
    convite = criar_convite(destinatario="tentativa", autoridade=bc)
    db.session.commit()

    from vavacoin.erros import ValorInvalido
    from vavacoin.operacoes import resgatar_convite

    with pytest.raises(ValorInvalido):
        resgatar_convite(bc, convite.codigo)
    db.session.rollback()
    conservacao()


# --- o reset não poupa ninguém ---------------------------------------------


def test_reset_recolhe_do_dono_do_cassino_tambem(app, bc, nova_pessoa):
    """Sem exceção: sem temporada, o reset é o que desfaz a concentração."""
    dono = nova_pessoa(com_convite=True)
    otario = nova_pessoa(com_convite=True)
    from vavacoin.moeda import mover

    mover(otario, dono, "50.00", motivo="mines")
    db.session.commit()
    assert dono.saldo == Decimal("100.00")
    assert otario.saldo == Decimal("0.00")
    conservacao()

    resetar_economia(autoridade=bc)
    db.session.commit()

    assert dono.saldo == Decimal("50.00")
    assert otario.saldo == Decimal("50.00")
    conservacao()
