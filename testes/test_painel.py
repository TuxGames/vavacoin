"""O painel do god mode pela web."""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SAQUE_INICIAL, SUPPLY_INICIAL
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import mover, supply_emitido
from vavacoin.modelos import Convite, RegistroAdministrativo, Usuario
from vavacoin.operacoes import criar_convite

SENHA_BC = "senha-do-banco-central-123"
SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


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


def _cadastrar(app, bc, usuario):
    codigo = criar_convite(destinatario=usuario, autoridade=bc).codigo
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": usuario,
            "nome_exibicao": usuario.capitalize(),
            "senha": SENHA,
            "confirmacao": SENHA,
        },
        follow_redirects=True,
    )
    return cliente


# --- quem entra -------------------------------------------------------------


def test_banco_central_entra_pela_tela(app, bc, painel):
    """A porta que antes não existia. Agora existe, e leva ao painel."""
    resposta = painel.get("/painel/")
    assert resposta.status_code == 200
    assert "Painel do Banco Central" in resposta.get_data(as_text=True)


def test_banco_central_sem_senha_nao_entra(app, bc):
    """Depois da gênese e antes do `flask senha-bc`, ninguém entra nele."""
    cliente = app.test_client()
    for tentativa in ["", "banco_central", SENHA_BC]:
        resposta = cliente.post(
            "/entrar",
            data={"nome_usuario": "banco_central", "senha": tentativa or "x"},
        )
        assert resposta.status_code == 401
    assert cliente.get("/painel/", follow_redirects=False).status_code == 302


def test_painel_inteiro_exige_o_banco_central(app, bc, painel):
    """Jogador logado leva 403 em toda rota do painel, não só na primeira."""
    ana_cliente = _cadastrar(app, bc, "ana")
    rotas_get = ["/painel/", "/painel/registros", "/painel/extrato/ana", "/painel/auditoria"]
    rotas_post = ["/painel/convite", "/painel/conta", "/painel/saldo", "/painel/reset"]

    for rota in rotas_get:
        assert ana_cliente.get(rota).status_code == 403, rota
    for rota in rotas_post:
        assert ana_cliente.post(rota, data={}).status_code == 403, rota


def test_painel_exige_login(app, bc):
    anonimo = app.test_client()
    assert anonimo.get("/painel/", follow_redirects=False).status_code == 302


def test_link_do_painel_so_aparece_para_o_admin(app, bc, painel):
    ana_cliente = _cadastrar(app, bc, "ana")
    assert "/painel/" not in ana_cliente.get("/carteira").get_data(as_text=True)
    assert "/painel/" in painel.get("/carteira").get_data(as_text=True)


# --- o que o painel faz -----------------------------------------------------


def test_painel_mostra_supply_inicial_e_atual(app, bc, painel):
    _cadastrar(app, bc, "ana")
    conservacao()

    corpo = painel.get("/painel/").get_data(as_text=True)
    assert "Supply inicial" in corpo
    assert "Supply atual" in corpo
    assert "Cunhado depois" in corpo
    assert str(SUPPLY_INICIAL) in corpo


def test_painel_mostra_o_quanto_ja_cunhou(app, bc, painel):
    ana_cliente = _cadastrar(app, bc, "ana")  # noqa: F841
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    mover(bc, ana, bc.saldo, motivo="esvaziando")
    db.session.commit()

    painel.post(
        "/painel/saldo",
        data={
            "nome_usuario": "ana",
            "novo_saldo": str(ana.saldo + Decimal("25.00")),
            "motivo": "corrigindo",
        },
        follow_redirects=True,
    )
    conservacao()

    corpo = painel.get("/painel/").get_data(as_text=True)
    assert "25.00" in corpo
    assert supply_emitido() == SUPPLY_INICIAL + Decimal("25.00")


def test_ajuste_pela_web_muda_o_saldo_e_fecha_a_auditoria(app, bc, painel):
    from vavacoin.auditoria import auditar

    _cadastrar(app, bc, "ana")
    conservacao()

    resposta = painel.post(
        "/painel/saldo",
        data={"nome_usuario": "ana", "novo_saldo": "72,50", "motivo": "aposta paga errado"},
        follow_redirects=True,
    )
    assert resposta.status_code == 200

    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == Decimal("72.50")
    assert auditar()["ok"] is True
    conservacao()


def test_ajuste_pela_web_sem_motivo_nao_passa(app, bc, painel):
    _cadastrar(app, bc, "ana")
    resposta = painel.post(
        "/painel/saldo",
        data={"nome_usuario": "ana", "novo_saldo": "999.00", "motivo": ""},
    )
    assert resposta.status_code == 400

    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == SAQUE_INICIAL
    conservacao()


