"""A posse do Caladinho: dono, aporte, retirada e lucro.

A regra que não pode falhar é a da retirada: o que está **comprometido** por
rodada aberta fica preso. Sem isso o dono esvazia a casa no meio de uma jogada
e quem ganha não recebe.

O dono também joga no próprio cassino, e isso não tem tratamento especial —
tem teste.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    TIPO_APORTE,
    TIPO_PREMIO,
    TIPO_RETIRADA,
    aportar,
    casa,
    criar_casa,
    criar_rodada,
    definir_dono,
    dono,
    exposicao_comprometida,
    livre_para_retirar,
    lucro_do_dono,
    retirar,
    retirar_do_caixa,
    revelar_casa,
)
from vavacoin.erros import CaixaComprometido, SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.mines import CASAS, tabela_de_multiplicadores
from vavacoin.modelos import Transacao, Usuario
from vavacoin.operacoes import ajustar_saldo


@pytest.fixture
def cassino(app, bc):
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta, "1000.00", "caixa inicial", autoridade=bc)
    db.session.commit()
    return conta


@pytest.fixture
def guga(app, bc, nova_pessoa):
    """A conta do dono. O nome real vem por CLI, nunca do código."""
    return nova_pessoa(nome="gugaearthur", com_convite=True, saldo="500.00")


@pytest.fixture
def com_dono(app, bc, cassino, guga):
    definir_dono(guga, autoridade=bc)
    db.session.commit()
    return guga


def _abrir_seguras(rodada, jogador, quantas):
    seguras = [c for c in range(CASAS) if c not in rodada.casas_com_mina]
    for posicao in seguras[:quantas]:
        revelar_casa(jogador, posicao)
    db.session.commit()
    return seguras


# --- apontar o dono ---------------------------------------------------------


def test_a_casa_nasce_sem_dono(app, bc, cassino):
    """"Sem dono" é um estado, não um caso especial."""
    assert cassino.dono_id is None
    assert cassino.dono_desde is None
    assert dono() is None
    assert lucro_do_dono() == Decimal("0.00")


def test_definir_dono_marca_desde_quando(app, bc, cassino, guga):
    definir_dono(guga, autoridade=bc)
    db.session.commit()

    assert dono().id == guga.id
    assert cassino.dono_desde is not None


def test_so_o_banco_central_aponta_o_dono(app, bc, cassino, guga):
    with pytest.raises(SemAutoridade):
        definir_dono(guga, autoridade=guga)
    db.session.rollback()
    assert dono() is None


def test_conta_de_sistema_nao_e_dona(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        definir_dono(bc, autoridade=bc)
    db.session.rollback()

    with pytest.raises(ValorInvalido):
        definir_dono(cassino, autoridade=bc)
    db.session.rollback()


def test_tirar_o_dono(app, bc, cassino, com_dono):
    definir_dono(None, autoridade=bc)
    db.session.commit()

    assert dono() is None
    assert cassino.dono_desde is None


def test_trocar_de_dono(app, bc, cassino, com_dono, nova_pessoa):
    """A posse é fixa por decisão, não por o código não saber trocar."""
    outro = nova_pessoa(nome="outro", com_convite=True)
    definir_dono(outro, autoridade=bc)
    db.session.commit()

    assert dono().id == outro.id


# --- aportar e retirar ------------------------------------------------------


def test_aportar_move_pelo_ledger_e_aparece_nos_dois_extratos(app, bc, cassino, com_dono):
    from vavacoin.auditoria import linhas_extrato

    conservacao()
    transacao = aportar(com_dono, "200.00")
    db.session.commit()

    assert transacao.tipo == TIPO_APORTE
    assert transacao.origem_id == com_dono.id
    assert transacao.destino_id == cassino.id
    assert com_dono.saldo == Decimal("300.00")
    assert cassino.saldo == Decimal("1200.00")

    do_dono = linhas_extrato(com_dono)[0]
    da_casa = linhas_extrato(cassino)[0]
    assert do_dono["valor_com_sinal"] == Decimal("-200.00")
    assert da_casa["valor_com_sinal"] == Decimal("200.00")
    conservacao()


def test_retirar_move_pelo_ledger(app, bc, cassino, com_dono):
    conservacao()
    transacao = retirar_do_caixa(com_dono, "400.00")
    db.session.commit()

    assert transacao.tipo == TIPO_RETIRADA
    assert transacao.origem_id == cassino.id
    assert transacao.destino_id == com_dono.id
    assert cassino.saldo == Decimal("600.00")
    assert com_dono.saldo == Decimal("900.00")
    conservacao()


def test_so_o_dono_aporta_e_retira(app, bc, cassino, com_dono, nova_pessoa):
    estranho = nova_pessoa(nome="estranho", com_convite=True, saldo="100.00")
    conservacao()

    with pytest.raises(SemAutoridade):
        aportar(estranho, "10.00")
    db.session.rollback()

    with pytest.raises(SemAutoridade):
        retirar_do_caixa(estranho, "10.00")
    db.session.rollback()
    conservacao()


def test_sem_dono_ninguem_aporta_nem_retira(app, bc, cassino, guga):
    with pytest.raises(SemAutoridade):
        aportar(guga, "10.00")
    db.session.rollback()

    with pytest.raises(SemAutoridade):
        retirar_do_caixa(guga, "10.00")
    db.session.rollback()


@pytest.mark.parametrize("valor", ["0.00", "-1.00", "0.001", "abc"])
def test_retirada_invalida(app, bc, cassino, com_dono, valor):
    with pytest.raises(ValorInvalido):
        retirar_do_caixa(com_dono, valor)
    db.session.rollback()
    conservacao()


# --- a fronteira do comprometido --------------------------------------------


def test_sem_rodada_aberta_o_livre_e_o_caixa_inteiro(app, bc, cassino, com_dono):
    assert exposicao_comprometida() == Decimal("0.00")
    assert livre_para_retirar() == cassino.saldo


def test_rodada_aberta_prende_o_comprometido(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    # A aposta de 10 pode pagar 250 (10 × 25). O caixa subiu para 1010.
    assert exposicao_comprometida() == Decimal("250.00")
    assert livre_para_retirar() == Decimal("760.00")
    conservacao()


def test_retirar_exatamente_o_livre_passa(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    livre = livre_para_retirar()

    retirar_do_caixa(com_dono, livre)
    db.session.commit()

    db.session.expire_all()
    assert livre_para_retirar() == Decimal("0.00")
    assert casa().saldo == exposicao_comprometida()
    conservacao()


def test_um_centavo_alem_do_livre_e_recusado_e_nao_move_nada(
    app, bc, cassino, com_dono, nova_pessoa
):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    conservacao()

    livre = livre_para_retirar()
    caixa_antes = casa().saldo
    saldo_antes = com_dono.saldo

    with pytest.raises(CaixaComprometido):
        retirar_do_caixa(com_dono, livre + Decimal("0.01"))
    db.session.rollback()

    db.session.expire_all()
    assert casa().saldo == caixa_antes
    assert db.session.get(Usuario, com_dono.id).saldo == saldo_antes
    conservacao()


def test_a_mensagem_diz_quanto_esta_livre(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    with pytest.raises(CaixaComprometido, match="760.00"):
        retirar_do_caixa(com_dono, "900.00")
    db.session.rollback()


def test_o_caso_feio_a_casa_paga_o_maximo_depois_de_esvaziada(
    app, bc, cassino, com_dono, nova_pessoa
):
    """Retirar todo o livre com rodada aberta, e a rodada ganhar no máximo.

    Se a casa não conseguir pagar aqui, a conta do comprometido está errada.
    É este teste que sustenta a regra inteira.
    """
    from vavacoin.auditoria import auditar

    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    rodada = criar_rodada(jogador, "10.00", 10)
    db.session.commit()

    # O dono tira tudo o que pode; sobra exatamente o comprometido.
    retirar_do_caixa(com_dono, livre_para_retirar())
    db.session.commit()
    db.session.expire_all()
    assert casa().saldo == Decimal("250.00")
    conservacao()

    # A rodada vai até o teto e paga 25× — o máximo possível.
    passos = tabela_de_multiplicadores(10)
    _abrir_seguras(rodada, jogador, passos[-1][0])

    db.session.refresh(rodada)
    assert rodada.premio == Decimal("250.00")
    db.session.expire_all()
    assert casa().saldo == Decimal("0.00"), "a casa pagou até o último centavo"
    assert db.session.get(Usuario, jogador.id).saldo == Decimal("340.00")
    assert auditar()["ok"] is True
    conservacao()


# --- lucro ------------------------------------------------------------------


def test_lucro_soma_apostas_menos_premios(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    assert lucro_do_dono() == Decimal("10.00"), "a aposta entrou"

    _abrir_seguras(rodada, jogador, 1)
    retirar(jogador)
    db.session.commit()

    db.session.refresh(rodada)
    assert lucro_do_dono() == Decimal("10.00") - rodada.premio
    conservacao()


def test_aporte_e_retirada_nao_contam_como_lucro(app, bc, cassino, com_dono):
    """Mexer no próprio caixa não é ganhar nem perder."""
    aportar(com_dono, "100.00")
    retirar_do_caixa(com_dono, "50.00")
    db.session.commit()

    assert lucro_do_dono() == Decimal("0.00")
    conservacao()


def test_lucro_conta_a_partir_da_data_em_que_assumiu(
    app, bc, cassino, guga, nova_pessoa
):
    """O que a casa ganhou antes de ele assumir não é lucro dele."""
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    revelar_casa(jogador, rodada.casas_com_mina[0])
    db.session.commit()

    # A casa ganhou 10 antes de ter dono.
    definir_dono(guga, autoridade=bc)
    db.session.commit()
    assert lucro_do_dono() == Decimal("0.00")

    # Do zero em diante, conta.
    segunda = criar_rodada(jogador, "20.00", 3)
    db.session.commit()
    assert lucro_do_dono() == Decimal("20.00")
    assert segunda is not None
    conservacao()


# --- o dono jogando no próprio cassino --------------------------------------


def test_o_dono_joga_no_proprio_cassino(app, bc, cassino, com_dono):
    """Sem tratamento especial: a aposta sai dele e entra na casa."""
    from vavacoin.auditoria import auditar

    conservacao()
    saldo_antes = com_dono.saldo

    rodada = criar_rodada(com_dono, "10.00", 3)
    db.session.commit()

    db.session.expire_all()
    assert db.session.get(Usuario, com_dono.id).saldo == saldo_antes - Decimal("10.00")
    assert casa().saldo == Decimal("1010.00")
    assert auditar()["ok"] is True
    conservacao()

    _abrir_seguras(rodada, com_dono, 2)
    retirar(com_dono)
    db.session.commit()

    db.session.refresh(rodada)
    premio = rodada.premio
    db.session.expire_all()
    assert db.session.get(Usuario, com_dono.id).saldo == (
        saldo_antes - Decimal("10.00") + premio
    )
    assert auditar()["ok"] is True
    conservacao()


def test_o_dono_perdendo_no_proprio_cassino(app, bc, cassino, com_dono):
    from vavacoin.auditoria import auditar

    rodada = criar_rodada(com_dono, "10.00", 3)
    db.session.commit()
    revelar_casa(com_dono, rodada.casas_com_mina[0])
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.premio == Decimal("0.00")
    # O dinheiro foi da conta dele para a casa, que também é dele. Continua
    # sendo duas contas, e o ledger explica as duas.
    assert auditar()["ok"] is True
    conservacao()


def test_o_dono_nao_escapa_do_teto_de_aposta(app, bc, cassino, com_dono):
    """Jogar na própria casa não dá limite maior."""
    from vavacoin.erros import ApostaAlta

    with pytest.raises(ApostaAlta):
        criar_rodada(com_dono, "21.00", 3)
    db.session.rollback()
    conservacao()


def test_premio_do_dono_sai_da_casa_e_nao_do_nada(app, bc, cassino, com_dono):
    rodada = criar_rodada(com_dono, "10.00", 3)
    db.session.commit()
    _abrir_seguras(rodada, com_dono, 1)
    retirar(com_dono)
    db.session.commit()

    db.session.refresh(rodada)
    premio = db.session.get(Transacao, rodada.transacao_premio_id)
    assert premio.tipo == TIPO_PREMIO
    assert premio.origem_id == cassino.id
    assert premio.destino_id == com_dono.id
    conservacao()


# --- pela CLI ---------------------------------------------------------------


def _rodar(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def test_apontar_o_dono_pela_cli(app, bc, cassino, guga):
    """O nome vem por argumento — nunca amarrado no código."""
    resultado = _rodar(app, "dono-cassino", "gugaearthur")

    assert resultado.exit_code == 0, resultado.output
    db.session.expire_all()
    assert dono().nome_usuario == "gugaearthur"


def test_cli_aceita_o_nome_com_outra_grafia(app, bc, cassino, guga):
    """Mesma regra do resto do site: compara normalizado."""
    assert _rodar(app, "dono-cassino", "GugaEArthur").exit_code == 0
    db.session.expire_all()
    assert dono().id == guga.id


def test_cli_recusa_conta_inexistente(app, bc, cassino):
    resultado = _rodar(app, "dono-cassino", "fantasma")
    assert resultado.exit_code != 0
    assert "inexistente" in resultado.output
    assert dono() is None


def test_cli_sem_argumento_mostra_o_dono_atual(app, bc, cassino, com_dono):
    resultado = _rodar(app, "dono-cassino")
    assert resultado.exit_code == 0
    assert "gugaearthur" in resultado.output


def test_cli_tira_o_dono(app, bc, cassino, com_dono):
    assert _rodar(app, "dono-cassino", "--sem-dono").exit_code == 0
    db.session.expire_all()
    assert dono() is None


# --- a tela do dono ---------------------------------------------------------


def _entrar(app, conta, senha="senha-boa-123"):
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": conta.nome_usuario, "senha": senha},
        follow_redirects=True,
    )
    return cliente


def test_a_tela_da_casa_e_so_do_dono(app, bc, cassino, com_dono, nova_pessoa):
    estranho = nova_pessoa(nome="estranho", com_convite=True, saldo="10.00")
    db.session.commit()

    assert _entrar(app, estranho).get("/caladinho/casa").status_code == 403
    assert _entrar(app, com_dono).get("/caladinho/casa").status_code == 200


def test_o_link_da_casa_so_aparece_para_o_dono(app, bc, cassino, com_dono, nova_pessoa):
    estranho = nova_pessoa(nome="estranho", com_convite=True, saldo="10.00")
    db.session.commit()

    de_fora = _entrar(app, estranho).get("/caladinho/").get_data(as_text=True)
    do_dono = _entrar(app, com_dono).get("/caladinho/").get_data(as_text=True)
    assert "/caladinho/casa" not in de_fora
    assert "/caladinho/casa" in do_dono


def test_aportar_e_retirar_pela_tela(app, bc, cassino, com_dono):
    cliente = _entrar(app, com_dono)
    conservacao()

    cliente.post("/caladinho/casa/aportar", data={"valor": "100.00"}, follow_redirects=True)
    db.session.expire_all()
    assert casa().saldo == Decimal("1100.00")
    conservacao()

    cliente.post("/caladinho/casa/retirar", data={"valor": "300.00"}, follow_redirects=True)
    db.session.expire_all()
    assert casa().saldo == Decimal("800.00")
    conservacao()


def test_a_tela_recusa_retirar_alem_do_livre(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    caixa_antes = casa().saldo

    cliente = _entrar(app, com_dono)
    resposta = cliente.post(
        "/caladinho/casa/retirar", data={"valor": "900.00"}, follow_redirects=True
    )

    assert "livre para retirar" in resposta.get_data(as_text=True)
    db.session.expire_all()
    assert casa().saldo == caixa_antes
    conservacao()


def test_a_tela_mostra_os_numeros(app, bc, cassino, com_dono, nova_pessoa):
    jogador = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    corpo = _entrar(app, com_dono).get("/caladinho/casa").get_data(as_text=True)
    assert "1010.00" in corpo  # caixa
    assert "250.00" in corpo   # comprometido
    assert "760.00" in corpo   # livre
