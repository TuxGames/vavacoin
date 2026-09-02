"""Crash: a curva, o ponto de estouro e o saque que não depende da rede.

O crash nasce com a mesma disciplina do mines — resultado no servidor,
liquidação idempotente com guarda de status, aposta e prêmio como dois
lançamentos, teto de banca conferido na aposta **e** antes de pagar. Os testes
que provam isso estão aqui, e todo teste que mexe em dinheiro passa pelo
``conservacao()``.

O que é próprio deste jogo é o tempo, e o desenho que tira a rede do caminho:
o resultado está decidido no instante da aposta (``alvo <= estouro``), e o
clique só serve para sair **antes** do alvo. Por isso os testes controlam o
relógio via ``momento=`` em vez de dormir.
"""

import random
from datetime import timedelta, timezone
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    criar_casa,
    criar_rodada_crash,
    definir_dono,
    exposicao_comprometida,
    historico_crash,
    resolver_crash,
    sacar_crash,
    visao_da_rodada_crash,
)
from vavacoin.crash import (
    ALVO_MINIMO,
    SEGUNDOS_PARA_DOBRAR,
    TETO_DO_MULTIPLICADOR,
    multiplicador_no_tempo,
    segundos_para_multiplicador,
    sortear_ponto_de_estouro,
    validar_alvo,
)
from vavacoin.erros import ApostaAlta, RodadaEmAndamento, SemRodadaAtiva, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import RodadaCrash, Transacao, agora
from vavacoin.operacoes import ajustar_saldo
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


def _depois_de(rodada, segundos):
    """Um instante exatamente ``segundos`` após o INÍCIO da rodada.

    Ancorado em ``iniciada_em``, e não em ``agora()``: entre criar a rodada e
    montar o instante passa tempo de verdade (banco, bcrypt), e somar a partir
    de agora faria o multiplicador esperado depender da velocidade da máquina.
    Foi assim que este teste passou sozinho e falhou na suíte inteira.
    """
    inicio = rodada.iniciada_em
    if inicio.tzinfo is None:  # o SQLite devolve ingênuo; o relógio é UTC
        inicio = inicio.replace(tzinfo=timezone.utc)
    return inicio + timedelta(seconds=float(segundos))


def _forcar_estouro(rodada, valor):
    """Fixa o ponto de estouro. O sorteio é testado à parte."""
    db.session.execute(
        db.update(RodadaCrash)
        .where(RodadaCrash.id == rodada.id)
        .values(ponto_de_estouro=Decimal(valor))
    )
    db.session.commit()
    db.session.expire_all()
    return db.session.get(RodadaCrash, rodada.id)


# --- a curva ----------------------------------------------------------------


def test_a_curva_dobra_no_tempo_combinado(app):
    assert multiplicador_no_tempo(0) == Decimal("1.00")
    assert multiplicador_no_tempo(SEGUNDOS_PARA_DOBRAR) == Decimal("2.00")
    assert multiplicador_no_tempo(SEGUNDOS_PARA_DOBRAR * 2) == Decimal("4.00")


def test_a_curva_nunca_comeca_abaixo_de_um(app):
    assert multiplicador_no_tempo(-5) == Decimal("1.00")


def test_o_inverso_da_curva_bate_com_a_curva(app):
    """``segundos_para_multiplicador`` é o que decide se o alvo já passou."""
    for alvo in ["1.50", "2.00", "5.00", "25.00"]:
        t = segundos_para_multiplicador(Decimal(alvo))
        assert multiplicador_no_tempo(t) == Decimal(alvo)


# --- o sorteio --------------------------------------------------------------


def test_o_sorteio_respeita_a_vantagem_no_valor_esperado(app):
    """A propriedade que faz o jogo ser honesto: ``E[retorno] = 1 - vantagem``.

    Vale para QUALQUER alvo, que é o que impede existir alvo "esperto". Aqui
    isso é conferido sobre a distribuição, com gerador determinístico.
    """
    fator = fator_de(Decimal("2.00"))
    gerador = random.Random(20260902)
    pontos = [sortear_ponto_de_estouro(fator, gerador) for _ in range(20000)]

    for alvo in [Decimal("1.50"), Decimal("2.00"), Decimal("5.00")]:
        ganhos = sum(1 for p in pontos if p >= alvo)
        retorno = Decimal(ganhos) / Decimal(len(pontos)) * alvo
        # O teto de 25x corta a cauda, então o retorno fica um pouco abaixo
        # do teórico; a folga cobre isso e o ruído da amostra.
        assert Decimal("0.90") < retorno < Decimal("1.00")


def test_o_sorteio_nunca_passa_do_teto(app):
    fator = fator_de(Decimal("2.00"))
    gerador = random.Random(7)
    for _ in range(2000):
        ponto = sortear_ponto_de_estouro(fator, gerador)
        assert Decimal("1.00") <= ponto <= TETO_DO_MULTIPLICADOR


