"""nome de usuario com maiuscula e acento; normalizado para comparar

A pessoa passa a escrever o nome como quiser; o sistema compara sempre a forma
normalizada (sem acento, minúscula). Esta migração cria a coluna, **preenche
as contas que já existem** e só então liga o UNIQUE — nessa ordem, porque um
NOT NULL/UNIQUE antes do preenchimento derrubaria a migração num banco com
gente dentro.

O UNIQUE sai de `nome_usuario` e vai para `nome_normalizado`: é a mudança que
faz "João" e "joao" serem a mesma conta. `nome_usuario` continua indexado,
porque ainda se busca e ordena por ele.

Recria a tabela `usuario` (SQLite não tem ALTER para NOT NULL). Backup antes.
"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro
from vavacoin.nomes import normalizar_nome


# revision identifiers, used by Alembic.
revision = '5e2a8edc70ea'
down_revision = '353a30f6e6f5'
branch_labels = None
depends_on = None


def _tabela_usuario(normalizado_nulo):
    """A tabela como ela é neste ponto da migração.

    Explícita, e não refletida, porque o SQLAlchemy não reflete CHECK do
    SQLite: um batch sem `copy_from` recriaria a tabela sem o `saldo >= 0`.
    """
    return sa.Table(
        "usuario",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome_usuario", sa.String(length=50), nullable=False),
        sa.Column("nome_normalizado", sa.String(length=50), nullable=normalizado_nulo),
        sa.Column("nome_exibicao", sa.String(length=80), nullable=False),
        sa.Column("senha_hash", sa.String(length=128), nullable=True),
        sa.Column("eh_banco_central", sa.Boolean(), nullable=False),
        sa.Column("saldo", vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("saldo >= 0", name="ck_usuario_saldo_nao_negativo"),
        sa.PrimaryKeyConstraint("id"),
    )


def upgrade():
    # 1. Coluna nova, ainda aceitando nulo: as linhas existentes não têm valor.
    op.add_column("usuario", sa.Column("nome_normalizado", sa.String(length=50), nullable=True))

    # 2. Preenche o que já existe. Em Python, e não em SQL, porque tirar acento
    #    é `unicodedata`, não `LOWER()`.
    conexao = op.get_bind()
    usuario = _tabela_usuario(normalizado_nulo=True)
    vistos = {}
    for linha in conexao.execute(
        sa.select(usuario.c.id, usuario.c.nome_usuario)
    ).fetchall():
        normalizado = normalizar_nome(linha.nome_usuario)
        if normalizado in vistos:
            # Duas contas que só se distinguiam por acento ou caixa. Não dá
            # para escolher qual sobrevive por conta própria — quem decide é
            # gente, olhando o saldo e o extrato das duas.
            raise RuntimeError(
                f"contas {vistos[normalizado]} e {linha.id} normalizam para "
                f"{normalizado!r}; renomeie uma antes de migrar"
            )
        vistos[normalizado] = linha.id
        conexao.execute(
            usuario.update()
            .where(usuario.c.id == linha.id)
            .values(nome_normalizado=normalizado)
        )

    # 3. O UNIQUE muda de coluna, e a nova vira obrigatória.
    #
    # O índice de `nome_usuario` pode não existir: a migração 353a30f6e6f5
    # recriou esta tabela com `copy_from` sem os índices, e levou junto o
    # UNIQUE que havia ali. Esta migração é também o conserto disso — por
    # isso o drop é condicional, e os dois índices são criados no fim.
    indices = {i["name"] for i in sa.inspect(conexao).get_indexes("usuario")}
    if "ix_usuario_nome_usuario" in indices:
        op.drop_index("ix_usuario_nome_usuario", table_name="usuario")
    with op.batch_alter_table(
        "usuario",
        schema=None,
        copy_from=_tabela_usuario(normalizado_nulo=True),
        recreate="always",
    ) as batch_op:
        batch_op.alter_column(
            "nome_normalizado", existing_type=sa.String(length=50), nullable=False
        )
    op.create_index("ix_usuario_nome_usuario", "usuario", ["nome_usuario"], unique=False)
    op.create_index(
        "ix_usuario_nome_normalizado", "usuario", ["nome_normalizado"], unique=True
    )


def downgrade():
    """Volta o UNIQUE para `nome_usuario`.

    Se houver duas contas que só diferem por acento ou caixa — possíveis
    depois desta migração —, o índice único falha aqui, e é o certo: voltar
    silenciosamente escolheria uma delas para quebrar.
    """
    indices = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("usuario")}
    for nome in ("ix_usuario_nome_normalizado", "ix_usuario_nome_usuario"):
        if nome in indices:
            op.drop_index(nome, table_name="usuario")
    with op.batch_alter_table(
        "usuario",
        schema=None,
        copy_from=_tabela_usuario(normalizado_nulo=False),
        recreate="always",
    ) as batch_op:
        batch_op.drop_column("nome_normalizado")
    op.create_index("ix_usuario_nome_usuario", "usuario", ["nome_usuario"], unique=True)
