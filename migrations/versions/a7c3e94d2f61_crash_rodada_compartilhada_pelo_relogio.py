"""crash: rodada compartilhada pelo relogio

A rodada de crash deixou de ser de cada um e passou a ser do RELOGIO: o numero
dela e `floor(epoch / ciclo)`, e por isso ela existe como ideia antes de
existir como linha. A tabela nova guarda duas coisas que a conta de relogio
nao da: a vantagem CONGELADA quando a janela abriu, e o ponto de estouro ja
calculado.

O estouro nao e sorteado por aposta — e derivado de um segredo do servidor
mais o numero da rodada. Por isso as vinte telas chegam na mesma curva sem o
servidor guardar estado por jogador, e por isso duas requisicoes que criem a
mesma rodada ao mesmo tempo chegam ao MESMO valor (a corrida perde no indice
unico e rele, sem duas curvas disputando a janela).

`rodada_crash.compartilhada_id` liga a aposta a rodada em que ela entrou.
Anulavel so por causa das apostas antigas, de quando cada uma tinha a propria
curva; a liquidacao sabe fechar as que aparecerem.

E o crash VOLTA AO AR. Ele foi desligado por decisao de produto ("os usuarios
querem ver o aviao subindo e explodindo em tempo real"), e a condicao era esta
feature. O padrao no codigo mudou para ligado, mas quem desligou de verdade
foi uma linha em `configuracao` — sem apagar essa linha, o padrao novo nao
vale nada em producao.

Revision ID: a7c3e94d2f61
Revises: c4e8b17d3a06
Create Date: 2026-09-11 23:58:00.000000

"""
from alembic import op
import sqlalchemy as sa
import vavacoin.dinheiro


revision = 'a7c3e94d2f61'
down_revision = 'c4e8b17d3a06'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rodada_crash_compartilhada',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('numero', sa.BigInteger(), nullable=False),
        sa.Column('vantagem', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('ponto_de_estouro', vavacoin.dinheiro.Dinheiro(), nullable=False),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            'ponto_de_estouro >= 100', name='ck_crash_compartilhada_estouro_minimo'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('numero'),
    )
    with op.batch_alter_table('rodada_crash_compartilhada', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_rodada_crash_compartilhada_numero'), ['numero'], unique=True
        )

    with op.batch_alter_table('rodada_crash', schema=None) as batch_op:
        batch_op.add_column(sa.Column('compartilhada_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_rodada_crash_compartilhada_id'),
            ['compartilhada_id'],
            unique=False,
        )
        batch_op.create_foreign_key(
            'fk_rodada_crash_compartilhada',
            'rodada_crash_compartilhada',
            ['compartilhada_id'],
            ['id'],
        )
        # Uma aposta por pessoa em cada rodada compartilhada, e o banco que
        # garanta: o indice de "uma rodada ativa por jogador" solta assim que a
        # aposta e liquidada, e sem este a pessoa apostaria de novo na mesma
        # janela logo depois de a sua ter resolvido.
        batch_op.create_index(
            'uq_uma_aposta_por_rodada_compartilhada',
            ['jogador_id', 'compartilhada_id'],
            unique=True,
        )

    # O crash volta ao ar. Apagar a linha (em vez de gravar "1") devolve o jogo
    # ao PADRAO do codigo, que e onde a decisao passa a morar — assim o dono
    # continua podendo desliga-lo pelo painel depois, sem que esta migration
    # tenha cravado nada por cima.
    op.execute(
        "DELETE FROM configuracao WHERE chave = 'caladinho_jogo_crash'"
    )


def downgrade():
    with op.batch_alter_table('rodada_crash', schema=None) as batch_op:
        batch_op.drop_index('uq_uma_aposta_por_rodada_compartilhada')
        batch_op.drop_constraint('fk_rodada_crash_compartilhada', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_rodada_crash_compartilhada_id'))
        batch_op.drop_column('compartilhada_id')
    op.drop_table('rodada_crash_compartilhada')