def test_vantagem_maior_estoura_mais_cedo(app):
    """A vantagem editável tem que mover a distribuição, não só a tela."""
    gerador = random.Random(11)
    baixa = [sortear_ponto_de_estouro(fator_de(Decimal("0.00")), gerador) for _ in range(5000)]
    gerador = random.Random(11)
    alta = [sortear_ponto_de_estouro(fator_de(Decimal("10.00")), gerador) for _ in range(5000)]

    assert sum(alta) < sum(baixa)


# --- o alvo -----------------------------------------------------------------


@pytest.mark.parametrize("ruim", ["1.00", "0.50", "0", "-1", "25.01", "100", "abc"])
def test_alvo_invalido_e_recusado(app, ruim):
    with pytest.raises(ValueError):
        validar_alvo(ruim)


def test_alvo_no_limite_e_aceito(app):
    assert validar_alvo(ALVO_MINIMO) == ALVO_MINIMO
    assert validar_alvo(TETO_DO_MULTIPLICADOR) == TETO_DO_MULTIPLICADOR


# --- a rodada ---------------------------------------------------------------


def test_a_aposta_sai_na_hora_e_e_um_lancamento(app, bc, cassino):
    antes = conservacao()
    saldo = cassino["ana"].saldo

    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    assert cassino["ana"].saldo == saldo - Decimal("10.00")
    assert rodada.transacao_aposta_id is not None
    lancamento = db.session.get(Transacao, rodada.transacao_aposta_id)
    assert lancamento.tipo == "aposta_crash"
    assert conservacao() == antes


def test_o_ponto_de_estouro_nao_vaza_enquanto_a_rodada_vive(app, bc, cassino):
    """O equivalente ao tabuleiro do mines: é o segredo que sustenta o jogo."""
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    visao = visao_da_rodada_crash(rodada)
    assert visao["ponto_de_estouro"] is None
    assert "alvo" in visao  # o alvo é do jogador, pode aparecer


def test_uma_rodada_ativa_por_vez(app, bc, cassino):
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    with pytest.raises(RodadaEmAndamento):
        criar_rodada_crash(cassino["ana"], "10.00", "2.00")


def test_conta_de_sistema_nao_joga(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        criar_rodada_crash(cassino["casa"], "10.00", "2.00")


def test_aposta_acima_do_teto_de_banca_e_recusada(app, bc, cassino, nova_pessoa):
    """Mesma regra do mines: 25× a aposta cabe em metade do caixa.

    Caixa de 2.000 → aposta máxima de 40 (metade do caixa, dividido pelo teto
    de 25×). Cem é recusado mesmo com saldo de sobra.
    """
    rico = nova_pessoa(nome="rico", saldo="300.00")
    db.session.commit()

    with pytest.raises(ApostaAlta):
        criar_rodada_crash(rico, "100.00", "2.00")


def test_a_exposicao_do_crash_entra_no_teto_de_banca(app, bc, cassino):
    """Somar só o mines seria o mesmo que não somar nada.

    Bastaria abrir a rodada cara no jogo que ficou de fora da conta.
    """
    antes = exposicao_comprometida()
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    assert exposicao_comprometida() == antes + Decimal("250.00")  # 10 × 25


# --- o desfecho -------------------------------------------------------------


def test_alvo_abaixo_do_estouro_ganha_no_alvo(app, bc, cassino):
    """Quem põe alvo e não clica não depende da rede para nada."""
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "5.00")
    saldo = cassino["ana"].saldo

    rodada = resolver_crash(cassino["ana"], momento=_depois_de(rodada, 30))
    db.session.commit()

    assert rodada.estado == RodadaCrash.RETIRADA
    assert rodada.multiplicador == Decimal("2.00")
    assert rodada.premio == Decimal("20.00")
    assert cassino["ana"].saldo == saldo + Decimal("20.00")
    assert conservacao() == antes


def test_alvo_acima_do_estouro_perde(app, bc, cassino):
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "5.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "2.00")
    saldo = cassino["ana"].saldo

    rodada = resolver_crash(cassino["ana"], momento=_depois_de(rodada, 30))
    db.session.commit()

    assert rodada.estado == RodadaCrash.ESTOURADA
    assert rodada.premio == Decimal("0.00")
    assert cassino["ana"].saldo == saldo
    assert conservacao() == antes


def test_a_rodada_nao_resolve_antes_da_hora(app, bc, cassino):
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(rodada, "5.00")

    # 2× só chega aos 8 segundos.
    rodada = resolver_crash(cassino["ana"], momento=_depois_de(rodada, 3))
    db.session.commit()

    assert rodada.estado == RodadaCrash.ATIVA


def test_resolver_duas_vezes_paga_uma_vez_so(app, bc, cassino):
    """A guarda de status: clique duplo e reenvio chegam ao mesmo lugar."""
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "5.00")

    momento = _depois_de(rodada, 30)
    resolver_crash(cassino["ana"], momento=momento)
    db.session.commit()
    saldo = cassino["ana"].saldo

    resolver_crash(cassino["ana"], momento=momento)
    db.session.commit()

    assert cassino["ana"].saldo == saldo
    premios = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == "premio_crash")
    ).scalars().all()
    assert len(premios) == 1
    assert conservacao() == antes


