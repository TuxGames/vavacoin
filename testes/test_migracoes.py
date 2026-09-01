"""A cadeia de migrações, rodada de verdade.

Existe por causa de um defeito que passou despercebido: a migração
``353a30f6e6f5`` recriou a tabela ``usuario`` com ``copy_from`` — que carrega
colunas e CHECKs, mas **não carrega índices** — e levou junto o UNIQUE de
``nome_usuario``. O banco em produção passou a aceitar duas contas com o mesmo
nome, e nenhum teste viu.

Nenhum teste viu porque a suíte monta o banco com ``db.create_all()``, a
partir do metadata dos modelos. Isso testa o que os modelos *dizem*, nunca o
que as migrações *fazem* — e é exatamente entre os dois que o erro morava.

Estes testes fecham essa distância: sobem um banco pelo caminho real
(``flask db upgrade``) e comparam o resultado com o metadata.
"""

import pathlib

import pytest
import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from vavacoin import criar_app
from vavacoin.config import ConfigTeste
from vavacoin.extensoes import db

RAIZ = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def app_migrado(tmp_path):
    """Um banco construído pelas migrações, não pelo ``create_all()``."""

    class ConfigMigracao(ConfigTeste):
        # Caminho curto e sem espaço: a URL do SQLAlchemy não lida bem com os
        # espaços do diretório real do projeto.
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (
            tmp_path / "migrado.sqlite3"
        ).as_posix()

    aplicacao = criar_app(ConfigMigracao)
    with aplicacao.app_context():
        upgrade(directory=str(RAIZ / "migrations"))
        yield aplicacao
        db.session.remove()


def _indices(tabela):
    return {
        indice["name"]: bool(indice["unique"])
        for indice in sa.inspect(db.engine).get_indexes(tabela)
    }


def test_migracoes_produzem_as_mesmas_colunas_dos_modelos(app_migrado):
    """O que as migrações constroem tem que ser o que os modelos declaram."""
    inspetor = sa.inspect(db.engine)
    for tabela, modelo in db.metadata.tables.items():
        if tabela == "alembic_version":
            continue
        no_banco = {c["name"] for c in inspetor.get_columns(tabela)}
        no_modelo = {c.name for c in modelo.columns}
        assert no_banco == no_modelo, f"{tabela}: {no_banco ^ no_modelo}"


def test_nome_normalizado_e_unico_no_banco_migrado(app_migrado):
    """O teste que teria pego o índice perdido.

    Não basta o modelo declarar ``unique=True``: o que vale em produção é o
    índice que a migração criou.
    """
    assert _indices("usuario").get("ix_usuario_nome_normalizado") is True


def test_indices_de_usuario_sobreviveram_as_recriacoes_de_tabela(app_migrado):
    """`usuario` foi recriada duas vezes; os índices têm que continuar lá."""
    indices = _indices("usuario")
    assert "ix_usuario_nome_usuario" in indices
    assert "ix_usuario_nome_normalizado" in indices


def test_joao_e_joao_colidem_no_banco_migrado(app_migrado):
    """A regra que o usuário pediu, verificada onde ela de fato mora."""
    from vavacoin.moeda import criar_genese
    from vavacoin.operacoes import criar_usuario

    bc = criar_genese()
    db.session.commit()

    criar_usuario("João", "senha-boa-123", autoridade=bc)
    db.session.commit()

    with pytest.raises(sa.exc.IntegrityError):
        criar_usuario("joao", "senha-boa-123", autoridade=bc)
        db.session.commit()
    db.session.rollback()


def test_saldo_negativo_continua_barrado_no_banco_migrado(app_migrado):
    """O CHECK que quase se perdeu junto com os índices."""
    from vavacoin.modelos import Usuario

    with pytest.raises(sa.exc.IntegrityError):
        db.session.execute(
            sa.insert(Usuario).values(
                nome_usuario="devedor",
                nome_normalizado="devedor",
                nome_exibicao="Devedor",
                eh_banco_central=False,
                saldo=-100,
                criado_em=sa.func.now(),
            )
        )
    db.session.rollback()


def test_downgrade_e_upgrade_voltam_ao_mesmo_lugar(app_migrado):
    """Migração que não desfaz é migração que ninguém tem coragem de rodar."""
    antes = _indices("usuario")
    downgrade(directory=str(RAIZ / "migrations"))
    upgrade(directory=str(RAIZ / "migrations"))
    assert _indices("usuario") == antes
