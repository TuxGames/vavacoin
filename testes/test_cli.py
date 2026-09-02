"""Os comandos de linha de comando.

Interessa aqui principalmente o ``senha-bc``: é ele que abre o god mode, e a
regra dele mudou (não há mais tamanho mínimo). Testes que descrevem regra
removida enganam mais do que ajudam, então estes descrevem a regra atual.
"""

from conftest import conservacao

from vavacoin.constantes import SUPPLY_INICIAL
from vavacoin.extensoes import db
from vavacoin.modelos import RegistroAdministrativo, banco_central


def _rodar(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


# --- senha do Banco Central -------------------------------------------------


def test_senha_curta_e_aceita(app, bc):
    """Não há mínimo. Uma letra basta — decisão do dono do projeto."""
    resultado = _rodar(app, "senha-bc", "--senha", "a")

    assert resultado.exit_code == 0, resultado.output
    db.session.expire_all()
    atual = banco_central()
    assert atual.verificar_senha("a")
    assert atual.is_active is True


def test_senha_vazia_e_recusada(app, bc):
    """Sem mínimo é escolha; sem senha é porta destrancada.

    O Banco Central entra pelo site: senha vazia abriria a conta que tem todo
    o dinheiro e todo o poder para quem soubesse o nome dela.
    """
    resultado = _rodar(app, "senha-bc", "--senha", "")

    assert resultado.exit_code != 0
    assert "vazia" in resultado.output

    db.session.expire_all()
    atual = banco_central()
    assert atual.senha_hash is None
    assert atual.is_active is False


def test_senha_e_guardada_com_hash(app, bc):
    """Curta ou longa, texto puro nunca toca o banco."""
    _rodar(app, "senha-bc", "--senha", "abc")

    db.session.expire_all()
    atual = banco_central()
    assert atual.senha_hash != "abc"
    assert "abc" not in atual.senha_hash
    assert atual.senha_hash.startswith("$2")


def test_trocar_a_senha_substitui_a_anterior(app, bc):
    _rodar(app, "senha-bc", "--senha", "primeira")
    _rodar(app, "senha-bc", "--senha", "segunda")

    db.session.expire_all()
    atual = banco_central()
    assert atual.verificar_senha("segunda")
    assert not atual.verificar_senha("primeira")


def test_definir_senha_fica_no_diario(app, bc):
    _rodar(app, "senha-bc", "--senha", "x")

    registro = (
        db.session.query(RegistroAdministrativo)
        .filter_by(acao="senha")
        .order_by(RegistroAdministrativo.id.desc())
        .first()
    )
    assert registro is not None
    assert registro.alvo == "banco_central"


def test_senha_bc_antes_da_genese(app):
    """Sem Banco Central não há senha para definir."""
    resultado = _rodar(app, "senha-bc", "--senha", "qualquer")

    assert resultado.exit_code != 0
    assert "genese" in resultado.output or "gênese" in resultado.output


# --- convite ----------------------------------------------------------------


def test_convite_sem_destinatario_pela_cli(app, bc):
    """`flask convite`, sem nenhuma opção, imprime o código e o link.

    O código continua sendo a PRIMEIRA linha: era a saída inteira antes do
    link existir, e é o que qualquer script que leia esta saída espera.
    """
    from vavacoin.modelos import Convite

    resultado = _rodar(app, "convite")

    assert resultado.exit_code == 0, resultado.output
    codigo, link = resultado.output.strip().splitlines()
    assert codigo
    assert link.endswith("/cadastro/" + codigo)
    convite = db.session.execute(
        db.select(Convite).where(Convite.codigo == codigo)
    ).scalar_one()
    assert convite.destinatario is None


def test_convite_com_destinatario_pela_cli(app, bc):
    from vavacoin.modelos import Convite

    resultado = _rodar(app, "convite", "--destinatario", "Fulano")

    assert resultado.exit_code == 0
    codigo = resultado.output.strip().splitlines()[0]
    convite = db.session.execute(
        db.select(Convite).where(Convite.codigo == codigo)
    ).scalar_one()
    assert convite.destinatario == "Fulano"


# --- os outros comandos -----------------------------------------------------


def test_genese_pela_cli_e_idempotente(app):
    assert _rodar(app, "genese").exit_code == 0
    assert _rodar(app, "genese").exit_code == 0

    db.session.expire_all()
    assert banco_central().saldo == SUPPLY_INICIAL
    conservacao()


def test_conservacao_e_auditoria_saem_zero(app, bc, nova_pessoa):
    nova_pessoa(com_convite=True)
    db.session.commit()

    assert _rodar(app, "conservacao").exit_code == 0
    resultado = _rodar(app, "auditoria")
    assert resultado.exit_code == 0
    assert "o ledger explica cada centavo" in resultado.output


def test_auditoria_sai_com_erro_quando_alguem_escreve_por_fora(app, bc, nova_pessoa):
    """O comando serve para cron e CI: precisa falhar de verdade."""
    from decimal import Decimal

    from vavacoin.modelos import Usuario

    ana = nova_pessoa(com_convite=True)
    db.session.execute(
        db.update(Usuario).where(Usuario.id == ana.id).values(saldo=Decimal("999.00"))
    )
    db.session.commit()

    resultado = _rodar(app, "auditoria")
    assert resultado.exit_code != 0
    assert "FALHOU" in resultado.output
