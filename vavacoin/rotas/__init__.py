"""Blueprints da web.

Quatro: o que é público, entrar e sair, a carteira da pessoa, e o painel do
Banco Central. Cassino e ranking continuam fora.
"""

from .admin import bp as bp_admin
from .auth import bp as bp_auth
from .carteira import bp as bp_carteira
from .publico import bp as bp_publico


def registrar_rotas(app):
    app.register_blueprint(bp_publico)
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_carteira)
    app.register_blueprint(bp_admin)
