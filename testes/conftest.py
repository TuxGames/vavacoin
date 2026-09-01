"""Fixtures e o helper que manda em toda a suíte."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from vavacoin import criar_app  # noqa: E402
from vavacoin.config import ConfigTeste  # noqa: E402
from vavacoin.constantes import SUPPLY_TOTAL  # noqa: E402
from vavacoin.extensoes import db  # noqa: E402
from vavacoin.moeda import criar_genese, soma_saldos  # noqa: E402
from vavacoin.operacoes import criar_convite, criar_usuario  # noqa: E402


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


def conservacao(esperado=SUPPLY_TOTAL):
    """A soma de TODOS os saldos, incluindo o do Banco Central, é o supply.

    É o único teste que roda em cima de qualquer coisa que mexa em dinheiro.
    Chamar antes e depois de cada operação, em cada teste.
    """
    total = soma_saldos()
    assert total == esperado, (
        f"massa violada: soma dos saldos é {total}, deveria ser {esperado}"
    )
    return total
