"""Dados: o multiplicador saindo da probabilidade, e a rodada que nasce pronta.

O jogo mais simples dos quatro e o único sem estado intermediário — cobra,
rola e paga na mesma transação. Por isso os testes aqui olham menos para
concorrência e mais para a **conta**: o multiplicador é o inverso da chance, a
faixa de alvos recusa o que o teto cortaria e o que pagaria menos que a
aposta, e o dado é do servidor.

Como sempre: todo teste que mexe em dinheiro passa pelo ``conservacao()``.
"""

import random
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    criar_casa,
    definir_dono,
    historico_dados,
    jogar_dados,
    ultima_rodada_dados,
)
from vavacoin.dados import (
    FACES,
    MAIOR,
    MENOR,
    MULTIPLICADOR_MINIMO,
    TETO_DO_MULTIPLICADOR,
    chance,
    ganhou,
    limites_do_alvo,
    multiplicador,
    multiplicador_justo,
    multiplicador_pagavel,
    rolar,
    tabela_de_multiplicadores,
    validar_alvo,
    validar_sentido,
)
from vavacoin.erros import ApostaAlta, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import RodadaDados, Transacao
from vavacoin.operacoes import ajustar_saldo
from vavacoin.vantagem import definir_vantagem, fator_de

SENHA = "senha-boa-123"


class DadoViciado:
    """Um "gerador" que devolve sempre o mesmo número. Só para teste."""

    def __init__(self, valor):
        self.valor = valor

    def randrange(self, inicio, fim):
        return self.valor


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


# --- a conta ----------------------------------------------------------------


def test_o_multiplicador_e_o_inverso_da_chance(app):
    """A definição de aposta justa: quem acerta uma em vinte recebe vinte."""
    assert multiplicador_justo(MENOR, 50) == Decimal(2)
    assert multiplicador_justo(MENOR, 25) == Decimal(4)
    assert multiplicador_justo(MENOR, 5) == Decimal(20)
    assert multiplicador_justo(MAIOR, 50) == Decimal(2)
    assert multiplicador_justo(MAIOR, 75) == Decimal(4)


def test_alvo_cinquenta_e_simetrico(app):
    """A simetria que faz o jogo ser lido sem explicação."""
    assert chance(MENOR, 50) == chance(MAIOR, 50) == Decimal("0.5")


def test_a_chance_bate_com_a_contagem_de_faces(app):
    """Conferido contra a definição, face a face, sem confiar na fórmula."""
    for alvo in (1, 7, 33, 50, 91, 99):
        for sentido in (MENOR, MAIOR):
            favoraveis = sum(
                1 for face in range(1, FACES + 1) if ganhou(sentido, alvo, face)
            )
            assert chance(sentido, alvo) == Decimal(favoraveis) / Decimal(FACES)


def test_a_vantagem_entra_no_multiplicador(app):
    assert multiplicador(MENOR, 50, fator_de(Decimal("0.00"))) == Decimal("2.00")
    assert multiplicador(MENOR, 50, fator_de(Decimal("2.00"))) == Decimal("1.96")
    assert multiplicador(MENOR, 50, fator_de(Decimal("-10.00"))) == Decimal("2.20")


# --- a faixa do alvo --------------------------------------------------------


def test_a_faixa_recusa_o_que_o_teto_cortaria(app):
    """Alvo raro demais seria truncado pelo teto — e número cortado na tela é
    o cassino parecendo que rouba. Melhor recusar."""
    for pct in ["-10.00", "0.00", "2.00", "10.00"]:
        fator = fator_de(Decimal(pct))
        minimo, maximo = limites_do_alvo(MENOR, fator)
        assert multiplicador(MENOR, minimo, fator) <= TETO_DO_MULTIPLICADOR
        # Um a menos já passaria do teto (ou não existe).
        if minimo > 1:
            assert multiplicador(MENOR, minimo - 1, fator) > TETO_DO_MULTIPLICADOR


def test_a_faixa_recusa_o_que_pagaria_menos_que_a_aposta(app):
    """Ganhar e receber menos do que apostou é indefensível."""
    for pct in ["0.00", "2.00", "10.00"]:
        fator = fator_de(Decimal(pct))
        minimo, maximo = limites_do_alvo(MENOR, fator)
        assert multiplicador(MENOR, maximo, fator) >= MULTIPLICADOR_MINIMO
        if maximo < FACES - 1:
            assert multiplicador(MENOR, maximo + 1, fator) < MULTIPLICADOR_MINIMO


