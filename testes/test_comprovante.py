"""O comprovante: recibo permanente de uma linha do ledger.

Portado do Benbals (``smoke_comprovante.py``), com o que se aplica aqui. O
que ele verificava sobre score congelado não existe no VavaCoin — não há
score. O que ele verificava sobre **acesso** é o coração da tela e está
inteiro: as duas partes e o Banco Central entram, terceiro é barrado.

E, como toda tela que olha dinheiro: **comprovante não move um centavo.**
"""

import pytest
from conftest import conservacao

from vavacoin.auditoria import linhas_extrato
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import Transacao
from vavacoin.operacoes import transferir

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": nome, "senha": senha},
        follow_redirects=True,
    )
    return cliente


@pytest.fixture
def cena(app, bc, nova_pessoa):
    """Ana paga Bia. Carol existe e não tem nada a ver com isso."""
    ana = nova_pessoa(nome="ana", saldo="100.00")
    bia = nova_pessoa(nome="bia", saldo="20.00")
    carol = nova_pessoa(nome="carol", saldo="10.00")

    transferir(ana, bia, "25.00", motivo="explicou a lista")
    db.session.commit()

    transacao = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == "transferencia")
    ).scalar_one()
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    return {"ana": ana, "bia": bia, "carol": carol, "tx": transacao}


# --- acesso: as duas partes e o Banco Central, mais ninguém -----------------


def test_remetente_ve_o_proprio_comprovante(app, cena):
    resposta = _entrar(app, "ana").get(f"/comprovante/{cena['tx'].id}")

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "25.00" in corpo
    assert "explicou a lista" in corpo


def test_destinatario_ve_o_mesmo_comprovante(app, cena):
    resposta = _entrar(app, "bia").get(f"/comprovante/{cena['tx'].id}")

    assert resposta.status_code == 200
    assert "25.00" in resposta.get_data(as_text=True)


def test_banco_central_ve_qualquer_comprovante(app, cena):
    """O BC audita tudo — é o mesmo desenho de acesso do Benbals."""
    resposta = _entrar(app, "banco_central", SENHA_BC).get(
        f"/comprovante/{cena['tx'].id}"
    )

    assert resposta.status_code == 200
    assert "25.00" in resposta.get_data(as_text=True)


def test_terceiro_nao_ve_comprovante_alheio(app, cena):
    """O ponto da tela inteira: quem não é parte não vê quem pagou quem.

    A base é uma turma de colégio. Um comprovante aberto por link diria, para
    qualquer um que adivinhasse um número, que fulano pagou X para sicrano.
    """
    resposta = _entrar(app, "carol").get(
        f"/comprovante/{cena['tx'].id}", follow_redirects=False
    )

    assert resposta.status_code == 302
    corpo = _entrar(app, "carol").get(
        f"/comprovante/{cena['tx'].id}", follow_redirects=True
    ).get_data(as_text=True)
    assert "25.00" not in corpo
    assert "explicou a lista" not in corpo


def test_deslogado_nao_ve_comprovante(app, cena):
    resposta = app.test_client().get(
        f"/comprovante/{cena['tx'].id}", follow_redirects=False
    )

    assert resposta.status_code == 302
    assert "/entrar" in resposta.headers["Location"]


def test_comprovante_inexistente_responde_igual_a_um_negado(app, cena):
    """Mesma resposta para "não existe" e "não é seu".

    Se a tela dissesse a diferença, daria para descobrir por tentativa
    quantas transferências existem e quando aconteceram.
    """
    negado = _entrar(app, "carol").get(
        f"/comprovante/{cena['tx'].id}", follow_redirects=True
    )
    inexistente = _entrar(app, "carol").get(
        "/comprovante/999999", follow_redirects=True
    )

    assert negado.status_code == inexistente.status_code == 200
    assert "Comprovante não encontrado." in negado.get_data(as_text=True)
    assert "Comprovante não encontrado." in inexistente.get_data(as_text=True)


# --- o comprovante não é uma operação ---------------------------------------


def test_abrir_comprovante_nao_move_dinheiro(app, cena):
    """Leitura do ledger, nunca escrita. Vale para todo mundo que abre."""
    antes = conservacao()
    saldo_ana, saldo_bia = cena["ana"].saldo, cena["bia"].saldo
    linhas_antes = db.session.query(Transacao).count()

    for nome, senha in [("ana", SENHA), ("bia", SENHA), ("banco_central", SENHA_BC)]:
        _entrar(app, nome, senha).get(f"/comprovante/{cena['tx'].id}")

    db.session.expire_all()
    assert cena["ana"].saldo == saldo_ana
    assert cena["bia"].saldo == saldo_bia
    assert db.session.query(Transacao).count() == linhas_antes
    assert conservacao() == antes


# --- as duas pontas do recibo -----------------------------------------------


def test_o_comprovante_mostra_as_duas_partes_e_o_numero(app, cena):
    corpo = _entrar(app, "ana").get(
        f"/comprovante/{cena['tx'].id}"
    ).get_data(as_text=True)

    assert "ana" in corpo
    assert "bia" in corpo
    assert "%08d" % cena["tx"].id in corpo  # o número de oito dígitos do Benbals


def test_o_extrato_leva_ao_comprovante_de_cada_linha(app, cena):
    corpo = _entrar(app, "ana").get("/carteira").get_data(as_text=True)

    assert f"/comprovante/{cena['tx'].id}" in corpo
    assert "comprovante" in corpo


def test_toda_linha_do_extrato_tem_comprovante_abrivel(app, cena):
    """Inclusive as sem contraparte: o ajuste do Banco Central que fundou a
    conta não veio de ninguém, e o recibo dele precisa abrir mesmo assim."""
    cliente = _entrar(app, "ana")

    for linha in linhas_extrato(cena["ana"]):
        resposta = cliente.get(f"/comprovante/{linha['id']}")
        assert resposta.status_code == 200, linha


def test_o_ator_do_ajuste_so_aparece_para_o_banco_central(app, bc, cena):
    """Quem mandou fazer é dado de auditoria, não de quem recebeu.

    O ajuste que fundou a conta da ana tem o BC como ator. Ela vê o próprio
    lançamento; quem apertou o botão é informação do painel.
    """
    ajuste = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == "ajuste")
    ).scalars().first()
    assert ajuste is not None and ajuste.ator_id is not None

    da_pessoa = _entrar(app, "ana").get(
        f"/comprovante/{ajuste.id}"
    ).get_data(as_text=True)
    do_painel = _entrar(app, "banco_central", SENHA_BC).get(
        f"/comprovante/{ajuste.id}"
    ).get_data(as_text=True)

    assert "Ator" not in da_pessoa
    assert "Ator" in do_painel
