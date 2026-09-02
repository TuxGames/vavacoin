"""torre: rodada com a torre sorteada na aposta

O terceiro jogo do Caladinho. A torre inteira e sorteada quando a aposta e
feita — ``armadilhas`` guarda a porta armadilhada de cada andar, em CSV, e e
segredo do servidor enquanto a rodada vive.

``mexida_em`` existe para a rodada abandonada expirar em vez de prender o
caixa da casa na exposicao comprometida para sempre.

``vantagem`` vem junto pela mesma razao dos outros dois jogos: congelar o
numero com que a rodada foi aberta.

Revision ID: e5c7f1a26b34
Revises: d3b8e2c4a107
Create Date: 2026-09-02 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'e5c7f1a26b34'
down_revision = 'd3b8e2c4a107'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rodada_torre',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jogador_id', sa.Integer(), nullable=False),
        sa.Column('aposta', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('vantagem', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('portas', sa.Integer(), nullable=False),
        sa.Column('armadilhas', sa.String(length=200), nullable=False),
        sa.Column('escolhas', sa.String(length=200), nullable=False),
        sa.Column('estado', sa.String(length=12), nullable=False),
        sa.Column('andar_estourado', sa.Integer(), nullable=True),
        sa.Column('multiplicador', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('premio', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('transacao_aposta_id', sa.Integer(), nullable=True),
        sa.Column('transacao_premio_id', sa.Integer(), nullable=True),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mexida_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('encerrada_em', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('aposta > 0', name='ck_torre_aposta_positiva'),
        sa.CheckConstraint('portas >= 2 AND portas <= 4', name='ck_torre_portas'),
        sa.CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name='ck_torre_estado'
        ),
        sa.ForeignKeyConstraint(['jogador_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['transacao_aposta_id'], ['transacao.id']),
        sa.ForeignKeyConstraint(['transacao_premio_id'], ['transacao.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('rodada_torre', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_rodada_torre_criada_em'), ['criada_em'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_torre_estado'), ['estado'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_torre_jogador_id'), ['jogador_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_torre_mexida_em'), ['mexida_em'], unique=False
        )
        batch_op.create_index(
            'uq_uma_rodada_torre_ativa_por_jogador',
            ['jogador_id'],
            unique=True,
            sqlite_where=sa.text("estado = 'ativa'"),
            postgresql_where=sa.text("estado = 'ativa'"),
        )


def downgrade():
    with op.batch_alter_table('rodada_torre', schema=None) as batch_op:
        batch_op.drop_index('uq_uma_rodada_torre_ativa_por_jogador')
        batch_op.drop_index(batch_op.f('ix_rodada_torre_mexida_em'))
        batch_op.drop_index(batch_op.f('ix_rodada_torre_jogador_id'))
        batch_op.drop_index(batch_op.f('ix_rodada_torre_estado'))
        batch_op.drop_index(batch_op.f('ix_rodada_torre_criada_em'))
    op.drop_table('rodada_torre')
