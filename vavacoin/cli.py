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
from .constantes import SUPPLY_TOTAL
from .extensoes import db
from .moeda import criar_genese, soma_saldos, verificar_conservacao
from .modelos import Usuario, banco_central
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
    """Emite um convite (um por aluno). Poder do Banco Central."""
    convite = criar_convite(
        codigo=codigo, destinatario=destinatario, autoridade=_autoridade()
    )
    db.session.commit()
    click.echo(convite.codigo)


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


@click.command("conservacao")
@with_appcontext
def comando_conservacao():
    """Confere que a soma de todos os saldos ainda é o supply."""
    total = verificar_conservacao()
    click.echo(f"OK: {total} VVC (supply {SUPPLY_TOTAL})")


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

    click.echo(f"supply esperado    {economia['supply_esperado']} VVC")
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
    usuario = db.session.execute(
        db.select(Usuario).where(Usuario.nome_usuario == nome_usuario)
    ).scalar_one_or_none()
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
@click.confirmation_option(prompt="Recolher todo o dinheiro e redistribuir os 50?")
@with_appcontext
def comando_resetar():
    """Reset da economia: recolhe de todos e redistribui o saque inicial."""
    quantos = resetar_economia(autoridade=_autoridade())
    db.session.commit()
    click.echo(f"Reset concluído para {quantos} participantes. Soma: {soma_saldos()}")


def registrar_comandos(app):
    app.cli.add_command(comando_genese)
    app.cli.add_command(comando_convite)
    app.cli.add_command(comando_criar_conta)
    app.cli.add_command(comando_conservacao)
    app.cli.add_command(comando_auditoria)
    app.cli.add_command(comando_extrato)
    app.cli.add_command(comando_resetar)
