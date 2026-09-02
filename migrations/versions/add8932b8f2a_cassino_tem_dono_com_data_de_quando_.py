"""cassino tem dono, com data de quando assumiu

`dono_id` é anulável: "sem dono" é um estado, não um caso especial — e é por
onde uma transferência de posse entra depois sem reescrever nada. `dono_desde`
é a data de onde o lucro do dono passa a ser somado do ledger.

A chave estrangeira obriga a recriar a tabela (SQLite não tem ADD CONSTRAINT),
então o `copy_from` é explícito e os índices são repostos no fim. Sem isso a
recriação levaria junto o `CHECK` de saldo e os dois índices de nome — foi
exatamente assim que perdemos o UNIQUE de `nome_usuario` uma vez.
"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'add8932b8f2a'
down_revision = 'ba91a934c8a1'
branch_labels = None
depends_on = None


INDICES = [
    ("ix_usuario_nome_usuario", "nome_usuario", False),
    ("ix_usuario_nome_normalizado", "nome_normalizado", True),
]


def _tabela(com_dono):
    """A tabela `usuario` antes/depois desta migração."""
    colunas = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome_usuario", sa.String(length=50), nullable=False),
        sa.Column("nome_normalizado", sa.String(length=50), nullable=False),
        sa.Column("nome_exibicao", sa.String(length=80), nullable=False),
        sa.Column("senha_hash", sa.String(length=128), nullable=True),
        sa.Column("eh_banco_central", sa.Boolean(), nullable=False),
        sa.Column("eh_cassino", sa.Boolean(), nullable=False),
    ]
    if com_dono:
        colunas += [
            sa.Column("dono_id", sa.Integer(), nullable=True),
            sa.Column("dono_desde", sa.DateTime(timezone=True), nullable=True),
        ]
    colunas += [
        sa.Column("saldo", vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
    ]

    restricoes = [
        sa.CheckConstraint("saldo >= 0", name="ck_usuario_saldo_nao_negativo"),
        sa.PrimaryKeyConstraint("id"),
    ]
    if com_dono:
        restricoes.insert(
            0,
            sa.ForeignKeyConstraint(
                ["dono_id"], ["usuario.id"], name="fk_usuario_dono_id_usuario"
            ),
        )
    return sa.Table("usuario", sa.MetaData(), *colunas, *restricoes)


def _tirar_indices():
    conexao = op.get_bind()
    existentes = {i["name"] for i in sa.inspect(conexao).get_indexes("usuario")}
    for nome, _coluna, _unico in INDICES:
        if nome in existentes:
            op.drop_index(nome, table_name="usuario")


def _repor_indices():
    for nome, coluna, unico in INDICES:
        op.create_index(nome, "usuario", [coluna], unique=unico)


def upgrade():
    # As colunas entram por ALTER nativo; a FK é que exige recriar a tabela.
    op.add_column("usuario", sa.Column("dono_id", sa.Integer(), nullable=True))
    op.add_column(
        "usuario", sa.Column("dono_desde", sa.DateTime(timezone=True), nullable=True)
    )

    _tirar_indices()
    with op.batch_alter_table(
        "usuario", schema=None, copy_from=_tabela(com_dono=True), recreate="always"
    ) as batch_op:
        batch_op.create_foreign_key(
            "fk_usuario_dono_id_usuario", "usuario", ["dono_id"], ["id"]
        )
    _repor_indices()


def downgrade():
    _tirar_indices()
    with op.batch_alter_table(
        "usuario", schema=None, copy_from=_tabela(com_dono=True), recreate="always"
    ) as batch_op:
        batch_op.drop_constraint("fk_usuario_dono_id_usuario", type_="foreignkey")
        batch_op.drop_column("dono_desde")
        batch_op.drop_column("dono_id")
    _repor_indices()
