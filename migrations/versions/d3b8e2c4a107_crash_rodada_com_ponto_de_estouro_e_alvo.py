"""crash: rodada com ponto de estouro e alvo

O segundo jogo do Caladinho. A rodada guarda o que decide o resultado já no
instante da aposta: o ponto de estouro sorteado pelo servidor e o alvo
declarado pelo jogador. ``alvo <= ponto_de_estouro`` responde se ganhou.

``vantagem`` vem junto pela mesma razão da rodada de mines: congelar o número
com que a rodada foi aberta, para que mudar a vantagem não afete quem já está
jogando.

Multiplicadores usam o tipo ``Dinheiro`` (inteiro de centésimos): 2,50× é
gravado como 250. Daí os CHECKs compararem com 100 e não com 1.

Revision ID: d3b8e2c4a107
Revises: c1f4a7d90b52
Create Date: 2026-09-02 15:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'd3b8e2c4a107'
down_revision = 'c1f4a7d90b52'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rodada_crash',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jogador_id', sa.Integer(), nullable=False),
        sa.Column('aposta', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('vantagem', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('ponto_de_estouro', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('alvo', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('estado', sa.String(length=12), nullable=False),
        sa.Column('multiplicador', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('premio', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('transacao_aposta_id', sa.Integer(), nullable=True),
        sa.Column('transacao_premio_id', sa.Integer(), nullable=True),
        sa.Column('iniciada_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('encerrada_em', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('aposta > 0', name='ck_crash_aposta_positiva'),
        sa.CheckConstraint('alvo > 100', name='ck_crash_alvo_acima_de_um'),
        sa.CheckConstraint('ponto_de_estouro >= 100', name='ck_crash_estouro_minimo'),
        sa.CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name='ck_crash_estado'
        ),
        sa.ForeignKeyConstraint(['jogador_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['transacao_aposta_id'], ['transacao.id']),
        sa.ForeignKeyConstraint(['transacao_premio_id'], ['transacao.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('rodada_crash', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_rodada_crash_estado'), ['estado'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_crash_iniciada_em'), ['iniciada_em'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_crash_jogador_id'), ['jogador_id'], unique=False
        )
        # Índice único parcial: no máximo uma rodada ativa por jogador, mesmo
        # em corrida entre processos.
        batch_op.create_index(
            'uq_uma_rodada_crash_ativa_por_jogador',
            ['jogador_id'],
            unique=True,
            sqlite_where=sa.text("estado = 'ativa'"),
            postgresql_where=sa.text("estado = 'ativa'"),
        )


def downgrade():
    with op.batch_alter_table('rodada_crash', schema=None) as batch_op:
        batch_op.drop_index('uq_uma_rodada_crash_ativa_por_jogador')
        batch_op.drop_index(batch_op.f('ix_rodada_crash_jogador_id'))
        batch_op.drop_index(batch_op.f('ix_rodada_crash_iniciada_em'))
        batch_op.drop_index(batch_op.f('ix_rodada_crash_estado'))
    op.drop_table('rodada_crash')
