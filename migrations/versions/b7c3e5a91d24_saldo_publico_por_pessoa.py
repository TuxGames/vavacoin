"""saldo publico por pessoa

O dono quer que o dinheiro apareca por padrao, e que cada um possa esconder o
seu no perfil. E opt-OUT: nasce visivel.

As contas que JA EXISTEM entram como publicas, e isso e deliberado — e
exatamente o que ele pediu ao dizer "as pessoas terem o dinheiro publico".
Cada uma desliga no perfil quando quiser; ninguem fica exposto sem ter como
sair.

(O CLAUDE.md descreve o ranking antigo como opt-in, "aparecer e escolha de
cada um". Esta e uma decisao nova do dono, com a natureza invertida, e nao
uma contradicao para o codigo resolver.)

Revision ID: b7c3e5a91d24
Revises: f1b4c8e27a93
Create Date: 2026-09-03 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c3e5a91d24'
down_revision = 'f1b4c8e27a93'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'saldo_publico',
                sa.Boolean(),
                nullable=False,
                # 1 = publico. As contas que ja existem entram assim.
                server_default=sa.text('1'),
            )
        )


def downgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.drop_column('saldo_publico')
