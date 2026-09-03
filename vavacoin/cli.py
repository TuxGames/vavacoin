"""Comandos de linha de comando.

Aqui moram os poderes do Banco Central. Não é acaso: o BC não autentica por
tela, então a única forma de exercer o que ele pode é ter acesso ao servidor.
Cada comando privilegiado busca o BC e o passa explicitamente para a operação.

Abrir o banco na mão continua sendo o que não se faz — é o começo do caminho
que fez saldo sumir no Benbals.
"""

import click
from flask.cli import with_appcontext

from .auditoria import auditar, linhas_extrato
from .autoridade import exigir_banco_central
from .constantes import SUPPLY_INICIAL, SUPPLY_MAXIMO
from .convites import link_de_convite
from .erros import ErroMonetario
from .extensoes import db
from .moeda import criar_genese, soma_saldos, verificar_conservacao
from .modelos import banco_central, buscar_usuario, registrar_acao
from .operacoes import criar_convite, criar_usuario, resetar_economia


def _autoridade():
    """O Banco Central, ou um erro legível se a gênese ainda não rodou."""
    bc = banco_central()
    if bc is None:
        raise click.ClickException("gênese ainda não rodou; use `flask genese`")
    return exigir_banco_central(bc)


@click.command("genese")
@with_appcontext
def comando_genese():
    """Cria o Banco Central com os 5.000,00. Rodar duas vezes não duplica.

    Único comando sem autoridade: é ele que cria a autoridade.
    """
    bc = criar_genese()
    db.session.commit()
    click.echo(f"Banco Central: {bc.nome_usuario} — saldo {bc.saldo} VVC")


@click.command("convite")
@click.option("--destinatario", default=None, help="Nome do aluno, para auditoria.")
@click.option("--codigo", default=None, help="Código fixo; se omitido, é sorteado.")
@with_appcontext
def comando_convite(destinatario, codigo):
    """Emite um convite (um por aluno). Poder do Banco Central.

    Imprime duas linhas: o código e o link. O código primeiro porque é o que
    já era impresso, e porque quem lê a saída com script lê a primeira linha.
    O link sai do endereço em ``VAVACOIN_BASE_URL`` — sem ela, o padrão é o
    servidor de desenvolvimento, e o link impresso avisa sozinho que está
    apontando para localhost.
    """
    convite = criar_convite(
        codigo=codigo, destinatario=destinatario, autoridade=_autoridade()
    )
    db.session.commit()
    click.echo(convite.codigo)
    click.echo(link_de_convite(convite.codigo))


@click.command("criar-conta")
@click.argument("nome_usuario")
@click.option(
    "--senha",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Pedida sem eco; guardada com hash bcrypt.",
)
@click.option("--exibicao", default=None, help="Nome que aparece para os outros.")
@with_appcontext
def comando_criar_conta(nome_usuario, senha, exibicao):
    """Cria uma conta com saldo zero. Poder do Banco Central."""
    usuario = criar_usuario(
        nome_usuario, senha, nome_exibicao=exibicao, autoridade=_autoridade()
    )
    db.session.commit()
    click.echo(f"conta {usuario.nome_usuario} criada com saldo {usuario.saldo}")


@click.command("senha-bc")
@click.option(
    "--senha",
    prompt="Senha do Banco Central",
    hide_input=True,
    confirmation_prompt=True,
    help=(
        "Pedida sem eco e confirmada duas vezes. Não há tamanho mínimo; "
        "vazia é recusada. Nunca passe por argumento em servidor "
        "compartilhado — fica no histórico do shell."
    ),
)
@with_appcontext
def comando_senha_bc(senha):
    """Define (ou troca) a senha do Banco Central.

    Só por aqui. Nunca no código, nunca em migration, nunca em seed — é a
    senha que dá god mode, e ela não pode existir em lugar nenhum que o git
    veja ou que alguém leia por cima do ombro.

    **Não há tamanho mínimo**: é decisão do dono do projeto, tomada mais de
    uma vez. Vazia continua recusada, e a diferença não é detalhe — o Banco
    Central entra pelo site, então senha vazia deixaria a conta que tem todo
    o dinheiro e todo o poder aberta para qualquer um que soubesse o nome
    dela. Sem mínimo é escolha; sem senha é porta destrancada.
    """
    bc = banco_central()
    if bc is None:
        raise click.ClickException("gênese ainda não rodou; use `flask genese`")
    if not senha:
        raise click.ClickException(
            "senha vazia deixaria o painel aberto para quem souber o nome da "
            "conta; qualquer senha serve, menos nenhuma"
        )
    bc.definir_senha(senha)
    registrar_acao(bc, "senha", alvo=bc.nome_usuario, detalhe="senha definida por CLI")
    db.session.commit()
    click.echo("Senha do Banco Central definida.")


