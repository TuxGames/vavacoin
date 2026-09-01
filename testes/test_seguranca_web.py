"""CSRF, CSP e as regras de higiene que só custam barato se nascerem juntas."""

import pathlib
import re

import pytest
from conftest import conservacao, isolar_login_por_requisicao

from vavacoin.config import CHAVE_DE_DESENVOLVIMENTO, Config, ConfigTeste
from vavacoin.extensoes import db
from vavacoin.modelos import Usuario
from vavacoin.operacoes import criar_convite
from vavacoin.seguranca import POLITICA

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "vavacoin" / "templates"


# --- CSRF -------------------------------------------------------------------


@pytest.fixture
def app_com_csrf(tmp_path):
    """App igual ao de produção no que importa aqui: CSRF ligado."""
    from vavacoin import criar_app

    class ConfigCSRF(ConfigTeste):
        WTF_CSRF_ENABLED = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'csrf.sqlite3'}"

    aplicacao = criar_app(ConfigCSRF)
    isolar_login_por_requisicao(aplicacao)
    with aplicacao.app_context():
        db.create_all()
        yield aplicacao
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize(
    "rota,dados",
    [
        ("/entrar", {"nome_usuario": "ana", "senha": "senha-boa-123"}),
        (
            "/cadastro",
            {
                "codigo": "x",
                "nome_usuario": "ana",
                "nome_exibicao": "Ana",
                "senha": "senha-boa-123",
                "confirmacao": "senha-boa-123",
            },
        ),
        ("/transferir", {"destinatario": "bia", "valor": "1.00"}),
        ("/transferir/confirmar", {"token": "x"}),
        ("/sair", {}),
    ],
)
def test_post_sem_token_csrf_e_recusado(app_com_csrf, rota, dados):
    """Nenhum POST do site aceita chegar sem token."""
    cliente = app_com_csrf.test_client()
    resposta = cliente.post(rota, data=dados)
    assert resposta.status_code == 400


def test_transferencia_com_token_valido_funciona(app_com_csrf):
    """A trava é contra requisição forjada, não contra o uso normal."""
    from vavacoin.moeda import criar_genese

    bc = criar_genese()
    db.session.commit()
    cliente = app_com_csrf.test_client()

    for nome in ["ana", "bia"]:
        codigo = criar_convite(destinatario=nome, autoridade=bc).codigo
        db.session.commit()
        pagina = cliente.get("/cadastro").get_data(as_text=True)
        cliente.post(
            "/cadastro",
            data={
                "csrf_token": _csrf(pagina),
                "codigo": codigo,
                "nome_usuario": nome,
                "nome_exibicao": nome.capitalize(),
                "senha": "senha-boa-123",
                "confirmacao": "senha-boa-123",
            },
            follow_redirects=True,
        )
        if nome == "ana":
            cliente.post(
                "/sair", data={"csrf_token": _csrf(pagina)}, follow_redirects=True
            )
    from vavacoin.modelos import buscar_usuario
    from vavacoin.operacoes import ajustar_saldo

    ajustar_saldo(buscar_usuario("bia"), "50.00", "saldo para o teste", autoridade=bc)
    ajustar_saldo(buscar_usuario("ana"), "50.00", "saldo para o teste", autoridade=bc)
    db.session.commit()
    conservacao()

    pagina = cliente.get("/transferir").get_data(as_text=True)
    revisao = cliente.post(
        "/transferir",
        data={
            "csrf_token": _csrf(pagina),
            "destinatario": "ana",
            "valor": "7.00",
            "motivo": "",
        },
        follow_redirects=True,
    ).get_data(as_text=True)

    cliente.post(
        "/transferir/confirmar",
        data={"csrf_token": _csrf(revisao), "token": _token(revisao)},
        follow_redirects=True,
    )

    db.session.expire_all()
    ana = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == "ana")
    ).scalar_one()
    assert ana.saldo == __import__("decimal").Decimal("57.00")
    conservacao()


def _csrf(corpo):
    achado = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', corpo)
    assert achado, "formulário renderizado sem token CSRF"
    return achado.group(1)


def _token(corpo):
    achado = re.search(r'name="token"[^>]*value="([^"]+)"', corpo)
    assert achado, "token de confirmação não apareceu"
    return achado.group(1)


def test_todo_formulario_renderizado_tem_token(app_com_csrf):
    """Varre as páginas com formulário e exige o token em cada uma."""
    cliente = app_com_csrf.test_client()
    for rota in ["/entrar", "/cadastro"]:
        corpo = cliente.get(rota).get_data(as_text=True)
        assert 'name="csrf_token"' in corpo, rota


# --- CSP e cabeçalhos -------------------------------------------------------


