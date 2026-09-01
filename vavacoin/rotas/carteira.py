"""A carteira da pessoa: saldo, extrato e transferência.

Nada aqui mostra saldo de terceiro — e, desde que a página de economia saiu,
o site não mostra agregado nenhum. Os números da economia continuam existindo
e conferíveis por ``flask auditoria``, na CLI.
"""

import secrets
import time

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_login import current_user, login_required

from ..auditoria import linhas_extrato
from ..dinheiro import para_decimal
from ..erros import ErroMonetario
from ..extensoes import db
from ..formularios import FormularioConfirmacao, FormularioTransferencia
from ..modelos import Usuario
from ..operacoes import transferir

bp = Blueprint("carteira", __name__)

#: Chave da transferência à espera de confirmação, na sessão do navegador.
PENDENTE = "transferencia_pendente"

#: Quanto tempo a confirmação continua valendo. Curto de propósito: uma
#: confirmação esquecida numa aba aberta não deve efetivar dinheiro amanhã.
VALIDADE_DA_CONFIRMACAO = 10 * 60


@bp.route("/carteira")
@login_required
def minha_carteira():
    return render_template(
        "carteira.html",
        saldo=current_user.saldo,
        linhas=linhas_extrato(current_user, limite=30),
    )


@bp.route("/transferir", methods=["GET", "POST"])
@login_required
def transferir_passo1():
    """Primeiro passo: só monta a intenção. Nada de dinheiro se move aqui."""
    formulario = FormularioTransferencia()
    if formulario.validate_on_submit():
        nome = formulario.destinatario.data.strip().lower()
        destino = db.session.execute(
            db.select(Usuario).where(Usuario.nome_usuario == nome)
        ).scalar_one_or_none()

        if destino is None or destino.eh_banco_central:
            # O Banco Central some do universo de destinos: não se paga ao BC,
            # e dizer que ele existe já é informação de mais.
            flash("Não existe ninguém com esse usuário.", "erro")
            return render_template("transferir.html", formulario=formulario), 404
        if destino.id == current_user.id:
            flash("Não dá para transferir para você mesmo.", "erro")
            return render_template("transferir.html", formulario=formulario), 400

        valor = formulario.valor.decimal
        if valor > current_user.saldo:
            flash(f"Você tem {current_user.saldo} VVC.", "erro")
            return render_template("transferir.html", formulario=formulario), 400

        session[PENDENTE] = {
            "token": secrets.token_urlsafe(16),
            "destino_id": destino.id,
            "destino_nome": destino.nome_exibicao,
            "destino_usuario": destino.nome_usuario,
            "valor": str(valor),
            "motivo": (formulario.motivo.data or "").strip() or None,
            "criado_em": time.time(),
        }
        return redirect(url_for("carteira.confirmar"))

    return render_template("transferir.html", formulario=formulario)


@bp.route("/transferir/confirmar", methods=["GET", "POST"])
@login_required
def confirmar():
    """Segundo passo: mostra valor e destinatário, e só então efetiva.

    Dinheiro que sai por um clique único é arrependimento garantido. O que
    executa é o que está na sessão — o mesmo que foi desenhado na tela —, não
    o que voltou no formulário; assim não há como o confirmado divergir do
    mostrado.
    """
    pendente = session.get(PENDENTE)
    if not pendente:
        flash("Nada para confirmar.", "erro")
        return redirect(url_for("carteira.transferir_passo1"))

    if time.time() - pendente["criado_em"] > VALIDADE_DA_CONFIRMACAO:
        session.pop(PENDENTE, None)
        flash("A confirmação expirou. Refaça a transferência.", "erro")
        return redirect(url_for("carteira.transferir_passo1"))

    formulario = FormularioConfirmacao(token=pendente["token"])
    if formulario.validate_on_submit():
        if not secrets.compare_digest(formulario.token.data, pendente["token"]):
            session.pop(PENDENTE, None)
            flash("Confirmação inválida. Refaça a transferência.", "erro")
            return redirect(url_for("carteira.transferir_passo1"))

        try:
            transferir(
                current_user.id,
                pendente["destino_id"],
                para_decimal(pendente["valor"]),
                motivo=pendente["motivo"],
            )
            db.session.commit()
        except ErroMonetario as erro:
            db.session.rollback()
            session.pop(PENDENTE, None)
            current_app.logger.info("transferência recusada: %s", erro)
            flash(str(erro), "erro")
            return redirect(url_for("carteira.transferir_passo1"))

        session.pop(PENDENTE, None)
        flash(
            f"{pendente['valor']} VVC para {pendente['destino_nome']}.",
            "ok",
        )
        return redirect(url_for("carteira.minha_carteira"))

    return render_template("confirmar.html", formulario=formulario, pendente=pendente)
