"""O link de convite: a tela de cadastro com o código no caminho.

O link é **conveniência**, não um segundo mecanismo de entrada. Estes testes
existem para garantir exatamente isso: o que muda é o campo vir preenchido, e
nada mais. Uso único, resgate e conservação de massa continuam sendo os
mesmos, pelo mesmo caminho.
"""

import pytest
from conftest import conservacao

from vavacoin.convites import link_de_convite
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import Convite, Usuario
from vavacoin.operacoes import criar_convite

SENHA = "senha-boa-123"


SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    """Os freios de login são globais; sem zerar, um teste contamina o outro."""
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cliente(app):
    return app.test_client()


@pytest.fixture
def painel(app, bc):
    """Cliente já logado como Banco Central."""
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": SENHA_BC},
        follow_redirects=True,
    )
    return cliente


def _convite(bc, destinatario="Fulano"):
    convite = criar_convite(destinatario=destinatario, autoridade=bc)
    db.session.commit()
    return convite


def _preencher(cliente, codigo, usuario="ana", url="/cadastro"):
    """Envia o formulário de cadastro. O código vai no CAMPO, como sempre."""
    return cliente.post(
        url,
        data={
            "codigo": codigo,
            "nome_usuario": usuario,
            "nome_exibicao": usuario.capitalize(),
            "senha": SENHA,
            "confirmacao": SENHA,
        },
        follow_redirects=True,
    )


def _valor_do_campo_codigo(html):
    """O `value=` do campo de código, ou "" se ele veio vazio."""
    import re

    campo = re.search(r'<input[^>]*id="codigo"[^>]*>', html)
    assert campo, "a tela de cadastro perdeu o campo de código"
    valor = re.search(r'value="([^"]*)"', campo.group(0))
    return valor.group(1) if valor else ""


# --- o link abre a tela com o código no campo -------------------------------


def test_link_valido_abre_o_cadastro_com_o_codigo_preenchido(app, bc, cliente):
    convite = _convite(bc)

    resposta = cliente.get(f"/cadastro/{convite.codigo}")

    assert resposta.status_code == 200
    assert _valor_do_campo_codigo(resposta.get_data(as_text=True)) == convite.codigo


def test_o_campo_continua_editavel(app, bc, cliente):
    """Quem abriu o link errado troca o código na mão e segue.

    O link preenche; quem manda no cadastro é o campo. Se o caminho vencesse
    o formulário, trocar o código na tela não teria efeito nenhum — que é
    exatamente o contrário do que a pessoa espera ao apagar e digitar outro.
    """
    _convite(bc, "Errado")
    certo = _convite(bc, "Certo")

    _preencher(cliente, certo.codigo, url=f"/cadastro/{_convite(bc, 'Outro').codigo}")

    conta = db.session.execute(
        db.select(Usuario).where(Usuario.nome_normalizado == "ana")
    ).scalar_one()
    resgatado = db.session.execute(
        db.select(Convite).where(Convite.usuario_id == conta.id)
    ).scalar_one()
    assert resgatado.codigo == certo.codigo


def test_cadastro_pelo_link_cria_a_conta_com_saldo_zero(app, bc, cliente):
    """O link dá entrada, não dinheiro — igual ao código digitado na mão."""
    convite = _convite(bc)
    antes = conservacao()

    _preencher(cliente, convite.codigo, url=f"/cadastro/{convite.codigo}")

    conta = db.session.execute(
        db.select(Usuario).where(Usuario.nome_normalizado == "ana")
    ).scalar_one()
    assert conta.saldo == 0
    assert conservacao() == antes


# --- link que não vale mais -------------------------------------------------


def test_link_de_convite_ja_resgatado_abre_a_tela_com_o_campo_vazio(app, bc, cliente):
    """Nem 404 seco, nem estouro: a tela abre e a pessoa tenta outro código."""
    convite = _convite(bc)
    _preencher(cliente, convite.codigo)

    # Cliente novo: quem se cadastrou saiu logado, e a tela de cadastro
    # redireciona quem já está dentro. Quem abre o link queimado é outra
    # pessoa, deslogada.
    resposta = app.test_client().get(f"/cadastro/{convite.codigo}")

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert _valor_do_campo_codigo(corpo) == ""
    assert "Convite já usado ou inexistente." in corpo


