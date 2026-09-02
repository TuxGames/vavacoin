"""Mines pela tela: o que o jogador vê depois de cada POST.

O bug que originou este arquivo: quem clicava numa mina e o clique chegava
duas vezes (toque duplo no celular, ou o navegador reenviando o POST cuja
resposta se perdeu) via um tabuleiro em branco e travado. A rodada tinha
encerrado na primeira requisição; a segunda caía na tela de rodada nova, sem
o tabuleiro revelado — e a pessoa nunca via onde estava a mina.
"""

import pytest
from conftest import conservacao

from vavacoin.caladinho import criar_casa, rodada_ativa
from vavacoin.extensoes import db
from vavacoin.mines import CASAS
from vavacoin.modelos import RodadaMines


@pytest.fixture
def cassino(app, bc):
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    from vavacoin.operacoes import ajustar_saldo

    ajustar_saldo(conta, "1000.00", "caixa inicial do teste", autoridade=bc)
    db.session.commit()
    return conta


@pytest.fixture
def jogador(app, bc, nova_pessoa):
    return nova_pessoa(nome="tux", com_convite=True, saldo="100.00")


def _entrar(app, conta, senha="senha-boa-123"):
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": conta.nome_usuario, "senha": senha},
        follow_redirects=True,
    )
    return cliente


def _comecar(cliente, aposta="5.00", minas=1):
    return cliente.post(
        "/caladinho/mines/comecar",
        data={"aposta": aposta, "minas": str(minas)},
        follow_redirects=True,
    )


def _mina_e_segura(jogador):
    rodada = rodada_ativa(jogador)
    minas = rodada.casas_com_mina
    segura = next(c for c in range(CASAS) if c not in minas)
    return minas[0], segura


def test_clique_duplicado_na_mina_mostra_o_tabuleiro_das_duas_vezes(
    app, bc, cassino, jogador
):
    """O segundo POST do mesmo clique não pode cair na tela de rodada nova."""
    conservacao()
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    mina, _ = _mina_e_segura(jogador)

    primeiro = cliente.post(
        "/caladinho/mines/revelar", data={"casa": mina}, follow_redirects=True
    )
    segundo = cliente.post(
        "/caladinho/mines/revelar", data={"casa": mina}, follow_redirects=True
    )

    assert "cal-mina" in primeiro.get_data(as_text=True)
    corpo = segundo.get_data(as_text=True)
    assert "cal-mina" in corpo, "o reenvio do clique perdeu o tabuleiro revelado"
    assert "Jogar de novo" in corpo
    conservacao()


def test_retirar_duas_vezes_mostra_o_resultado_das_duas_vezes(
    app, bc, cassino, jogador
):
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    _, segura = _mina_e_segura(jogador)
    cliente.post("/caladinho/mines/revelar", data={"casa": segura})

    cliente.post("/caladinho/mines/retirar", follow_redirects=True)
    segundo = cliente.post("/caladinho/mines/retirar", follow_redirects=True)

    assert "Jogar de novo" in segundo.get_data(as_text=True)
    conservacao()


def test_voltar_ao_mines_depois_de_perder_retoma_o_resultado(
    app, bc, cassino, jogador
):
    """Recarregar /mines sem parâmetro não pode virar tabuleiro em branco."""
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    mina, _ = _mina_e_segura(jogador)
    cliente.post("/caladinho/mines/revelar", data={"casa": mina})

    corpo = cliente.get("/caladinho/mines").get_data(as_text=True)
    assert "cal-mina" in corpo
    assert "Jogar de novo" in corpo


def test_jogar_de_novo_leva_ao_formulario_de_aposta(app, bc, cassino, jogador):
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    mina, _ = _mina_e_segura(jogador)
    cliente.post("/caladinho/mines/revelar", data={"casa": mina})

    corpo = cliente.get("/caladinho/mines?nova=1").get_data(as_text=True)
    assert "Começar" in corpo
    assert "cal-mina" not in corpo


def test_rodada_ativa_tem_precedencia_sobre_a_encerrada(app, bc, cassino, jogador):
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    mina, _ = _mina_e_segura(jogador)
    cliente.post("/caladinho/mines/revelar", data={"casa": mina})
    _comecar(cliente, aposta="3.00", minas=2)

    corpo = cliente.get("/caladinho/mines").get_data(as_text=True)
    assert "cal-mina" not in corpo
    assert rodada_ativa(jogador) is not None


def test_nenhuma_rodada_encerrada_mostra_o_formulario(app, bc, cassino, jogador):
    cliente = _entrar(app, jogador)
    corpo = cliente.get("/caladinho/mines").get_data(as_text=True)
    assert "Começar" in corpo


def test_rodada_de_outro_jogador_nao_aparece(app, bc, cassino, jogador, nova_pessoa):
    outro = nova_pessoa(nome="zeca", com_convite=True, saldo="50.00")
    cliente = _entrar(app, jogador)
    _comecar(cliente)
    mina, _ = _mina_e_segura(jogador)
    cliente.post("/caladinho/mines/revelar", data={"casa": mina})
    alheia = db.session.execute(db.select(RodadaMines.id)).scalars().first()

    resposta = _entrar(app, outro).get(f"/caladinho/mines?rodada={alheia}")
    assert resposta.status_code == 404
