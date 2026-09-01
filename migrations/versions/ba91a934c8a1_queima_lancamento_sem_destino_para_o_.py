"""queima: lancamento sem destino, para o supply poder descer

O simétrico da emissão. Antes, dinheiro só entrava no mundo (linha sem
origem); agora também sai (linha sem destino). É o que permite baixar o saldo
do Banco Central sem mentir — mandar para outra conta não reduz o supply,
apenas muda onde ele está.

Com o teto de 10.000, isto deixou de ser luxo: sem queima, o teto seria uma
catraca de uma via.

**Recria a tabela `transacao`, que é o ledger.** Faça backup antes e rode
`flask auditoria` depois.

O `copy_from` é explícito, com todas as colunas, todos os CHECK e as FKs, e os
índices são recriados no fim — o SQLAlchemy não reflete CHECK do SQLite, então
um batch sem isso recriaria a tabela sem nenhuma das travas que protegem o
ledger. Já perdemos os índices de `usuario` exatamente assim.
"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


# revision identifiers, used by Alembic.
revision = 'ba91a934c8a1'
down_revision = '7289a82f96d2'
branch_labels = None
depends_on = None


INDICES = [
    ("ix_transacao_origem_id", "origem_id"),
    ("ix_transacao_destino_id", "destino_id"),
    ("ix_transacao_ator_id", "ator_id"),
    ("ix_transacao_tipo", "tipo"),
    ("ix_transacao_criado_em", "criado_em"),
]


def _tabela_atual():
    """A tabela `transacao` como ela está ANTES desta migração.

    `copy_from` serve para o Alembic saber o que copiar — inclusive os CHECK,
    que o SQLite não deixa refletir. As mudanças vêm como operações do batch,
    abaixo; descrevê-las aqui não teria efeito nenhum.
    """
    return sa.Table(
        "transacao",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origem_id", sa.Integer(), nullable=True),
        sa.Column("destino_id", sa.Integer(), nullable=False),
        sa.Column("valor", vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("motivo", sa.String(length=200), nullable=True),
        sa.Column("ator_id", sa.Integer(), nullable=True),
        sa.Column("saldo_origem_depois", vavacoin.dinheiro.Dinheiro(), nullable=True),
        sa.Column("saldo_destino_depois", vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valor > 0", name="ck_transacao_valor_positivo"),
        sa.CheckConstraint(
            "origem_id IS NULL OR origem_id <> destino_id",
            name="ck_transacao_origem_diferente_destino",
        ),
        sa.CheckConstraint(
            "(origem_id IS NOT NULL) OR tipo IN ('genese', 'emissao')",
            name="ck_transacao_sem_origem_so_emite",
        ),
        sa.ForeignKeyConstraint(["origem_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(["destino_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(
            ["ator_id"], ["usuario.id"], name="fk_transacao_ator_id_usuario"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def _tirar_indices():
    conexao = op.get_bind()
    existentes = {i["name"] for i in sa.inspect(conexao).get_indexes("transacao")}
    for nome, _coluna in INDICES:
        if nome in existentes:
            op.drop_index(nome, table_name="transacao")


def _repor_indices():
    for nome, coluna in INDICES:
        op.create_index(nome, "transacao", [coluna], unique=False)


def upgrade():
    _tirar_indices()
    with op.batch_alter_table(
        "transacao", schema=None, copy_from=_tabela_atual(), recreate="always"
    ) as batch_op:
        batch_op.alter_column(
            "destino_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.alter_column(
            "saldo_destino_depois",
            existing_type=vavacoin.dinheiro.Dinheiro(),
            nullable=True,
        )
        # O "origem <> destino" precisa tolerar destino nulo.
        batch_op.drop_constraint(
            "ck_transacao_origem_diferente_destino", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_transacao_origem_diferente_destino",
            "origem_id IS NULL OR destino_id IS NULL OR origem_id <> destino_id",
        )
        batch_op.create_check_constraint(
            "ck_transacao_tem_algum_lado",
            "origem_id IS NOT NULL OR destino_id IS NOT NULL",
        )
        batch_op.create_check_constraint(
            "ck_transacao_sem_destino_so_queima",
            "(destino_id IS NOT NULL) OR tipo = 'queima'",
        )
    _repor_indices()


def downgrade():
    """Volta a exigir destino.

    Se já houver queima no ledger, o NOT NULL falha aqui — e é o certo:
    apagar as linhas por baixo perderia o registro de dinheiro destruído, e o
    supply reconstruído passaria a mentir.
    """
    _tirar_indices()
    with op.batch_alter_table("transacao", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("ck_transacao_sem_destino_so_queima", type_="check")
        batch_op.drop_constraint("ck_transacao_tem_algum_lado", type_="check")
        batch_op.drop_constraint(
            "ck_transacao_origem_diferente_destino", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_transacao_origem_diferente_destino",
            "origem_id IS NULL OR origem_id <> destino_id",
        )
        batch_op.alter_column(
            "saldo_destino_depois",
            existing_type=vavacoin.dinheiro.Dinheiro(),
            nullable=False,
        )
        batch_op.alter_column(
            "destino_id", existing_type=sa.Integer(), nullable=False
        )
    _repor_indices()