@click.command("emitir")
@click.argument("valor")
@click.option("--motivo", required=True, help="Por quê. Fica no ledger.")
@with_appcontext
def comando_emitir(valor, motivo):
    """Cunha VVC novo no Banco Central, até o teto do supply.

    Mesmo caminho de emissão do ajuste de saldo: uma linha sem origem no
    ledger, com ator e motivo. Não existe um segundo tipo de gênese.
    """
    from .moeda import TIPO_EMISSAO, cabe_emitir, mover, supply_emitido

    bc = _autoridade()
    try:
        transacao = mover(
            None, bc, valor, tipo=TIPO_EMISSAO, motivo=motivo, ator=bc
        )
    except ErroMonetario as erro:
        raise click.ClickException(str(erro)) from erro
    db.session.commit()
    click.echo(
        f"Emitidos {transacao.valor} VVC. "
        f"Supply agora {supply_emitido()} de {SUPPLY_MAXIMO} "
        f"(ainda cabem {cabe_emitir()})."
    )


@click.command("criar-cassino")
@with_appcontext
def comando_criar_cassino():
    """Cria a conta da casa do Caladinho. Idempotente.

    É uma conta no ledger como qualquer outra, com saldo próprio: não é o
    Banco Central e não é a conta pessoal de ninguém. Nasce sem senha, então
    não entra pelo site.
    """
    from .caladinho import criar_casa

    conta = criar_casa(autoridade=_autoridade())
    db.session.commit()
    click.echo(f"Casa do Caladinho: {conta.nome_usuario} — caixa {conta.saldo} VVC")


@click.command("dono-cassino")
@click.argument("nome_usuario", required=False)
@click.option(
    "--sem-dono", is_flag=True, help="Tira o dono; a casa fica sem ninguém."
)
@with_appcontext
def comando_dono_cassino(nome_usuario, sem_dono):
    """Aponta de quem é a casa do Caladinho.

    O nome vem por argumento, nunca no código: quem é o dono é decisão de
    quem opera, e amarrar isso num literal transformaria uma troca de posse
    em deploy.
    """
    from .caladinho import definir_dono, dono

    if sem_dono:
        definir_dono(None, autoridade=_autoridade())
        db.session.commit()
        click.echo("A casa do Caladinho ficou sem dono.")
        return

    if not nome_usuario:
        atual = dono()
        if atual is None:
            raise click.ClickException(
                "diga o nome da conta, ou use --sem-dono; hoje não há dono"
            )
        click.echo(f"Dono atual: {atual.nome_usuario}")
        return

    alvo = buscar_usuario(nome_usuario)
    if alvo is None:
        raise click.ClickException(f"conta inexistente: {nome_usuario}")

    try:
        definir_dono(alvo, autoridade=_autoridade())
    except ErroMonetario as erro:
        raise click.ClickException(str(erro)) from erro
    db.session.commit()
    click.echo(f"O Caladinho agora é de {alvo.nome_usuario}.")


