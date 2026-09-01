"""Blueprints da web.

Três, e só três nesta fatia: o que é público, o que é entrar e sair, e o que
é a carteira da pessoa. Cassino, ranking e administração por tela ficam de
fora — administração continua só na CLI.
"""

from .auth import bp as bp_auth
from .carteira import bp as bp_carteira
from .publico import bp as bp_publico


def registrar_rotas(app):
    app.register_blueprint(bp_publico)
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_carteira)
