"""A porta de entrada: painel de quem já usa, apresentação de quem chega.

A mesma rota serve as duas coisas de propósito. Quem está logado abre o site
todo dia e precisa de saldo, atalho de transferência e o que aconteceu — não
de um texto explicando o que é a moeda. Quem não está precisa exatamente do
texto.
"""

from flask import Blueprint, render_template
from flask_login import current_user

from ..auditoria import linhas_extrato
from ..formularios import FormularioTransferencia

bp = Blueprint("publico", __name__)


@bp.route("/")
def inicio():
    if not current_user.is_authenticated:
        return render_template("inicio.html")

    return render_template(
        "inicio.html",
        # O formulário do atalho posta em /transferir e cai na mesma revisão
        # de sempre: o caminho do dinheiro não ganha atalho, só a navegação.
        formulario=FormularioTransferencia(),
        linhas=linhas_extrato(current_user, limite=5),
    )
