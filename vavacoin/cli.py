"""Comandos de linha de comando.

O mínimo para operar a economia sem abrir o banco na mão. Abrir o banco na
mão é o começo do caminho que fez saldo sumir no Benbals.
"""

import click
from flask.cli import with_appcontext

from .constantes import SUPPLY_TOTAL
from .extensoes import db
from .moeda import criar_genese, soma_saldos, verificar_conservacao
from .operacoes import criar_convite, resetar_economia


@click.command("genese")
@with_appcontext
def comando_genese():
    """Cria o Banco Central com os 5.000,00. Rodar duas vezes não duplica."""
    bc = criar_genese()
    db.session.commit()
    click.echo(f"Banco Central: {bc.nome_usuario} — saldo {bc.saldo} VVC")


@click.command("convite")
@click.option("--destinatario", default=None, help="Nome do aluno, para auditoria.")
@click.option("--codigo", default=None, help="Código fixo; se omitido, é sorteado.")
@with_appcontext
def comando_convite(destinatario, codigo):
    """Emite um convite (um por aluno)."""
    convite = criar_convite(codigo=codigo, destinatario=destinatario)
    db.session.commit()
    click.echo(convite.codigo)


@click.command("conservacao")
@with_appcontext
def comando_conservacao():
    """Confere que a soma de todos os saldos ainda é o supply."""
    total = verificar_conservacao()
    click.echo(f"OK: {total} VVC (supply {SUPPLY_TOTAL})")


@click.command("resetar")
@click.confirmation_option(prompt="Recolher todo o dinheiro e redistribuir os 50?")
@with_appcontext
def comando_resetar():
    """Reset da economia: recolhe tudo e redistribui o saque inicial."""
    quantos = resetar_economia()
    db.session.commit()
    click.echo(f"Reset concluído para {quantos} participantes. Soma: {soma_saldos()}")


def registrar_comandos(app):
    app.cli.add_command(comando_genese)
    app.cli.add_command(comando_convite)
    app.cli.add_command(comando_conservacao)
    app.cli.add_command(comando_resetar)
