"""VaVáCoin — fábrica da aplicação.

Esta fatia é só o núcleo monetário: não há rotas, telas nem cassino. As
extensões já ficam ligadas para que a próxima fatia não precise mexer aqui.
"""

from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import Config
from .extensoes import csrf, db, login_manager, migrate


@event.listens_for(Engine, "connect")
def _pragmas_sqlite(conexao_dbapi, _registro):
    """Ajusta o SQLite para se comportar como banco transacional de verdade.

    - ``isolation_level = None`` desliga o controle de transação implícito do
      pysqlite, que abre BEGIN nas horas erradas e quebra ``SAVEPOINT``.
    - ``foreign_keys = ON`` porque no SQLite as FKs vêm desligadas.
    - ``busy_timeout`` faz um escritor concorrente esperar em vez de estourar
      "database is locked" na hora.
    """
    if conexao_dbapi.__class__.__module__.split(".")[0] != "sqlite3":
        return
    conexao_dbapi.isolation_level = None
    cursor = conexao_dbapi.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


@event.listens_for(Engine, "begin")
def _begin_immediate_sqlite(conexao):
    """Abre a transação já como escritora.

    Com ``BEGIN`` normal o SQLite só pega o lock de escrita no primeiro
    UPDATE, e duas transações que leram o mesmo saldo antes disso viram uma
    delas morrer no meio do movimento. ``BEGIN IMMEDIATE`` serializa os
    escritores desde o início — é o que faz o lock de linha ter algum
    significado num banco que não tem ``SELECT ... FOR UPDATE``.
    """
    if conexao.engine.dialect.name == "sqlite":
        conexao.exec_driver_sql("BEGIN IMMEDIATE")


def criar_app(config=Config):
    """Fábrica da aplicação."""
    app = Flask(__name__)
    app.config.from_object(config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import modelos  # noqa: F401  (registra as tabelas no metadata)
    from .cli import registrar_comandos

    registrar_comandos(app)

    @login_manager.user_loader
    def carregar_usuario(id_usuario):
        usuario = db.session.get(modelos.Usuario, int(id_usuario))
        # O Banco Central não autentica. Uma conta de tesouraria que loga é
        # um caixa que qualquer um esvazia — o bug que o Benbals tem hoje.
        if usuario is not None and usuario.eh_banco_central:
            return None
        return usuario

    return app
