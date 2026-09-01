"""A web mínima: entrar por convite, ver a própria conta, transferir.

Todo teste que mexe em dinheiro passa pelo ``conservacao()`` — a web não é
exceção ao invariante, é só mais um caminho até o ``mover()``.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SAQUE_INICIAL, SUPPLY_TOTAL
from vavacoin.extensoes import db
from vavacoin.limite import limitador_login
from vavacoin.modelos import Usuario
from vavacoin.operacoes import criar_convite

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    """O freio de login é global; sem zerar, um teste contamina o outro."""
    limitador_login._falhas.clear()
    yield
    limitador_login._falhas.clear()


@pytest.fixture
def cliente(app):
    return app.test_client()


def _cadastrar(cliente, bc, usuario="ana", senha=SENHA, codigo=None):
    """Faz o caminho real de entrada: convite emitido, cadastro preenchido."""
    if codigo is None:
        codigo = criar_convite(destinatario=usuario, autoridade=bc).codigo
        db.session.commit()
    return cliente.post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": usuario,
            "nome_exibicao": usuario.capitalize(),
            "senha": senha,
            "confirmacao": senha,
        },
        follow_redirects=True,
    )


def _entrar(cliente, usuario="ana", senha=SENHA):
    return cliente.post(
        "/entrar",
        data={"nome_usuario": usuario, "senha": senha},
        follow_redirects=True,
    )


# --- páginas públicas -------------------------------------------------------


def test_inicio_e_publico(app, cliente):
    assert cliente.get("/").status_code == 200


def test_economia_e_publica_e_mostra_agregados(app, bc, nova_pessoa, cliente):
    nova_pessoa(com_convite=True)
    nova_pessoa(com_convite=True)
    db.session.commit()
    conservacao()

    resposta = cliente.get("/economia")
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert str(SUPPLY_TOTAL) in corpo
    assert "100.00" in corpo  # em circulação
    assert "4900.00" in corpo  # saldo do Banco Central, público
    conservacao()


def test_economia_nao_mostra_saldo_de_ninguem(app, bc, nova_pessoa, cliente):
    """Agregados sim; conta de fulano, não."""
    ana = nova_pessoa(nome="ana", com_convite=True)
    from vavacoin.moeda import mover

    bia = nova_pessoa(nome="bia", com_convite=True)
    mover(ana, bia, "13.00")
    db.session.commit()
    conservacao()

    corpo = cliente.get("/economia").get_data(as_text=True)
    assert "ana" not in corpo
    assert "bia" not in corpo
    assert "37.00" not in corpo  # saldo da ana
    assert "63.00" not in corpo  # saldo da bia


# --- cadastro por convite ---------------------------------------------------


def test_cadastro_por_convite_cria_conta_e_saca_50(app, bc, cliente):
    conservacao()
    resposta = _cadastrar(cliente, bc)

    assert resposta.status_code == 200
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    assert bc.saldo == SUPPLY_TOTAL - SAQUE_INICIAL
    conservacao()

    # Já entra logado.
    assert cliente.get("/carteira").status_code == 200


def test_cadastro_sem_convite_valido_nao_cria_conta(app, bc, cliente):
    """E, principalmente, não deixa conta órfã para trás."""
    conservacao()
    resposta = _cadastrar(cliente, bc, codigo="nao-existe")

    assert resposta.status_code == 400
    assert db.session.query(Usuario).count() == 1  # só o Banco Central
    conservacao()


def test_convite_ja_resgatado_nao_serve_de_novo(app, bc, cliente):
    conservacao()
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()
    _cadastrar(cliente, bc, usuario="ana", codigo=convite.codigo)
    conservacao()

    outro = app.test_client()
    resposta = _cadastrar(outro, bc, usuario="bia", codigo=convite.codigo)

    assert resposta.status_code == 400
    assert (
        db.session.execute(
            db.select(Usuario).where(Usuario.nome_usuario == "bia")
        ).scalar_one_or_none()
        is None
    )
    conservacao()


def test_nome_de_usuario_repetido_e_recusado(app, bc, cliente):
    conservacao()
    _cadastrar(cliente, bc, usuario="ana")
    conservacao()

    outro = app.test_client()
    resposta = _cadastrar(outro, bc, usuario="ana")

    assert resposta.status_code == 409
    conservacao()


def test_senhas_diferentes_nao_cadastram(app, bc, cliente):
    conservacao()
    codigo = criar_convite(destinatario="Ana", autoridade=bc).codigo
    db.session.commit()
    cliente.post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": "ana",
            "nome_exibicao": "Ana",
            "senha": SENHA,
            "confirmacao": "outra-coisa-123",
        },
    )
    assert db.session.query(Usuario).count() == 1
    conservacao()


# --- login ------------------------------------------------------------------


def test_login_e_logout(app, bc, cliente):
    _cadastrar(cliente, bc)
    cliente.post("/sair", follow_redirects=True)
    assert cliente.get("/carteira", follow_redirects=False).status_code == 302

    resposta = _entrar(cliente)
    assert resposta.status_code == 200
    assert cliente.get("/carteira").status_code == 200


def test_senha_errada_nao_entra(app, bc, cliente):
    _cadastrar(cliente, bc)
    cliente.post("/sair", follow_redirects=True)

    resposta = _entrar(cliente, senha="chute")
    assert resposta.status_code == 401
    assert cliente.get("/carteira", follow_redirects=False).status_code == 302


def test_banco_central_nao_entra_pela_tela(app, bc, cliente):
    """A porta não existe: sem senha, não há senha que sirva."""
    for tentativa in ["", "banco_central", "senha", SENHA]:
        resposta = cliente.post(
            "/entrar",
            data={"nome_usuario": "banco_central", "senha": tentativa or "x"},
        )
        assert resposta.status_code == 401
    assert cliente.get("/carteira", follow_redirects=False).status_code == 302
    conservacao()


def test_mensagem_de_erro_nao_entrega_quem_tem_conta(app, bc, cliente):
    """Usuário inexistente e senha errada respondem a mesma coisa."""
    _cadastrar(cliente, bc, usuario="ana")
    outro = app.test_client()

    inexistente = outro.post(
        "/entrar", data={"nome_usuario": "ninguem", "senha": "x"}
    ).get_data(as_text=True)
    senha_errada = outro.post(
        "/entrar", data={"nome_usuario": "ana", "senha": "x"}
    ).get_data(as_text=True)

    assert "Usuário ou senha incorretos." in inexistente
    assert "Usuário ou senha incorretos." in senha_errada


def test_rate_limit_no_login(app, bc, cliente):
    """Cinco erros e a porta fecha por um tempo."""
    _cadastrar(cliente, bc, usuario="ana")
    outro = app.test_client()

    for _ in range(5):
        assert (
            outro.post("/entrar", data={"nome_usuario": "ana", "senha": "x"}).status_code
            == 401
        )

    bloqueado = outro.post("/entrar", data={"nome_usuario": "ana", "senha": "x"})
    assert bloqueado.status_code == 429
    assert "Tentativas demais" in bloqueado.get_data(as_text=True)

    # Mesmo com a senha certa: enquanto bloqueado, não passa.
    assert outro.post(
        "/entrar", data={"nome_usuario": "ana", "senha": SENHA}
    ).status_code == 429


def test_login_certo_zera_o_contador(app, bc, cliente):
    _cadastrar(cliente, bc, usuario="ana")
    outro = app.test_client()
    for _ in range(4):
        outro.post("/entrar", data={"nome_usuario": "ana", "senha": "x"})

    assert _entrar(outro, "ana").status_code == 200
    outro.post("/sair", follow_redirects=True)
    for _ in range(4):
        outro.post("/entrar", data={"nome_usuario": "ana", "senha": "x"})
    assert _entrar(outro, "ana").status_code == 200


# --- carteira ---------------------------------------------------------------


def test_rotas_da_carteira_exigem_login(app, cliente):
    for rota in ["/carteira", "/transferir", "/transferir/confirmar"]:
        assert cliente.get(rota, follow_redirects=False).status_code == 302


def test_carteira_mostra_saldo_e_extrato_proprios(app, bc, cliente):
    _cadastrar(cliente, bc, usuario="ana")
    corpo = cliente.get("/carteira").get_data(as_text=True)

    assert "50.00" in corpo
    assert "saque_inicial" in corpo
    assert "banco_central" in corpo
    conservacao()


def test_carteira_nao_mostra_conta_alheia(app, bc, cliente):
    """Não há rota para o saldo de terceiro — e o extrato é só o seu."""
    _cadastrar(cliente, bc, usuario="ana")
    outro = app.test_client()
    _cadastrar(outro, bc, usuario="bia")
    conservacao()

    corpo = cliente.get("/carteira").get_data(as_text=True)
    assert "bia" not in corpo
    assert cliente.get("/carteira/bia").status_code == 404


# --- transferência ----------------------------------------------------------


def _preparar_duas_contas(app, bc, cliente):
    _cadastrar(cliente, bc, usuario="ana")
    outro = app.test_client()
    _cadastrar(outro, bc, usuario="bia")
    conservacao()
    return cliente, outro


def test_transferencia_so_efetiva_depois_de_confirmar(app, bc, cliente):
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)

    revisao = ana_cliente.post(
        "/transferir",
        data={"destinatario": "bia", "valor": "12.50", "motivo": "questão 3"},
        follow_redirects=True,
    )
    corpo = revisao.get_data(as_text=True)
    assert revisao.status_code == 200
    assert "12.50" in corpo
    assert "Bia" in corpo
    assert "questão 3" in corpo

    # Nada saiu ainda: a revisão não move dinheiro.
    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    conservacao()

    ana_cliente.post("/transferir/confirmar", data={"token": _token(corpo)})
    db.session.expire_all()
    bia = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "bia")
    ).scalar_one()
    assert db.session.get(Usuario, ana.id).saldo == Decimal("37.50")
    assert bia.saldo == Decimal("62.50")
    conservacao()


def _token(corpo):
    """Pesca o token de confirmação renderizado na página."""
    import re

    achado = re.search(r'name="token"[^>]*value="([^"]+)"', corpo)
    assert achado, "token de confirmação não apareceu na página"
    return achado.group(1)


def test_confirmar_sem_pendencia_nao_faz_nada(app, bc, cliente):
    _preparar_duas_contas(app, bc, cliente)
    resposta = cliente.post(
        "/transferir/confirmar", data={"token": "inventado"}, follow_redirects=True
    )
    assert resposta.status_code == 200
    assert "Nada para confirmar." in resposta.get_data(as_text=True)
    conservacao()


def test_token_trocado_nao_efetiva(app, bc, cliente):
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    ana_cliente.post(
        "/transferir", data={"destinatario": "bia", "valor": "10.00", "motivo": ""}
    )

    resposta = ana_cliente.post(
        "/transferir/confirmar", data={"token": "outro-token"}, follow_redirects=True
    )
    assert "Confirmação inválida" in resposta.get_data(as_text=True)

    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    conservacao()


def test_confirmacao_expirada_nao_efetiva(app, bc, cliente, monkeypatch):
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    revisao = ana_cliente.post(
        "/transferir",
        data={"destinatario": "bia", "valor": "10.00", "motivo": ""},
        follow_redirects=True,
    )
    token = _token(revisao.get_data(as_text=True))

    import vavacoin.rotas.carteira as rota

    monkeypatch.setattr(rota, "VALIDADE_DA_CONFIRMACAO", -1)
    resposta = ana_cliente.post(
        "/transferir/confirmar", data={"token": token}, follow_redirects=True
    )

    assert "expirou" in resposta.get_data(as_text=True)
    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    conservacao()


@pytest.mark.parametrize(
    "valor,esperado",
    [("0", 400), ("-5.00", 400), ("0.001", 400), ("muito", 400), ("70.00", 400)],
)
def test_valores_recusados_nao_chegam_na_confirmacao(app, bc, cliente, valor, esperado):
    """Zero, negativo, sub-centavo, lixo e mais do que se tem."""
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    resposta = ana_cliente.post(
        "/transferir", data={"destinatario": "bia", "valor": valor, "motivo": ""}
    )
    assert resposta.status_code in (200, esperado)
    assert "Confirmar transferência" not in resposta.get_data(as_text=True)

    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    conservacao()


def test_transferencia_para_si_mesmo_e_recusada(app, bc, cliente):
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    resposta = ana_cliente.post(
        "/transferir", data={"destinatario": "ana", "valor": "10.00", "motivo": ""}
    )
    assert resposta.status_code == 400
    assert "você mesmo" in resposta.get_data(as_text=True)
    conservacao()


def test_nao_da_para_transferir_para_o_banco_central(app, bc, cliente):
    """O BC não é destino: não se paga ao banco central."""
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    resposta = ana_cliente.post(
        "/transferir",
        data={"destinatario": "banco_central", "valor": "10.00", "motivo": ""},
    )
    assert resposta.status_code == 404
    conservacao()


def test_destinatario_inexistente(app, bc, cliente):
    ana_cliente, _ = _preparar_duas_contas(app, bc, cliente)
    resposta = ana_cliente.post(
        "/transferir", data={"destinatario": "fantasma", "valor": "10.00", "motivo": ""}
    )
    assert resposta.status_code == 404
    conservacao()


def test_transferencia_aparece_nos_dois_extratos(app, bc, cliente):
    ana_cliente, bia_cliente = _preparar_duas_contas(app, bc, cliente)
    revisao = ana_cliente.post(
        "/transferir",
        data={"destinatario": "bia", "valor": "5.00", "motivo": "lugar na fila"},
        follow_redirects=True,
    )
    ana_cliente.post(
        "/transferir/confirmar", data={"token": _token(revisao.get_data(as_text=True))}
    )
    conservacao()

    extrato_ana = ana_cliente.get("/carteira").get_data(as_text=True)
    extrato_bia = bia_cliente.get("/carteira").get_data(as_text=True)

    assert "-5.00" in extrato_ana
    assert "lugar na fila" in extrato_ana
    assert "lugar na fila" in extrato_bia
    assert "45.00" in extrato_ana
    assert "55.00" in extrato_bia
