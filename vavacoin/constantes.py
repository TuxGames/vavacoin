"""Números que definem a economia. Mudar qualquer um destes muda o jogo."""

from decimal import Decimal

#: Supply total, fixo e imutável. Nunca se cunha VaVáCoin.
SUPPLY_TOTAL = Decimal("5000.00")

#: O que cada pessoa saca ao entrar — do que já existe, nunca criado.
SAQUE_INICIAL = Decimal("50.00")

#: Quantas pessoas o supply comporta com o saque atual (5.000 / 50 = 100).
#: Se a turma passar disso, a decisão registrada é reduzir SAQUE_INICIAL.
CAPACIDADE = int(SUPPLY_TOTAL / SAQUE_INICIAL)

#: Identificador da conta do Banco Central.
USUARIO_BANCO_CENTRAL = "banco_central"