@click.command("criar-reino")
@click.argument("nome")
@click.option(
    "--cofre",
    default=None,
    help="Conta existente que vira o cofre, em vez de criar uma vazia.",
)
@click.option(
    "--sim",
    is_flag=True,
    default=False,
    help="Confirma a adoção sem perguntar.",
)
@with_appcontext
def comando_criar_reino(nome, cofre, sim):
    """Cria um reino. Idempotente: rodar de novo não duplica.

    O nome vem por argumento porque reino é dado, não código: "Alfheim" é uma
    linha da tabela, e o segundo reino tem de nascer sem tocar no primeiro.

    Sem ``--cofre``, o cofre nasce vazio e sem senha, como a casa do
    Caladinho: pôr dinheiro nele é ajuste do Banco Central pelo painel, e
    assim o dinheiro entra em circulação pelo mesmo caminho registrado que
    todo mundo.

    Com ``--cofre <conta>``, uma conta que **já existe** vira o cofre — o caso
    de quem montou o reino na mão antes de existir a tabela, com o dinheiro já
    dentro. Adotar não move um centavo: o saldo fica onde está e o extrato
    continua explicando o que explicava.

    A adoção **apaga a senha da conta**, porque cofre não entra pela tela. É
    uma porta que fecha na cara de quem usava essa conta para entrar, então o
    comando avisa e pergunta antes — ``--sim`` responde de antemão.
    """
    from .reinos import criar_reino, exigir_adotavel, reino_por_nome

    if reino_por_nome(nome) is not None:
        reino = reino_por_nome(nome)
        click.echo(
            f"Reino {reino.nome} já existe: cofre {reino.cofre.nome_usuario} — "
            f"{reino.cofre.saldo} VVC. Nada foi alterado."
        )
        return

    conta = None
    if cofre:
        conta = buscar_usuario(cofre)
        if conta is None:
            raise click.ClickException(f"conta não encontrada: {cofre}")
        try:
            exigir_adotavel(conta)
        except ErroMonetario as erro:
            raise click.ClickException(str(erro)) from erro

        click.echo(f"A conta {conta.nome_usuario} vai virar o cofre de {nome}.")
        click.echo(f"  saldo que fica onde está: {conta.saldo} VVC")
        click.echo(
            f"  lançamentos no extrato, intactos: "
            f"{_quantos_lancamentos(conta)}"
        )
        if conta.senha_hash:
            click.echo(
                "  ATENÇÃO: a senha vai ser apagada e a conta deixa de entrar "
                "pelo site. Quem usava esta conta para entrar perde o acesso."
            )
        if not sim:
            click.confirm("Adotar mesmo assim?", abort=True)

    try:
        reino = criar_reino(nome, autoridade=_autoridade(), cofre=conta)
    except ErroMonetario as erro:
        raise click.ClickException(str(erro)) from erro
    db.session.commit()
    click.echo(
        f"Reino {reino.nome}: cofre {reino.cofre.nome_usuario} — "
        f"{reino.cofre.saldo} VVC, juros {reino.juros_diarios}% ao dia"
    )


def _quantos_lancamentos(conta):
    """Quantas linhas do ledger falam desta conta. Só para a mensagem."""
    from .modelos import Transacao

    return db.session.execute(
        db.select(db.func.count(Transacao.id)).where(
            (Transacao.origem_id == conta.id)
            | (Transacao.destino_id == conta.id)
            | (Transacao.ator_id == conta.id)
        )
    ).scalar_one()


@click.command("operador-reino")
@click.argument("reino")
@click.argument("nome_usuario", required=False)
@click.option("--tirar", is_flag=True, help="Tira o papel em vez de dar.")
@with_appcontext
def comando_operador_reino(reino, nome_usuario, tirar):
    """Dá (ou tira) o papel de operador de um reino.

    O poder é do reino, não da pessoa: quem tem o papel opera, e tirar o
    papel tira o poder. Dá para haver mais de um — cada um é um ministro.

    Sem o nome da conta, lista quem opera hoje. Idempotente: dar o papel a
    quem já o tem não muda nada, e tirá-lo de quem não o tem também não.
    """
    from .reinos import definir_operador, eh_operador, operadores, reino_por_nome, tirar_operador

    alvo_reino = reino_por_nome(reino)
    if alvo_reino is None:
        raise click.ClickException(f"reino inexistente: {reino}")

    if not nome_usuario:
        atuais = operadores(alvo_reino)
        if not atuais:
            click.echo(f"{alvo_reino.nome} não tem operador.")
            return
        for pessoa in atuais:
            click.echo(f"{alvo_reino.nome}: {pessoa.nome_usuario}")
        return

    pessoa = buscar_usuario(nome_usuario)
    if pessoa is None:
        raise click.ClickException(f"conta inexistente: {nome_usuario}")

    try:
        if tirar:
            tirar_operador(alvo_reino, pessoa, autoridade=_autoridade())
            db.session.commit()
            click.echo(f"{pessoa.nome_usuario} não opera mais {alvo_reino.nome}.")
            return
        ja_era = eh_operador(alvo_reino, pessoa)
        definir_operador(alvo_reino, pessoa, autoridade=_autoridade())
    except ErroMonetario as erro:
        raise click.ClickException(str(erro)) from erro
    db.session.commit()
    click.echo(
        f"{pessoa.nome_usuario} "
        f"{'já operava' if ja_era else 'opera'} {alvo_reino.nome}."
    )

