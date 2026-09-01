"""Nome de usuário com maiúscula e acento.

A pessoa escreve como quiser; o sistema compara a forma normalizada. São dois
problemas diferentes e os dois têm teste aqui: **unicidade** (``João`` e
``joao`` não podem virar duas contas) e **login** (quem se cadastrou com
acento entra digitando sem).
"""

from decimal import Decimal

import pytest
from conftest import conservacao
from sqlalchemy.exc import IntegrityError

from vavacoin.constantes import SAQUE_INICIAL
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import Usuario, buscar_usuario
from vavacoin.nomes import normalizar_nome
from vavacoin.operacoes import criar_convite, criar_usuario

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


# --- a função de normalização ----------------------------------------------


@pytest.mark.parametrize(
    "escrito,esperado",
    [
        ("João", "joao"),
        ("JOÃO", "joao"),
        ("joao", "joao"),
        ("Ana Clara", "ana clara"),
        ("  Ana   Clara  ", "ana clara"),
        ("Conceição", "conceicao"),
        ("Muñoz", "munoz"),
        ("Über", "uber"),
        ("Tux123", "tux123"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalizar_nome(escrito, esperado):
    assert normalizar_nome(escrito) == esperado


# --- unicidade --------------------------------------------------------------


def test_joao_e_joao_sao_a_mesma_conta(app, bc):
    """A regra central: acento e caixa não criam gente nova."""
    criar_usuario("João", SENHA, autoridade=bc)
    db.session.commit()

    with pytest.raises(IntegrityError):
        criar_usuario("joao", SENHA, autoridade=bc)
        db.session.commit()
    db.session.rollback()

    assert db.session.query(Usuario).filter_by(nome_normalizado="joao").count() == 1


@pytest.mark.parametrize("segundo", ["JOAO", "Joao", "joão", "JoÃo"])
def test_variacoes_do_mesmo_nome_colidem(app, bc, segundo):
    criar_usuario("João", SENHA, autoridade=bc)
    db.session.commit()

    with pytest.raises(IntegrityError):
        criar_usuario(segundo, SENHA, autoridade=bc)
        db.session.commit()
    db.session.rollback()


def test_nomes_de_fato_diferentes_convivem(app, bc):
    criar_usuario("João", SENHA, autoridade=bc)
    criar_usuario("Joana", SENHA, autoridade=bc)
    db.session.commit()
    assert db.session.query(Usuario).count() == 3  # os dois mais o BC


# --- exibição ---------------------------------------------------------------


def test_guarda_como_a_pessoa_escreveu(app, bc):
    """O acento não some da tela — some só da comparação."""
    joao = criar_usuario("João", SENHA, autoridade=bc)
    db.session.commit()

    assert joao.nome_usuario == "João"
    assert joao.nome_normalizado == "joao"


def test_definir_nome_recusa_nome_que_normaliza_para_vazio(app, bc):
    usuario = Usuario(nome_exibicao="x")
    for vazio in ["", "   ", None]:
        with pytest.raises(ValueError):
            usuario.definir_nome(vazio)


# --- busca e login ----------------------------------------------------------


@pytest.mark.parametrize("digitado", ["João", "joao", "JOÃO", "  joao  ", "JoAo"])
def test_busca_acha_de_qualquer_jeito(app, bc, digitado):
    criado = criar_usuario("João", SENHA, autoridade=bc)
    db.session.commit()
    assert buscar_usuario(digitado).id == criado.id


def test_busca_de_quem_nao_existe(app, bc):
    assert buscar_usuario("fantasma") is None
    assert buscar_usuario("") is None
    assert buscar_usuario(None) is None


def test_login_com_acento_e_sem_acento(app, bc):
    """Quem se cadastrou como "João" entra digitando "joao" no celular."""
    criar_usuario("João", SENHA, autoridade=bc)
    db.session.commit()
    cliente = app.test_client()

    resposta = cliente.post(
        "/entrar", data={"nome_usuario": "joao", "senha": SENHA}, follow_redirects=True
    )
    assert resposta.status_code == 200
    assert cliente.get("/carteira").status_code == 200

    cliente.post("/sair", follow_redirects=True)
    resposta = cliente.post(
        "/entrar", data={"nome_usuario": "JOÃO", "senha": SENHA}, follow_redirects=True
    )
    assert resposta.status_code == 200


def test_cadastro_pela_web_aceita_maiuscula_e_acento(app, bc):
    conservacao()
    codigo = criar_convite(destinatario="João", autoridade=bc).codigo
    db.session.commit()
    cliente = app.test_client()

    resposta = cliente.post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": "João",
            "nome_exibicao": "João",
            "senha": SENHA,
            "confirmacao": SENHA,
        },
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    joao = buscar_usuario("joao")
    assert joao is not None
    assert joao.nome_usuario == "João"
    assert joao.saldo == SAQUE_INICIAL
    conservacao()


def test_cadastro_recusa_nome_que_ja_existe_com_outra_grafia(app, bc):
    conservacao()
    criar_usuario("João", SENHA, autoridade=bc)
    codigo = criar_convite(destinatario="outro", autoridade=bc).codigo
    db.session.commit()

    resposta = app.test_client().post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": "JOAO",
            "nome_exibicao": "Outro João",
            "senha": SENHA,
            "confirmacao": SENHA,
        },
    )

    assert resposta.status_code == 409
    assert db.session.query(Usuario).filter_by(nome_normalizado="joao").count() == 1
    conservacao()


def test_nome_com_espaco_e_recusado_pelo_formulario(app, bc):
    """Espaço fica de fora para o nome caber numa URL sem escape."""
    codigo = criar_convite(destinatario="x", autoridade=bc).codigo
    db.session.commit()

    resposta = app.test_client().post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": "ana clara",
            "nome_exibicao": "Ana Clara",
            "senha": SENHA,
            "confirmacao": SENHA,
        },
    )
    assert resposta.status_code == 200  # reexibe o formulário com erro
    assert buscar_usuario("ana clara") is None


# --- transferência ----------------------------------------------------------


def test_transferir_para_quem_tem_acento_no_nome(app, bc):
    """O caso que aparece na vida real: manda-se sem acento, chega certo."""
    joao = criar_usuario("João", SENHA, autoridade=bc)
    ana = criar_usuario("Ana", SENHA, autoridade=bc)
    codigo = criar_convite(destinatario="Ana", autoridade=bc).codigo
    db.session.commit()
    from vavacoin.operacoes import resgatar_convite

    resgatar_convite(ana, codigo)
    db.session.commit()
    conservacao()

    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": "ana", "senha": SENHA}, follow_redirects=True
    )
    revisao = cliente.post(
        "/transferir",
        data={"destinatario": "joao", "valor": "10.00", "motivo": "sem acento"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "João" in revisao
    import re

    token = re.search(r'name="token"[^>]*value="([^"]+)"', revisao).group(1)
    cliente.post("/transferir/confirmar", data={"token": token})

    db.session.expire_all()
    assert db.session.get(Usuario, joao.id).saldo == Decimal("10.00")
    conservacao()
