"""O destaque do ranking segue a pessoa logada, não a posição.

Bug visto em produção: o dono, com 88,63 VVC, era o 2º e via o 2º destacado;
com 68,63 virou 3º e o destaque ficou onde estava, na Letícia. A causa era
``.row:nth-child(2) > .posicao`` — uma regra de CSS que pintava a segunda
linha do cartão, e não uma pessoa.

Um teste que só verificasse "existe uma linha destacada" teria passado com o
bug inteiro no ar. Por isso **todo teste daqui põe a pessoa logada fora da 2ª
posição** e exige que o destaque a acompanhe.
"""

import re

import pytest

from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import CHAVE_REINOS_VISIVEIS, definir_config
from vavacoin.ranking import eh_voce
from vavacoin.reinos import criar_reino, entrar_no_reino

SENHA = "senha-boa-123"

#: Cada linha do ranking, com as classes e o conteúdo.
LINHA = re.compile(r'<div class="row row-evt([^"]*)">(.*?)</div>\s*</div>', re.S)


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def turma(app, bc, nova_pessoa):
    """Quatro pessoas com saldos distintos, para posições estáveis."""
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()

    pessoas = {}
    for nome, saldo, exibicao in (
        ("leticia", "500.00", "Leticia"),
        ("arthur", "88.63", "Arthur e Gustavo"),
        ("bruno", "70.00", "Bruno"),
        ("caio", "10.00", "Caio"),
    ):
        pessoa = nova_pessoa(nome=nome, saldo=saldo)
        pessoa.nome_exibicao = exibicao
        pessoas[nome] = pessoa
    db.session.commit()

    for pessoa in pessoas.values():
        entrar_no_reino(reino, pessoa)
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    pessoas["reino"] = reino
    return pessoas


def _entrar(app, nome):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": SENHA}, follow_redirects=True
    )
    return cliente


def _destacadas(corpo):
    """Os nomes das linhas que saíram com a classe ``voce``."""
    return [
        conteudo
        for classes, conteudo in LINHA.findall(corpo)
        if "voce" in classes.split()
    ]


def _nome_em(pedaco, pessoas):
    for chave, pessoa in pessoas.items():
        if chave != "reino" and pessoa.nome_exibicao in pedaco:
            return chave
    return None


def _telas(app, pessoas):
    """As duas telas de ranking, para o mesmo visitante."""
    return {
        "geral": "/ranking",
        "reino": f"/reino/{pessoas['reino'].nome_normalizado}/cidadaos",
    }


# --- a regra ----------------------------------------------------------------


def test_eh_voce_compara_identidade(app, bc, turma):
    assert eh_voce(turma["arthur"], turma["arthur"])
    assert not eh_voce(turma["arthur"], turma["bruno"])


def test_eh_voce_recusa_visitante_sem_login(app, bc, turma):
    class Anonimo:
        is_authenticated = False
        id = None

    assert not eh_voce(turma["arthur"], Anonimo())
    assert not eh_voce(turma["arthur"], None)


# --- o bug, nas duas telas --------------------------------------------------


@pytest.mark.parametrize("tela", ["geral", "reino"])
def test_o_destaque_segue_quem_esta_logado_e_nao_a_posicao(app, bc, turma, tela):
    """O caso exato do print: quem olha é o **3º**, e o destaque é dele.

    Com 70,00 o Bruno é o 3º, e a 2ª posição é de outra pessoa (88,63). É de
    propósito que quem entra não seja o segundo colocado — com o bug, a
    coincidência escondia tudo.
    """
    corpo = _entrar(app, "bruno").get(_telas(app, turma)[tela]).get_data(as_text=True)

    destacadas = _destacadas(corpo)
    assert len(destacadas) == 1
    assert _nome_em(destacadas[0], turma) == "bruno"
    assert "Arthur e Gustavo" not in destacadas[0]


@pytest.mark.parametrize("tela", ["geral", "reino"])
def test_o_segundo_colocado_nao_e_destacado_para_os_outros(app, bc, turma, tela):
    """O bug era exatamente este: a 2ª linha dourada para qualquer visitante."""
    corpo = _entrar(app, "caio").get(_telas(app, turma)[tela]).get_data(as_text=True)

    destacadas = _destacadas(corpo)
    assert len(destacadas) == 1
    assert _nome_em(destacadas[0], turma) == "caio"


@pytest.mark.parametrize("tela", ["geral", "reino"])
def test_perder_posicao_leva_o_destaque_junto(app, bc, turma, tela):
    """O que o dono viu: 2º com 88,63, 3º com 68,63, e o dourado parado.

    Aqui o destaque tem de descer com ele.
    """
    caminho = _telas(app, turma)[tela]
    antes = _entrar(app, "arthur").get(caminho).get_data(as_text=True)
    assert _nome_em(_destacadas(antes)[0], turma) == "arthur"

    turma["arthur"].saldo = turma["arthur"].saldo - 20
    db.session.commit()

    depois = _entrar(app, "arthur").get(caminho).get_data(as_text=True)
    destacadas = _destacadas(depois)
    assert len(destacadas) == 1
    assert _nome_em(destacadas[0], turma) == "arthur"
    # E a 2ª posição, que agora é do Bruno, não ficou destacada.
    assert "Bruno" not in destacadas[0]


@pytest.mark.parametrize("tela", ["geral", "reino"])
def test_quem_escondeu_o_saldo_se_acha_na_parte_de_baixo(app, bc, turma, tela):
    """Lá não existe posição nenhuma, e a pessoa continua precisando se ver."""
    turma["bruno"].saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "bruno").get(_telas(app, turma)[tela]).get_data(as_text=True)

    destacadas = _destacadas(corpo)
    assert len(destacadas) == 1
    assert _nome_em(destacadas[0], turma) == "bruno"


@pytest.mark.parametrize("tela", ["geral", "reino"])
def test_ninguem_destacado_quando_quem_ve_nao_esta_na_lista(app, bc, turma, tela):
    """O Banco Central não é gente do ranking: nenhuma linha é dele."""
    bc.definir_senha("senha-do-painel")
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": "senha-do-painel"},
        follow_redirects=True,
    )

    corpo = cliente.get(_telas(app, turma)[tela]).get_data(as_text=True)

    assert _destacadas(corpo) == []


def test_as_duas_telas_destacam_a_mesma_pessoa(app, bc, turma):
    """Uma regra só: o destaque não pode divergir entre as telas.

    Era exatamente aqui que o seletor de CSS falhava — o mesmo
    ``nth-child(2)`` pegava o 1º lugar numa tela e o 2º na outra, porque uma
    delas abre o cartão com um título.
    """
    caminhos = _telas(app, turma)
    cliente = _entrar(app, "bruno")

    do_geral = _destacadas(cliente.get(caminhos["geral"]).get_data(as_text=True))
    do_reino = _destacadas(cliente.get(caminhos["reino"]).get_data(as_text=True))

    assert _nome_em(do_geral[0], turma) == _nome_em(do_reino[0], turma) == "bruno"


def test_o_css_nao_destaca_mais_por_posicao_na_arvore(app):
    """A tranca contra a volta do seletor estrutural.

    Ele não quebra teste nenhum de rota — o HTML sai igual —, então o teste
    precisa olhar o CSS.
    """
    import pathlib

    css = pathlib.Path("vavacoin/static/base.css").read_text(encoding="utf-8")
    # Sem os comentários: o parágrafo que explica o bug cita o seletor pelo
    # nome, e um teste que casasse com ele acusaria a própria explicação.
    regras = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    assert "nth-child" not in regras
    assert ".row.voce" in regras
