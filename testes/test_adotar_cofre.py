"""Adotar uma conta existente como cofre de um reino.

O caso real: o reino foi montado na mão pelo painel antes de existir a tabela
— uma conta "Banco" com o dinheiro dentro e histórico no extrato. Criar um
cofre vazio e depois mover o dinheiro seria trabalho e um lançamento que não
aconteceu de verdade.

**Adotar muda o papel da conta, não o dinheiro dela.** O saldo fica onde está,
os lançamentos continuam explicando o que explicavam, e a conservação e a
auditoria não sentem nada. O que muda é que a conta vira conta de sistema e
**para de autenticar** — que é o efeito destrutivo, e é de propósito: se o
jeito de mandar no reino fosse entrar na conta do cofre, quem soubesse a senha
seria rei.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.auditoria import auditar
from vavacoin.caladinho import criar_casa
from vavacoin.erros import ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import mover, soma_saldos, supply_emitido
from vavacoin.modelos import (
    RegistroAdministrativo,
    Transacao,
    Usuario,
    buscar_usuario,
)
from vavacoin.operacoes import ajustar_saldo, criar_usuario, encerrar_conta
from vavacoin.reinos import (
    criar_reino,
    definir_operador,
    entrar_no_reino,
    exigir_adotavel,
    operadores,
)

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def montado_na_mao(app, bc):
    """O cenário de produção: duas contas criadas pelo painel, com dinheiro.

    ``Banco`` tem saldo e lançamentos; ``Bento`` é a pessoa que vai operar.
    """
    bento = criar_usuario(
        "Bento", SENHA, nome_exibicao="Rei de alfheim", autoridade=bc
    )
    conta_banco = criar_usuario(
        "Banco", SENHA, nome_exibicao="Banco de alfheim", autoridade=bc
    )
    db.session.commit()
    ajustar_saldo(bento, "53.34", "saldo do rei", autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta_banco, "450.69", "dinheiro do reino", autoridade=bc)
    db.session.commit()
    # Um pouco de histórico, para provar que ele sobrevive.
    mover(conta_banco, bento, "10.00", motivo="pagamento antigo")
    db.session.commit()
    return {"bento": bento, "banco": conta_banco}


def _auditoria_fecha():
    relatorio = auditar()
    assert relatorio["ok"], relatorio
    assert relatorio["ledger"]["saldos_divergentes"] == []
    assert relatorio["ledger"]["linhas_inconsistentes"] == []
    return True


# --- adotar não mexe em dinheiro --------------------------------------------


def test_adotar_nao_move_um_centavo(app, bc, montado_na_mao):
    """O número que importa: o saldo fica exatamente onde estava."""
    conta = montado_na_mao["banco"]
    antes = conservacao()
    saldo = conta.saldo
    lancamentos = db.session.query(Transacao).count()

    reino = criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()
    db.session.expire_all()

    assert reino.cofre_id == conta.id
    assert db.session.get(Usuario, conta.id).saldo == saldo
    assert db.session.query(Transacao).count() == lancamentos
    assert conservacao() == antes


def test_adotar_mantem_a_soma_e_a_auditoria(app, bc, montado_na_mao):
    supply_antes, soma_antes = supply_emitido(), soma_saldos()

    criar_reino("Alfheim", autoridade=bc, cofre=montado_na_mao["banco"])
    db.session.commit()

    assert supply_emitido() == supply_antes
    assert soma_saldos() == soma_antes
    assert soma_saldos() == supply_emitido()
    assert _auditoria_fecha()


def test_o_ledger_continua_explicando_o_saldo(app, bc, montado_na_mao):
    """Os lançamentos antigos ficam, e continuam sendo a explicação do saldo."""
    conta = montado_na_mao["banco"]
    antes = db.session.execute(
        db.select(db.func.count(Transacao.id)).where(
            (Transacao.origem_id == conta.id) | (Transacao.destino_id == conta.id)
        )
    ).scalar_one()
    assert antes > 0

    criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    depois = db.session.execute(
        db.select(db.func.count(Transacao.id)).where(
            (Transacao.origem_id == conta.id) | (Transacao.destino_id == conta.id)
        )
    ).scalar_one()
    assert depois == antes
    assert _auditoria_fecha()


# --- o que a adoção muda ----------------------------------------------------


def test_a_conta_adotada_deixa_de_autenticar(app, bc, montado_na_mao):
    """O efeito destrutivo, e o motivo dele: cofre não entra pela tela."""
    conta = montado_na_mao["banco"]
    cliente = app.test_client()
    entrou = cliente.post(
        "/entrar",
        data={"nome_usuario": "Banco", "senha": SENHA},
        follow_redirects=True,
    )
    assert "/sair" in entrou.get_data(as_text=True), "antes da adoção, entrava"
    cliente.post("/sair", follow_redirects=True)

    criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()
    db.session.expire_all()

    conta = db.session.get(Usuario, conta.id)
    assert conta.senha_hash is None
    assert conta.is_active is False
    assert conta.eh_cofre
    assert conta.eh_conta_de_sistema

    resposta = app.test_client().post(
        "/entrar",
        data={"nome_usuario": "Banco", "senha": SENHA},
        follow_redirects=True,
    )
    assert "/sair" not in resposta.get_data(as_text=True)


def test_o_painel_recusa_dar_senha_a_conta_adotada(app, bc, montado_na_mao):
    """A guarda de conta de sistema passa a valer para ela."""
    conta = montado_na_mao["banco"]
    criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    bc.definir_senha("senha-do-painel")
    db.session.commit()
    painel = app.test_client()
    painel.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": "senha-do-painel"},
        follow_redirects=True,
    )
    painel.post(
        f"/painel/conta/{conta.id}",
        data={"saldo": str(conta.saldo), "senha": "reabrindo"},
        follow_redirects=True,
    )

    db.session.expire_all()
    assert db.session.get(Usuario, conta.id).senha_hash is None


def test_a_conta_adotada_nao_vira_cidada_nem_operadora(app, bc, montado_na_mao):
    conta = montado_na_mao["banco"]
    reino = criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        entrar_no_reino(reino, conta)
    with pytest.raises(ValorInvalido):
        definir_operador(reino, conta, autoridade=bc)


def test_a_adocao_fica_registrada(app, bc, montado_na_mao):
    conta = montado_na_mao["banco"]
    criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.acao == "reino")
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert "Banco" in registro.detalhe
    assert "440.69" in registro.detalhe
    assert registro.alvo == "Alfheim"
    assert registro.criado_em is not None


# --- quem não pode ser adotado ----------------------------------------------


def test_o_banco_central_nao_vira_cofre(app, bc):
    with pytest.raises(ValorInvalido):
        exigir_adotavel(bc)


def test_a_casa_do_cassino_nao_vira_cofre(app, bc):
    casa = criar_casa(autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        exigir_adotavel(casa)


def test_conta_encerrada_nao_vira_cofre(app, bc, montado_na_mao):
    conta = montado_na_mao["banco"]
    encerrar_conta(conta, "saiu", autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        exigir_adotavel(conta)


def test_cofre_de_outro_reino_nao_e_adotado_de_novo(app, bc, montado_na_mao):
    conta = montado_na_mao["banco"]
    criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        exigir_adotavel(conta)


def test_cidadao_de_um_reino_precisa_sair_antes(app, bc, montado_na_mao):
    """A mensagem diz o que fazer, em vez de só recusar."""
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    conta = montado_na_mao["banco"]
    entrar_no_reino(outro, conta)
    db.session.commit()

    with pytest.raises(ValorInvalido) as erro:
        exigir_adotavel(conta)
    assert "saia de lá antes" in str(erro.value)


def test_operador_de_reino_precisa_perder_o_papel_antes(app, bc, montado_na_mao):
    """Cofre que opera um reino é contradição: ele não autentica.

    O papel ficaria inerte e mentindo. Recusar é melhor do que tirar o papel
    por conta própria — quem deu decide se tira.
    """
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    conta = montado_na_mao["banco"]
    definir_operador(outro, conta, autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido) as erro:
        exigir_adotavel(conta)
    assert "opera um reino" in str(erro.value)


def test_a_recusa_nao_deixa_nada_pela_metade(app, bc, montado_na_mao):
    """Adoção recusada não pode ter apagado a senha no caminho."""
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    conta = montado_na_mao["banco"]
    entrar_no_reino(outro, conta)
    db.session.commit()
    antes = conservacao()

    with pytest.raises(ValorInvalido):
        criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.rollback()

    db.session.expire_all()
    conta = db.session.get(Usuario, conta.id)
    assert conta.senha_hash is not None
    assert not conta.eh_cofre
    assert conservacao() == antes


# --- idempotência -----------------------------------------------------------


def test_rodar_duas_vezes_nao_duplica_reino(app, bc, montado_na_mao):
    from vavacoin.modelos import Reino

    conta = montado_na_mao["banco"]
    antes = conservacao()

    primeiro = criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()
    segundo = criar_reino("Alfheim", autoridade=bc, cofre=conta)
    db.session.commit()

    assert primeiro.id == segundo.id
    assert db.session.query(Reino).count() == 1
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_o_reino_criado_sem_cofre_continua_nascendo_vazio(app, bc):
    """O caminho antigo não mudou."""
    reino = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()

    assert reino.cofre.saldo == Decimal("0.00")
    assert reino.cofre.eh_cofre
    assert reino.cofre.senha_hash is None


# --- o cenário completo, pela CLI -------------------------------------------


def test_o_cenario_de_producao_pela_cli(app, bc, montado_na_mao):
    """Adotar o Banco e dar o papel ao Bento, como vai ser feito de verdade."""
    from click.testing import CliRunner

    antes = conservacao()
    runner = CliRunner()

    resultado = runner.invoke(
        app.cli, ["criar-reino", "Alfheim", "--cofre", "Banco", "--sim"]
    )
    assert resultado.exit_code == 0, resultado.output
    assert "440.69" in resultado.output
    # O aviso do efeito colateral tem de estar na saída.
    assert "perde o acesso" in resultado.output

    papel = runner.invoke(app.cli, ["operador-reino", "Alfheim", "Bento"])
    assert papel.exit_code == 0, papel.output

    db.session.expire_all()
    from vavacoin.reinos import reino_por_nome

    reino = reino_por_nome("Alfheim")
    assert reino.cofre.nome_usuario == "Banco"
    assert reino.cofre.saldo == Decimal("440.69")
    assert [o.nome_usuario for o in operadores(reino)] == ["Bento"]
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_a_cli_recusa_conta_inexistente(app, bc, montado_na_mao):
    from click.testing import CliRunner

    resultado = CliRunner().invoke(
        app.cli, ["criar-reino", "Alfheim", "--cofre", "ninguem", "--sim"]
    )

    assert resultado.exit_code != 0
    assert "não encontrada" in resultado.output


def test_a_cli_avisa_antes_e_respeita_o_nao(app, bc, montado_na_mao):
    """Sem ``--sim``, o comando pergunta — e "não" não muda nada."""
    from click.testing import CliRunner

    resultado = CliRunner().invoke(
        app.cli, ["criar-reino", "Alfheim", "--cofre", "Banco"], input="n\n"
    )

    assert resultado.exit_code != 0
    db.session.expire_all()
    conta = buscar_usuario("Banco")
    assert conta.senha_hash is not None
    assert not conta.eh_cofre
