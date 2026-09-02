"""dados: rodada que nasce ja resolvida

O quarto jogo do Caladinho, e o unico sem estado intermediario: a rolagem, o
resultado e os dois lancamentos acontecem na mesma transacao. Nao ha estado
'ativa', logo nao ha indice parcial de rodada ativa nem rodada abandonada.

A linha e gravada mesmo assim porque e ela que responde "que numero saiu?" e e
dela que a tela rele o resultado depois de um refresh.

Revision ID: f7a9d3e18c05
Revises: e5c7f1a26b34
Create Date: 2026-09-02 17:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'f7a9d3e18c05'
down_revision = 'e5c7f1a26b34'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rodada_dados',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jogador_id', sa.Integer(), nullable=False),
        sa.Column('aposta', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('vantagem', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('sentido', sa.String(length=6), nullable=False),
        sa.Column('alvo', sa.Integer(), nullable=False),
        sa.Column('resultado', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=8), nullable=False),
        sa.Column('multiplicador', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('premio', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('transacao_aposta_id', sa.Integer(), nullable=True),
        sa.Column('transacao_premio_id', sa.Integer(), nullable=True),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('aposta > 0', name='ck_dados_aposta_positiva'),
        sa.CheckConstraint('alvo >= 1 AND alvo <= 99', name='ck_dados_alvo'),
        sa.CheckConstraint(
            'resultado >= 1 AND resultado <= 100', name='ck_dados_resultado'
        ),
        sa.CheckConstraint("sentido IN ('menor', 'maior')", name='ck_dados_sentido'),
        sa.CheckConstraint("estado IN ('ganha', 'perdida')", name='ck_dados_estado'),
        sa.ForeignKeyConstraint(['jogador_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['transacao_aposta_id'], ['transacao.id']),
        sa.ForeignKeyConstraint(['transacao_premio_id'], ['transacao.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('rodada_dados', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_rodada_dados_criada_em'), ['criada_em'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_dados_estado'), ['estado'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_rodada_dados_jogador_id'), ['jogador_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('rodada_dados', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rodada_dados_jogador_id'))
        batch_op.drop_index(batch_op.f('ix_rodada_dados_estado'))
        batch_op.drop_index(batch_op.f('ix_rodada_dados_criada_em'))
    op.drop_table('rodada_dados')
