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
    criar_rodada_crash,
    criar_rodada_torre,
    abrir_porta,
    expirar_torres_abandonadas,
    historico_crash,
    historico_torre,
    rodada_torre_ativa,
    sacar_torre,
    ultima_rodada_torre,
    visao_da_rodada_torre,
    resolver_crash,
    rodada_crash_ativa,
    sacar_crash,
    ultima_rodada_crash,
    visao_da_rodada_crash,
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
    ultima_rodada,
    visao_da_rodada,
)
from ..erros import ErroDeJogo, ErroMonetario, SemRodadaAtiva, ValorInvalido
from ..formularios import FormularioCaixaDoDono
from ..extensoes import db
from ..mines import (
    CASAS,
    MAX_MINAS,
    MIN_MINAS,
    TETO_DO_MULTIPLICADOR,
    tabela_de_multiplicadores,
)
from ..crash import (
    ALVO_MINIMO,
    SEGUNDOS_PARA_DOBRAR,
    TETO_DO_MULTIPLICADOR as TETO_CRASH,
)
from ..torre import (
    MAX_PORTAS,
    MIN_PORTAS,
    TETO_DO_MULTIPLICADOR as TETO_TORRE,
    tabela_de_multiplicadores as tabela_da_torre,
)
from ..modelos import (
    CHAVE_CAIXA_VISIVEL,
    RodadaCrash,
    RodadaMines,
    RodadaTorre,
    config_ligada,
)
from ..vantagem import (
    JOGOS,
    MAXIMA,
    MINIMA,
    definir_vantagem,
    fator_de,
    todas as vantagens_vigentes,
    vantagem as vantagem_do_jogo,
)

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
        jogos=JOGOS,
        vantagens=vantagens_vigentes(),
        vantagem_min=MINIMA,
        vantagem_max=MAXIMA,
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


