"""O que qualquer um vê sem entrar."""

from flask import Blueprint, render_template

from ..auditoria import estado_da_economia

bp = Blueprint("publico", __name__)


@bp.route("/")
def inicio():
    return render_template("inicio.html")


@bp.route("/economia")
def economia():
    """Estado da economia, público.

    O CLAUDE.md manda o saldo da casa ser público sempre; começar pela
    economia inteira aberta é a versão mais forte disso. O que é público vira
    personagem do jogo; o que é escondido vira suspeita que ninguém consegue
    desprovar depois.

    Agregados apenas: quanto existe, quanto está circulando e quanto ainda
    não foi emitido. Saldo de pessoa não aparece aqui.
    """
    return render_template("economia.html", estado=estado_da_economia())
