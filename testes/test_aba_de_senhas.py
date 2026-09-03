"""A aba de senhas: só o Banco Central, e todo acesso registrado.

Não é poder novo — o painel já troca senha de qualquer conta. É a mesma
informação numa página própria, fora do caminho, porque informação reunida
convida a ser aberta sem motivo.

**O que esta tela não faz:** mostrar a senha que a pessoa escolheu. O projeto
guarda ``senha_hash`` (bcrypt) e nada mais, e bcrypt não volta. O teste
``test_a_tela_nao_mostra_senha_porque_nao_ha_senha_guardada`` existe para que
isso fique dito no lugar onde alguém iria procurar.
"""

import pytest

from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    RegistroAdministrativo,
    Usuario,
    definir_config,
)
from vavacoin.reinos import criar_reino, definir_operador

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cena(app, bc, nova_pessoa):
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    rei = nova_pessoa(nome="rei", saldo="10.00")
    definir_operador(reino, rei, autoridade=bc)
    db.session.commit()
    ana = nova_pessoa(nome="ana", saldo="10.00")
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    return {"reino": reino, "rei": rei, "ana": ana}


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


# --- quem entra -------------------------------------------------------------


def test_o_banco_central_abre_a_aba(app, bc, cena):
    resposta = _entrar(app, "banco_central", SENHA_BC).get("/painel/senhas")

    assert resposta.status_code == 200
    assert "ana" in resposta.get_data(as_text=True)


def test_operador_de_reino_nao_abre_a_aba(app, bc, cena):
    """Ser rei de um reino não é god mode.

    O portão é o do painel inteiro (``eh_admin``), e ``eh_admin`` é só o Banco
    Central — nem operador de reino, nem dono de cassino.
    """
    resposta = _entrar(app, "rei").get("/painel/senhas")

    assert resposta.status_code == 403


def test_pessoa_comum_nao_abre_a_aba(app, bc, cena):
    assert _entrar(app, "ana").get("/painel/senhas").status_code == 403


def test_dono_do_cassino_nao_abre_a_aba(app, bc, cena, nova_pessoa):
    from vavacoin.caladinho import criar_casa, definir_dono

    criar_casa(autoridade=bc)
    db.session.commit()
    gustavo = nova_pessoa(nome="gustavo", saldo="10.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    assert _entrar(app, "gustavo").get("/painel/senhas").status_code == 403


def test_deslogado_nao_abre_a_aba(app, bc, cena):
    assert app.test_client().get("/painel/senhas").status_code in (302, 403)


# --- o acesso fica registrado -----------------------------------------------


def test_todo_acesso_vai_para_o_diario(app, bc, cena):
    """Ler senha de gente é poder, e poder sem rastro não existe aqui."""
    _entrar(app, "banco_central", SENHA_BC).get("/painel/senhas")

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.acao == "senhas")
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert registro is not None
    assert registro.ator_id == bc.id
    assert registro.criado_em is not None


def test_cada_abertura_gera_um_registro(app, bc, cena):
    cliente = _entrar(app, "banco_central", SENHA_BC)
    cliente.get("/painel/senhas")
    cliente.get("/painel/senhas")

    quantos = db.session.query(RegistroAdministrativo).filter_by(acao="senhas").count()
    assert quantos == 2


def test_acesso_negado_nao_registra(app, bc, cena):
    """Só o que aconteceu entra no diário."""
    _entrar(app, "rei").get("/painel/senhas")

    assert db.session.query(RegistroAdministrativo).filter_by(acao="senhas").count() == 0


# --- o que a tela mostra, e o que não tem como mostrar ----------------------


def test_a_tela_nao_mostra_senha_porque_nao_ha_senha_guardada(app, bc, cena):
    """O pedido era "ver as senhas", e isso é impossível neste banco.

    ``Usuario`` guarda só ``senha_hash``, que é bcrypt — função de mão única.
    O ``CLAUDE.md`` registra "texto puro" como decisão, mas o código nunca
    implementou isso. A tela mostra se a conta TEM senha e deixa trocá-la;
    recuperar a escolhida não é questão de tela, é de aritmética.
    """
    corpo = _entrar(app, "banco_central", SENHA_BC).get("/painel/senhas").get_data(
        as_text=True
    )

    assert SENHA not in corpo, "a senha em claro não existe para ser mostrada"
    hash_da_ana = db.session.get(Usuario, cena["ana"].id).senha_hash
    assert hash_da_ana.startswith("$2")  # bcrypt
    assert hash_da_ana not in corpo, "e o hash também não vai para a tela"
    assert "com senha" in corpo


def test_a_aba_troca_a_senha_de_quem_esqueceu(app, bc, cena):
    """O que dá para fazer por quem perdeu a senha: trocar."""
    ana = cena["ana"]
    antes = db.session.get(Usuario, ana.id).senha_hash
    cliente = _entrar(app, "banco_central", SENHA_BC)

    cliente.post(
        f"/painel/conta/{ana.id}",
        data={"saldo": str(ana.saldo), "senha": "outra-senha-boa"},
        follow_redirects=True,
    )

    db.session.expire_all()
    depois = db.session.get(Usuario, ana.id).senha_hash
    assert depois != antes
    assert db.session.get(Usuario, ana.id).verificar_senha("outra-senha-boa")


def test_a_aba_nao_tem_link_no_menu(app, bc, cena):
    """Fora do caminho de propósito: quem chega, chega sabendo."""
    corpo = _entrar(app, "banco_central", SENHA_BC).get("/").get_data(as_text=True)

    assert "/painel/senhas" not in corpo
