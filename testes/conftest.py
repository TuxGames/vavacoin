"""Fixtures e o helper que manda em toda a suíte."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from flask import g  # noqa: E402

from vavacoin import criar_app  # noqa: E402
from vavacoin.config import ConfigTeste  # noqa: E402
from vavacoin.constantes import SUPPLY_INICIAL  # noqa: E402
from vavacoin.extensoes import db  # noqa: E402
from vavacoin.moeda import criar_genese, soma_saldos, supply_emitido  # noqa: E402
from vavacoin.operacoes import criar_convite, criar_usuario  # noqa: E402


def isolar_login_por_requisicao(aplicacao):
    """Impede que o usuário logado vaze de uma requisição para a outra.

    Artefato de teste, não do app: os testes rodam dentro de um app context
    já empilhado (para poder consultar o banco nas asserções), e o Flask
    reaproveita esse contexto nas requisições em vez de criar um novo. Como o
    Flask-Login guarda o usuário em ``g._login_user``, dois ``test_client``
    diferentes acabariam compartilhando quem está logado.

    Em produção cada requisição ganha o próprio contexto e isso não acontece.
    """

    @aplicacao.before_request
    def _limpar_cache_do_login():
        g.pop("_login_user", None)


@pytest.fixture
def app(tmp_path):
    """App de teste com um SQLite em arquivo.

    Arquivo, e não ``:memory:``, porque o teste de concorrência precisa de
    duas conexões enxergando o mesmo banco — e porque é assim que roda de
    verdade.
    """
    banco = tmp_path / "teste.sqlite3"
    ConfigTeste.SQLALCHEMY_DATABASE_URI = f"sqlite:///{banco}"
    aplicacao = criar_app(ConfigTeste)
    isolar_login_por_requisicao(aplicacao)
    with aplicacao.app_context():
        db.create_all()
        yield aplicacao
        db.session.remove()
        db.drop_all()


@pytest.fixture
def bc(app):
    """Banco Central com o supply, já commitado."""
    banco_central = criar_genese()
    db.session.commit()
    return banco_central


@pytest.fixture
def nova_pessoa(app, bc):
    """Fábrica: cria conta e, se pedido, resgata o convite dela.

    Depende do ``bc`` porque criar conta e emitir convite são poderes do
    Banco Central — a fixture exerce a autoridade explicitamente, como a CLI.
    """
    contador = {"n": 0}

    def criar(nome=None, senha="senha-boa-123", com_convite=False):
        contador["n"] += 1
        nome = nome or f"aluno{contador['n']}"
        usuario = criar_usuario(nome, senha, autoridade=bc)
        db.session.commit()
        if com_convite:
            from vavacoin.operacoes import resgatar_convite

            convite = criar_convite(destinatario=nome, autoridade=bc)
            db.session.commit()
            resgatar_convite(usuario, convite.codigo)
            db.session.commit()
        return usuario

    return criar


def conservacao(esperado=None):
    """A soma de TODOS os saldos, incluindo o do Banco Central, é o supply.

    Chamar antes e depois de cada operação, em cada teste.

    Sem ``esperado``, o alvo é o supply que o **ledger** diz existir, e não a
    constante 5.000: desde que o administrador pode cunhar ao ajustar saldo,
    fixar o número faria este helper acusar erro em toda correção legítima.
    Cunhagem se verifica por ``supply_emitido()``, que os testes de ajuste
    checam explicitamente.
    """
    esperado = supply_emitido() if esperado is None else esperado
    total = soma_saldos()
    assert total == esperado, (
        f"massa violada: soma dos saldos é {total}, deveria ser {esperado}"
    )
    return total
