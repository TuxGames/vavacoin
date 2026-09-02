"""conta encerrada

Conta com historico nao se apaga: se encerra. O saldo volta ao Banco Central
por `mover()`, com motivo, e as linhas do ledger FICAM — e por isso a conta
precisa continuar existindo como linha, senao a auditoria passa a acusar para
sempre lancamentos que apontam para ninguem.

`encerrada_em` em branco e a conta viva. Preenchido, a conta nao autentica e
nao recebe transferencia.

Revision ID: b4e1c8a75d29
Revises: a2d6b09e4713
Create Date: 2026-09-02 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b4e1c8a75d29'
down_revision = 'a2d6b09e4713'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('encerrada_em', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.drop_column('encerrada_em')
