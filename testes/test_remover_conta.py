"""Apagar de verdade uma conta que já mexeu com dinheiro.

O degrau acima de encerrar. A conta some da tabela ``usuario``; o ledger fica
inteiro, apontando para uma conta-sombra.

**O teste que mais importa aqui é o de emissão.** Neste sistema, lançamento
sem ``origem_id`` *é* cunhagem — é o ramo do ``mover()`` que cria dinheiro.
O jeito óbvio de apagar alguém (anular as referências) faria a auditoria ler
o histórico de quem saiu como VVC nascido do nada, e o supply passaria a
mentir para sempre. ``test_nenhuma_linha_reatribuida_vira_emissao`` é o
alarme contra isso.
"""

import pytest

from vavacoin.auditoria import conferir_ledger, linhas_extrato
from vavacoin.caladinho import criar_casa, definir_dono
from vavacoin.erros import ErroMonetario, MotivoObrigatorio, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import PedidoDeCidadania, Transacao, Usuario
from vavacoin.moeda import supply_emitido
from vavacoin.operacoes import (
    ajustar_saldo,
    encerrar_conta,
    referencias_da_conta,
    remover_conta,
    transferir,
)
from vavacoin.ranking import gente
from vavacoin.reinos import (
    cidadaos,
    convidar,
    criar_reino,
    definir_operador,
    distribuir,
    entrar_no_reino,
    ranking_de_cidadaos,
)

from conftest import conservacao

SENHA_BC = "senha-do-painel"
SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def reino(app, bc):
    novo = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    return novo


@pytest.fixture
def rei(app, bc, reino, nova_pessoa):
    pessoa = nova_pessoa(nome="rei_alf")
    definir_operador(reino, pessoa, autoridade=bc)
    db.session.commit()
    return pessoa


# --- primeiro: a conta encerrada não devia continuar aparecendo -------------
#
# Metade do incômodo era isto, e era bug. A parte que não é estética: esta é
# a lista para quem o reino DISTRIBUI dinheiro.


def test_conta_encerrada_sai_da_tabela_de_cidadaos(app, bc, reino, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="20.00")
    entrar_no_reino(reino, pessoa)
    db.session.commit()
    assert pessoa in cidadaos(reino)

    encerrar_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    assert cidadaos(reino) == []


def test_conta_encerrada_sai_do_ranking_do_reino(app, bc, reino, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="20.00")
    entrar_no_reino(reino, pessoa)
    db.session.commit()

    encerrar_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    ranking, escondidos = ranking_de_cidadaos(reino)
    assert ranking == []
    assert escondidos == []


def test_o_reino_nao_distribui_para_conta_encerrada(app, bc, reino, rei, nova_pessoa):
    """O bug com dinheiro: repasse a uma conta que ninguém mais abre."""
    ajustar_saldo(reino.cofre, "100.00", "cofre", autoridade=bc)
    pessoa = nova_pessoa(nome="testa", saldo="20.00")
    entrar_no_reino(reino, pessoa)
    db.session.commit()

    encerrar_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()
    antes = conservacao()
    saldo_antes = pessoa.saldo

    with pytest.raises(ValorInvalido):
        distribuir(reino, rei, "5.00", [pessoa], "auxílio")
    db.session.rollback()

    assert conservacao() == antes
    assert pessoa.saldo == saldo_antes


def test_encerrar_fecha_pendencia_de_cidadania(app, bc, reino, rei, nova_pessoa):
    """Pendência sem saída prenderia o nome no índice único para sempre."""
    pessoa = nova_pessoa(nome="testa", saldo="20.00")
    convidar(reino, pessoa, rei)
    db.session.commit()

    encerrar_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    pendentes = (
        db.session.execute(
            db.select(PedidoDeCidadania).where(
                PedidoDeCidadania.usuario_id == pessoa.id,
                PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
            )
        )
        .scalars()
        .all()
    )
    assert pendentes == []


# --- remover de verdade -----------------------------------------------------


def _sem_origem():
    return db.session.execute(
        db.select(db.func.count())
        .select_from(Transacao)
        .where(Transacao.origem_id.is_(None))
    ).scalar_one()


