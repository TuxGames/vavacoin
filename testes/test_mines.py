"""Mines: a matemática, o dinheiro e as travas.

O que este arquivo persegue é o que morde num cassino: pagar duas vezes,
cobrar sem poder pagar, e o tabuleiro vazar antes da hora.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    TIPO_APOSTA,
    TIPO_PREMIO,
    VALIDADE_DO_MINES,
    casa,
    criar_casa,
    criar_rodada,
    expirar_mines_abandonadas,
    exposicao_comprometida,
    limite_de_aposta,
    retirar,
    revelar_casa,
    rodada_ativa,
    visao_da_rodada,
)
from vavacoin.erros import ApostaAlta, RodadaEmAndamento, SemRodadaAtiva, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.mines import (
    CASAS,
    TETO_DO_MULTIPLICADOR,
    aposta_maxima,
    multiplicador,
    multiplicador_justo,
    multiplicador_pagavel,
    premio_maximo,
    tabela_de_multiplicadores,
)
from vavacoin.dinheiro import ZERO
from vavacoin.moeda import soma_saldos
from vavacoin.modelos import RodadaMines, Transacao, agora, buscar_usuario
from vavacoin.operacoes import ajustar_saldo


@pytest.fixture
def cassino(app, bc):
    """A casa criada e com caixa para pagar."""
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    from vavacoin.operacoes import ajustar_saldo

    ajustar_saldo(conta, "1000.00", "caixa inicial do teste", autoridade=bc)
    db.session.commit()
    return conta


@pytest.fixture
def jogador(app, bc, nova_pessoa):
    return nova_pessoa(nome="tux", com_convite=True, saldo="100.00")


def _abrir_seguras(rodada, jogador, quantas):
    """Abre ``quantas`` casas que não têm mina."""
    seguras = [c for c in range(CASAS) if c not in rodada.casas_com_mina]
    for posicao in seguras[:quantas]:
        revelar_casa(jogador, posicao)
    db.session.commit()
    return seguras


# --- matemática -------------------------------------------------------------


def test_multiplicador_justo_e_fracao_de_inteiros(app):
    """1 mina, 1ª casa: 25/24. Exato, sem ruído de ponto flutuante."""
    assert multiplicador_justo(1, 1) == Decimal(25) / Decimal(24)
    assert multiplicador_justo(1, 0) == Decimal(1)


def test_multiplicador_do_original(app):
    """O exemplo documentado no cassino original: 25/24 × 0,98 → 1,02."""
    assert multiplicador(1, 1) == Decimal("1.02")


def test_multiplicador_cresce_a_cada_casa(app):
    valores = [multiplicador(3, k) for k in range(1, 10)]
    assert valores == sorted(valores)
    assert len(set(valores)) == len(valores)


def test_multiplicador_pagavel_para_no_teto(app):
    """Abrir além do teto não paga mais nada."""
    tabela = tabela_de_multiplicadores(3)
    assert tabela[-1][1] == TETO_DO_MULTIPLICADOR

    ultimo_k = tabela[-1][0]
    assert multiplicador(3, ultimo_k) >= TETO_DO_MULTIPLICADOR
    assert multiplicador_pagavel(3, ultimo_k + 1) == TETO_DO_MULTIPLICADOR


def test_aposta_maxima_e_caixa_dividido_por_cinquenta(app):
    """0,50 × caixa ÷ 25 — a conta que o dono fez."""
    assert aposta_maxima(Decimal("1000.00")) == Decimal("20.00")
    assert premio_maximo(Decimal("20.00")) == Decimal("500.00")


def test_aposta_maxima_desconta_o_comprometido(app):
    assert aposta_maxima(Decimal("1000.00"), Decimal("250.00")) == Decimal("10.00")
    assert aposta_maxima(Decimal("1000.00"), Decimal("500.00")) == Decimal("0.00")


# --- dinheiro ---------------------------------------------------------------


def test_aposta_e_premio_sao_dois_lancamentos(app, bc, cassino, jogador):
    conservacao()
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    aposta = db.session.get(Transacao, rodada.transacao_aposta_id)
    assert aposta.tipo == TIPO_APOSTA
    assert aposta.origem_id == jogador.id
    assert aposta.destino_id == cassino.id
    assert jogador.saldo == Decimal("90.00")
    assert cassino.saldo == Decimal("1010.00")
    conservacao()

    _abrir_seguras(rodada, jogador, 1)
    retirar(jogador)
    db.session.commit()

    db.session.refresh(rodada)
    premio = db.session.get(Transacao, rodada.transacao_premio_id)
    assert premio.tipo == TIPO_PREMIO
    assert premio.origem_id == cassino.id
    assert premio.destino_id == jogador.id
    conservacao()


def test_auditoria_fecha_depois_de_rodada_ganha(app, bc, cassino, jogador):
    from vavacoin.auditoria import auditar

    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    _abrir_seguras(rodada, jogador, 2)
    retirar(jogador)
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.estado == RodadaMines.RETIRADA
    assert rodada.premio > 0
    assert auditar()["ok"] is True
    conservacao()


def test_auditoria_fecha_depois_de_rodada_perdida(app, bc, cassino, jogador):
    from vavacoin.auditoria import auditar

    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    revelar_casa(jogador, rodada.casas_com_mina[0])
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.estado == RodadaMines.ESTOURADA
    assert rodada.premio == Decimal("0.00")
    assert jogador.saldo == Decimal("90.00")
    assert cassino.saldo == Decimal("1010.00")
    assert auditar()["ok"] is True
    conservacao()


def test_auditoria_fecha_com_rodada_abandonada_no_meio(app, bc, cassino, jogador):
    """Ninguém retira, ninguém estoura: a aposta fica com a casa."""
    from vavacoin.auditoria import auditar

    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    _abrir_seguras(rodada, jogador, 2)

    assert rodada_ativa(jogador) is not None
    assert auditar()["ok"] is True
    conservacao()


# --- travas -----------------------------------------------------------------


def test_uma_rodada_ativa_por_vez(app, bc, cassino, jogador):
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    conservacao()

    with pytest.raises(RodadaEmAndamento):
        criar_rodada(jogador, "10.00", 3)
    db.session.rollback()

    assert db.session.query(RodadaMines).count() == 1
    conservacao()


def test_recarregar_retoma_a_mesma_rodada(app, bc, cassino, jogador):
    """GET não cria rodada nem sorteia de novo."""
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    minas = rodada.casas_com_mina

    de_novo = rodada_ativa(jogador)
    assert de_novo.id == rodada.id
    assert de_novo.casas_com_mina == minas


def test_abrir_a_mesma_casa_duas_vezes_nao_muda_nada(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    seguras = _abrir_seguras(rodada, jogador, 1)

    antes = rodada.multiplicador
    revelar_casa(jogador, seguras[0])
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.multiplicador == antes
    assert len(rodada.casas_reveladas) == 1
    conservacao()


def test_retirar_duas_vezes_paga_uma_so(app, bc, cassino, jogador):
    """A trava de estado é o que separa 'clicou duas vezes' de 'ganhou duas'."""
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    _abrir_seguras(rodada, jogador, 2)
    retirar(jogador)
    db.session.commit()
    saldo = jogador.saldo

    with pytest.raises(SemRodadaAtiva):
        retirar(jogador)
    db.session.rollback()

    db.session.expire_all()
    assert jogador.saldo == saldo
    assert db.session.query(Transacao).filter_by(tipo=TIPO_PREMIO).count() == 1
    conservacao()


def test_revelar_depois_de_encerrada(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    revelar_casa(jogador, rodada.casas_com_mina[0])
    db.session.commit()

    with pytest.raises(SemRodadaAtiva):
        revelar_casa(jogador, 0)
    db.session.rollback()
    conservacao()


def test_retirar_sem_abrir_casa(app, bc, cassino, jogador):
    criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        retirar(jogador)
    db.session.rollback()
    conservacao()


# --- teto de banca ----------------------------------------------------------


def test_aposta_no_limite_passa_e_um_centavo_acima_e_recusada(app, bc, cassino, jogador):
    """Fronteira exata: caixa 1000 → máximo 20,00."""
    from vavacoin.operacoes import ajustar_saldo

    ajustar_saldo(jogador, "100.00", "saldo para o teste", autoridade=bc)
    db.session.commit()
    assert limite_de_aposta() == Decimal("20.00")
    conservacao()

    saldo_antes = jogador.saldo
    caixa_antes = cassino.saldo
    with pytest.raises(ApostaAlta):
        criar_rodada(jogador, "20.01", 3)
    db.session.rollback()

    db.session.expire_all()
    assert jogador.saldo == saldo_antes, "recusada não pode mover dinheiro"
    assert cassino.saldo == caixa_antes
    assert db.session.query(RodadaMines).count() == 0
    conservacao()

    criar_rodada(jogador, "20.00", 3)
    db.session.commit()
    assert db.session.query(RodadaMines).count() == 1
    conservacao()


def test_rodada_ativa_reduz_o_limite_da_proxima(app, bc, cassino, nova_pessoa):
    """O buraco que o original não fecha: duas apostas somadas."""
    ana = nova_pessoa(nome="ana", com_convite=True, saldo="100.00")
    bia = nova_pessoa(nome="bia", com_convite=True, saldo="100.00")
    conservacao()

    criar_rodada(ana, "20.00", 3)
    db.session.commit()

    # A rodada da ana comprometeu 500 (= 20 × 25). O caixa subiu 20, mas o
    # disponível agora é 0,50 × 1020 − 500 = 10.
    assert exposicao_comprometida() == Decimal("500.00")
    assert limite_de_aposta() == Decimal("0.40")

    with pytest.raises(ApostaAlta):
        criar_rodada(bia, "20.00", 3)
    db.session.rollback()
    conservacao()


def test_limite_zero_sem_casa(app, bc, jogador):
    assert casa() is None
    assert limite_de_aposta() == Decimal("0.00")


# --- saque forçado no teto --------------------------------------------------


def test_ao_bater_o_teto_a_rodada_encerra_e_paga_o_maximo(app, bc, cassino, jogador):
    """Levar até 25× encerra sozinho e paga exatamente 25× a aposta.

    Não pode existir estado em que a pessoa segue abrindo casas acima do que
    a casa cobriu na hora da aposta — é o buraco que o teto fecha.
    """
    from vavacoin.auditoria import auditar

    rodada = criar_rodada(jogador, "10.00", 10)
    db.session.commit()
    saldo_antes = jogador.saldo
    passos = tabela_de_multiplicadores(10)

    _abrir_seguras(rodada, jogador, passos[-1][0])

    db.session.refresh(rodada)
    assert rodada.estado == RodadaMines.RETIRADA
    assert rodada.premio == Decimal("250.00")  # 10,00 × 25
    db.session.expire_all()
    assert jogador.saldo == saldo_antes + Decimal("250.00")
    assert auditar()["ok"] is True
    conservacao()


# --- o tabuleiro é segredo --------------------------------------------------


def test_minas_nao_aparecem_enquanto_a_rodada_vive(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()

    visao = visao_da_rodada(rodada)
    assert visao["minas"] is None, "o tabuleiro não pode vazar antes do fim"
    assert visao["reveladas"] == []


def test_registra_qual_casa_estourou(app, bc, cassino, jogador):
    """A tela marca a mina em que a pessoa pisou, diferente das outras.

    Também responde "qual casa eu cliquei?" se alguém contestar a rodada.
    """
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    pisada = rodada.casas_com_mina[1]

    revelar_casa(jogador, pisada)
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.casa_estourada == pisada
    assert visao_da_rodada(rodada)["casa_estourada"] == pisada


def test_rodada_retirada_nao_tem_casa_estourada(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    _abrir_seguras(rodada, jogador, 1)
    retirar(jogador)
    db.session.commit()

    db.session.refresh(rodada)
    assert rodada.casa_estourada is None


def test_minas_aparecem_quando_encerra(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 3)
    db.session.commit()
    revelar_casa(jogador, rodada.casas_com_mina[0])
    db.session.commit()

    visao = visao_da_rodada(rodada)
    assert sorted(visao["minas"]) == sorted(rodada.casas_com_mina)
    assert len(visao["minas"]) == 3


def test_sorteio_gera_tabuleiros_diferentes(app, bc, cassino, nova_pessoa):
    """Minas por `secrets`: duas rodadas não saem iguais."""
    tabuleiros = set()
    for i in range(8):
        pessoa = nova_pessoa(nome=f"jogador{i}", com_convite=True, saldo="50.00")
        rodada = criar_rodada(pessoa, "1.00", 3)
        db.session.commit()
        tabuleiros.add(tuple(rodada.casas_com_mina))
    assert len(tabuleiros) > 1
    conservacao()


def test_quantidade_de_minas_e_a_escolhida(app, bc, cassino, jogador):
    rodada = criar_rodada(jogador, "10.00", 7)
    db.session.commit()
    minas = rodada.casas_com_mina
    assert len(minas) == 7
    assert len(set(minas)) == 7
    assert all(0 <= m < CASAS for m in minas)


# --- validações -------------------------------------------------------------


@pytest.mark.parametrize("escolha", [0, 25, -1, "abc", None])
def test_escolha_de_minas_invalida(app, bc, cassino, jogador, escolha):
    with pytest.raises(ValorInvalido):
        criar_rodada(jogador, "10.00", escolha)
    db.session.rollback()
    conservacao()


@pytest.mark.parametrize("aposta", ["0.00", "-1.00", "0.001", "abc"])
def test_aposta_invalida(app, bc, cassino, jogador, aposta):
    with pytest.raises(ValorInvalido):
        criar_rodada(jogador, aposta, 3)
    db.session.rollback()
    conservacao()


def test_aposta_maior_que_o_saldo(app, bc, cassino, nova_pessoa):
    pobre = nova_pessoa(nome="pobre", com_convite=True, saldo="1.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        criar_rodada(pobre, "5.00", 3)
    db.session.rollback()

    assert pobre.saldo == Decimal("1.00")
    conservacao()


def test_conta_de_sistema_nao_joga(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        criar_rodada(bc, "10.00", 3)
    db.session.rollback()

    with pytest.raises(ValorInvalido):
        criar_rodada(cassino, "10.00", 3)
    db.session.rollback()
    conservacao()


def test_banco_central_poe_dinheiro_no_caixa_pelo_ajuste(app, bc):
    """A casa é uma conta como outra qualquer: o ajuste de saldo funciona nela.

    Não precisou de caminho novo — só o `caladinho` não ser recusado como o
    Banco Central é.
    """
    from vavacoin.operacoes import ajustar_saldo

    conta = criar_casa(autoridade=bc)
    db.session.commit()
    conservacao()

    ajustar_saldo(conta, "2500.00", "caixa do Caladinho", autoridade=bc)
    db.session.commit()

    assert conta.saldo == Decimal("2500.00")
    assert limite_de_aposta() == Decimal("50.00")  # 2500 / 50
    conservacao()

    # E dá para tirar de volta.
    ajustar_saldo(conta, "100.00", "recolhendo o excesso", autoridade=bc)
    db.session.commit()
    assert conta.saldo == Decimal("100.00")
    conservacao()


def test_o_cassino_aparece_na_lista_de_contas_do_painel(app, bc):
    """O atalho do painel precisa que a conta esteja lá."""
    criar_casa(autoridade=bc)
    bc.definir_senha("senha-do-banco-central-123")
    db.session.commit()

    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": "senha-do-banco-central-123"},
        follow_redirects=True,
    )
    corpo = cliente.get("/painel/").get_data(as_text=True)

    assert "caladinho" in corpo
    assert "Pôr dinheiro no caixa" in corpo


def test_casa_do_cassino_e_idempotente(app, bc):
    primeira = criar_casa(autoridade=bc)
    db.session.commit()
    segunda = criar_casa(autoridade=bc)
    db.session.commit()
    assert primeira.id == segunda.id
    assert primeira.eh_cassino is True
    assert primeira.senha_hash is None, "a casa não entra pelo site"
    assert primeira.is_active is False


# --- a rodada abandonada ----------------------------------------------------
#
# Conserto de produção: rodada de mines aberta segura `premio_maximo` na
# exposição comprometida. Com o cassino no ar, um punhado de abas fechadas
# recusa aposta de quem quer jogar — sem ninguém estar jogando.


def _cassino_com_caixa(bc, nova_pessoa):
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta, "2000.00", "caixa do teste", autoridade=bc)
    db.session.commit()
    ana = nova_pessoa(nome="ana", saldo="100.00")
    return conta, ana


def _passou_o_prazo():
    return agora() + VALIDADE_DO_MINES + timedelta(minutes=1)


def test_o_caixa_comprometido_volta_a_zero_depois_do_prazo(app, bc, nova_pessoa):
    """O motivo de a expiração existir, medido no número que importa."""
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")

    criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()
    assert exposicao_comprometida() == Decimal("250.00")  # 10 × 25

    expirar_mines_abandonadas(momento=_passou_o_prazo())
    db.session.commit()

    assert exposicao_comprometida() == Decimal("0.00")


def test_rodada_abandonada_expira_pagando_o_conquistado(app, bc, nova_pessoa):
    """Quem fechou a aba não perde o que já tinha aberto."""
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")
    antes = conservacao()

    rodada = criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(ana, segura)
    db.session.commit()
    saldo = ana.saldo

    expirar_mines_abandonadas(momento=_passou_o_prazo())
    db.session.commit()
    db.session.expire_all()

    rodada = db.session.get(RodadaMines, rodada.id)
    assert rodada.estado == RodadaMines.RETIRADA
    assert rodada.premio > ZERO
    assert buscar_usuario("ana").saldo == saldo + rodada.premio
    assert conservacao() == antes


def test_abandonada_sem_abrir_casa_devolve_a_aposta(app, bc, nova_pessoa):
    """Zero casas é multiplicador 1,00×: a aposta de volta.

    Quem não chegou a arriscar nada não perde nada — e a casa não ganha nada,
    que é o simétrico. A regra de "revele ao menos uma casa antes de retirar"
    continua valendo para quem saca de verdade; aqui não há saque, há
    devolução.
    """
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")
    antes = conservacao()
    saldo = ana.saldo

    criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()

    expirar_mines_abandonadas(momento=_passou_o_prazo())
    db.session.commit()
    db.session.expire_all()

    assert buscar_usuario("ana").saldo == saldo
    assert conservacao() == antes


def test_rodada_expirada_nao_resolve_duas_vezes(app, bc, nova_pessoa):
    """A guarda de status vale para a varredura como vale para o clique.

    Duas varreduras concorrentes — duas abas, dois processos — não podem pagar
    o mesmo prêmio duas vezes.
    """
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")
    antes = conservacao()

    rodada = criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(ana, segura)
    db.session.commit()

    momento = _passou_o_prazo()
    expirar_mines_abandonadas(momento=momento)
    db.session.commit()
    saldo = buscar_usuario("ana").saldo

    # Segunda varredura: a rodada não está mais ativa, então não há o que
    # varrer — e nada é pago de novo.
    assert expirar_mines_abandonadas(momento=momento) == []
    db.session.commit()
    db.session.expire_all()

    assert buscar_usuario("ana").saldo == saldo
    premios = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == "premio_mines")
    ).scalars().all()
    assert len(premios) == 1
    assert conservacao() == antes


def test_rodada_recente_nao_expira(app, bc, nova_pessoa):
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")

    criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()

    expirar_mines_abandonadas()
    db.session.commit()

    assert rodada_ativa(ana) is not None


def test_abrir_casa_adia_a_expiracao(app, bc, nova_pessoa):
    """Quem está jogando devagar não é interrompido no meio."""
    _cassino_com_caixa(bc, nova_pessoa)
    ana = buscar_usuario("ana")

    rodada = criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()
    antes = rodada.mexida_em

    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(ana, segura)
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(RodadaMines, rodada.id).mexida_em >= antes


def test_a_criacao_de_rodada_varre_o_caixa_preso(app, bc, nova_pessoa):
    """A varredura acontece onde ela decide alguma coisa.

    Sem ela, a rodada esquecida de uma pessoa recusa a aposta de outra: o
    caixa parece comprometido e o teto de banca corta.
    """
    conta, _ = _cassino_com_caixa(bc, nova_pessoa)
    esquecida = nova_pessoa(nome="esquecida", saldo="100.00")
    db.session.commit()

    criar_rodada(esquecida, "40.00", minas_escolhidas=3)
    db.session.commit()
    # 40 × 25 = 1.000 preso, de um caixa de 2.010: sobra pouco.
    assert limite_de_aposta() < Decimal("40.00")

    # O tempo passa. A próxima pessoa a apostar varre a esquecida e volta a
    # encontrar o caixa inteiro.
    db.session.execute(
        db.update(RodadaMines)
        .where(RodadaMines.estado == RodadaMines.ATIVA)
        .values(mexida_em=agora() - VALIDADE_DO_MINES - timedelta(minutes=1))
    )
    db.session.commit()

    ana = buscar_usuario("ana")
    rodada = criar_rodada(ana, "40.00", minas_escolhidas=3)
    db.session.commit()

    assert rodada is not None
    assert conservacao() == soma_saldos()