def test_cabecalhos_de_seguranca_em_toda_resposta(app, cliente_qualquer):
    for rota in ["/", "/entrar", "/cadastro", "/rota-que-nao-existe"]:
        resposta = cliente_qualquer.get(rota)
        assert resposta.headers["Content-Security-Policy"] == POLITICA
        assert resposta.headers["X-Content-Type-Options"] == "nosniff"
        assert resposta.headers["X-Frame-Options"] == "DENY"
        assert resposta.headers["Referrer-Policy"] == "same-origin"


@pytest.fixture
def cliente_qualquer(app):
    return app.test_client()


def test_csp_nao_afrouxa_com_unsafe(app):
    """`unsafe-inline` e `unsafe-eval` são exatamente o que a CSP evita."""
    assert "unsafe-inline" not in POLITICA
    assert "unsafe-eval" not in POLITICA
    assert "default-src 'self'" in POLITICA
    assert "form-action 'self'" in POLITICA
    assert "frame-ancestors 'none'" in POLITICA


# --- higiene dos templates --------------------------------------------------


def test_nenhum_template_tem_estilo_inline_ou_handler():
    """A CSP quebraria isso em produção; o teste avisa antes de subir.

    É o juiz da adaptação do visual do Benbals, que usa `style=` e `onclick=`
    à vontade: nada disso entra aqui, por mais tentador que seja na hora de
    portar uma tela.

    O motivo é concreto: o **motivo** da transferência é texto que uma pessoa
    escreve e outra lê no extrato. É por ali que um escape que falhe vira XSS,
    e a CSP é a segunda tranca.

    `<script src=...>` é permitido — o menu off-canvas é um arquivo servido
    pela própria origem, que `script-src 'self'` cobre. O que não passa é
    script com corpo embutido.
    """
    problemas = []
    for arquivo in TEMPLATES.glob("*.html"):
        texto = arquivo.read_text(encoding="utf-8")
        if re.search(r'\sstyle\s*=\s*"', texto):
            problemas.append(f"{arquivo.name}: atributo style=")
        if re.search(r"\son[a-z]+\s*=\s*[\"']", texto):
            problemas.append(f"{arquivo.name}: handler on*=")
        if "<style" in texto:
            problemas.append(f"{arquivo.name}: tag <style> embutida")
        for corpo in re.findall(r"<script\b[^>]*>(.*?)</script>", texto, re.S):
            if corpo.strip():
                problemas.append(f"{arquivo.name}: <script> com corpo embutido")
        if re.search(r"<script\b(?![^>]*\ssrc=)", texto):
            problemas.append(f"{arquivo.name}: <script> sem src")
    assert not problemas, problemas


def test_o_css_e_o_js_nao_puxam_nada_de_fora():
    """A CSP é `default-src 'self'`: um @import ou uma URL externa não carrega.

    O `stylepage.css` do Benbals foi descartado justamente por isto — ele
    importa fonte do Google e uma imagem de fundo de um bucket S3.
    """
    estaticos = TEMPLATES.parent / "static"
    problemas = []
    for arquivo in list(estaticos.glob("*.css")) + list(estaticos.glob("*.js")):
        texto = arquivo.read_text(encoding="utf-8")
        for url in re.findall(r"url\(\s*['\"]?(https?://[^)'\"]+)", texto):
            problemas.append(f"{arquivo.name}: {url}")
        if "@import" in texto:
            problemas.append(f"{arquivo.name}: @import")
    assert not problemas, problemas


# --- configuração -----------------------------------------------------------


def test_cookie_de_sessao_e_httponly_e_samesite():
    assert Config.SESSION_COOKIE_HTTPONLY is True
    assert Config.SESSION_COOKIE_SAMESITE == "Lax"


def test_producao_exige_https_no_cookie():
    from vavacoin.config import ConfigProducao

    assert ConfigProducao.SESSION_COOKIE_SECURE is True


def test_producao_recusa_subir_com_a_chave_padrao():
    """A chave de desenvolvimento é pública: está no repositório.

    Localmente isso é só um aviso — senão o `flask run` do dia a dia pararia
    de funcionar. Publicando, é impedimento.
    """
    from vavacoin import criar_app
    from vavacoin.config import ConfigProducao

    class ProducaoSemChave(ConfigProducao):
        SECRET_KEY = CHAVE_DE_DESENVOLVIMENTO

    with pytest.raises(RuntimeError, match="VAVACOIN_SECRET_KEY"):
        criar_app(ProducaoSemChave)


def test_desenvolvimento_sobe_com_a_chave_padrao():
    """O aviso não pode virar pedra no caminho de quem está codando."""
    from vavacoin import criar_app

    class DesenvolvimentoSemChave(Config):
        SECRET_KEY = CHAVE_DE_DESENVOLVIMENTO
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    assert criar_app(DesenvolvimentoSemChave) is not None
