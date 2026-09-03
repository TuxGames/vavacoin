"""VavaCoin — fábrica da aplicação.

Web: entrar por convite, ver o próprio saldo e extrato, transferir, e o
painel do Banco Central. Cassino e ranking continuam fora.
"""

from flask import Flask, render_template
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import CHAVE_DE_DESENVOLVIMENTO, Config
from .extensoes import csrf, db, login_manager, migrate
from .seguranca import aplicar_cabecalhos


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

    # Com a chave padrão qualquer um forja o cookie de sessão de qualquer
    # conta. Localmente isso é só um aviso; publicando, é impedimento — daí a
    # checagem ser ligada pela config de produção, e não adivinhada.
    if app.config["SECRET_KEY"] == CHAVE_DE_DESENVOLVIMENTO:
        if app.config.get("EXIGE_SEGREDO_PROPRIO"):
            raise RuntimeError(
                "defina VAVACOIN_SECRET_KEY antes de publicar "
                "(a chave padrão é pública, está no repositório)"
            )
        app.logger.warning(
            "usando a chave de desenvolvimento; não publique assim"
        )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import modelos  # noqa: F401  (registra as tabelas no metadata)
    from .cli import registrar_comandos
    from .rotas import registrar_rotas

    registrar_comandos(app)
    registrar_rotas(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Entre para ver sua carteira."

    # CSP e companhia em toda resposta, inclusive nas de erro.
    app.after_request(aplicar_cabecalhos)

    # "Sou eu?" é uma pergunta de toda tela que lista gente, e a resposta tem
    # de ser a mesma em todas — ver `ranking.eh_voce`. Global do Jinja, e não
    # context processor, porque não custa consulta nenhuma.
    from .ranking import eh_voce

    app.jinja_env.globals["eh_voce"] = eh_voce

    @app.context_processor
    def interruptores_do_menu():
        """O que o menu precisa saber para não oferecer porta trancada.

        Consultado só para quem está logado: para o visitante o menu tem
        dois links fixos, e uma consulta ao banco ali seria paga em toda tela
        de login sem mudar nada na tela.

        O link é o espelho da rota, nunca a tranca: quem fecha os reinos é o
        ``before_request`` do blueprint. Se um dia os dois discordarem, quem
        manda é o de lá.
        """
        from flask_login import current_user

        if not current_user.is_authenticated:
            return {"reinos_visiveis": False, "ranking_visivel": False}
        return {
            "reinos_visiveis": modelos.config_ligada(
                modelos.CHAVE_REINOS_VISIVEIS
            ),
            # Nasce ligado, ao contrário dos reinos: é o que o dono quer usar
            # agora.
            "ranking_visivel": modelos.config_ligada(
                modelos.CHAVE_RANKING_VISIVEL, padrao=True
            ),
        }

    @app.errorhandler(404)
    def nao_encontrado(_erro):
        return render_template(
            "erro.html", codigo=404, mensagem="Essa página não existe."
        ), 404

    @app.errorhandler(403)
    def proibido(_erro):
        return render_template(
            "erro.html", codigo=403, mensagem="Isso não é para você."
        ), 403

    @app.errorhandler(500)
    def erro_interno(_erro):
        # A sessão pode ter ficado num estado sujo; devolvê-la limpa evita
        # que a próxima requisição herde uma transação abortada.
        db.session.rollback()
        return render_template(
            "erro.html", codigo=500, mensagem="Deu errado aqui dentro."
        ), 500

    @login_manager.user_loader
    def carregar_usuario(id_usuario):
        # O Banco Central entra como qualquer conta — decisão registrada no
        # CLAUDE.md. O que o protege agora não é a porta fechada, é a senha
        # (por CLI, com hash), o freio de tentativas e o rastro de tudo que
        # ele faz.
        return db.session.get(modelos.Usuario, int(id_usuario))

    return app
