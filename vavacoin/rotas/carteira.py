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

from ..auditoria import linhas_extrato, resumo_da_conta
from ..dinheiro import para_decimal
from ..erros import ErroMonetario
from ..extensoes import db
from ..formularios import FormularioConfirmacao, FormularioTransferencia
from ..modelos import Convite, Transacao, Usuario, buscar_usuario
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


@bp.route("/comprovante/<int:transacao_id>")
@login_required
def comprovante(transacao_id):
    """O recibo permanente de uma linha do ledger. **Só leitura.**

    Portado do Benbals (``/comprovante/<transaction_id>``), com o mesmo
    desenho de acesso, que é a parte que importa: **as duas partes envolvidas
    e o Banco Central**, mais ninguém. Não é link público. Aqui isso pesa
    mais do que lá — a base é uma turma de colégio, e um comprovante diz
    quem pagou quanto para quem.

    O que ficou de fora é o que não existe aqui: o Benbals congela score e
    faixa das partes no instante da transferência, e o VavaCoin não tem
    score. Sem esses campos, some junto o aviso de "score não disponível".

    Quem não tem acesso e quem pede um número que não existe recebem a mesma
    resposta — volta para o começo com a mesma frase. A diferença entre "não
    existe" e "não é seu" só serviria para alguém mapear, por tentativa, que
    transferências existem.
    """
    transacao = db.session.get(Transacao, transacao_id)
    envolvido = transacao is not None and current_user.id in (
        transacao.origem_id,
        transacao.destino_id,
    )
    # A checagem de existência vem separada da de acesso porque o Banco
    # Central passa pela segunda: sem ela, um número que não existe viraria
    # erro 500 no god mode em vez da mesma resposta que todo mundo recebe.
    if transacao is None or not (envolvido or current_user.eh_admin):
        flash("Comprovante não encontrado.", "erro")
        return redirect(url_for("publico.inicio"))

    nomes = dict(db.session.execute(db.select(Usuario.id, Usuario.nome_usuario)).all())
    exibidos = dict(db.session.execute(db.select(Usuario.id, Usuario.nome_exibicao)).all())
    saiu = transacao.origem_id == current_user.id
    return render_template(
        "comprovante.html",
        transacao=transacao,
        # A gênese e a emissão não têm origem; a queima não tem destino. O
        # travessão é o mesmo que o extrato já usa para "não veio de ninguém".
        origem=exibidos.get(transacao.origem_id, "—"),
        origem_usuario=nomes.get(transacao.origem_id),
        destino=exibidos.get(transacao.destino_id, "—"),
        destino_usuario=nomes.get(transacao.destino_id),
        # Só para o Banco Central, que é quem audita: quem mandou fazer,
        # quando não foi o dono da conta de origem.
        ator=exibidos.get(transacao.ator_id) if current_user.eh_admin else None,
        saiu=saiu,
    )


@bp.route("/perfil")
@login_required
def perfil():
    """O próprio perfil, e só o próprio.

    O Benbals tem perfil público em ``/perfil/<usuario>``, com saldo à vista.
    Aqui não: a decisão registrada é que saldo de terceiro não aparece para
    ninguém além do Banco Central. Trazer a tela sem trazer essa parte é de
    propósito.
    """
    convite = db.session.execute(
        db.select(Convite).where(Convite.usuario_id == current_user.id)
    ).scalar_one_or_none()
    return render_template(
        "perfil.html",
        usuario=current_user,
        resumo=resumo_da_conta(current_user),
        convite=convite,
    )


@bp.route("/transferir", methods=["GET", "POST"])
@login_required
def transferir_passo1():
    """Primeiro passo: só monta a intenção. Nada de dinheiro se move aqui."""
    formulario = FormularioTransferencia()
    if formulario.validate_on_submit():
        destino = buscar_usuario(formulario.destinatario.data)

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
