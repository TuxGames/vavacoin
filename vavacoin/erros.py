"""Erros do núcleo monetário.

Todos herdam de :class:`ErroMonetario` para que uma camada de cima possa
tratar "deu problema com dinheiro" sem precisar enumerar os casos, e para que
nenhum deles seja confundido com um erro genérico de programação.
"""


class ErroMonetario(Exception):
    """Base de tudo que impede um movimento de dinheiro de acontecer."""


class ValorInvalido(ErroMonetario):
    """Valor zero, negativo, com precisão abaixo do centavo, ou de tipo errado."""


class MesmaConta(ErroMonetario):
    """Origem e destino são a mesma conta.

    Não é inofensivo "não fazer nada": o ledger ganharia uma linha que não
    move nada e a conta seria travada duas vezes.
    """


class SaldoInsuficiente(ErroMonetario):
    """A origem não tem o valor pedido. Nada é movido — nem parcialmente."""


class MassaViolada(ErroMonetario):
    """A soma dos saldos deixou de ser o supply.

    Se isto for levantado, existe um caminho de escrita fora do ``mover()``.
    Não trate: conserte o caminho.
    """


class ConviteInvalido(ErroMonetario):
    """Código de convite inexistente."""


class ConviteJaResgatado(ErroMonetario):
    """O código já foi usado. Os 50 são da pessoa, e a pessoa já sacou."""


class UsuarioJaResgatou(ErroMonetario):
    """A conta já resgatou um convite. Dez contas não viram 500 VVC."""


class GeneseAusente(ErroMonetario):
    """O Banco Central ainda não foi criado; não há dinheiro para mover."""


class SupplyInsuficiente(ErroMonetario):
    """O Banco Central não tem saldo não emitido suficiente.

    Com saque de 50 e supply de 5.000, isto acontece a partir da centésima
    primeira pessoa. A decisão registrada no CLAUDE.md é reduzir o saque
    inicial, nunca cunhar.
    """
