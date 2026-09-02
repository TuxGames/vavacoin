"""Apagar e encerrar conta — o bug do Benbals, não repetido.

O ``delete_user`` do Benbals **faz o saldo da pessoa sumir**, quebrando o
invariante de supply; lá isso só não estoura porque falha antes em erro de
chave estrangeira. Aqui a auditoria reconstrói cada saldo somando o ledger,
então apagar quem tem lançamentos deixaria a auditoria acusando para sempre.

Daí serem duas operações, e qual delas vale não ser escolha de quem clica:

- conta virgem (saldo zero, nenhum rastro) apaga de verdade;
- conta com história encerra: o saldo volta ao Banco Central por ``mover()``,
  com motivo, e as linhas do ledger ficam.

Todo teste daqui confere a conservação, e os que mexem em conta com história
conferem também a **auditoria**, que é a checagem que o Benbals não tem.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.auditoria import auditar
from vavacoin.caladinho import criar_casa, definir_dono
from vavacoin.erros import ContaComHistorico, MotivoObrigatorio, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import TIPO_ENCERRAMENTO, mover, soma_saldos, supply_emitido
from vavacoin.modelos import (
    Convite,
    RegistroAdministrativo,
    Transacao,
    Usuario,
    buscar_usuario,
)
from vavacoin.operacoes import (
    ajustar_saldo,
    apagar_conta,
    criar_convite,
    criar_usuario,
    destino_da_conta,
    encerrar_conta,
    resgatar_convite,
)

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def virgem(app, bc):
    """Conta recém-criada: saldo zero e nenhum rastro. O caso do teste dele."""
    conta = criar_usuario("teste1", SENHA, autoridade=bc)
    db.session.commit()
    return conta


def _auditoria_fecha():
    relatorio = auditar()
    assert relatorio["ok"], relatorio
    assert relatorio["ledger"]["saldos_divergentes"] == []
    assert relatorio["ledger"]["linhas_inconsistentes"] == []
    return True


# --- o que a conta aceita ---------------------------------------------------


def test_conta_virgem_aceita_apagar(app, bc, virgem):
    assert destino_da_conta(virgem) == "apagar"


def test_conta_com_saldo_so_aceita_encerrar(app, bc, virgem):
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()

    assert destino_da_conta(virgem) == "encerrar"


def test_conta_com_lancamento_so_aceita_encerrar_mesmo_com_saldo_zero(app, bc, virgem):
    """O saldo voltou a zero, mas o ledger continua falando dela.

    É o caso traiçoeiro: quem olha só o saldo conclui que dá para apagar, e
    apagar deixaria lançamentos apontando para ninguém.
    """
    ajustar_saldo(virgem, "10.00", "entrou", autoridade=bc)
    db.session.commit()
    mover(virgem, bc, "10.00", motivo="e saiu")
    db.session.commit()

    assert virgem.saldo == Decimal("0.00")
    assert destino_da_conta(virgem) == "encerrar"


def test_conta_que_jogou_so_aceita_encerrar(app, bc, nova_pessoa):
    """Rodada é rastro mesmo quando não sobrou dinheiro nenhum."""
    from vavacoin.caladinho import criar_rodada

    casa = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(casa, "1000.00", "caixa", autoridade=bc)
    db.session.commit()
    ana = nova_pessoa(nome="ana", saldo="10.00")
    criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()

    assert destino_da_conta(ana) == "encerrar"


# --- apagar de verdade ------------------------------------------------------


def test_apagar_conta_virgem_some_com_ela(app, bc, virgem):
    antes = conservacao()
    conta_id = virgem.id

    apagar_conta(virgem, autoridade=bc)
    db.session.commit()

    assert db.session.get(Usuario, conta_id) is None
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_apagar_conta_virgem_mantem_a_conservacao_e_a_auditoria(app, bc, virgem):
    """O teste que o Benbals não tem, e que é o motivo deste arquivo existir."""
    supply_antes = supply_emitido()
    soma_antes = soma_saldos()

    apagar_conta(virgem, autoridade=bc)
    db.session.commit()

    assert supply_emitido() == supply_antes
    assert soma_saldos() == soma_antes
    assert soma_saldos() == supply_emitido()
    assert _auditoria_fecha()


def test_apagar_conta_com_saldo_e_recusado(app, bc, virgem):
    """A recusa é do servidor, não da tela."""
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()
    antes = conservacao()

    with pytest.raises(ContaComHistorico):
        apagar_conta(virgem, autoridade=bc)
    db.session.rollback()

    assert db.session.get(Usuario, virgem.id) is not None
    assert conservacao() == antes


def test_apagar_conta_com_historico_e_recusado(app, bc, virgem):
    ajustar_saldo(virgem, "10.00", "entrou", autoridade=bc)
    db.session.commit()
    mover(virgem, bc, "10.00", motivo="e saiu")
    db.session.commit()

    with pytest.raises(ContaComHistorico):
        apagar_conta(virgem, autoridade=bc)
    db.session.rollback()

    assert db.session.get(Usuario, virgem.id) is not None
    assert _auditoria_fecha()


def test_apagar_leva_o_convite_junto(app, bc):
    """O convite registrava que aquela pessoa entrou, e a entrada foi apagada.

    Deixá-lo livre faria o código valer de novo para quem o tivesse guardado;
    deixá-lo apontando para ninguém seria lixo.
    """
    conta = criar_usuario("teste2", SENHA, autoridade=bc)
    db.session.commit()
    convite = criar_convite(destinatario="Teste", autoridade=bc)
    db.session.commit()
    resgatar_convite(conta, convite.codigo)
    db.session.commit()
    codigo = convite.codigo

    apagar_conta(conta, autoridade=bc)
    db.session.commit()

    assert (
        db.session.execute(
            db.select(Convite).where(Convite.codigo == codigo)
        ).scalar_one_or_none()
        is None
    )
    assert _auditoria_fecha()


# --- encerrar ---------------------------------------------------------------


def test_encerrar_devolve_exatamente_o_saldo_ao_banco_central(app, bc, virgem):
    """O número que importa: o saldo volta inteiro, e por ``mover()``."""
    ajustar_saldo(virgem, "42.50", "dinheiro", autoridade=bc)
    db.session.commit()
    antes = conservacao()
    bc_antes = bc.saldo

    encerrar_conta(virgem, "conta de teste", autoridade=bc)
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Usuario, virgem.id).saldo == Decimal("0.00")
    assert db.session.get(Usuario, bc.id).saldo == bc_antes + Decimal("42.50")
    assert soma_saldos() == supply_emitido()
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_a_devolucao_e_um_lancamento_com_tipo_proprio(app, bc, virgem):
    """Não some por ``UPDATE``: aparece no extrato dos dois lados."""
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()

    encerrar_conta(virgem, "conta de teste", autoridade=bc)
    db.session.commit()

    lancamento = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == TIPO_ENCERRAMENTO)
    ).scalar_one()
    assert lancamento.origem_id == virgem.id
    assert lancamento.destino_id == bc.id
    assert lancamento.valor == Decimal("10.00")
    assert lancamento.motivo == "conta de teste"


def test_encerrar_mantem_as_linhas_do_ledger(app, bc, nova_pessoa):
    """O extrato de quem transacionou com ela continua fazendo sentido."""
    ana = nova_pessoa(nome="ana", saldo="50.00")
    bia = nova_pessoa(nome="bia", saldo="10.00")
    mover(ana, bia, "20.00", motivo="pagamento")
    db.session.commit()
    quantas = db.session.query(Transacao).count()

    encerrar_conta(ana, "saiu do jogo", autoridade=bc)
    db.session.commit()

    # A transferência continua lá, e ganhou a devolução por cima.
    assert db.session.query(Transacao).count() == quantas + 1
    assert db.session.get(Usuario, ana.id) is not None
    assert _auditoria_fecha()


def test_encerrar_sem_saldo_nao_move_dinheiro(app, bc, virgem):
    ajustar_saldo(virgem, "10.00", "entrou", autoridade=bc)
    db.session.commit()
    mover(virgem, bc, "10.00", motivo="e saiu")
    db.session.commit()
    quantas = db.session.query(Transacao).count()

    encerrar_conta(virgem, "conta de teste", autoridade=bc)
    db.session.commit()

    assert db.session.query(Transacao).count() == quantas
    assert db.session.get(Usuario, virgem.id).encerrada


def test_encerrar_pede_motivo(app, bc, virgem):
    with pytest.raises(MotivoObrigatorio):
        encerrar_conta(virgem, "", autoridade=bc)


def test_encerrar_duas_vezes_e_recusado(app, bc, virgem):
    encerrar_conta(virgem, "primeira", autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        encerrar_conta(virgem, "segunda", autoridade=bc)


def test_conta_encerrada_fica_registrada_no_diario(app, bc, virgem):
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()

    encerrar_conta(virgem, "conta de teste", autoridade=bc)
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.alvo == virgem.nome_usuario)
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert "encerrada" in registro.detalhe
    assert "10.00" in registro.detalhe
    assert registro.motivo == "conta de teste"


def test_apagar_fica_registrado_no_diario(app, bc, virgem):
    nome = virgem.nome_usuario
    apagar_conta(virgem, autoridade=bc)
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.alvo == nome)
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert "apagada" in registro.detalhe


# --- a conta encerrada não entra --------------------------------------------


def test_conta_encerrada_nao_entra(app, bc):
    """O encerramento vira realidade no login por ``is_active``.

    ``login_user`` recusa quem não é ativo, então não há uma segunda checagem
    para alguém esquecer de fazer.
    """
    conta = criar_usuario("saiu", SENHA, autoridade=bc)
    db.session.commit()
    cliente = app.test_client()

    entrou = cliente.post(
        "/entrar",
        data={"nome_usuario": "saiu", "senha": SENHA},
        follow_redirects=True,
    )
    assert "/sair" in entrou.get_data(as_text=True), "antes de encerrar, entra"
    cliente.post("/sair", follow_redirects=True)

    encerrar_conta(conta, "conta de teste", autoridade=bc)
    db.session.commit()

    resposta = app.test_client().post(
        "/entrar",
        data={"nome_usuario": "saiu", "senha": SENHA},
        follow_redirects=True,
    )
    assert resposta.status_code == 403
    assert "/sair" not in resposta.get_data(as_text=True)


def test_conta_encerrada_nao_recebe_transferencia(app, bc, nova_pessoa):
    """Mandar dinheiro para conta que ninguém abre é dinheiro parado."""
    ana = nova_pessoa(nome="ana", saldo="50.00")
    bia = nova_pessoa(nome="bia", saldo="10.00")
    encerrar_conta(bia, "saiu", autoridade=bc)
    db.session.commit()
    antes = conservacao()

    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": "ana", "senha": SENHA}, follow_redirects=True
    )
    resposta = cliente.post(
        "/transferir",
        data={"destinatario": "bia", "valor": "5.00", "motivo": "oi"},
        follow_redirects=True,
    )

    assert "Não existe ninguém com esse usuário." in resposta.get_data(as_text=True)
    assert conservacao() == antes


# --- as contas que não se tocam ---------------------------------------------


def test_o_banco_central_nao_se_apaga(app, bc):
    with pytest.raises(ValorInvalido):
        apagar_conta(bc, autoridade=bc)
    with pytest.raises(ValorInvalido):
        encerrar_conta(bc, "não", autoridade=bc)


def test_a_casa_do_cassino_nao_se_apaga(app, bc):
    casa = criar_casa(autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        apagar_conta(casa, autoridade=bc)
    with pytest.raises(ValorInvalido):
        encerrar_conta(casa, "não", autoridade=bc)


def test_o_dono_do_cassino_nao_sai_sem_passar_a_posse(app, bc, nova_pessoa):
    """Apagar quem responde pela casa deixaria a casa órfã por acidente."""
    criar_casa(autoridade=bc)
    db.session.commit()
    gustavo = nova_pessoa(nome="gustavo", saldo="0.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        encerrar_conta(gustavo, "tchau", autoridade=bc)

    # Passada a posse, sai normalmente.
    outro = nova_pessoa(nome="outro", saldo="0.00")
    definir_dono(outro, autoridade=bc)
    db.session.commit()

    encerrar_conta(gustavo, "tchau", autoridade=bc)
    db.session.commit()
    assert db.session.get(Usuario, gustavo.id).encerrada


# --- a web ------------------------------------------------------------------


@pytest.fixture
def painel(app, bc):
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": SENHA_BC},
        follow_redirects=True,
    )
    return cliente


def test_o_painel_oferece_apagar_para_a_conta_virgem(app, bc, painel, virgem):
    corpo = painel.get("/painel/").get_data(as_text=True)

    assert f"/painel/conta/{virgem.id}/apagar" in corpo
    assert f"/painel/conta/{virgem.id}/encerrar" not in corpo


def test_o_painel_nao_oferece_apagar_para_conta_com_historico(app, bc, painel, virgem):
    """O botão de apagar de verdade nem aparece onde ele mentiria."""
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()

    corpo = painel.get("/painel/").get_data(as_text=True)

    assert f"/painel/conta/{virgem.id}/apagar" not in corpo
    assert f"/painel/conta/{virgem.id}/encerrar" in corpo


def test_apagar_pelo_painel(app, bc, painel, virgem):
    antes = conservacao()
    conta_id = virgem.id

    resposta = painel.post(f"/painel/conta/{conta_id}/apagar", follow_redirects=True)

    assert resposta.status_code == 200
    assert db.session.get(Usuario, conta_id) is None
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_encerrar_pelo_painel(app, bc, painel, virgem):
    ajustar_saldo(virgem, "30.00", "dinheiro", autoridade=bc)
    db.session.commit()
    antes = conservacao()
    bc_antes = bc.saldo

    resposta = painel.post(
        f"/painel/conta/{virgem.id}/encerrar",
        data={"motivo": "conta de teste"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    db.session.expire_all()
    assert db.session.get(Usuario, virgem.id).encerrada
    assert db.session.get(Usuario, bc.id).saldo == bc_antes + Decimal("30.00")
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_apagar_pelo_painel_recusa_conta_com_saldo(app, bc, painel, virgem):
    """Entre desenhar a tela e clicar, a conta pode ter recebido dinheiro."""
    ajustar_saldo(virgem, "10.00", "dinheiro", autoridade=bc)
    db.session.commit()
    antes = conservacao()

    painel.post(f"/painel/conta/{virgem.id}/apagar", follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Usuario, virgem.id) is not None
    assert conservacao() == antes


def test_so_o_banco_central_apaga(app, bc, nova_pessoa, virgem):
    ana = nova_pessoa(nome="ana", saldo="10.00")
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": "ana", "senha": SENHA}, follow_redirects=True
    )

    resposta = cliente.post(f"/painel/conta/{virgem.id}/apagar")

    assert resposta.status_code in (302, 403)
    assert db.session.get(Usuario, virgem.id) is not None
