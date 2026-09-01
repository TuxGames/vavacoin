"""O que qualquer um vê sem entrar."""

from flask import Blueprint, render_template

bp = Blueprint("publico", __name__)


@bp.route("/")
def inicio():
    return render_template("inicio.html")