@click.command("conservacao")
@with_appcontext
def comando_conservacao():
    """Confere que a soma de todos os saldos ainda é o supply."""
    total = verificar_conservacao()
    click.echo(f"OK: {total} VVC (supply inicial {SUPPLY_INICIAL})")


@click.command("auditoria")
@with_appcontext
def comando_auditoria():
    """Relatório completo: massa conservada e ledger explicando os saldos.

    Sai com código de erro se algo estiver errado, para poder rodar em cron
    ou em CI sem depender de alguém ler a saída.
    """
    relatorio = auditar()
    economia = relatorio["economia"]
    ledger = relatorio["ledger"]

    click.echo(f"supply inicial     {economia['supply_inicial']} VVC")
    click.echo(f"supply atual       {economia['supply_atual']} VVC")
    click.echo(f"supply máximo      {economia['supply_maximo']} VVC")
    click.echo(f"ainda cabe emitir  {economia['cabe_emitir']} VVC")
    # Hífen comum, não o menos tipográfico: o console do Windows é cp1252 e
    # estoura com U+2212. A tela pode ter o sinal bonito; a CLI, não.
    click.echo(f"cunhado - queimado {economia['cunhado_depois']} VVC")
    click.echo(f"soma dos saldos    {economia['soma_dos_saldos']} VVC")
    click.echo(f"diferença          {economia['diferenca']} VVC")
    click.echo(f"não emitido (BC)   {economia['nao_emitido']} VVC")
    click.echo(f"em circulação      {economia['em_circulacao']} VVC")
    click.echo(f"contas             {economia['contas']}")
    click.echo(f"participantes      {economia['participantes']}")
    click.echo(f"transações         {economia['transacoes']}")

    for divergencia in ledger["saldos_divergentes"]:
        click.echo(
            f"  ! {divergencia['usuario']}: saldo {divergencia['saldo']}, "
            f"pelo ledger {divergencia['pelo_ledger']} "
            f"(diferença {divergencia['diferenca']})"
        )
    for linha in ledger["linhas_inconsistentes"]:
        click.echo(
            f"  ! transação {linha['transacao']} ({linha['lado']}): "
            f"gravado {linha['gravado']}, reconstruído {linha['reconstruido']}"
        )

    if not relatorio["ok"]:
        raise click.ClickException(
            "auditoria FALHOU: existe escrita de saldo fora do mover()"
        )
    click.echo("auditoria OK: o ledger explica cada centavo")


@click.command("extrato")
@click.argument("nome_usuario")
@click.option("--limite", default=20, show_default=True)
@with_appcontext
def comando_extrato(nome_usuario, limite):
    """Extrato de uma conta, do mais recente para o mais antigo."""
    usuario = buscar_usuario(nome_usuario)
    if usuario is None:
        raise click.ClickException(f"conta inexistente: {nome_usuario}")

    click.echo(f"{usuario.nome_exibicao} — saldo atual {usuario.saldo} VVC")
    for linha in linhas_extrato(usuario, limite=limite):
        quando = linha["quando"].strftime("%d/%m %H:%M")
        motivo = f" — {linha['motivo']}" if linha["motivo"] else ""
        click.echo(
            f"{quando}  {linha['valor_com_sinal']:>10}  "
            f"saldo {linha['saldo_depois']:>10}  "
            f"{linha['tipo']} com {linha['contraparte']}{motivo}"
        )


@click.command("resetar")
@click.confirmation_option(prompt="Recolher o saldo de todos para o Banco Central?")
@with_appcontext
def comando_resetar():
    """Reset da economia: recolhe o saldo de todos para o Banco Central."""
    quantos = resetar_economia(autoridade=_autoridade())
    db.session.commit()
    click.echo(f"Reset concluído para {quantos} participantes. Soma: {soma_saldos()}")


def registrar_comandos(app):
    app.cli.add_command(comando_genese)
    app.cli.add_command(comando_senha_bc)
    app.cli.add_command(comando_criar_cassino)
    app.cli.add_command(comando_dono_cassino)
    app.cli.add_command(comando_criar_reino)
    app.cli.add_command(comando_operador_reino)
    app.cli.add_command(comando_emitir)
    app.cli.add_command(comando_convite)
    app.cli.add_command(comando_criar_conta)
    app.cli.add_command(comando_conservacao)
    app.cli.add_command(comando_auditoria)
    app.cli.add_command(comando_extrato)
    app.cli.add_command(comando_resetar)
