"""O painel do Banco Central — god mode.

O Banco Central é a autoridade do jogo e agora entra pela tela. É uma decisão
tomada de olhos abertos, registrada no CLAUDE.md: **quem entrar nele é dono de
tudo**. O que o código faz é não piorar isso — senha com hash, definida só por
CLI; os dois freios de login; e, principalmente, **rastro de tudo**.

Rastro em dois lugares, porque são duas perguntas diferentes:

- o **ledger** responde "de onde veio e para onde foi cada centavo", inclusive
  nos ajustes do administrador, que passam por ``mover()`` como qualquer
  outro movimento;
- o **diário** (``RegistroAdministrativo``) responde "quem decidiu isso e por
  quê", inclusive nas ações que não mexem em dinheiro.
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from ..auditoria import auditar, estado_da_economia, linhas_extrato
from ..erros import ErroMonetario
from ..extensoes import db
from ..caladinho import casa as casa_do_cassino
from ..caladinho import criar_casa, exposicao_comprometida, limite_de_aposta
from ..formularios import (
    FormularioAjusteDeSaldo,
    FormularioCriarConta,
    FormularioEmitirConvite,
    FormularioReset,
    FormularioVisibilidadeDoCaixa,
)
from ..modelos import (
    CHAVE_CAIXA_VISIVEL,
    Convite,
    RegistroAdministrativo,
    Usuario,
    buscar_usuario,
    config_ligada,
    definir_config,
    registrar_acao,
)
from ..operacoes import ajustar_saldo, criar_convite, criar_usuario, resetar_economia

bp = Blueprint("admin", __name__, url_prefix="/painel")


@bp.before_request
@login_required
def exigir_administrador():
    """Uma única porta para o painel inteiro.

    Guardar rota por rota é como se esquece uma. Aqui qualquer coisa sob
    ``/painel`` passa por esta checagem antes de existir.
    """
    if not current_user.is_authenticated or not current_user.eh_admin:
        abort(403)


def _formularios():
    """Os quatro formulários do painel, em branco.

    O de ajuste aceita vir pré-preenchido por ``?conta=fulano`` — é o que o
    link "ajustar" de cada linha da tabela faz. Sem isso, mexer no saldo de
    alguém exige digitar o nome de novo, olhando para a linha logo acima.
    """
    form_ajuste = FormularioAjusteDeSaldo()
    conta = request.args.get("conta")
    if conta and not form_ajuste.nome_usuario.data:
        form_ajuste.nome_usuario.data = conta
    return {
        "form_convite": FormularioEmitirConvite(),
        "form_conta": FormularioCriarConta(),
        "form_ajuste": form_ajuste,
        "form_reset": FormularioReset(),
        "form_caixa": FormularioVisibilidadeDoCaixa(
            visivel=config_ligada(CHAVE_CAIXA_VISIVEL)
        ),
    }


def _pagina(**extras):
    contas = list(
        db.session.execute(
            db.select(Usuario)
            .where(Usuario.eh_banco_central.is_(False))
            .order_by(Usuario.saldo.desc(), Usuario.nome_usuario)
        ).scalars()
    )
    convites_livres = list(
        db.session.execute(
            db.select(Convite).where(Convite.usuario_id.is_(None)).order_by(Convite.id)
        ).scalars()
    )
    registros = list(
        db.session.execute(
            db.select(RegistroAdministrativo)
            .order_by(RegistroAdministrativo.id.desc())
            .limit(30)
        ).scalars()
    )
    conta_da_casa = casa_do_cassino()
    contexto = {
        "estado": estado_da_economia(),
        "contas": contas,
        "convites_livres": convites_livres,
        "registros": registros,
        "casa": conta_da_casa,
        "caixa_visivel": config_ligada(CHAVE_CAIXA_VISIVEL),
        "comprometido": exposicao_comprometida() if conta_da_casa else None,
        "aposta_max": limite_de_aposta() if conta_da_casa else None,
    }
    contexto.update(_formularios())
    contexto.update(extras)
    return render_template("painel.html", **contexto)


@bp.route("/")
def painel():
    return _pagina()


@bp.route("/convite", methods=["POST"])
def emitir_convite():
    formulario = FormularioEmitirConvite()
    if not formulario.validate_on_submit():
        return _pagina(form_convite=formulario), 400

    convite = criar_convite(
        destinatario=formulario.destinatario.data.strip() or None,
        autoridade=current_user,
    )
    db.session.commit()
    flash(f"Convite emitido: {convite.codigo}", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/conta", methods=["POST"])
def criar_conta():
    formulario = FormularioCriarConta()
    if not formulario.validate_on_submit():
        return _pagina(form_conta=formulario), 400

    try:
        usuario = criar_usuario(
            # Como a pessoa escreveu: a unicidade é pela forma normalizada.
            formulario.nome_usuario.data.strip(),
            formulario.senha.data,
            nome_exibicao=formulario.nome_exibicao.data.strip(),
            autoridade=current_user,
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Esse nome de usuário já existe.", "erro")
        return _pagina(form_conta=formulario), 409

    flash(f"Conta {usuario.nome_usuario} criada com saldo 0,00.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/saldo", methods=["POST"])
def ajustar():
    """Conserta o saldo de alguém. Para cima, cunha — e o ledger diz quanto."""
    formulario = FormularioAjusteDeSaldo()
    if not formulario.validate_on_submit():
        return _pagina(form_ajuste=formulario), 400

    alvo = buscar_usuario(formulario.nome_usuario.data)
    if alvo is None:
        flash("Não existe ninguém com esse usuário.", "erro")
        return _pagina(form_ajuste=formulario), 404

    anterior = alvo.saldo
    try:
        ajustar_saldo(
            alvo,
            formulario.novo_saldo.decimal,
            formulario.motivo.data.strip(),
            autoridade=current_user,
        )
        db.session.commit()
    except ErroMonetario as erro:
        db.session.rollback()
        current_app.logger.info("ajuste recusado: %s", erro)
        flash(str(erro), "erro")
        return _pagina(form_ajuste=formulario), 400

    flash(
        f"Saldo de {alvo.nome_usuario}: {anterior} → {alvo.saldo} VVC.",
        "ok",
    )
    return redirect(url_for("admin.painel"))


@bp.route("/extrato/<nome_usuario>")
def extrato_de(nome_usuario):
    """Extrato de qualquer um. Olhar também deixa rastro."""
    alvo = buscar_usuario(nome_usuario)
    if alvo is None:
        abort(404)

    registrar_acao(current_user, "extrato", alvo=alvo.nome_usuario)
    db.session.commit()
    return render_template(
        "painel_extrato.html",
        alvo=alvo,
        linhas=linhas_extrato(alvo, limite=200),
    )


@bp.route("/reset", methods=["POST"])
def reset():
    """Recolhe de todos e redistribui os 50. Exige digitar a palavra."""
    formulario = FormularioReset()
    if not formulario.validate_on_submit():
        flash("Para resetar, digite RESETAR na confirmação.", "erro")
        return _pagina(form_reset=formulario), 400

    quantos = resetar_economia(
        autoridade=current_user,
        motivo=formulario.motivo.data.strip() or "reset da economia",
    )
    db.session.commit()
    flash(f"Reset concluído para {quantos} participantes.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/cassino", methods=["POST"])
def cassino():
    """Cria a casa e liga/desliga a exibição do caixa para os jogadores.

    O interruptor é dado no banco, não constante no código: trocar de ideia
    sobre mostrar o caixa não pode exigir deploy.
    """
    formulario = FormularioVisibilidadeDoCaixa()
    if not formulario.validate_on_submit():
        return _pagina(form_caixa=formulario), 400

    if casa_do_cassino() is None:
        criar_casa(autoridade=current_user)

    definir_config(CHAVE_CAIXA_VISIVEL, formulario.visivel.data)
    registrar_acao(
        current_user,
        "cassino",
        alvo="caixa",
        detalhe="visível" if formulario.visivel.data else "escondido",
    )
    db.session.commit()
    flash("Caladinho atualizado.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/auditoria")
def auditoria():
    """A mesma auditoria da CLI, para conferir sem sair do painel."""
    relatorio = auditar()
    if relatorio["ok"]:
        flash("Auditoria OK: o ledger explica cada centavo.", "ok")
    else:
        flash(
            "AUDITORIA FALHOU: existe saldo que o ledger não explica. "
            "Rode `flask auditoria` no servidor para o detalhe.",
            "erro",
        )
    return redirect(url_for("admin.painel"))


@bp.route("/registros")
def registros():
    """O diário inteiro, não só os últimos trinta."""
    pagina = request.args.get("pagina", 1, type=int)
    consulta = db.select(RegistroAdministrativo).order_by(
        RegistroAdministrativo.id.desc()
    )
    itens = list(
        db.session.execute(consulta.limit(100).offset((pagina - 1) * 100)).scalars()
    )
    return render_template("painel_registros.html", registros=itens, pagina=pagina)
