"""vantagem da casa editavel e congelada na rodada

A vantagem deixou de ser 2% escritos no código e virou dado, editável pelo
dono no painel da casa (tabela ``configuracao``, uma linha por jogo — não
precisa de migração, a tabela já existe).

O que precisa de coluna é o **congelamento**: a rodada guarda a vantagem que
valia no instante da aposta, para que mudar a vantagem não afete quem já está
jogando.

As rodadas que já existem recebem 2.00, que é exatamente a vantagem com que
foram jogadas — a conta delas não muda. O tipo é o ``Dinheiro`` do projeto
(inteiro de centésimos), então 2,00% é gravado como 200.

Revision ID: c1f4a7d90b52
Revises: add8932b8f2a
Create Date: 2026-09-02 15:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'c1f4a7d90b52'
down_revision = 'add8932b8f2a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rodada_mines', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'vantagem',
                vavacoin.dinheiro.Dinheiro(),
                nullable=False,
                # 200 centésimos = 2,00%, a vantagem fixa que valia até aqui.
                server_default=sa.text('200'),
            )
        )


def downgrade():
    with op.batch_alter_table('rodada_mines', schema=None) as batch_op:
        batch_op.drop_column('vantagem')
