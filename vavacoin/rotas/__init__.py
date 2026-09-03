"""Blueprints da web.

Sete: o que é público, entrar e sair, a carteira da pessoa, o painel do Banco
Central, o Caladinho — o cassino —, os reinos, onde mora o RPG, e o ranking
geral.
"""

from .admin import bp as bp_admin
from .auth import bp as bp_auth
from .caladinho import bp as bp_caladinho
from .carteira import bp as bp_carteira
from .publico import bp as bp_publico
from .ranking import bp as bp_ranking
from .reino import bp as bp_reino


def registrar_rotas(app):
    app.register_blueprint(bp_publico)
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_carteira)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_caladinho)
    app.register_blueprint(bp_reino)
    app.register_blueprint(bp_ranking)