def test_todo_alvo_da_faixa_paga_entre_um_e_o_teto(app):
    """A propriedade que a faixa existe para garantir, varrida inteira."""
    for pct in ["-10.00", "0.00", "2.00", "10.00"]:
        fator = fator_de(Decimal(pct))
        for sentido in (MENOR, MAIOR):
            minimo, maximo = limites_do_alvo(sentido, fator)
            for alvo in range(minimo, maximo + 1):
                pago = multiplicador_pagavel(sentido, alvo, fator)
                assert MULTIPLICADOR_MINIMO <= pago <= TETO_DO_MULTIPLICADOR


def test_a_faixa_espelha_entre_os_sentidos(app):
    """Em "maior" os favoráveis são ``100 - alvo``: a faixa vira do avesso."""
    fator = fator_de(Decimal("2.00"))
    menor_min, menor_max = limites_do_alvo(MENOR, fator)
    maior_min, maior_max = limites_do_alvo(MAIOR, fator)
    assert maior_min == FACES - menor_max
    assert maior_max == FACES - menor_min


def test_a_faixa_encolhe_em_evento_generoso(app):
    """Com a casa pagando mais, o alvo raro bate no teto mais cedo."""
    generoso = limites_do_alvo(MENOR, fator_de(Decimal("-10.00")))
    normal = limites_do_alvo(MENOR, fator_de(Decimal("2.00")))
    assert generoso[0] > normal[0]


@pytest.mark.parametrize("ruim", [0, -1, 100, 101, "abc", None])
def test_alvo_invalido_e_recusado(app, ruim):
    with pytest.raises(ValueError):
        validar_alvo(MENOR, ruim)


@pytest.mark.parametrize("ruim", ["igual", "", None, "MENOR"])
def test_sentido_invalido_e_recusado(app, ruim):
    with pytest.raises(ValueError):
        validar_sentido(ruim)


def test_a_tabela_mostra_alvos_de_referencia(app):
    fator = fator_de(Decimal("2.00"))
    for sentido in (MENOR, MAIOR):
        linhas = tabela_de_multiplicadores(sentido, fator)
        assert linhas
        minimo, maximo = limites_do_alvo(sentido, fator)
        assert all(minimo <= alvo <= maximo for alvo, _, _ in linhas)


# --- a rolagem --------------------------------------------------------------


def test_a_rolagem_cobre_as_cem_faces(app):
    vistos = {rolar(random.Random(s)) for s in range(4000)}
    assert min(vistos) == 1
    assert max(vistos) == FACES


def test_ganhar_e_perder_seguem_o_sentido(app):
    assert ganhou(MENOR, 50, 50) is True
    assert ganhou(MENOR, 50, 51) is False
    assert ganhou(MAIOR, 50, 51) is True
    assert ganhou(MAIOR, 50, 50) is False


# --- o dinheiro -------------------------------------------------------------


def test_ganhar_paga_o_multiplicador(app, bc, cassino):
    antes = conservacao()
    saldo = cassino["ana"].saldo

    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(1)
    )
    db.session.commit()

    assert rodada.estado == RodadaDados.GANHA
    assert rodada.resultado == 1
    assert rodada.multiplicador == Decimal("1.96")
    assert rodada.premio == Decimal("19.60")
    assert cassino["ana"].saldo == saldo - Decimal("10.00") + Decimal("19.60")
    assert conservacao() == antes


def test_perder_deixa_a_aposta_com_a_casa(app, bc, cassino):
    antes = conservacao()
    saldo = cassino["ana"].saldo
    caixa = cassino["casa"].saldo

    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(100)
    )
    db.session.commit()

    assert rodada.estado == RodadaDados.PERDIDA
    assert rodada.premio == Decimal("0.00")
    assert cassino["ana"].saldo == saldo - Decimal("10.00")
    assert cassino["casa"].saldo == caixa + Decimal("10.00")
    assert conservacao() == antes


def test_aposta_e_premio_sao_dois_lancamentos(app, bc, cassino):
    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(1)
    )
    db.session.commit()

    aposta = db.session.get(Transacao, rodada.transacao_aposta_id)
    premio = db.session.get(Transacao, rodada.transacao_premio_id)
    assert aposta.tipo == "aposta_dados"
    assert premio.tipo == "premio_dados"
    assert aposta.valor == Decimal("10.00")
    assert premio.valor == Decimal("19.60")


def test_rodada_perdida_nao_tem_lancamento_de_premio(app, bc, cassino):
    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(100)
    )
    db.session.commit()

    assert rodada.transacao_premio_id is None


