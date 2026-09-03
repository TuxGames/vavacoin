"""Os reinos na web: entrar, sair, pagar imposto — e a mesa do operador.

Sem JavaScript, como o resto do site: cada ação é um formulário que posta e a
página recarrega com o que o servidor decidiu.

A tela do operador tem **token de uso único** em cobrança e distribuição. Não
é enfeite: distribuir para vinte pessoas com o botão clicado duas vezes pagaria
quarenta vezes, e o operador não tem como desfazer isso.
"""

import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from ..erros import ErroMonetario
from ..extensoes import db
from ..modelos import (
    CHAVE_REINOS_VISIVEIS,
    Cobranca,
    Divida,
    Usuario,
    buscar_usuario,
    config_ligada,
)
from ..reinos import (
    JUROS_MAXIMO,
    JUROS_MINIMO,
    cidadania_de,
    cidadaos,
    faixa_de_negociacao,
    negociar_divida,
    perdoar_divida,
    pode_negociar,
    cobrar,
    devido,
    distribuir,
    dividas_em_aberto,
    definir_juros,
    eh_cidadao,
    eh_operador,
    entrar_no_reino,
    operadores,
    pagar_divida,
    reino_por_nome,
    reinos,
    restante,
    sair_do_reino,
    total_devido,
)

bp = Blueprint("reino", __name__, url_prefix="/reino")

#: Token de uso único das ações em lote, guardado na sessão do navegador.
LOTE = "token_do_lote"


@bp.before_request
@login_required
def exigir_pessoa():
    """Reino é para quem está na economia."""
    return None


def _visivel():
    """A página aparece? Interruptor do Banco Central, como as outras."""
    return config_ligada(CHAVE_REINOS_VISIVEIS, padrao=False)


def _exigir_visivel():
    # O Banco Central vê sempre: é ele quem liga o interruptor, e precisa
    # conferir a tela antes de mostrá-la para a turma.
    if not _visivel() and not current_user.eh_admin:
        abort(404)


def _reino_ou_404(nome):
    reino = reino_por_nome(nome)
    if reino is None:
        abort(404)
    return reino


def _novo_token():
    token = secrets.token_urlsafe(16)
    session[LOTE] = token
    return token


def _consumir_token(enviado):
    """Gasta o token de uso único. Segundo clique não encontra nada.

    É o que impede a distribuição de pagar duas vezes quando o botão é
    clicado duas vezes ou o navegador reenvia o POST.
    """
    guardado = session.pop(LOTE, None)
    if not enviado or not guardado or not secrets.compare_digest(enviado, guardado):
        return None
    return enviado


@bp.route("/")
def lista():
    _exigir_visivel()
    todos = reinos()
    if len(todos) == 1:
        return redirect(url_for("reino.ver", nome=todos[0].nome_normalizado))
    return render_template("reinos.html", reinos=todos, eh_cidadao=eh_cidadao)


@bp.route("/<nome>")
def ver(nome):
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    minhas = [
        d for d in dividas_em_aberto(current_user, reino=reino) if devido(d) > 0
    ]
    # Cidadania e exclusiva: quem ja e de outro reino nao ve o botao de
    # entrar, ve de onde precisa sair antes.
    atual = cidadania_de(current_user)
    outro = atual.reino if atual is not None and atual.reino_id != reino.id else None

    return render_template(
        "reino.html",
        reino=reino,
        cofre=reino.cofre.saldo,
        cidadaos=cidadaos(reino),
        sou_cidadao=eh_cidadao(reino, current_user),
        outro_reino=outro,
        sou_operador=eh_operador(reino, current_user),
        operadores=operadores(reino),
        dividas=[(d, devido(d), restante(d)) for d in minhas],
        total=total_devido(current_user, reino=reino),
    )


@bp.route("/<nome>/entrar", methods=["POST"])
def entrar(nome):
    """A pessoa pede para entrar. Ato dela, sempre."""
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    try:
        entrar_no_reino(reino, current_user)
        db.session.commit()
        flash(f"Você entrou em {reino.nome}.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.ver", nome=nome))


@bp.route("/<nome>/sair", methods=["POST"])
def sair(nome):
    """A pessoa pede para sair. A dívida em aberto congela, não some."""
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    try:
        sair_do_reino(reino, current_user)
        db.session.commit()
        flash(f"Você saiu de {reino.nome}.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.ver", nome=nome))


@bp.route("/divida/<int:divida_id>/pagar", methods=["POST"])
def pagar(divida_id):
    """O devedor paga, no todo ou em parte. É ato dele — só dele."""
    divida = db.session.get(Divida, divida_id)
    if divida is None or divida.devedor_id != current_user.id:
        # Mesma resposta para "não existe" e "não é sua", como no comprovante.
        flash("Dívida não encontrada.", "erro")
        return redirect(url_for("publico.inicio"))

    quanto = (request.form.get("quanto") or "").strip().replace(",", ".")
    try:
        pagar_divida(divida, quanto or None)
        db.session.commit()
        flash("Pagamento feito.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.ver", nome=divida.reino.nome_normalizado))


