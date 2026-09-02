"""O Caladinho na web.

Sem JavaScript: cada casa do tabuleiro é um formulário que posta a posição, e
a página recarrega com o que o servidor decidiu. A CSP do projeto não permite
script embutido, e o jogo não precisa de um — o resultado nunca foi do
navegador de qualquer forma.

``GET`` nunca cria rodada nem sorteia nada: recarregar a página retoma a mesma
rodada ativa, com as mesmas minas e as mesmas casas abertas. Sair e voltar não
escapa de mina nem gera tabuleiro novo.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..caladinho import (
    aportar,
    casa,
    criar_rodada,
    dono,
    exposicao_comprometida,
    historico,
    limite_de_aposta,
    livre_para_retirar,
    lucro_do_dono,
    retirar,
    retirar_do_caixa,
    revelar_casa,
    rodada_ativa,
    visao_da_rodada,
)
from ..erros import ErroDeJogo, ErroMonetario
from ..formularios import FormularioCaixaDoDono
from ..extensoes import db
from ..mines import (
    CASAS,
    MAX_MINAS,
    MIN_MINAS,
    TETO_DO_MULTIPLICADOR,
    VANTAGEM_DA_CASA,
    tabela_de_multiplicadores,
)
from ..modelos import CHAVE_CAIXA_VISIVEL, RodadaMines, config_ligada

bp = Blueprint("caladinho", __name__, url_prefix="/caladinho")


@bp.before_request
@login_required
def exigir_jogador():
    """O cassino é para quem está na economia."""
    return None


def _caixa_visivel():
    """O saldo da casa aparece? Interruptor do painel do Banco Central."""
    conta = casa()
    if conta is None or not config_ligada(CHAVE_CAIXA_VISIVEL, padrao=False):
        return None
    return conta.saldo


def _eh_dono():
    conta = dono()
    return conta is not None and conta.id == current_user.id


@bp.route("/")
def lobby():
    return render_template(
        "caladinho.html", caixa=_caixa_visivel(), eh_dono=_eh_dono()
    )


@bp.route("/casa")
def painel_da_casa():
    """A tela do dono: caixa, comprometido, livre e lucro."""
    if not _eh_dono():
        abort(403)

    conta = casa()
    return render_template(
        "caladinho_casa.html",
        caixa=conta.saldo,
        comprometido=exposicao_comprometida(),
        livre=livre_para_retirar(),
        lucro=lucro_do_dono(),
        desde=conta.dono_desde,
        formulario=FormularioCaixaDoDono(),
    )


@bp.route("/casa/aportar", methods=["POST"])
def casa_aportar():
    formulario = FormularioCaixaDoDono()
    if not _eh_dono():
        abort(403)
    if not formulario.validate_on_submit():
        flash(" ".join(m for c in formulario for m in c.errors), "erro")
        return redirect(url_for("caladinho.painel_da_casa"))

    try:
        aportar(current_user, formulario.valor.decimal)
        db.session.commit()
        flash(f"Aportou {formulario.valor.decimal} VVC.", "ok")
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.painel_da_casa"))


@bp.route("/casa/retirar", methods=["POST"])
def casa_retirar():
    formulario = FormularioCaixaDoDono()
    if not _eh_dono():
        abort(403)
    if not formulario.validate_on_submit():
        flash(" ".join(m for c in formulario for m in c.errors), "erro")
        return redirect(url_for("caladinho.painel_da_casa"))

    try:
        retirar_do_caixa(current_user, formulario.valor.decimal)
        db.session.commit()
        flash(f"Retirou {formulario.valor.decimal} VVC.", "ok")
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.painel_da_casa"))


@bp.route("/mines")
def mines():
    """A rodada ativa, ou o resultado da que acabou de encerrar.

    ``?rodada=`` mostra uma rodada encerrada com o tabuleiro à mostra — é para
    onde revelar e retirar redirecionam. Sem ele, retoma a rodada ativa; sem
    rodada ativa, mostra o formulário. Em nenhum caso o GET cria ou sorteia
    coisa alguma.
    """
    rodada = rodada_ativa(current_user)

    encerrada_id = request.args.get("rodada", type=int)
    if rodada is None and encerrada_id is not None:
        encerrada = db.session.get(RodadaMines, encerrada_id)
        if encerrada is None or encerrada.jogador_id != current_user.id:
            abort(404)
        rodada = encerrada

    minas = request.args.get("minas", type=int) or 3
    minas = min(max(minas, MIN_MINAS), MAX_MINAS)
    if rodada is not None:
        minas = rodada.minas_escolhidas

    return render_template(
        "mines.html",
        rodada=visao_da_rodada(rodada),
        casas=CASAS,
        minas=minas,
        min_minas=MIN_MINAS,
        max_minas=MAX_MINAS,
        teto=TETO_DO_MULTIPLICADOR,
        vantagem=(1 - VANTAGEM_DA_CASA) * 100,
        tabela=tabela_de_multiplicadores(minas),
        aposta_max=limite_de_aposta(),
        caixa=_caixa_visivel(),
        historico=historico(current_user),
    )


@bp.route("/mines/comecar", methods=["POST"])
def comecar():
    try:
        criar_rodada(
            current_user,
            (request.form.get("aposta") or "").strip().replace(",", "."),
            request.form.get("minas"),
        )
        db.session.commit()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.mines"))


@bp.route("/mines/revelar", methods=["POST"])
def revelar():
    encerrada = None
    try:
        rodada = revelar_casa(current_user, request.form.get("casa"))
        db.session.commit()
        if rodada.encerrada:
            # Leva para a tela do resultado: quem perdeu tem que poder ver
            # onde estavam as minas.
            encerrada = rodada.id
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.mines", rodada=encerrada))


@bp.route("/mines/retirar", methods=["POST"])
def sacar():
    encerrada = None
    try:
        rodada = retirar(current_user)
        db.session.commit()
        encerrada = rodada.id
        flash(f"Retirou {rodada.premio} VVC.", "ok")
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.mines", rodada=encerrada))