def test_conta_de_sistema_nao_joga(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        jogar_dados(cassino["casa"], "10.00", MENOR, 50)


def test_aposta_acima_do_teto_de_banca_e_recusada(app, bc, cassino, nova_pessoa):
    """Caixa de 2.000 → aposta máxima de 40. A mesma regra dos quatro jogos."""
    rico = nova_pessoa(nome="rico", saldo="300.00")
    db.session.commit()

    with pytest.raises(ApostaAlta):
        jogar_dados(rico, "100.00", MENOR, 50)


def test_aposta_maior_que_o_saldo_e_recusada(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        jogar_dados(cassino["ana"], "500.00", MENOR, 50)


@pytest.mark.parametrize("ruim", ["0.00", "-1.00", "abc"])
def test_aposta_invalida_e_recusada(app, bc, cassino, ruim):
    with pytest.raises(ValorInvalido):
        jogar_dados(cassino["ana"], ruim, MENOR, 50)


def test_alvo_fora_da_faixa_e_recusado_pelo_servidor(app, bc, cassino):
    """O ``min``/``max`` do campo é conforto; quem decide é o servidor."""
    with pytest.raises(ValorInvalido):
        jogar_dados(cassino["ana"], "10.00", MENOR, 99)


def test_a_vantagem_da_rodada_fica_gravada(app, bc, cassino):
    """Não protege rodada aberta — não existe —, mas explica a linha depois."""
    definir_vantagem("dados", "5.00", cassino["dono"])
    db.session.commit()

    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(1)
    )
    db.session.commit()

    assert rodada.vantagem == Decimal("5.00")
    assert rodada.multiplicador == Decimal("1.90")  # 2 × 0.95


def test_evento_generoso_paga_acima_do_justo(app, bc, cassino):
    definir_vantagem("dados", "-10.00", cassino["dono"])
    db.session.commit()
    antes = conservacao()

    rodada = jogar_dados(
        cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(1)
    )
    db.session.commit()

    assert rodada.multiplicador == Decimal("2.20")  # acima do justo, que é 2
    assert rodada.premio == Decimal("22.00")
    assert conservacao() == antes


def test_muitas_rodadas_conservam_a_massa(app, bc, cassino):
    """O jogo inteiro, cem vezes, sem um centavo aparecer ou sumir."""
    antes = conservacao()
    gerador = random.Random(4242)

    for _ in range(100):
        try:
            jogar_dados(cassino["ana"], "1.00", MENOR, 50, aleatorio=gerador)
            db.session.commit()
        except ValorInvalido:
            db.session.rollback()
            break  # ficou sem saldo, e tudo bem

    assert conservacao() == antes


# --- a web ------------------------------------------------------------------


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def test_a_tela_dos_dados_abre(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/dados").get_data(as_text=True)
    assert "Dados" in corpo
    assert "dados/jogar" in corpo


def test_o_lobby_leva_aos_dados(app, bc, cassino):
    corpo = _entrar(app, "ana").get("/caladinho/").get_data(as_text=True)
    assert "/caladinho/dados" in corpo


def test_jogar_pela_web(app, bc, cassino):
    antes = conservacao()
    cliente = _entrar(app, "ana")

    resposta = cliente.post(
        "/caladinho/dados/jogar",
        data={"aposta": "10.00", "sentido": MENOR, "alvo": "50"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    rodada = db.session.execute(db.select(RodadaDados)).scalar_one()
    assert rodada.estado in (RodadaDados.GANHA, RodadaDados.PERDIDA)
    assert conservacao() == antes


def test_o_resultado_sobrevive_a_recarregar(app, bc, cassino):
    """A lição do tabuleiro em branco, de novo: o resultado não vive no
    redirect. Recarregar sem parâmetro nenhum ainda mostra a última rolagem."""
    cliente = _entrar(app, "ana")
    cliente.post(
        "/caladinho/dados/jogar",
        data={"aposta": "10.00", "sentido": MENOR, "alvo": "50"},
        follow_redirects=True,
    )
    rodada = ultima_rodada_dados(cassino["ana"])

    corpo = cliente.get("/caladinho/dados").get_data(as_text=True)

    assert f">{rodada.resultado}<" in corpo


def test_o_historico_dos_dados_aparece(app, bc, cassino):
    jogar_dados(cassino["ana"], "10.00", MENOR, 50, aleatorio=DadoViciado(1))
    db.session.commit()

    assert len(historico_dados(cassino["ana"])) == 1
    corpo = _entrar(app, "ana").get("/caladinho/dados").get_data(as_text=True)
    assert "menor que" in corpo
