"""reinos: cofre, cidadania e divida

Generico desde o primeiro dia: "Alfheim" e uma linha da tabela `reino`.

O cofre e uma conta de verdade no ledger (`usuario.eh_cofre`), para participar
da mesma conservacao de massa que todo mundo — e e conta de SISTEMA, que nao
entra pela tela. Quem opera e uma pessoa com papel em `operador_do_reino`.

`divida` guarda o imposto cobrado e nao pago. Cobrar nao move dinheiro: cria a
linha. Os juros saem dos carimbos de tempo na leitura, sem tarefa agendada, e
a taxa fica CONGELADA na divida — mudar a taxa do reino nao reprecifica
cobranca antiga.

`quitacao` e o valor que o credor fixou ao negociar. Nem desconto nem perdao
movem dinheiro: divida nunca foi dinheiro no ledger.

Revision ID: c8f2a41b6e07
Revises: b4e1c8a75d29
Create Date: 2026-09-02 20:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


revision = 'c8f2a41b6e07'
down_revision = 'b4e1c8a75d29'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('eh_cofre', sa.Boolean(), nullable=False,
                      server_default=sa.text('0'))
        )

    op.create_table(
        'reino',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('nome_normalizado', sa.String(length=60), nullable=False),
        sa.Column('cofre_id', sa.Integer(), nullable=False),
        sa.Column('juros_diarios', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['cofre_id'], ['usuario.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cofre_id'),
    )
    with op.batch_alter_table('reino', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_reino_nome_normalizado'), ['nome_normalizado'], unique=True
        )

    op.create_table(
        'cidadania',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('entrou_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('saiu_em', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuario.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('cidadania', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_cidadania_reino_id'), ['reino_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_cidadania_usuario_id'), ['usuario_id'], unique=False
        )
        # UMA cidadania ativa por pessoa, em qualquer reino: sem `reino_id`
        # na chave. E a exclusividade e do banco, nao da rota.
        batch_op.create_index(
            'uq_uma_cidadania_ativa_por_pessoa',
            ['usuario_id'],
            unique=True,
            sqlite_where=sa.text('saiu_em IS NULL'),
            postgresql_where=sa.text('saiu_em IS NULL'),
        )

    op.create_table(
        'operador_do_reino',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('desde', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuario.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('operador_do_reino', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_operador_do_reino_reino_id'), ['reino_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_operador_do_reino_usuario_id'), ['usuario_id'], unique=False
        )
        batch_op.create_index(
            'uq_um_papel_por_pessoa_no_reino',
            ['reino_id', 'usuario_id'],
            unique=True,
        )

    op.create_table(
        'cobranca',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=12), nullable=False),
        sa.Column('parametro', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('motivo', sa.String(length=200), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tipo IN ('absoluta', 'percentual')", name='ck_cobranca_tipo'),
        sa.CheckConstraint('parametro > 0', name='ck_cobranca_parametro'),
        sa.ForeignKeyConstraint(['operador_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    with op.batch_alter_table('cobranca', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_cobranca_reino_id'), ['reino_id'], unique=False
        )

    op.create_table(
        'divida',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('devedor_id', sa.Integer(), nullable=False),
        sa.Column('cobranca_id', sa.Integer(), nullable=True),
        sa.Column('cobrada_por_id', sa.Integer(), nullable=False),
        sa.Column('principal', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('juros_cristalizados', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('pago', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('juros_desde', sa.DateTime(timezone=True), nullable=False),
        sa.Column('juros_diarios', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('quitacao', vavacoin.dinheiro.Dinheiro(), nullable=True),
        sa.Column('motivo', sa.String(length=200), nullable=False),
        sa.Column('cobrada_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('quitada_em', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('principal > 0', name='ck_divida_principal'),
        sa.CheckConstraint('pago >= 0', name='ck_divida_pago'),
        sa.CheckConstraint('juros_cristalizados >= 0', name='ck_divida_juros'),
        sa.ForeignKeyConstraint(['cobranca_id'], ['cobranca.id']),
        sa.ForeignKeyConstraint(['cobrada_por_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['devedor_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('divida', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_divida_cobranca_id'), ['cobranca_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_divida_cobrada_em'), ['cobrada_em'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_divida_devedor_id'), ['devedor_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_divida_reino_id'), ['reino_id'], unique=False
        )


def downgrade():
    op.drop_table('divida')
    op.drop_table('cobranca')
    op.drop_table('operador_do_reino')
    op.drop_table('cidadania')
    op.drop_table('reino')
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.drop_column('eh_cofre')
