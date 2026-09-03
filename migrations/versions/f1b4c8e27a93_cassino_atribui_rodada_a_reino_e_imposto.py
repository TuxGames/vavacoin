"""cassino atribui rodada a reino, e o imposto sobre o lucro

O acordo e o cassino pagar uma fatia do lucro que tira dos CIDADAOS de cada
reino — nao do lucro total. Cada rodada passa a carregar `reino_id`,
congelado no instante da aposta; nulo e o "nao cidadao", e e valor legitimo.

Congelado, e nao calculado depois: se a atribuicao saisse da cidadania atual,
alguem entrando ou saindo reescreveria imposto de rodada passada e a conta do
mes mudaria sozinha.

As rodadas que ja existem ficam com `reino_id` nulo. E a verdade: elas
aconteceram antes de existir reino, entao nenhuma delas foi de cidadao de
reino nenhum.

`reino.aliquota_cassino` nasce em 10%, que e o combinado de hoje — valor
inicial de coluna, nao constante no codigo. `reino.abatimento` guarda o
prejuizo que ainda vai abater lucro futuro.

Revision ID: f1b4c8e27a93
Revises: e9a3d7c05f18
Create Date: 2026-09-03 12:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


revision = 'f1b4c8e27a93'
down_revision = 'e9a3d7c05f18'
branch_labels = None
depends_on = None

RODADAS = ('rodada_mines', 'rodada_crash', 'rodada_torre', 'rodada_dados')


def upgrade():
    for tabela in RODADAS:
        with op.batch_alter_table(tabela, schema=None) as batch_op:
            batch_op.add_column(sa.Column('reino_id', sa.Integer(), nullable=True))
            batch_op.create_index(
                batch_op.f(f'ix_{tabela}_reino_id'), ['reino_id'], unique=False
            )
            batch_op.create_foreign_key(
                f'fk_{tabela}_reino', 'reino', ['reino_id'], ['id']
            )

    with op.batch_alter_table('reino', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'aliquota_cassino',
                vavacoin.dinheiro.Dinheiro(),
                nullable=False,
                # 1000 centesimos = 10,00%, o combinado de hoje.
                server_default=sa.text('1000'),
            )
        )
        batch_op.add_column(
            sa.Column(
                'abatimento',
                vavacoin.dinheiro.Dinheiro(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )

    op.create_table(
        'liquidacao_de_imposto',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fim', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lucro_bruto', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('abatimento_usado', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('lucro_tributavel', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('aliquota', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('imposto', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('liquidado_por_id', sa.Integer(), nullable=False),
        sa.Column('transacao_id', sa.Integer(), nullable=True),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('fim > inicio', name='ck_liquidacao_periodo'),
        sa.CheckConstraint('imposto >= 0', name='ck_liquidacao_imposto'),
        sa.CheckConstraint('abatimento_usado >= 0', name='ck_liquidacao_abatimento'),
        sa.ForeignKeyConstraint(['liquidado_por_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.ForeignKeyConstraint(['transacao_id'], ['transacao.id']),
        sa.PrimaryKeyConstraint('id'),
        # Um periodo por reino, liquidado uma vez so. E a guarda que impede o
        # mesmo lucro de ser cobrado duas vezes.
        sa.UniqueConstraint('reino_id', 'inicio', 'fim', name='uq_um_periodo_por_reino'),
    )
    with op.batch_alter_table('liquidacao_de_imposto', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_liquidacao_de_imposto_reino_id'), ['reino_id'], unique=False
        )


def downgrade():
    op.drop_table('liquidacao_de_imposto')
    with op.batch_alter_table('reino', schema=None) as batch_op:
        batch_op.drop_column('abatimento')
        batch_op.drop_column('aliquota_cassino')
    for tabela in RODADAS:
        with op.batch_alter_table(tabela, schema=None) as batch_op:
            batch_op.drop_constraint(f'fk_{tabela}_reino', type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{tabela}_reino_id'))
            batch_op.drop_column('reino_id')
