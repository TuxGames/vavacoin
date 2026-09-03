"""conta sombra, para a remocao de verdade

A coluna que marca a conta-sombra: a que fica no lugar de quem o Banco
Central apagou, para que as linhas do ledger continuem tendo dono.

Sem ela, apagar alguem exigiria anular ``transacao.origem_id`` — e linha sem
origem, neste sistema, **e emissao**. O supply passaria a contar o historico
de quem saiu como dinheiro criado do nada.

Revision ID: a2d6f90b41c7
Revises: b7c3e5a91d24
"""

import sqlalchemy as sa
from alembic import op

revision = "a2d6f90b41c7"
down_revision = "b7c3e5a91d24"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usuario") as lote:
        lote.add_column(
            sa.Column(
                "eh_removida",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("usuario") as lote:
        lote.drop_column("eh_removida")
