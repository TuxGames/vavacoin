"""Blueprints da web.

Cinco: o que é público, entrar e sair, a carteira da pessoa, o painel do Banco
Central e o Caladinho — o cassino, com o mines como primeiro jogo.
"""

from .admin import bp as bp_admin
from .auth import bp as bp_auth
from .caladinho import bp as bp_caladinho
from .carteira import bp as bp_carteira
from .publico import bp as bp_publico


def registrar_rotas(app):
    app.register_blueprint(bp_publico)
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_carteira)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_caladinho)