@bp.route("/casa/vantagem/<jogo>", methods=["POST"])
def casa_vantagem(jogo):
    """Salva a vantagem de um jogo. Editar na linha e salvar, nada mais.

    Mesmo formato do ajuste de saldo do painel do Banco Central, e pelo mesmo
    motivo: o dono reclamou que mudar número por caminho longo dá trabalho
    demais. Sem modal, sem confirmação, sem segundo passo.
    """
    if not _eh_dono():
        abort(403)
    try:
        nova = definir_vantagem(jogo, request.form.get("vantagem"), current_user)
        db.session.commit()
        flash(f"Vantagem do {jogo}: {nova}%.", "ok")
    except (ValorInvalido, ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.painel_da_casa"))


@bp.route("/mines")
def mines():
    """A rodada ativa, o resultado da última encerrada, ou o formulário.

    A ordem é essa, e a última encerrada entra por padrão de propósito: o
    resultado não pode depender de o navegador ter chegado com o ``?rodada=``
    do redirect. Reenviar o POST do clique, recarregar a página ou voltar pelo
    lobby chegam aqui sem parâmetro nenhum, e antes disso a tela respondia com
    tabuleiro fechado — o mesmo desenho de rodada nova, o que fazia parecer que
    o jogo tinha travado com a aposta cobrada.

    ``?nova=1`` é o caminho de volta ao formulário de aposta, e é para onde
    aponta o "Jogar de novo". ``?rodada=`` abre uma encerrada específica. Em
    nenhum caso o GET cria ou sorteia coisa alguma.
    """
    rodada = rodada_ativa(current_user)

    encerrada_id = request.args.get("rodada", type=int)
    if rodada is None and encerrada_id is not None:
        encerrada = db.session.get(RodadaMines, encerrada_id)
        if encerrada is None or encerrada.jogador_id != current_user.id:
            abort(404)
        rodada = encerrada
    elif rodada is None and not request.args.get("nova"):
        rodada = ultima_rodada(current_user)

    minas = request.args.get("minas", type=int) or 3
    minas = min(max(minas, MIN_MINAS), MAX_MINAS)
    if rodada is not None:
        minas = rodada.minas_escolhidas

    # A tabela na tela tem que ser a que vale para ESTA rodada. Com rodada
    # aberta é a congelada nela; sem rodada, a vigente — que é a que a próxima
    # aposta vai congelar.
    vantagem = rodada.vantagem if rodada is not None else vantagem_do_jogo("mines")

    return render_template(
        "mines.html",
        rodada=visao_da_rodada(rodada),
        casas=CASAS,
        minas=minas,
        min_minas=MIN_MINAS,
        max_minas=MAX_MINAS,
        teto=TETO_DO_MULTIPLICADOR,
        vantagem=vantagem,
        tabela=tabela_de_multiplicadores(minas, fator_de(vantagem)),
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
    except SemRodadaAtiva:
        # O mesmo clique chegou duas vezes — toque duplo, ou o navegador
        # reenviando o POST cuja resposta se perdeu. A rodada foi resolvida
        # pela primeira requisição; o GET cai na última encerrada e mostra o
        # tabuleiro. Reclamar aqui seria acusar a pessoa de um erro que é da
        # rede.
        db.session.rollback()
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
    except SemRodadaAtiva:
        db.session.rollback()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.mines", rodada=encerrada))


@bp.route("/crash")
def crash():
    """A rodada ativa, o resultado da última encerrada, ou o formulário.

    Mesma ordem do mines, e pelo mesmo motivo: resultado que só existe no
    ``?rodada=`` do redirect não sobrevive a rede ruim.

    A diferença é que aqui o GET **resolve** a rodada cujo prazo venceu. Não é
    sortear na leitura: o ponto de estouro foi sorteado na aposta e o alvo foi
    declarado junto, então o desfecho já existia — o GET só aplica. Sem isso,
    fechar a aba deixaria a rodada aberta para sempre, prendendo o caixa da
    casa na exposição comprometida.
    """
    try:
        resolver_crash(current_user)
        db.session.commit()
    except (ErroDeJogo, ErroMonetario):
        db.session.rollback()

    rodada = rodada_crash_ativa(current_user)

    encerrada_id = request.args.get("rodada", type=int)
    if rodada is None and encerrada_id is not None:
        encerrada = db.session.get(RodadaCrash, encerrada_id)
        if encerrada is None or encerrada.jogador_id != current_user.id:
            abort(404)
        rodada = encerrada
    elif rodada is None and not request.args.get("nova"):
        rodada = ultima_rodada_crash(current_user)

    vantagem = rodada.vantagem if rodada is not None else vantagem_do_jogo("crash")

    return render_template(
        "crash.html",
        rodada=visao_da_rodada_crash(rodada),
        teto=TETO_CRASH,
        alvo_minimo=ALVO_MINIMO,
        segundos_para_dobrar=SEGUNDOS_PARA_DOBRAR,
        vantagem=vantagem,
        aposta_max=limite_de_aposta(),
        caixa=_caixa_visivel(),
        historico=historico_crash(current_user),
    )


@bp.route("/crash/comecar", methods=["POST"])
def crash_comecar():
    try:
        criar_rodada_crash(
            current_user,
            (request.form.get("aposta") or "").strip().replace(",", "."),
            (request.form.get("alvo") or "").strip().replace(",", "."),
        )
        db.session.commit()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.crash"))


@bp.route("/crash/sacar", methods=["POST"])
def crash_sacar():
    encerrada = None
    try:
        rodada = sacar_crash(current_user)
        db.session.commit()
        if rodada is not None:
            encerrada = rodada.id
            if rodada.premio > 0:
                flash(f"Retirou {rodada.premio} VVC.", "ok")
    except SemRodadaAtiva:
        # O mesmo clique chegou duas vezes, ou a rodada foi resolvida pela
        # leitura da página no meio do caminho. A rede não é erro da pessoa.
        db.session.rollback()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.crash", rodada=encerrada))


@bp.route("/torre")
def torre():
    """A rodada ativa, o resultado da última encerrada, ou o formulário.

    Mesma ordem do mines e do crash, pela mesma lição: o resultado não pode
    depender de o navegador ter chegado com o ``?rodada=`` do redirect.

    O GET varre as rodadas abandonadas de todo mundo antes de desenhar. Não é
    sortear nem decidir nada — é fechar rodada que já passou da validade,
    pagando o que ela tinha conquistado, para que o caixa da casa não fique
    preso por quem fechou a aba.
    """
    try:
        expirar_torres_abandonadas()
        db.session.commit()
    except (ErroDeJogo, ErroMonetario):
        db.session.rollback()

    rodada = rodada_torre_ativa(current_user)

    encerrada_id = request.args.get("rodada", type=int)
    if rodada is None and encerrada_id is not None:
        encerrada = db.session.get(RodadaTorre, encerrada_id)
        if encerrada is None or encerrada.jogador_id != current_user.id:
            abort(404)
        rodada = encerrada
    elif rodada is None and not request.args.get("nova"):
        rodada = ultima_rodada_torre(current_user)

    portas = request.args.get("portas", type=int) or 3
    portas = min(max(portas, MIN_PORTAS), MAX_PORTAS)
    if rodada is not None:
        portas = rodada.portas

    vantagem = rodada.vantagem if rodada is not None else vantagem_do_jogo("torre")

    return render_template(
        "torre.html",
        rodada=visao_da_rodada_torre(rodada),
        portas=portas,
        min_portas=MIN_PORTAS,
        max_portas=MAX_PORTAS,
        teto=TETO_TORRE,
        vantagem=vantagem,
        tabela=tabela_da_torre(portas, fator_de(vantagem)),
        aposta_max=limite_de_aposta(),
        caixa=_caixa_visivel(),
        historico=historico_torre(current_user),
    )


@bp.route("/torre/comecar", methods=["POST"])
def torre_comecar():
    try:
        criar_rodada_torre(
            current_user,
            (request.form.get("aposta") or "").strip().replace(",", "."),
            request.form.get("portas"),
        )
        db.session.commit()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.torre"))


@bp.route("/torre/abrir", methods=["POST"])
def torre_abrir():
    encerrada = None
    try:
        rodada = abrir_porta(current_user, request.form.get("porta"))
        db.session.commit()
        if rodada.encerrada:
            # Leva para a tela do resultado: quem perdeu tem que poder ver
            # onde estavam as armadilhas.
            encerrada = rodada.id
    except SemRodadaAtiva:
        # O mesmo clique chegou duas vezes — toque duplo, ou o navegador
        # reenviando o POST cuja resposta se perdeu. A rodada já foi
        # resolvida; reclamar seria acusar a pessoa de um erro que é da rede.
        db.session.rollback()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.torre", rodada=encerrada))


@bp.route("/torre/sacar", methods=["POST"])
def torre_sacar():
    encerrada = None
    try:
        rodada = sacar_torre(current_user)
        db.session.commit()
        encerrada = rodada.id
        flash(f"Retirou {rodada.premio} VVC.", "ok")
    except SemRodadaAtiva:
        db.session.rollback()
    except (ErroDeJogo, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("caladinho.torre", rodada=encerrada))
