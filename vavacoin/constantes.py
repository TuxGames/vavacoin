"""Números que definem a economia. Mudar qualquer um destes muda o jogo."""

from decimal import Decimal

#: Supply do dia zero. **Não é mais o supply total**: o administrador pode
#: cunhar ao ajustar saldo, e o supply de verdade é o que o ledger diz
#: (``moeda.supply_emitido()``). Este número é o ponto de partida, e serve de
#: referência para enxergar o quanto já foi cunhado além dele.
SUPPLY_INICIAL = Decimal("5000.00")

#: O que cada pessoa saca ao entrar — do que já existe, nunca criado.
SAQUE_INICIAL = Decimal("50.00")

#: Quantas pessoas o supply inicial comporta com o saque atual (5.000 / 50).
#: Se a turma passar disso, a decisão registrada é reduzir SAQUE_INICIAL.
CAPACIDADE = int(SUPPLY_INICIAL / SAQUE_INICIAL)

#: Identificador da conta do Banco Central.
USUARIO_BANCO_CENTRAL = "banco_central"