def _sem_destino():
    return db.session.execute(
        db.select(db.func.count())
        .select_from(Transacao)
        .where(Transacao.destino_id.is_(None))
    ).scalar_one()


def test_remover_conta_com_historico_some_com_a_linha(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()
    conta_id = pessoa.id

    remover_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    assert db.session.get(Usuario, conta_id) is None


def test_remover_mantem_conservacao_e_auditoria(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    outra = nova_pessoa(nome="outra", saldo="10.00")
    transferir(outra.id, pessoa.id, "4.00", motivo="rachar a conta")
    db.session.commit()
    antes = conservacao()

    remover_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    assert conservacao() == antes
    assert conferir_ledger()["ok"]


def test_nenhuma_linha_reatribuida_vira_emissao(app, bc, nova_pessoa):
    """O alarme central: apagar gente não pode virar cunhagem.

    Se a remoção anulasse ``origem_id``, cada linha da pessoa passaria a ser
    lançamento sem origem — que é como o sistema representa dinheiro criado —
    e o supply subiria sozinho.
    """
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    outra = nova_pessoa(nome="outra", saldo="10.00")
    transferir(pessoa.id, outra.id, "7.00", motivo="pago")
    db.session.commit()
    supply_antes = supply_emitido()
    sem_origem_antes = _sem_origem()

    remover_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    assert supply_emitido() == supply_antes
    assert _sem_origem() == sem_origem_antes
    assert _sem_destino() == 0


def test_o_extrato_da_contraparte_continua_legivel(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    outra = nova_pessoa(nome="outra", saldo="10.00")
    transferir(pessoa.id, outra.id, "7.00", motivo="pago")
    db.session.commit()

    remover_conta(pessoa, "conta de teste", autoridade=bc)
    db.session.commit()

    recebida = [
        linha for linha in linhas_extrato(outra) if linha["motivo"] == "pago"
    ]
    assert len(recebida) == 1
    assert recebida[0]["contraparte"] == "conta removida"
    assert recebida[0]["valor_com_sinal"] == 7


def test_duas_contas_que_transacionaram_entre_si_podem_sumir(app, bc, nova_pessoa):
    """O caso que derruba a ideia de uma conta-sombra compartilhada.

    Com uma sombra só, esta linha ficaria sombra → sombra e esbarraria no
    ``CHECK origem_id <> destino_id``. E, mesmo sem o CHECK, a auditoria
    passaria a acusar toda linha reatribuída, porque o ``saldo_*_depois`` de
    cada uma é conferido contra o saldo reconstruído daquele id.
    """
    ana = nova_pessoa(nome="ana", saldo="30.00")
    bia = nova_pessoa(nome="bia", saldo="10.00")
    transferir(ana.id, bia.id, "5.00", motivo="entre as duas")
    db.session.commit()
    antes = conservacao()

    remover_conta(ana, "teste", autoridade=bc)
    db.session.commit()
    remover_conta(bia, "teste", autoridade=bc)
    db.session.commit()

    assert conservacao() == antes
    assert conferir_ledger()["ok"]


def test_remover_a_mesma_conta_duas_vezes_nao_quebra(app, bc, nova_pessoa):
    """O segundo POST chega com a linha já apagada, e não acha ninguém."""
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    encerrar_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()
    conta_id = pessoa.id
    antes = conservacao()
    painel = _painel(app, bc)

    for _ in range(2):
        painel.post(
            f"/painel/conta/{conta_id}/remover",
            data={"motivo": "conta de teste"},
            follow_redirects=True,
        )
        db.session.expire_all()

    assert db.session.get(Usuario, conta_id) is None
    assert conservacao() == antes
    assert conferir_ledger()["ok"]
    # Uma sombra, não duas: o segundo clique não criou conta nenhuma.
    assert (
        db.session.execute(
            db.select(db.func.count())
            .select_from(Usuario)
            .where(Usuario.eh_removida.is_(True))
        ).scalar_one()
        == 1
    )


def test_a_sombra_nao_e_gente_e_nao_entra_em_lista_nenhuma(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()

    remover_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()

    sombra = db.session.execute(
        db.select(Usuario).where(Usuario.eh_removida.is_(True))
    ).scalar_one()
    assert sombra.eh_conta_de_sistema
    assert sombra.saldo == 0
    assert sombra.senha_hash is None
    assert sombra.encerrada
    assert sombra.id not in [p.id for p in gente()]


def test_a_sombra_tambem_nao_se_remove(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()
    remover_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()

    sombra = db.session.execute(
        db.select(Usuario).where(Usuario.eh_removida.is_(True))
    ).scalar_one()
    with pytest.raises(ValorInvalido):
        remover_conta(sombra, "teste", autoridade=bc)


def test_remover_pede_motivo(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()
    with pytest.raises(MotivoObrigatorio):
        remover_conta(pessoa, "   ", autoridade=bc)


def test_o_saldo_volta_ao_banco_central_antes_de_sumir(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()
    bc_antes = bc.saldo

    remover_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()

    assert bc.saldo == bc_antes + 30


# --- as recusas -------------------------------------------------------------


def test_o_banco_central_nao_se_remove(app, bc):
    with pytest.raises(ValorInvalido):
        remover_conta(bc, "teste", autoridade=bc)


def test_a_casa_do_cassino_nao_se_remove(app, bc):
    casa = criar_casa(autoridade=bc)
    db.session.commit()
    with pytest.raises(ValorInvalido):
        remover_conta(casa, "teste", autoridade=bc)


def test_o_cofre_de_reino_nao_se_remove(app, bc, reino):
    with pytest.raises(ValorInvalido):
        remover_conta(reino.cofre, "teste", autoridade=bc)


def test_o_dono_do_cassino_nao_se_remove(app, bc, nova_pessoa):
    criar_casa(autoridade=bc)
    db.session.commit()
    pessoa = nova_pessoa(nome="dona")
    definir_dono(pessoa, autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        remover_conta(pessoa, "teste", autoridade=bc)


def test_o_operador_de_reino_nao_se_remove(app, bc, reino, rei):
    with pytest.raises(ValorInvalido):
        remover_conta(rei, "teste", autoridade=bc)


def test_so_o_banco_central_remove(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    comum = nova_pessoa(nome="outra")
    db.session.commit()
    with pytest.raises(ErroMonetario):
        remover_conta(pessoa, "teste", autoridade=comum)


# --- a tela -----------------------------------------------------------------


def _painel(app, bc):
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": SENHA_BC},
        follow_redirects=True,
    )
    return cliente


def test_a_tela_diz_o_que_vai_acontecer(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    encerrar_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()
    esperado = referencias_da_conta(pessoa)

    corpo = (
        _painel(app, bc)
        .get(f"/painel/conta/{pessoa.id}/remover")
        .get_data(as_text=True)
    )

    assert "testa" in corpo
    assert str(esperado) in corpo


def test_a_rota_remove_e_a_conta_some_do_painel(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    encerrar_conta(pessoa, "teste", autoridade=bc)
    db.session.commit()
    conta_id = pessoa.id
    painel = _painel(app, bc)

    painel.post(
        f"/painel/conta/{conta_id}/remover",
        data={"motivo": "conta de teste"},
        follow_redirects=True,
    )
    db.session.expire_all()

    assert db.session.get(Usuario, conta_id) is None
    assert "testa" not in painel.get("/painel").get_data(as_text=True)
    assert conferir_ledger()["ok"]


def test_a_rota_confere_de_novo_a_recusa(app, bc, reino, rei):
    """A tela pode ter sido desenhada antes de a pessoa virar operadora."""
    painel = _painel(app, bc)

    painel.post(
        f"/painel/conta/{rei.id}/remover",
        data={"motivo": "teste"},
        follow_redirects=True,
    )
    db.session.expire_all()

    assert db.session.get(Usuario, rei.id) is not None


def test_gente_comum_nao_abre_a_tela(app, bc, nova_pessoa):
    pessoa = nova_pessoa(nome="testa", saldo="30.00")
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "testa", "senha": SENHA},
        follow_redirects=True,
    )

    resposta = cliente.get(f"/painel/conta/{pessoa.id}/remover")

    assert resposta.status_code in (302, 403, 404)
