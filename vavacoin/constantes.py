"""Números que definem a economia. Mudar qualquer um destes muda o jogo."""

from decimal import Decimal

#: Supply do dia zero. **Não é mais o supply total**: o administrador pode
#: cunhar ao ajustar saldo, e o supply de verdade é o que o ledger diz
#: (``moeda.supply_emitido()``). Este número é o ponto de partida, e serve de
#: referência para enxergar o quanto já foi cunhado além dele.
SUPPLY_INICIAL = Decimal("5000.00")

# Não existe mais saque inicial: quem resgata o convite entra com saldo zero.
# O dinheiro chega depois, por transferência de outra pessoa ou por ajuste do
# Banco Central. Com isso some também a "capacidade" da economia — quantas
# pessoas cabem deixou de ser conta de divisão e virou decisão de quem
# distribui.

#: Identificador da conta do Banco Central.
USUARIO_BANCO_CENTRAL = "banco_central"

#: Identificador da conta da casa. O cassino se chama Caladinho; mines e
#: roleta são jogos dentro dele.
USUARIO_CASSINO = "caladinho"
