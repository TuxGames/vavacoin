"""Torre: a escada de multiplicadores, a altura que sai do teto, e a expiração.

Mesma disciplina dos outros dois jogos — sorteio no servidor na aposta,
segredo enquanto a rodada vive, liquidação idempotente com guarda de status,
aposta e prêmio como dois lançamentos, teto de banca conferido na aposta e
antes de pagar. Todo teste que mexe em dinheiro passa pelo ``conservacao()``.

O que é próprio deste jogo: a altura da torre **sai do teto de 25×** em vez de
ser um número escolhido à parte, e a rodada abandonada expira em vez de
prender o caixa da casa para sempre.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    VALIDADE_DA_TORRE,
    abrir_porta,
    criar_casa,
    criar_rodada_torre,
    definir_dono,
    expirar_torres_abandonadas,
    exposicao_comprometida,
    historico_torre,
    rodada_torre_ativa,
    sacar_torre,
    visao_da_rodada_torre,
)
from vavacoin.erros import ApostaAlta, RodadaEmAndamento, SemRodadaAtiva, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import RodadaTorre, Transacao, agora
from vavacoin.operacoes import ajustar_saldo
from vavacoin.torre import (
    MAX_PORTAS,
    MIN_PORTAS,
    TETO_DO_MULTIPLICADOR,
    altura,
    multiplicador_justo,
    multiplicador_pagavel,
    tabela_de_multiplicadores,
    validar_portas,
)
from vavacoin.vantagem import definir_vantagem, fator_de

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cassino(app, bc, nova_pessoa):
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta, "2000.00", "caixa do teste", autoridade=bc)
    db.session.commit()

    gustavo = nova_pessoa(nome="gustavo", saldo="100.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    ana = nova_pessoa(nome="ana", saldo="100.00")
    return {"casa": conta, "dono": gustavo, "ana": ana}


def _porta_segura(rodada, andar=None):
    """Uma porta que não é a armadilha do andar."""
    andar = len(rodada.portas_abertas) if andar is None else andar
    armadilha = rodada.armadilha_por_andar[andar]
    return next(p for p in range(rodada.portas) if p != armadilha)


def _subir(jogador, rodada, andares):
    """Sobe ``andares`` andares sempre acertando a porta."""
    for _ in range(andares):
        db.session.expire_all()
        atual = rodada_torre_ativa(jogador)
        if atual is None or atual.encerrada:
            break
        abrir_porta(jogador, _porta_segura(atual))
        db.session.commit()


# --- a escada ---------------------------------------------------------------


def test_o_multiplicador_e_o_inverso_da_chance(app):
    """``(portas / seguras) ** andares``, exato."""
    assert multiplicador_justo(2, 1) == Decimal(2)
    assert multiplicador_justo(2, 3) == Decimal(8)
    assert multiplicador_justo(4, 2) == Decimal(16) / Decimal(9)


def test_a_altura_sai_do_teto_e_nao_de_um_numero_escolhido(app):
    """Cada dificuldade tem exatamente os andares que cabem até 25×."""
    assert altura(2) == 5
    assert altura(3) == 8
    assert altura(4) == 12
    for portas in (2, 3, 4):
        tabela = tabela_de_multiplicadores(portas)
        assert len(tabela) == altura(portas)
        assert tabela[-1][1] == TETO_DO_MULTIPLICADOR
        # Nenhum andar antes do topo chega ao teto.
        assert all(v < TETO_DO_MULTIPLICADOR for _, v in tabela[:-1])


def test_a_altura_encolhe_em_evento_generoso(app):
    """A vantagem entra antes do teto, então ela mexe na altura.

    Por isso a altura é congelada com o fator, na aposta: a torre não pode
    mudar de tamanho no meio da rodada.
    """
    assert altura(3, fator_de(Decimal("-10.00"))) < altura(3, fator_de(Decimal("10.00")))


def test_nenhum_andar_paga_acima_do_teto_em_qualquer_vantagem(app):
    """A propriedade de que a guarda de exposição depende.

    O ``min`` do teto é aplicado DEPOIS do fator; se algum dia alguém inverter
    isso, ``premio_maximo = aposta × 25`` vira mentira e a casa aceita aposta
    que não cobre.
    """
    for pct in ["-10.00", "0.00", "2.00", "10.00"]:
        fator = fator_de(Decimal(pct))
        for portas in (2, 3, 4):
            for andar in range(1, altura(portas, fator) + 1):
                assert multiplicador_pagavel(portas, andar, fator) <= TETO_DO_MULTIPLICADOR


@pytest.mark.parametrize("ruim", [1, 5, 0, -1, "abc", None])
def test_dificuldade_invalida_e_recusada(app, ruim):
    with pytest.raises(ValueError):
        validar_portas(ruim)


def test_dificuldade_nos_limites_e_aceita(app):
    assert validar_portas(MIN_PORTAS) == MIN_PORTAS
    assert validar_portas(MAX_PORTAS) == MAX_PORTAS


# --- a rodada ---------------------------------------------------------------


def test_a_aposta_sai_na_hora_e_e_um_lancamento(app, bc, cassino):
    antes = conservacao()
    saldo = cassino["ana"].saldo

    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    assert cassino["ana"].saldo == saldo - Decimal("10.00")
    lancamento = db.session.get(Transacao, rodada.transacao_aposta_id)
    assert lancamento.tipo == "aposta_torre"
    assert conservacao() == antes


def test_a_torre_inteira_e_sorteada_na_aposta(app, bc, cassino):
    """Uma armadilha por andar, até o topo, decididas antes do primeiro clique."""
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    armadilhas = rodada.armadilha_por_andar
    assert len(armadilhas) == altura(3, fator_de(rodada.vantagem))
    assert all(0 <= a < 3 for a in armadilhas)


def test_as_armadilhas_nao_vazam_enquanto_a_rodada_vive(app, bc, cassino):
    """Se este teste falhar, o jogo acabou: dá para ler a torre na fonte."""
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    assert visao_da_rodada_torre(rodada)["armadilhas"] is None


def test_as_armadilhas_aparecem_quando_encerra(app, bc, cassino):
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    armadilha = rodada.armadilha_por_andar[0]
    abrir_porta(cassino["ana"], armadilha)
    db.session.commit()

    visao = visao_da_rodada_torre(rodada)
    assert visao["armadilhas"] is not None
    assert visao["andar_estourado"] == 0


def test_uma_rodada_ativa_por_vez(app, bc, cassino):
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    with pytest.raises(RodadaEmAndamento):
        criar_rodada_torre(cassino["ana"], "10.00", portas=3)


def test_conta_de_sistema_nao_joga(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        criar_rodada_torre(cassino["casa"], "10.00", portas=3)


def test_aposta_acima_do_teto_de_banca_e_recusada(app, bc, cassino, nova_pessoa):
    """Caixa de 2.000 → aposta máxima de 40. A mesma regra dos três jogos."""
    rico = nova_pessoa(nome="rico", saldo="300.00")
    db.session.commit()

    with pytest.raises(ApostaAlta):
        criar_rodada_torre(rico, "100.00", portas=3)


def test_a_exposicao_da_torre_entra_no_teto_de_banca(app, bc, cassino):
    antes = exposicao_comprometida()
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    assert exposicao_comprometida() == antes + Decimal("250.00")  # 10 × 25


# --- subir, cair e sacar ----------------------------------------------------


def test_acertar_sobe_e_acumula(app, bc, cassino):
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    abrir_porta(cassino["ana"], _porta_segura(rodada))
    db.session.commit()
    db.session.expire_all()

    rodada = db.session.get(RodadaTorre, rodada.id)
    assert rodada.estado == RodadaTorre.ATIVA
    assert rodada.andares_subidos == 1
    assert rodada.multiplicador == multiplicador_pagavel(3, 1, fator_de(rodada.vantagem))


def test_pisar_na_armadilha_perde_tudo(app, bc, cassino):
    antes = conservacao()
    saldo = cassino["ana"].saldo
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    abrir_porta(cassino["ana"], rodada.armadilha_por_andar[0])
    db.session.commit()
    db.session.expire_all()

    rodada = db.session.get(RodadaTorre, rodada.id)
    assert rodada.estado == RodadaTorre.ESTOURADA
    assert rodada.premio == Decimal("0.00")
    assert rodada.andares_subidos == 0
    assert cassino["ana"].saldo == saldo - Decimal("10.00")
    assert conservacao() == antes


def test_sacar_paga_o_acumulado(app, bc, cassino):
    antes = conservacao()
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    _subir(cassino["ana"], rodada, 2)
    saldo = cassino["ana"].saldo

    rodada = sacar_torre(cassino["ana"])
    db.session.commit()

    esperado = multiplicador_pagavel(3, 2, fator_de(rodada.vantagem))
    assert rodada.estado == RodadaTorre.RETIRADA
    assert rodada.premio == Decimal("10.00") * esperado
    assert cassino["ana"].saldo == saldo + rodada.premio
    assert conservacao() == antes


def test_chegar_ao_topo_saca_sozinho(app, bc, cassino):
    """No topo não há mais o que ganhar subindo: encerra e paga, como o mines."""
    antes = conservacao()
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=2)
    db.session.commit()
    andares = len(rodada.armadilha_por_andar)

    _subir(cassino["ana"], rodada, andares)
    db.session.expire_all()

    rodada = db.session.get(RodadaTorre, rodada.id)
    assert rodada.estado == RodadaTorre.RETIRADA
    assert rodada.multiplicador == TETO_DO_MULTIPLICADOR
    assert rodada.premio == Decimal("250.00")  # 10 × 25
    assert conservacao() == antes


def test_sacar_duas_vezes_paga_uma_vez_so(app, bc, cassino):
    """A guarda de status: clique duplo e reenvio chegam ao mesmo lugar."""
    antes = conservacao()
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    _subir(cassino["ana"], rodada, 1)

    sacar_torre(cassino["ana"])
    db.session.commit()
    saldo = cassino["ana"].saldo

    with pytest.raises(SemRodadaAtiva):
        sacar_torre(cassino["ana"])
    db.session.rollback()

    assert cassino["ana"].saldo == saldo
    premios = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == "premio_torre")
    ).scalars().all()
    assert len(premios) == 1
    assert conservacao() == antes


def test_porta_fora_do_andar_e_recusada(app, bc, cassino):
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    for ruim in (-1, 3, 99, "abc"):
        with pytest.raises(ValorInvalido):
            abrir_porta(cassino["ana"], ruim)


# --- a vantagem congelada ---------------------------------------------------


def test_a_torre_congela_a_vantagem_da_aposta(app, bc, cassino):
    """Mudar a vantagem no meio não muda a escada de quem já está subindo."""
    antes = conservacao()
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    assert rodada.vantagem == Decimal("2.00")

    definir_vantagem("torre", "10.00", cassino["dono"])
    db.session.commit()

    _subir(cassino["ana"], rodada, 1)
    rodada = sacar_torre(cassino["ana"])
    db.session.commit()

    esperado = multiplicador_pagavel(3, 1, fator_de(Decimal("2.00")))
    assert rodada.premio == Decimal("10.00") * esperado
    assert conservacao() == antes


# --- a rodada abandonada ----------------------------------------------------


def test_rodada_abandonada_expira_pagando_o_conquistado(app, bc, cassino):
    """Quem fechou a aba não perde o que já tinha subido."""
    antes = conservacao()
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    _subir(cassino["ana"], rodada, 2)
    saldo = cassino["ana"].saldo

    expirar_torres_abandonadas(momento=agora() + VALIDADE_DA_TORRE + timedelta(minutes=1))
    db.session.commit()
    db.session.expire_all()

    rodada = db.session.get(RodadaTorre, rodada.id)
    assert rodada.estado == RodadaTorre.RETIRADA
    assert cassino["ana"].saldo == saldo + rodada.premio
    assert conservacao() == antes


def test_rodada_abandonada_sem_subir_devolve_a_aposta(app, bc, cassino):
    """Zero andares é multiplicador 1,00×: a aposta de volta, nem mais nem menos."""
    antes = conservacao()
    saldo = cassino["ana"].saldo
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    expirar_torres_abandonadas(momento=agora() + VALIDADE_DA_TORRE + timedelta(minutes=1))
    db.session.commit()
    db.session.expire_all()

    assert cassino["ana"].saldo == saldo
    assert conservacao() == antes


def test_a_expiracao_devolve_o_caixa_a_casa(app, bc, cassino):
    """O motivo de a expiração existir: rodada esquecida prendia o caixa."""
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    assert exposicao_comprometida() == Decimal("250.00")

    expirar_torres_abandonadas(momento=agora() + VALIDADE_DA_TORRE + timedelta(minutes=1))
    db.session.commit()

    assert exposicao_comprometida() == Decimal("0.00")


def test_rodada_recente_nao_expira(app, bc, cassino):
    criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    expirar_torres_abandonadas()
    db.session.commit()

    assert rodada_torre_ativa(cassino["ana"]) is not None


def test_mexer_na_rodada_adia_a_expiracao(app, bc, cassino):
    """Quem está jogando devagar não é interrompido."""
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    antes = rodada.mexida_em

    _subir(cassino["ana"], rodada, 1)
    db.session.expire_all()

    assert db.session.get(RodadaTorre, rodada.id).mexida_em >= antes


# --- a web ------------------------------------------------------------------


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def test_a_tela_da_torre_abre(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/torre").get_data(as_text=True)
    assert "Torre" in corpo
    assert "torre/comecar" in corpo


def test_o_lobby_leva_a_torre(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/").get_data(as_text=True)
    assert "/caladinho/torre" in corpo


def test_a_tela_nao_entrega_as_armadilhas(app, bc, cassino):
    cliente = _entrar(app, "ana")
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()

    corpo = cliente.get("/caladinho/torre").get_data(as_text=True)

    # A porta armadilhada do primeiro andar não pode estar marcada em lugar
    # nenhum do HTML enquanto a rodada vive.
    assert "💥" not in corpo


def test_jogar_pela_web_do_comeco_ao_fim(app, bc, cassino):
    antes = conservacao()
    cliente = _entrar(app, "ana")

    cliente.post(
        "/caladinho/torre/comecar",
        data={"aposta": "10.00", "portas": "3"},
        follow_redirects=True,
    )
    rodada = db.session.execute(db.select(RodadaTorre)).scalar_one()
    cliente.post(
        "/caladinho/torre/abrir",
        data={"porta": str(_porta_segura(rodada))},
        follow_redirects=True,
    )
    resposta = cliente.post("/caladinho/torre/sacar", follow_redirects=True)

    assert resposta.status_code == 200
    db.session.expire_all()
    assert db.session.get(RodadaTorre, rodada.id).encerrada
    assert conservacao() == antes


def test_o_historico_da_torre_aparece(app, bc, cassino):
    rodada = criar_rodada_torre(cassino["ana"], "10.00", portas=3)
    db.session.commit()
    abrir_porta(cassino["ana"], rodada.armadilha_por_andar[0])
    db.session.commit()

    assert len(historico_torre(cassino["ana"])) == 1