# --- o saque manual ---------------------------------------------------------


def test_saque_manual_paga_pelo_numero_de_agora(app, bc, cassino):
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "8.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "20.00")
    saldo = cassino["ana"].saldo

    # 8 segundos = 2.00×, bem antes do alvo de 8×.
    rodada = sacar_crash(cassino["ana"], momento=_depois_de(rodada, SEGUNDOS_PARA_DOBRAR))
    db.session.commit()

    assert rodada.estado == RodadaCrash.RETIRADA
    assert rodada.multiplicador == Decimal("2.00")
    assert cassino["ana"].saldo == saldo + Decimal("20.00")
    assert conservacao() == antes


def test_clicar_depois_do_alvo_entrega_o_alvo_e_nao_a_derrota(app, bc, cassino):
    """O ponto do desenho inteiro: a rede não transforma vitória em derrota.

    A pessoa clicou tarde — de propósito, ou porque o POST demorou. O alvo já
    tinha sido atingido, e o alvo é resolvido pelo servidor sem depender de
    clique nenhum. O clique atrasado não tira o que já estava ganho.
    """
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "3.00")
    saldo = cassino["ana"].saldo

    # Clica MUITO depois: a curva já passou do alvo e até do estouro.
    rodada = sacar_crash(cassino["ana"], momento=_depois_de(rodada, 60))
    db.session.commit()

    assert rodada.estado == RodadaCrash.RETIRADA
    assert rodada.multiplicador == Decimal("2.00")  # o alvo, não o estouro
    assert cassino["ana"].saldo == saldo + Decimal("20.00")
    assert conservacao() == antes


def test_atraso_de_rede_nao_custa_a_rodada(app, bc, cassino):
    """O caso concreto que motivou o alvo.

    A pessoa clica em 1.90× e o POST chega 250 ms depois, quando a curva já
    passou de 2.00× — que é onde estava o estouro. Com saque manual puro ela
    perderia a rodada. Com alvo em 2.00×, ela leva o alvo.
    """
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "2.00")
    saldo = cassino["ana"].saldo

    momento_do_clique = segundos_para_multiplicador(Decimal("2.00")) + Decimal("0.25")
    rodada = sacar_crash(cassino["ana"], momento=_depois_de(rodada, momento_do_clique))
    db.session.commit()

    assert rodada.premio == Decimal("20.00")
    assert conservacao() == antes


def test_sacar_sem_rodada_reclama(app, bc, cassino):
    with pytest.raises(SemRodadaAtiva):
        sacar_crash(cassino["ana"])


# --- a vantagem congelada ---------------------------------------------------


def test_a_rodada_de_crash_congela_a_vantagem(app, bc, cassino):
    """Mesma disciplina do mines: mudar a vantagem não afeta quem já jogou."""
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    assert rodada.vantagem == Decimal("2.00")

    definir_vantagem("crash", "9.00", cassino["dono"])
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(RodadaCrash, rodada.id).vantagem == Decimal("2.00")


# --- a web ------------------------------------------------------------------


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def test_a_tela_do_crash_abre(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/crash").get_data(as_text=True)
    assert "Crash" in corpo
    assert "crash/comecar" in corpo


def test_a_tela_nao_entrega_o_ponto_de_estouro(app, bc, cassino):
    """Se este teste falhar, o jogo acabou: dá para ler o resultado na fonte."""
    cliente = _entrar(app, "ana")
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    rodada = _forcar_estouro(rodada, "17.42")

    corpo = cliente.get("/caladinho/crash").get_data(as_text=True)

    assert "17.42" not in corpo


def test_o_lobby_leva_ao_crash(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/").get_data(as_text=True)
    assert "/caladinho/crash" in corpo


def test_jogar_pela_web_do_comeco_ao_fim(app, bc, cassino):
    antes = conservacao()
    cliente = _entrar(app, "ana")

    cliente.post(
        "/caladinho/crash/comecar",
        data={"aposta": "10.00", "alvo": "2.00"},
        follow_redirects=True,
    )
    rodada = db.session.execute(db.select(RodadaCrash)).scalar_one()
    assert rodada.estado == RodadaCrash.ATIVA

    resposta = cliente.post("/caladinho/crash/sacar", follow_redirects=True)
    assert resposta.status_code == 200

    db.session.expire_all()
    assert db.session.get(RodadaCrash, rodada.id).encerrada
    assert conservacao() == antes


def test_o_historico_do_crash_aparece(app, bc, cassino):
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(rodada, "5.00")
    resolver_crash(cassino["ana"], momento=_depois_de(rodada, 30))
    db.session.commit()

    assert len(historico_crash(cassino["ana"])) == 1
    corpo = _entrar(app, "ana").get("/caladinho/crash").get_data(as_text=True)
    assert "2.00" in corpo