# --- a mesa do operador -----------------------------------------------------


@bp.route("/<nome>/operar")
def operar(nome):
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    if not eh_operador(reino, current_user):
        abort(403)

    lista_de_cidadaos = cidadaos(reino)
    devidos = {p.id: total_devido(p, reino=reino) for p in lista_de_cidadaos}

    # As dividas que ESTA pessoa pode negociar. A lista sai do que ela
    # criou (ou herdou, quando o autor nao e mais operador), nao do reino
    # inteiro: quem negocia e quem cobrou.
    negociaveis = []
    for divida in db.session.execute(
        db.select(Divida)
        .where(Divida.reino_id == reino.id, Divida.quitada_em.is_(None))
        .order_by(Divida.id)
    ).scalars():
        if not pode_negociar(divida, current_user):
            continue
        piso, teto = faixa_de_negociacao(divida)
        negociaveis.append((divida, devido(divida), piso, teto))

    return render_template(
        "reino_operar.html",
        reino=reino,
        cofre=reino.cofre.saldo,
        cidadaos=lista_de_cidadaos,
        devidos=devidos,
        negociaveis=negociaveis,
        juros_min=JUROS_MINIMO,
        juros_max=JUROS_MAXIMO,
        token=_novo_token(),
        absoluta=Cobranca.ABSOLUTA,
        percentual=Cobranca.PERCENTUAL,
    )


def _marcados(reino):
    """As pessoas que o operador marcou na checklist."""
    ids = {int(i) for i in request.form.getlist("cidadao") if i.isdigit()}
    return [p for p in cidadaos(reino) if p.id in ids]


@bp.route("/<nome>/cobrar", methods=["POST"])
def cobrar_imposto(nome):
    """Gera as dívidas. Não move um centavo — quem paga é o devedor."""
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    if not eh_operador(reino, current_user):
        abort(403)

    token = _consumir_token(request.form.get("token"))
    if token is None:
        flash("Essa cobrança já foi enviada.", "erro")
        return redirect(url_for("reino.operar", nome=nome))

    try:
        _, criadas = cobrar(
            reino,
            current_user,
            request.form.get("tipo"),
            request.form.get("parametro"),
            request.form.get("motivo_cobranca"),
            pessoas=_marcados(reino),
            token=token,
        )
        db.session.commit()
        flash(f"{len(criadas)} dívida(s) criada(s).", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.operar", nome=nome))


@bp.route("/<nome>/distribuir", methods=["POST"])
def distribuir_do_cofre(nome):
    """Paga o mesmo valor a cada marcado. Tudo ou nada."""
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    if not eh_operador(reino, current_user):
        abort(403)

    if _consumir_token(request.form.get("token")) is None:
        flash("Essa distribuição já foi enviada.", "erro")
        return redirect(url_for("reino.operar", nome=nome))

    try:
        total, alvos = distribuir(
            reino,
            current_user,
            request.form.get("valor"),
            _marcados(reino),
            request.form.get("motivo_repasse"),
        )
        db.session.commit()
        flash(f"{total} VVC para {len(alvos)} cidadão(s).", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.operar", nome=nome))


@bp.route("/divida/<int:divida_id>/negociar", methods=["POST"])
def negociar(divida_id):
    """O credor fixa o valor de quitação. **Não move dinheiro.**"""
    divida = db.session.get(Divida, divida_id)
    if divida is None or not pode_negociar(divida, current_user):
        # Mesma resposta para "não existe" e "não é sua", como no comprovante.
        flash("Dívida não encontrada.", "erro")
        return redirect(url_for("publico.inicio"))

    destino = divida.reino.nome_normalizado
    try:
        negociar_divida(divida, request.form.get("valor"), current_user)
        db.session.commit()
        flash("Dívida renegociada.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.operar", nome=destino))


@bp.route("/divida/<int:divida_id>/perdoar", methods=["POST"])
def perdoar(divida_id):
    """O credor apaga a dívida. Também não move dinheiro."""
    divida = db.session.get(Divida, divida_id)
    if divida is None or not pode_negociar(divida, current_user):
        flash("Dívida não encontrada.", "erro")
        return redirect(url_for("publico.inicio"))

    destino = divida.reino.nome_normalizado
    try:
        perdoado = perdoar_divida(divida, current_user)
        db.session.commit()
        flash(f"Dívida de {perdoado} VVC perdoada.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.operar", nome=destino))


@bp.route("/<nome>/juros", methods=["POST"])
def juros(nome):
    _exigir_visivel()
    reino = _reino_ou_404(nome)
    if not eh_operador(reino, current_user):
        abort(403)
    try:
        novos = definir_juros(reino, request.form.get("juros"), current_user)
        db.session.commit()
        flash(f"Juros: {novos}% ao dia.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("reino.operar", nome=nome))
