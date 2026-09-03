"""cidadania por convite ou pedido

Dois caminhos, uma tabela. O reino convida e a pessoa aceita, ou a pessoa pede
e o operador aprova — muda quem comecou (`origem`) e, por consequencia, quem
confirma. Duas tabelas gemeas acabariam divergindo numa regra so.

O invariante e o mesmo dos dois lados: ninguem entra sozinho e ninguem e
colocado a forca. A exclusividade de cidadania e conferida na CONFIRMACAO,
nao no envio.

Revision ID: e9a3d7c05f18
Revises: c8f2a41b6e07
Create Date: 2026-09-03 11:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e9a3d7c05f18'
down_revision = 'c8f2a41b6e07'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pedido_de_cidadania',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reino_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('origem', sa.String(length=8), nullable=False),
        sa.Column('criado_por_id', sa.Integer(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('estado', sa.String(length=10), nullable=False),
        sa.Column('respondido_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('respondido_por_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("origem IN ('reino', 'pessoa')", name='ck_pedido_origem'),
        sa.CheckConstraint(
            "estado IN ('pendente', 'aceito', 'recusado')", name='ck_pedido_estado'
        ),
        sa.ForeignKeyConstraint(['criado_por_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['reino_id'], ['reino.id']),
        sa.ForeignKeyConstraint(['respondido_por_id'], ['usuario.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuario.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('pedido_de_cidadania', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_pedido_de_cidadania_estado'), ['estado'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_pedido_de_cidadania_reino_id'), ['reino_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_pedido_de_cidadania_usuario_id'),
            ['usuario_id'],
            unique=False,
        )
        # Uma pendencia por dupla pessoa/reino: convidar de novo quem ja tem
        # convite aberto e o mesmo convite, nao um novo.
        batch_op.create_index(
            'uq_uma_pendencia_por_pessoa_no_reino',
            ['reino_id', 'usuario_id'],
            unique=True,
            sqlite_where=sa.text("estado = 'pendente'"),
            postgresql_where=sa.text("estado = 'pendente'"),
        )


def downgrade():
    op.drop_table('pedido_de_cidadania')
