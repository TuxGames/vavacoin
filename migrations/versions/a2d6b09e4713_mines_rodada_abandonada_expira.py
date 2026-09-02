"""mines: rodada abandonada expira

Conserto de producao. Rodada de mines aberta segura `premio_maximo` na
exposicao comprometida; quem fecha a aba congela esse pedaco do caixa da casa
para sempre, e um punhado de abas fechadas passa a recusar aposta de quem quer
jogar — sem ninguem estar jogando.

`mexida_em` e a coluna que faltava para saber quando a rodada foi abandonada.
E a mesma coluna, com a mesma funcao, que a rodada de torre ja nasceu tendo.

As rodadas que ja existem recebem `criada_em` no lugar de `agora()`: elas
foram abandonadas quando foram abandonadas, e datar tudo de hoje daria a
todas mais trinta minutos de sobrevida que elas nao merecem. As sete rodadas
antigas de 01/09, em particular, ja estao encerradas e nao sao varridas de
qualquer forma.

Revision ID: a2d6b09e4713
Revises: f7a9d3e18c05
Create Date: 2026-09-02 17:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2d6b09e4713'
down_revision = 'f7a9d3e18c05'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rodada_mines', schema=None) as batch_op:
        # Nasce anulavel para o backfill caber; vira NOT NULL logo abaixo.
        batch_op.add_column(
            sa.Column('mexida_em', sa.DateTime(timezone=True), nullable=True)
        )
    op.execute('UPDATE rodada_mines SET mexida_em = criada_em')
    with op.batch_alter_table('rodada_mines', schema=None) as batch_op:
        batch_op.alter_column(
            'mexida_em', existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_mines_mexida_em'), ['mexida_em'], unique=False
        )


def downgrade():
    with op.batch_alter_table('rodada_mines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rodada_mines_mexida_em'))
        batch_op.drop_column('mexida_em')
