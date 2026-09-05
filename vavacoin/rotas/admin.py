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
from ..convites import link_de_convite
from ..erros import ContaComHistorico, ErroMonetario, ValorInvalido
from ..extensoes import db
from ..caladinho import casa as casa_do_cassino
from ..caladinho import criar_casa, exposicao_comprometida, limite_de_aposta
from ..formularios import (
    FormularioCadastroAberto,
    FormularioRankingVisivel,
    MOTIVO_PADRAO,
    FormularioAjusteDeSaldo,
    FormularioCriarConta,
    FormularioEmitirConvite,
    FormularioLinhaDaConta,
    FormularioReinosVisiveis,
    FormularioReset,
    FormularioVisibilidadeDoCaixa,
)
from ..nomes import normalizar_nome
from ..modelos import (
    CHAVE_CADASTRO_ABERTO,
    CHAVE_RANKING_VISIVEL,
    CHAVE_CAIXA_VISIVEL,
    CHAVE_REINOS_VISIVEIS,
    Convite,
    RegistroAdministrativo,
    Usuario,
    buscar_usuario,
    config_ligada,
    definir_config,
    registrar_acao,
)
from ..operacoes import (
    ajustar_saldo,
    apagar_conta,
    criar_convite,
    criar_usuario,
    destino_da_conta,
    destinos_das_contas,
    encerrar_conta,
    referencias_da_conta,
    remover_conta,
    resetar_economia,
)

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
        "form_ranking": FormularioRankingVisivel(
            visivel=config_ligada(CHAVE_RANKING_VISIVEL, padrao=True)
        ),
        "form_cadastro": FormularioCadastroAberto(
            aberto=config_ligada(CHAVE_CADASTRO_ABERTO, padrao=True)
        ),
        "form_reinos": FormularioReinosVisiveis(
            visiveis=config_ligada(CHAVE_REINOS_VISIVEIS)
        ),
    }


