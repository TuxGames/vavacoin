"""avisos do reino, e a marca de dispensa por pessoa

O recado que o operador manda para os cidadaos. Fora do ledger de proposito:
nao move dinheiro e nao e lancamento.

Revision ID: c4e8b17d3a06
Revises: a2d6f90b41c7
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8b17d3a06"
down_revision = "a2d6f90b41c7"
branch_labels = None
depends_on = None

TAMANHO = 500


def upgrade():
    op.create_table(
        "aviso_do_reino",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reino_id", sa.Integer(), nullable=False),
        sa.Column("autor_id", sa.Integer(), nullable=False),
        sa.Column("texto", sa.String(length=TAMANHO), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(texto)) > 0", name="ck_aviso_texto_nao_vazio"),
        sa.CheckConstraint(f"length(texto) <= {TAMANHO}", name="ck_aviso_tamanho"),
        sa.ForeignKeyConstraint(["autor_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(["reino_id"], ["reino.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    with op.batch_alter_table("aviso_do_reino") as lote:
        lote.create_index("ix_aviso_do_reino_reino_id", ["reino_id"])
        lote.create_index("ix_aviso_do_reino_criado_em", ["criado_em"])

    op.create_table(
        "aviso_visto",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aviso_id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("visto_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aviso_id"], ["aviso_do_reino.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("aviso_visto") as lote:
        lote.create_index("ix_aviso_visto_aviso_id", ["aviso_id"])
        lote.create_index("ix_aviso_visto_usuario_id", ["usuario_id"])
        # Dispensar duas vezes e o mesmo que uma, e isso e fato do banco.
        lote.create_index("uq_um_visto_por_pessoa", ["aviso_id", "usuario_id"], unique=True)


def downgrade():
    op.drop_table("aviso_visto")
    op.drop_table("aviso_do_reino")
