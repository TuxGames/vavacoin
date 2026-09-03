"""Cidadania por convite ou por pedido — sempre com as duas partes.

Dois caminhos, uma tabela: o reino convida e a pessoa aceita, ou a pessoa pede
e o operador aprova. O invariante é o mesmo dos dois lados e é o princípio do
projeto inteiro: **ninguém entra sozinho e ninguém é colocado à força.**

O que estes testes guardam, além do óbvio:

- a exclusividade é conferida na **confirmação**, não no envio — entre
  convidar e aceitar podem passar dias;
- não há pendência duplicada para a mesma dupla pessoa/reino;
- responder é idempotente dos dois lados.
"""

import pytest
from conftest import conservacao

from vavacoin.erros import SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    PedidoDeCidadania,
    definir_config,
)
from vavacoin.reinos import (
    aceitar_pedido,
    convidar,
    criar_reino,
    definir_operador,
    eh_cidadao,
    pedir_cidadania,
    pendencias_da_pessoa,
    pendencias_do_reino,
    pode_responder,
    recusar_pedido,
    sair_do_reino,
)

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cena(app, bc, nova_pessoa):
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    rei = nova_pessoa(nome="rei", saldo="10.00")
    definir_operador(reino, rei, autoridade=bc)
    db.session.commit()
    ana = nova_pessoa(nome="ana", saldo="10.00")
    bia = nova_pessoa(nome="bia", saldo="10.00")
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    return {"reino": reino, "rei": rei, "ana": ana, "bia": bia}


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


# --- o reino convida, a pessoa aceita ---------------------------------------


def test_convite_nao_da_cidadania_sozinho(app, bc, cena):
    """O convite abre uma conversa; quem fecha é a pessoa."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]

    pedido = convidar(reino, ana, rei)
    db.session.commit()

    assert pedido.pendente
    assert pedido.eh_convite
    assert not eh_cidadao(reino, ana)


def test_a_pessoa_aceita_e_vira_cidada(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    aceitar_pedido(pedido, ana)
    db.session.commit()

    assert eh_cidadao(reino, ana)
    assert db.session.get(PedidoDeCidadania, pedido.id).estado == PedidoDeCidadania.ACEITO


def test_o_operador_nao_aceita_o_convite_que_ele_mesmo_mandou(app, bc, cena):
    """Seria entrar sozinho com um passo a mais."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    assert not pode_responder(pedido, rei)
    with pytest.raises(SemAutoridade):
        aceitar_pedido(pedido, rei)


