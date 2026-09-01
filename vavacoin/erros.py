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


class SemAutoridade(ErroMonetario):
    """A operação exige o Banco Central e não recebeu o Banco Central.

    O BC é a autoridade do jogo (emite convite, cria conta, roda gênese,
    executa reset) e é também uma conta com dinheiro. Quem entra nele é dono
    de tudo — por isso os poderes precisam ser pedidos explicitamente, e não
    ficar implícitos em "quem conseguiu chamar a função".
    """


class MotivoObrigatorio(ErroMonetario):
    """Ação do administrador que mexe em dinheiro chegou sem motivo escrito.

    O motivo é o que transforma um saldo que mudou sozinho numa decisão que
    alguém tomou. Sem ele, o diário do god mode não responde nada.
    """


class ErroDeJogo(ErroMonetario):
    """Base do que impede uma rodada de começar ou de continuar."""


class CasaIndisponivel(ErroDeJogo):
    """A conta da casa ainda não existe (falta `flask criar-cassino`)."""


class ApostaAlta(ErroDeJogo):
    """O prêmio máximo desta aposta passa do que a casa aguenta.

    Recusada **antes** de cobrar. Cobrar e falhar no prêmio é o pior dos
    mundos: o jogador perde o dinheiro e não tem o jogo.
    """


class RodadaEmAndamento(ErroDeJogo):
    """Já existe uma rodada ativa. Uma por vez."""


class SemRodadaAtiva(ErroDeJogo):
    """Não há rodada para revelar ou retirar.

    É o que responde ao clique duplo e ao recarregar depois do fim: a segunda
    tentativa não encontra rodada ativa, então não cobra nem paga de novo.
    """
