"""Entrar, sair e a única porta de entrada: o cadastro por convite."""

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from ..erros import ErroMonetario
from ..extensoes import db
from ..formularios import FormularioCadastro, FormularioLogin
from ..limite import limitador_taxa, trava_por_falhas
from ..modelos import Usuario

bp = Blueprint("auth", __name__)


def _endereco():
    """De onde veio a tentativa. Chave do limite de taxa."""
    return request.remote_addr or "desconhecido"


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("publico.inicio"))

    formulario = FormularioLogin()
    if formulario.validate_on_submit():
        nome = formulario.nome_usuario.data.strip().lower()

        # Freio 1, do servidor: rajada de um mesmo endereço. Conta toda
        # tentativa, certa ou errada, e é o mais barato de avaliar.
        espera = limitador_taxa.registrar(_endereco())
        if espera:
            flash(f"Tentativas demais. Espere {espera} segundos.", "erro")
            return render_template("login.html", formulario=formulario), 429

        # Freio 2, da conta: erros seguidos nesta conta específica. É este
        # que impede chute paciente — o de cima, sozinho, ainda deixaria
        # milhares de tentativas por dia contra uma senha fraca.
        espera = trava_por_falhas.segundos_de_bloqueio(nome)
        if espera:
            flash(
                f"Conta temporariamente bloqueada. Espere {espera} segundos.",
                "erro",
            )
            return render_template("login.html", formulario=formulario), 429

        usuario = db.session.execute(
            db.select(Usuario).where(Usuario.nome_usuario == nome)
        ).scalar_one_or_none()

        # `is_active` False barra o Banco Central aqui: `login_user` recusa.
        # A conta dele nem chega a comparar senha, porque senha ele não tem.
        if usuario is None or not usuario.verificar_senha(formulario.senha.data):
            trava_por_falhas.registrar_falha(nome)
            # Mensagem única de propósito: dizer "usuário não existe" entrega
            # quem tem conta para quem está sondando.
            flash("Usuário ou senha incorretos.", "erro")
            return render_template("login.html", formulario=formulario), 401

        if not login_user(usuario):
            trava_por_falhas.registrar_falha(nome)
            flash("Esta conta não entra pelo site.", "erro")
            return render_template("login.html", formulario=formulario), 403

        trava_por_falhas.limpar(nome)
        return redirect(url_for("publico.inicio"))

    return render_template("login.html", formulario=formulario)


@bp.route("/sair", methods=["POST"])
@login_required
def logout():
    """Só por POST: link de logout é CSRF de brincadeira, mas é CSRF."""
    logout_user()
    flash("Você saiu.", "ok")
    return redirect(url_for("publico.inicio"))


@bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    """Cria a conta e saca os 50 do Banco Central, num passo só.

    Não existe cadastro sem convite: é o que garante que os 50 são da pessoa
    e que ninguém entra sem ter pedido.
    """
    if current_user.is_authenticated:
        return redirect(url_for("publico.inicio"))

    formulario = FormularioCadastro()
    if formulario.validate_on_submit():
        from ..operacoes import cadastrar_por_convite

        nome = formulario.nome_usuario.data.strip().lower()
        try:
            usuario = cadastrar_por_convite(
                nome,
                formulario.senha.data,
                formulario.codigo.data.strip(),
                nome_exibicao=formulario.nome_exibicao.data.strip(),
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Esse nome de usuário já existe.", "erro")
            return render_template("cadastro.html", formulario=formulario), 409
        except ErroMonetario as erro:
            db.session.rollback()
            current_app.logger.info("cadastro recusado: %s", erro)
            flash(str(erro), "erro")
            return render_template("cadastro.html", formulario=formulario), 400

        login_user(usuario)
        flash("Conta criada. Os 50 VVC já estão com você.", "ok")
        return redirect(url_for("publico.inicio"))

    return render_template("cadastro.html", formulario=formulario)
