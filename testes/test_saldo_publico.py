"""Saldo público por pessoa, e a tabela de cidadãos do reino.

**Opt-out:** a conta nasce com o saldo visível e a pessoa esconde no perfil.
É decisão do dono, e é o inverso do ranking antigo — que era opt-in.

O teste que mais importa aqui não é nenhum caso isolado: é
``test_a_regra_e_uma_so``. A preferência vale em **todo lugar** que mostra
saldo de outra pessoa, e o jeito de garantir isso é existir uma função só.
Regra por tela é como duas telas começam a discordar sobre a mesma escolha da
pessoa.
"""

import pytest

from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import CHAVE_REINOS_VISIVEIS, Usuario, definir_config
from vavacoin.reinos import criar_reino, definir_operador, entrar_no_reino

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


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

    ana = nova_pessoa(nome="ana", saldo="123.45")
    bia = nova_pessoa(nome="bia", saldo="678.90")
    for pessoa in (ana, bia, rei):
        entrar_no_reino(reino, pessoa) if pessoa is not rei else None
    entrar_no_reino(reino, rei)
    db.session.commit()

    # Nomes de exibição distintos: "rei" sozinho é substring de "reino" e
    # acharia a palavra na URL da página em vez da linha da tabela.
    ana.nome_exibicao = "Aninha"
    bia.nome_exibicao = "Bianca"
    rei.nome_exibicao = "Zulmira"

    definir_config(CHAVE_REINOS_VISIVEIS, True)
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    return {"reino": reino, "rei": rei, "ana": ana, "bia": bia}


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


# --- a preferência ----------------------------------------------------------


def test_conta_nova_nasce_publica(app, bc, nova_pessoa):
    """Opt-out: nasce visível, e quem quiser esconde."""
    pessoa = nova_pessoa(nome="nova", saldo="10.00")
    db.session.commit()

    assert pessoa.saldo_publico is True


def test_a_pessoa_esconde_o_proprio_saldo(app, bc, cena):
    ana = cena["ana"]
    cliente = _entrar(app, "ana")

    cliente.post("/perfil/saldo-publico", data={}, follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo_publico is False


def test_a_pessoa_volta_a_mostrar(app, bc, cena):
    ana = cena["ana"]
    ana.saldo_publico = False
    db.session.commit()
    cliente = _entrar(app, "ana")

    cliente.post(
        "/perfil/saldo-publico", data={"publico": "y"}, follow_redirects=True
    )

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo_publico is True


def test_ninguem_esconde_o_saldo_alheio(app, bc, cena):
    """O interruptor é sobre a própria conta, e só.

    A rota nem recebe de quem — mexe em ``current_user``. Bia mexendo no
    próprio não pode encostar na ana.
    """
    ana, bia = cena["ana"], cena["bia"]
    _entrar(app, "bia").post("/perfil/saldo-publico", data={}, follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo_publico is True
    assert db.session.get(Usuario, bia.id).saldo_publico is False


# --- a regra única ----------------------------------------------------------


def test_a_regra_e_uma_so(app, bc, cena):
    """Todo lugar que mostra saldo alheio passa por ``saldo_visivel_para``.

    Os quatro casos da regra, num teste só, porque é uma função só:
    a própria pessoa sempre vê; o Banco Central sempre vê; terceiro vê se está
    público; e fora do login ninguém vê.
    """
    ana, bia = cena["ana"], cena["bia"]

    assert ana.saldo_visivel_para(ana) is True
    assert ana.saldo_visivel_para(bc) is True
    assert ana.saldo_visivel_para(bia) is True
    assert ana.saldo_visivel_para(None) is False

    ana.saldo_publico = False
    db.session.commit()

    assert ana.saldo_visivel_para(ana) is True, "a própria pessoa sempre vê"
    assert ana.saldo_visivel_para(bc) is True, "o BC audita tudo"
    assert ana.saldo_visivel_para(bia) is False, "terceiro não vê"
    assert ana.saldo_visivel_para(None) is False


def test_quem_escondeu_ainda_ve_o_proprio_saldo_na_carteira(app, bc, cena):
    ana = cena["ana"]
    ana.saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "ana").get("/carteira").get_data(as_text=True)

    assert "123.45" in corpo


# --- a tabela de cidadãos ---------------------------------------------------


def test_a_tabela_mostra_nome_e_saldo_de_quem_esta_publico(app, bc, cena):
    corpo = _entrar(app, "ana").get("/reino/alfheim/cidadaos").get_data(as_text=True)

    assert "bia" in corpo
    assert "678.90" in corpo


def test_quem_escondeu_aparece_sem_o_numero(app, bc, cena):
    """Some o saldo, não a pessoa.

    Sumir da lista revelaria a escolha pela ausência, que é o contrário de
    esconder.
    """
    bia = cena["bia"]
    bia.saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "ana").get("/reino/alfheim/cidadaos").get_data(as_text=True)

    assert "bia" in corpo, "o nome continua na tabela"
    assert "678.90" not in corpo, "o número não"


def test_a_tabela_exige_login(app, bc, cena):
    """Público entre quem tem conta, não aberto e indexável na web."""
    resposta = app.test_client().get(
        "/reino/alfheim/cidadaos", follow_redirects=False
    )

    assert resposta.status_code == 302
    assert "/entrar" in resposta.headers["Location"]


def test_contas_de_sistema_ficam_fora_da_tabela(app, bc, cena):
    """Cofre, Banco Central e cassino não são cidadãos de reino nenhum."""
    corpo = _entrar(app, "ana").get("/reino/alfheim/cidadaos").get_data(as_text=True)

    assert cena["reino"].cofre.nome_usuario not in corpo
    assert "banco_central" not in corpo


def test_a_tabela_e_alfabetica_e_nao_um_ranking(app, bc, cena):
    """Ordenar por dinheiro faria disto um ranking, que ninguém pediu.

    A ordem é por nome de usuário: ana, bia, rei. A bia tem cinco vezes o
    saldo da ana e mesmo assim vem depois; a rei tem o menor de todos e vem
    por último.
    """
    corpo = _entrar(app, "ana").get("/reino/alfheim/cidadaos").get_data(as_text=True)

    assert corpo.index("Aninha") < corpo.index("Bianca") < corpo.index("Zulmira")


# --- a mesa do operador usa a mesma regra -----------------------------------


def test_o_operador_nao_e_excecao(app, bc, cena):
    """A preferência vale para ele como para qualquer um.

    Ele cobra por alíquota, não precisa do saldo de cada um — e "uma regra
    só" perderia o sentido se a tela do operador tivesse a dela.
    """
    bia = cena["bia"]
    bia.saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "rei").get("/reino/alfheim/operar").get_data(as_text=True)

    assert "bia" in corpo
    assert "678.90" not in corpo


def test_o_banco_central_continua_vendo_tudo_no_painel(app, bc, cena):
    """God mode não muda: é o poder dele, e já está registrado como tal."""
    ana = cena["ana"]
    ana.saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "banco_central", SENHA_BC).get("/painel/").get_data(
        as_text=True
    )

    assert "123.45" in corpo