def test_ajuste_de_quem_nao_existe(app, bc, painel):
    resposta = painel.post(
        "/painel/saldo",
        data={
            "nome_usuario": "fantasma",
            "novo_saldo": "10.00",
            "motivo": "conta que não existe",
        },
    )
    assert resposta.status_code == 404
    conservacao()


def test_emitir_convite_pelo_painel(app, bc, painel):
    resposta = painel.post(
        "/painel/convite", data={"destinatario": "Fulano"}, follow_redirects=True
    )
    assert resposta.status_code == 200
    assert db.session.query(Convite).count() == 1
    assert "Convite emitido" in resposta.get_data(as_text=True)


def test_criar_conta_pelo_painel(app, bc, painel):
    resposta = painel.post(
        "/painel/conta",
        data={"nome_usuario": "novato", "nome_exibicao": "Novato", "senha": SENHA},
        follow_redirects=True,
    )
    assert resposta.status_code == 200
    novato = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "novato")
    ).scalar_one()
    assert novato.saldo == Decimal("0.00")
    conservacao()


def test_criar_conta_pelo_painel_aceita_senha_curta(app, bc, painel):
    """Mesma regra do cadastro público: sem mínimo."""
    resposta = painel.post(
        "/painel/conta",
        data={"nome_usuario": "curto", "nome_exibicao": "Curto", "senha": "a"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    conta = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "curto")
    ).scalar_one()
    assert conta.verificar_senha("a")
    conservacao()


def test_criar_conta_pelo_painel_recusa_senha_vazia(app, bc, painel):
    resposta = painel.post(
        "/painel/conta",
        data={"nome_usuario": "vazio", "nome_exibicao": "Vazio", "senha": ""},
    )

    assert resposta.status_code == 400
    assert (
        db.session.execute(
            db.select(Usuario).where(Usuario.nome_usuario == "vazio")
        ).scalar_one_or_none()
        is None
    )
    conservacao()


def test_extrato_de_qualquer_um(app, bc, painel):
    _cadastrar(app, bc, "ana")
    resposta = painel.get("/painel/extrato/ana")
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert "saque inicial" in corpo
    assert "50.00" in corpo


def test_olhar_extrato_alheio_deixa_rastro(app, bc, painel):
    """Olhar é poder; poder deixa rastro."""
    _cadastrar(app, bc, "ana")
    painel.get("/painel/extrato/ana")

    registro = (
        db.session.query(RegistroAdministrativo)
        .filter_by(acao="extrato")
        .order_by(RegistroAdministrativo.id.desc())
        .first()
    )
    assert registro is not None
    assert registro.alvo == "ana"


def test_reset_pelo_painel_exige_a_palavra(app, bc, painel):
    ana_cliente = _cadastrar(app, bc, "ana")
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    mover(ana, bc, "20.00", motivo="gastou", sessao=db.session)
    db.session.commit()
    assert ana.saldo == Decimal("30.00")

    sem_palavra = painel.post(
        "/painel/reset", data={"confirmacao": "sim", "motivo": "tentativa"}
    )
    assert sem_palavra.status_code == 400
    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == Decimal("30.00")

    com_palavra = painel.post(
        "/painel/reset",
        data={"confirmacao": "RESETAR", "motivo": "fim do bimestre"},
        follow_redirects=True,
    )
    assert com_palavra.status_code == 200
    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == SAQUE_INICIAL
    conservacao()
    assert ana_cliente is not None


def test_diario_aparece_no_painel(app, bc, painel):
    painel.post("/painel/convite", data={"destinatario": "Fulano"}, follow_redirects=True)
    corpo = painel.get("/painel/").get_data(as_text=True)
    assert "convite" in corpo
    assert "Fulano" in corpo

    inteiro = painel.get("/painel/registros").get_data(as_text=True)
    assert "Diário do painel" in inteiro
    assert "Fulano" in inteiro


def test_auditoria_pelo_painel(app, bc, painel):
    _cadastrar(app, bc, "ana")
    resposta = painel.get("/painel/auditoria", follow_redirects=True)
    assert "Auditoria OK" in resposta.get_data(as_text=True)


def test_auditoria_pelo_painel_acusa_sabotagem(app, bc, painel):
    """UPDATE por fora continua sendo pego — inclusive pelo painel."""
    _cadastrar(app, bc, "ana")
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    db.session.execute(
        db.update(Usuario).where(Usuario.id == ana.id).values(saldo=Decimal("999.00"))
    )
    db.session.commit()

    resposta = painel.get("/painel/auditoria", follow_redirects=True)
    assert "AUDITORIA FALHOU" in resposta.get_data(as_text=True)