def test_link_de_codigo_inexistente_nao_explode(app, bc, cliente):
    resposta = cliente.get("/cadastro/codigo-que-nunca-existiu")

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert _valor_do_campo_codigo(corpo) == ""
    assert "Convite já usado ou inexistente." in corpo


def test_link_ja_resgatado_nao_cria_conta(app, bc, cliente):
    """Abrir o link queimado e insistir no envio continua sendo recusado."""
    convite = _convite(bc)
    _preencher(cliente, convite.codigo, usuario="ana")

    # Deslogada, senão a tela redireciona antes de olhar o convite e o teste
    # passaria sem exercitar a recusa.
    _preencher(
        app.test_client(),
        convite.codigo,
        usuario="bia",
        url=f"/cadastro/{convite.codigo}",
    )

    assert (
        db.session.execute(
            db.select(Usuario).where(Usuario.nome_normalizado == "bia")
        ).scalar_one_or_none()
        is None
    )
    conservacao()


def test_o_mesmo_link_duas_vezes_cria_uma_conta_so(app, bc, cliente):
    """A regra de uso único é do convite, e o link não a contorna."""
    convite = _convite(bc)
    url = f"/cadastro/{convite.codigo}"

    _preencher(cliente, convite.codigo, usuario="ana", url=url)
    app.test_client().post(  # cliente novo: a primeira conta ficou logada
        url,
        data={
            "codigo": convite.codigo,
            "nome_usuario": "bia",
            "nome_exibicao": "Bia",
            "senha": SENHA,
            "confirmacao": SENHA,
        },
        follow_redirects=True,
    )

    resgates = db.session.execute(
        db.select(Convite).where(Convite.codigo == convite.codigo)
    ).scalar_one()
    assert resgates.resgatado
    assert db.session.query(Usuario).count() == 2  # o Banco Central e a ana
    conservacao()


# --- o caminho antigo não regrediu ------------------------------------------


def test_digitar_o_codigo_na_mao_continua_funcionando(app, bc, cliente):
    """O link não substituiu nada: quem recebeu o código solto entra igual."""
    convite = _convite(bc)

    resposta = cliente.get("/cadastro")
    assert resposta.status_code == 200
    assert _valor_do_campo_codigo(resposta.get_data(as_text=True)) == ""

    _preencher(cliente, convite.codigo)

    conta = db.session.execute(
        db.select(Usuario).where(Usuario.nome_normalizado == "ana")
    ).scalar_one()
    assert conta.saldo == 0
    conservacao()


# --- a montagem do link -----------------------------------------------------


def test_link_aponta_para_a_rota_de_cadastro(app, bc):
    """Sem requisição, o endereço sai da configuração — nunca do código."""
    app.config["BASE_URL"] = "https://exemplo.invalido"

    with app.app_context():
        assert (
            link_de_convite("abc123") == "https://exemplo.invalido/cadastro/abc123"
        )


def test_dentro_da_requisicao_o_link_usa_o_host_de_verdade(app, bc):
    """O host por onde a pessoa navega vence a configuração.

    Assim mudar o site de endereço muda o link junto, sem ninguém lembrar de
    trocar uma variável de ambiente.
    """
    app.config["BASE_URL"] = "https://errado.invalido"

    with app.test_request_context(base_url="https://vavacoin.exemplo"):
        assert (
            link_de_convite("abc123") == "https://vavacoin.exemplo/cadastro/abc123"
        )


def test_o_painel_mostra_o_link_de_cada_convite_livre(app, bc, painel):
    convite = _convite(bc, "Fulano")

    corpo = painel.get("/painel/").get_data(as_text=True)

    assert f"/cadastro/{convite.codigo}" in corpo
    assert convite.codigo in corpo  # o código continua à vista
    assert 'data-copiar=' in corpo