def test_terceiro_nao_aceita_convite_alheio(app, bc, cena):
    reino, rei, ana, bia = cena["reino"], cena["rei"], cena["ana"], cena["bia"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    with pytest.raises(SemAutoridade):
        aceitar_pedido(pedido, bia)


def test_so_o_operador_convida(app, bc, cena):
    """O cofre não autentica; quem convida é uma pessoa com o papel."""
    reino, ana, bia = cena["reino"], cena["ana"], cena["bia"]

    with pytest.raises(SemAutoridade):
        convidar(reino, bia, ana)


# --- a pessoa pede, o operador aprova ---------------------------------------


def test_pedido_nao_da_cidadania_sozinho(app, bc, cena):
    reino, ana = cena["reino"], cena["ana"]

    pedido = pedir_cidadania(reino, ana)
    db.session.commit()

    assert pedido.pendente
    assert not pedido.eh_convite
    assert not eh_cidadao(reino, ana)


def test_o_operador_aprova_o_pedido(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = pedir_cidadania(reino, ana)
    db.session.commit()

    aceitar_pedido(pedido, rei)
    db.session.commit()

    assert eh_cidadao(reino, ana)


def test_a_pessoa_nao_aprova_o_proprio_pedido(app, bc, cena):
    """O outro lado do mesmo invariante: ninguém entra sozinho."""
    reino, ana = cena["reino"], cena["ana"]
    pedido = pedir_cidadania(reino, ana)
    db.session.commit()

    assert not pode_responder(pedido, ana)
    with pytest.raises(SemAutoridade):
        aceitar_pedido(pedido, ana)


# --- exclusividade na confirmação -------------------------------------------


def test_a_exclusividade_e_conferida_na_confirmacao_nao_no_envio(app, bc, cena):
    """Entre convidar e aceitar podem passar dias.

    Convidar quem já é cidadão de outro reino é legítimo — ela é que decide
    sair de lá ou recusar. O que não pode é a aceitação passar por cima da
    exclusividade.
    """
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    rei2 = cena["bia"]
    definir_operador(outro, rei2, autoridade=bc)
    db.session.commit()

    # Convidada pelos dois enquanto não é de nenhum: os dois convites valem.
    convite_a = convidar(reino, ana, rei)
    convite_b = convidar(outro, ana, rei2)
    db.session.commit()

    aceitar_pedido(convite_a, ana)
    db.session.commit()
    assert eh_cidadao(reino, ana)

    # O segundo convite continua de pé, mas aceitar agora é recusado.
    with pytest.raises(ValorInvalido):
        aceitar_pedido(convite_b, ana)
    db.session.rollback()

    assert not eh_cidadao(outro, ana)
    assert db.session.get(PedidoDeCidadania, convite_b.id).pendente


def test_depois_de_sair_o_convite_pendente_ainda_serve(app, bc, cena):
    """A pendência sobrevive à recusa da exclusividade, para a segunda chance."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    definir_operador(outro, cena["bia"], autoridade=bc)
    db.session.commit()

    convidar(reino, ana, rei)
    convite_b = convidar(outro, ana, cena["bia"])
    db.session.commit()
    aceitar_pedido(pendencias_da_pessoa(ana)[0], ana)
    db.session.commit()

    sair_do_reino(reino, ana)
    db.session.commit()

    aceitar_pedido(db.session.get(PedidoDeCidadania, convite_b.id), ana)
    db.session.commit()

    assert eh_cidadao(outro, ana)


# --- sem pendência duplicada ------------------------------------------------


def test_convidar_duas_vezes_nao_cria_duas_pendencias(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]

    primeiro = convidar(reino, ana, rei)
    db.session.commit()
    segundo = convidar(reino, ana, rei)
    db.session.commit()

    assert primeiro.id == segundo.id
    assert len(pendencias_do_reino(reino)) == 1


def test_pedir_depois_de_ser_convidado_nao_duplica(app, bc, cena):
    """Os dois lados querendo a mesma coisa é uma conversa, não duas."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]

    convite = convidar(reino, ana, rei)
    db.session.commit()
    pedido = pedir_cidadania(reino, ana)
    db.session.commit()

    assert convite.id == pedido.id
    assert len(pendencias_do_reino(reino)) == 1


def test_o_banco_impede_a_pendencia_duplicada(app, bc, cena):
    """Não é a função que segura: é o índice único parcial."""
    from sqlalchemy.exc import IntegrityError

    from vavacoin.modelos import agora

    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    convidar(reino, ana, rei)
    db.session.commit()

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.insert(PedidoDeCidadania).values(
                reino_id=reino.id,
                usuario_id=ana.id,
                origem=PedidoDeCidadania.PESSOA,
                criado_por_id=ana.id,
                criado_em=agora(),
                estado=PedidoDeCidadania.PENDENTE,
            )
        )
        db.session.flush()
    db.session.rollback()


def test_convidar_quem_ja_e_cidadao_e_recusado(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    aceitar_pedido(convidar(reino, ana, rei), ana)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        convidar(reino, ana, rei)


def test_conta_de_sistema_nao_recebe_convite(app, bc, cena):
    from vavacoin.caladinho import criar_casa

    casa = criar_casa(autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        convidar(cena["reino"], casa, cena["rei"])


# --- recusa e idempotência --------------------------------------------------


def test_a_pessoa_recusa_o_convite(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    recusar_pedido(pedido, ana)
    db.session.commit()

    assert db.session.get(PedidoDeCidadania, pedido.id).estado == PedidoDeCidadania.RECUSADO
    assert not eh_cidadao(reino, ana)


def test_o_operador_recusa_o_pedido(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = pedir_cidadania(reino, ana)
    db.session.commit()

    recusar_pedido(pedido, rei)
    db.session.commit()

    assert not eh_cidadao(reino, ana)


def test_quem_enviou_pode_desistir(app, bc, cena):
    """Desistir do convite que se mandou é tão legítimo quanto recusá-lo."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    recusar_pedido(pedido, rei)
    db.session.commit()

    assert db.session.get(PedidoDeCidadania, pedido.id).estado == PedidoDeCidadania.RECUSADO


def test_responder_duas_vezes_e_recusado(app, bc, cena):
    """Guarda de status: o segundo clique não encontra pendência."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    aceitar_pedido(pedido, ana)
    db.session.commit()

    with pytest.raises((ValorInvalido, SemAutoridade)):
        aceitar_pedido(db.session.get(PedidoDeCidadania, pedido.id), ana)
    db.session.rollback()

    assert db.session.query(PedidoDeCidadania).count() == 1


def test_depois_de_recusar_da_para_convidar_de_novo(app, bc, cena):
    """A pendência fechada libera a dupla; recusar não é banimento."""
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    recusar_pedido(convidar(reino, ana, rei), ana)
    db.session.commit()

    novo = convidar(reino, ana, rei)
    db.session.commit()

    assert novo.pendente
    assert db.session.query(PedidoDeCidadania).count() == 2


# --- pela web ---------------------------------------------------------------


def test_convidar_e_aceitar_pela_web(app, bc, cena):
    reino, ana = cena["reino"], cena["ana"]
    antes = conservacao()

    _entrar(app, "rei").post(
        "/reino/alfheim/convidar", data={"pessoa": str(ana.id)}, follow_redirects=True
    )
    db.session.expire_all()
    pedido = db.session.execute(db.select(PedidoDeCidadania)).scalar_one()
    assert pedido.pendente

    _entrar(app, "ana").post(
        f"/reino/pedido/{pedido.id}/aceitar", follow_redirects=True
    )

    db.session.expire_all()
    assert eh_cidadao(reino, db.session.get(type(ana), ana.id))
    assert conservacao() == antes


def test_pedir_e_aprovar_pela_web(app, bc, cena):
    reino, ana = cena["reino"], cena["ana"]

    _entrar(app, "ana").post("/reino/alfheim/pedir", follow_redirects=True)
    db.session.expire_all()
    pedido = db.session.execute(db.select(PedidoDeCidadania)).scalar_one()

    _entrar(app, "rei").post(
        f"/reino/pedido/{pedido.id}/aceitar", follow_redirects=True
    )

    db.session.expire_all()
    assert eh_cidadao(reino, db.session.get(type(ana), ana.id))


def test_terceiro_nao_aceita_pela_web(app, bc, cena):
    reino, rei, ana = cena["reino"], cena["rei"], cena["ana"]
    pedido = convidar(reino, ana, rei)
    db.session.commit()

    _entrar(app, "bia").post(
        f"/reino/pedido/{pedido.id}/aceitar", follow_redirects=True
    )

    db.session.expire_all()
    assert db.session.get(PedidoDeCidadania, pedido.id).pendente