def _pagina(**extras):
    # Todas as contas, inclusive as de sistema: o Banco Central e a casa do
    # Caladinho aparecem para consulta. Quem não pode ser editado a tela
    # mostra sem campo — a regra de verdade está na rota, não no HTML.
    contas = list(
        db.session.execute(
            db.select(Usuario).order_by(
                Usuario.eh_banco_central.desc(),
                Usuario.eh_cassino.desc(),
                Usuario.saldo.desc(),
                Usuario.nome_usuario,
            )
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
        "linhas": {c.id: FormularioLinhaDaConta(saldo=str(c.saldo)) for c in contas},
        "erro_na_linha": {},
        "convites_livres": convites_livres,
        # Qual dos dois a conta aceita — calculado no servidor, para o botão
        # de apagar nem existir onde apagar não vale.
        "destino_da_conta": destinos_das_contas(contas),
        # O link é montado aqui, e não no template, porque o `url_for`
        # externo precisa do host da requisição — que o template tem, mas
        # repetido em cada linha do laço.
        "links_de_convite": {c.id: link_de_convite(c.codigo) for c in convites_livres},
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


@bp.route("/conta/<int:conta_id>", methods=["POST"])
def salvar_conta(conta_id):
    """Salva nome, senha e saldo de uma conta, direto da linha da tabela.

    O saldo continua passando por ``ajustar_saldo`` — nunca por ``UPDATE`` na
    linha. A tabela mudou a tela, não o caminho do dinheiro: sem isso a
    auditoria pararia de fechar no primeiro ajuste.
    """
    conta = db.session.get(Usuario, conta_id)
    if conta is None:
        abort(404)

    formulario = FormularioLinhaDaConta()
    if not formulario.validate_on_submit():
        erros = " ".join(
            m for campo in formulario for m in campo.errors
        )
        return _pagina(erro_na_linha={conta_id: erros or "dados inválidos"}), 400

    motivo = (formulario.motivo.data or "").strip() or MOTIVO_PADRAO
    novo_nome = (formulario.nome_usuario.data or "").strip()
    nova_senha = formulario.senha.data or ""

    try:
        # Renomear: "joao" para "João" é a mesma pessoa; para um nome que já
        # é de outra conta, não.
        if novo_nome and normalizar_nome(novo_nome) != conta.nome_normalizado:
            if conta.eh_conta_de_sistema:
                raise ValorInvalido("conta de sistema não é renomeada")
            if buscar_usuario(novo_nome) is not None:
                raise ValorInvalido(f"já existe uma conta chamada {novo_nome}")
        if novo_nome and novo_nome != conta.nome_usuario:
            if conta.eh_conta_de_sistema:
                raise ValorInvalido("conta de sistema não é renomeada")
            conta.definir_nome(novo_nome)

        if nova_senha:
            # Vale para TODA conta de sistema, e não só para o cassino: é o
            # que impede o cofre de um reino ganhar senha e virar "quem sabe
            # a senha é rei" — o bug das contas de tesouraria do Benbals.
            if conta.eh_conta_de_sistema:
                raise ValorInvalido("conta de sistema não entra pelo site")
            conta.definir_senha(nova_senha)

        if formulario.saldo.decimal != conta.saldo:
            ajustar_saldo(
                conta, formulario.saldo.decimal, motivo, autoridade=current_user
            )
        db.session.commit()
    except (ErroMonetario, ValueError) as erro:
        db.session.rollback()
        current_app.logger.info("edição da conta %s recusada: %s", conta_id, erro)
        return _pagina(erro_na_linha={conta_id: str(erro)}), 400

    flash(f"{conta.nome_usuario} salvo.", "ok")
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
            (formulario.motivo.data or "").strip() or MOTIVO_PADRAO,
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
    """Recolhe o saldo de todos para o Banco Central. Exige digitar a palavra."""
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


@bp.route("/conta/<int:conta_id>/apagar", methods=["POST"])
def apagar(conta_id):
    """Apaga a conta virgem. Recusa qualquer outra.

    A tela já mostra "apagar" só onde apagar vale, mas a decisão é conferida
    aqui de novo: entre desenhar a página e clicar, a conta pode ter recebido
    dinheiro — e aí apagar deixaria o ledger sem explicar centavos.
    """
    alvo = db.session.get(Usuario, conta_id)
    if alvo is None:
        abort(404)
    try:
        nome = apagar_conta(alvo, autoridade=current_user)
        db.session.commit()
        flash(f"Conta {nome} apagada.", "ok")
    except (ContaComHistorico, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("admin.painel"))


@bp.route("/conta/<int:conta_id>/encerrar", methods=["POST"])
def encerrar(conta_id):
    """Encerra a conta e devolve o saldo ao Banco Central, por ``mover()``."""
    alvo = db.session.get(Usuario, conta_id)
    if alvo is None:
        abort(404)
    try:
        encerrar_conta(
            alvo,
            (request.form.get("motivo") or "").strip() or "encerrada pelo painel",
            autoridade=current_user,
        )
        db.session.commit()
        flash(f"Conta {alvo.nome_usuario} encerrada.", "ok")
    except ErroMonetario as erro:
        db.session.rollback()
        flash(str(erro), "erro")
    return redirect(url_for("admin.painel"))


@bp.route("/conta/<int:conta_id>/remover", methods=["GET", "POST"])
def remover(conta_id):
    """Apaga a conta de verdade, com uma tela de conferência antes.

    A tela é ``GET`` e não convence de nada: mostra o usuário, o saldo e
    quantas linhas do ledger vão passar a apontar para "conta removida". É o
    número que decide, não uma frase.

    O ``POST`` confere tudo de novo. Entre desenhar a tela e clicar, a conta
    pode ter virado operadora de reino ou dona do cassino — e é o servidor
    quem recusa, como no apagar.
    """
    alvo = db.session.get(Usuario, conta_id)
    if alvo is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "remover_conta.html",
            conta=alvo,
            referencias=referencias_da_conta(alvo),
        )

    try:
        nome = remover_conta(
            alvo,
            (request.form.get("motivo") or "").strip() or "removida pelo painel",
            autoridade=current_user,
        )
        db.session.commit()
        flash(f"Conta {nome} removida.", "ok")
    except (ContaComHistorico, ErroMonetario) as erro:
        db.session.rollback()
        flash(str(erro), "erro")
        return redirect(url_for("admin.remover", conta_id=conta_id))
    return redirect(url_for("admin.painel"))


@bp.route("/reinos", methods=["POST"])
def reinos_visiveis():
    """Liga e desliga a página dos reinos para a turma.

    Nasce desligada, e é o mesmo interruptor do caixa do Caladinho: dado no
    banco, não constante no código — trocar de ideia não pode exigir deploy.

    Desligar fecha a página **e** a rota, não só o link do menu: o blueprint
    inteiro devolve 404 com o interruptor no zero. Esconder a porta sem
    trancá-la é meio caminho, e meio caminho aqui vira uma cobrança feita numa
    tela que ninguém deveria estar vendo.
    """
    formulario = FormularioReinosVisiveis()
    if not formulario.validate_on_submit():
        return _pagina(form_reinos=formulario), 400

    definir_config(CHAVE_REINOS_VISIVEIS, formulario.visiveis.data)
    registrar_acao(
        current_user,
        "reino",
        alvo="página",
        detalhe="visível" if formulario.visiveis.data else "escondida",
    )
    db.session.commit()
    flash("Reinos atualizados.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/cadastro", methods=["POST"])
def cadastro_aberto():
    """Liga e desliga a exigência de código de convite.

    Nasce ligado — o dono tirou a obrigação do convite porque quem entra
    começa com saldo zero. Desligar o interruptor volta a exigir o código sem
    precisar de deploy, e é por isso que ele existe em vez de o convite ter
    sido simplesmente arrancado.
    """
    formulario = FormularioCadastroAberto()
    if not formulario.validate_on_submit():
        return _pagina(form_cadastro=formulario), 400

    definir_config(CHAVE_CADASTRO_ABERTO, formulario.aberto.data)
    registrar_acao(
        current_user,
        "cadastro",
        alvo="convite",
        detalhe="opcional" if formulario.aberto.data else "obrigatório",
    )
    db.session.commit()
    flash("Cadastro atualizado.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/ranking", methods=["POST"])
def ranking_visivel():
    """Liga e desliga o ranking geral.

    Nasce ligado — é o que o dono quer usar agora. Desligar esconde o link e
    fecha a rota; o link é espelho, e quem tranca é o ``before_request`` do
    blueprint do ranking.
    """
    formulario = FormularioRankingVisivel()
    if not formulario.validate_on_submit():
        return _pagina(form_ranking=formulario), 400

    definir_config(CHAVE_RANKING_VISIVEL, formulario.visivel.data)
    registrar_acao(
        current_user,
        "ranking",
        alvo="geral",
        detalhe="visível" if formulario.visivel.data else "escondido",
    )
    db.session.commit()
    flash("Ranking atualizado.", "ok")
    return redirect(url_for("admin.painel"))


@bp.route("/senhas")
def senhas():
    """A aba discreta de senhas. Só o Banco Central, e todo acesso registrado.

    Fica fora do caminho de propósito: é a mesma informação de sempre, mas
    reunida, e informação reunida convida a ser aberta sem motivo. Não há link
    para cá no menu — quem chega, chega sabendo.

    **O que esta tela NÃO faz, e não tem como fazer:** mostrar a senha que a
    pessoa escolheu. O projeto guarda ``senha_hash`` (bcrypt) e nada mais; o
    ``CLAUDE.md`` registra "texto puro" como decisão, mas o código nunca
    implementou isso, e bcrypt não volta. Nenhuma tela recupera o que não foi
    guardado — o que dá para fazer por quem esqueceu a senha é **trocar**, que
    é o que a linha de cada conta oferece aqui e no painel.

    O acesso vai para o diário do god mode. Ler senha de gente é poder, e
    poder sem rastro é o que este projeto evita desde a primeira linha.
    """
    contas = list(
        db.session.execute(
            db.select(Usuario).order_by(
                Usuario.eh_banco_central.desc(),
                Usuario.eh_cassino.desc(),
                Usuario.nome_usuario,
            )
        ).scalars()
    )
    registrar_acao(
        current_user,
        "senhas",
        alvo=None,
        detalhe=f"abriu a aba de senhas ({len(contas)} contas)",
    )
    db.session.commit()
    return render_template("painel_senhas.html", contas=contas)


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
