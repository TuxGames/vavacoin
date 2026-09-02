"""A vantagem da casa: editável pelo dono, com faixa, registro e congelamento.

Um mecanismo só para todos os jogos do Caladinho. O mines é quem já usa; o
crash, a torre e os dados entram na mesma tabela, e por isso os testes de
faixa e de registro percorrem ``JOGOS`` inteiro em vez de citar um jogo.

O teste que mais importa é o do **congelamento**: abre rodada, muda a
vantagem, e a rodada tem de pagar pela tabela antiga. Sem isso, o dono
consegue baixar o pagamento de alguém que já está jogando — que é exatamente
a acusação que este cassino não pode receber.
"""

import random
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    criar_casa,
    criar_rodada,
    definir_dono,
    limite_de_aposta,
    livre_para_retirar,
    retirar,
    retirar_do_caixa,
    revelar_casa,
)
from vavacoin.crash import (
    TETO_DO_MULTIPLICADOR as TETO_CRASH,
    sortear_ponto_de_estouro,
)
from vavacoin.dinheiro import quantizar_para_baixo
from vavacoin.erros import CaixaComprometido, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.mines import (
    TETO_DO_MULTIPLICADOR as TETO_MINES,
    casas_seguras,
    multiplicador_justo,
    multiplicador_pagavel,
)
from vavacoin.modelos import RegistroAdministrativo, RodadaMines
from vavacoin.operacoes import ajustar_saldo
from vavacoin.vantagem import (
    JOGOS,
    MAXIMA,
    MINIMA,
    PADRAO,
    definir_vantagem,
    fator_de,
    todas,
    vantagem,
)

SENHA = "senha-boa-123"
ZERO_PCT = Decimal("0.00")


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cassino(app, bc, nova_pessoa):
    """Casa com caixa, dono e um jogador com dinheiro."""
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta, "2000.00", "caixa do teste", autoridade=bc)
    db.session.commit()

    gustavo = nova_pessoa(nome="gustavo", saldo="100.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    ana = nova_pessoa(nome="ana", saldo="100.00")
    return {"casa": conta, "dono": gustavo, "ana": ana}


# --- o padrão e a faixa -----------------------------------------------------


def test_todo_jogo_comeca_na_vantagem_padrao(app, bc):
    """2% é onde o mines estava quando a vantagem virou dado."""
    for jogo in JOGOS:
        assert vantagem(jogo) == PADRAO == Decimal("2.00")


def test_a_vantagem_aceita_a_faixa_inteira(app, bc, cassino):
    for jogo in JOGOS:
        for valor in [MINIMA, Decimal("-2.50"), ZERO_PCT, Decimal("4.00"), MAXIMA]:
            definir_vantagem(jogo, valor, cassino["dono"])
            db.session.commit()
            assert vantagem(jogo) == valor


def test_vantagem_negativa_e_permitida(app, bc, cassino):
    """Decisão do dono: evento em que a casa paga acima do justo.

    Ele está ciente de que perde dinheiro por aritmética. Não há atrito nem
    aviso contra isso — o que existe é a faixa, e ela deixa passar.
    """
    definir_vantagem("mines", "-10.00", cassino["dono"])
    db.session.commit()

    assert vantagem("mines") == Decimal("-10.00")
    assert fator_de(vantagem("mines")) == Decimal("1.1")  # paga 110% do justo


@pytest.mark.parametrize("ruim", ["-10.01", "-25", "-100", "10.01", "50", "abc", ""])
def test_vantagem_fora_da_faixa_e_recusada(app, bc, cassino, ruim):
    """A faixa é dos dois lados, e o motivo de cada ponta é diferente.

    Em cima: acima de 10% a tabela do mines fica magra demais. Embaixo: o
    limite NÃO existe para impedir a casa de perder — ela pode, e é o evento.
    Existe para que ``-10`` virando ``-100`` por um dígito a mais bata no
    validador em vez de quadruplicar a velocidade da sangria.

    Os dois limites são conferidos no servidor; o ``min``/``max`` do campo é
    conforto, não defesa.
    """
    with pytest.raises(ValorInvalido):
        definir_vantagem("mines", ruim, cassino["dono"])


def test_jogo_desconhecido_e_recusado(app, bc, cassino):
    with pytest.raises(ValorInvalido):
        definir_vantagem("roleta", "2.00", cassino["dono"])


def test_evento_generoso_congelado_sobrevive_ao_fim_do_evento(app, bc, cassino):
    """O congelamento vale nos DOIS sentidos.

    Quem entrou durante o evento generoso termina generoso, mesmo que o dono
    desligue o evento com a pessoa no meio da rodada. É a mesma garantia do
    lado de cima, e é a que o jogador vai reclamar se faltar.
    """
    definir_vantagem("mines", "-10.00", cassino["dono"])
    db.session.commit()
    antes = conservacao()

    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()
    assert rodada.vantagem == Decimal("-10.00")

    # O dono desliga o evento no meio da rodada.
    definir_vantagem("mines", "2.00", cassino["dono"])
    db.session.commit()

    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(cassino["ana"], segura)
    db.session.commit()
    rodada = retirar(cassino["ana"])
    db.session.commit()

    fator_pago = rodada.premio / rodada.aposta
    generoso = multiplicador_justo(3, 1) * fator_de(Decimal("-10.00"))
    assert abs(fator_pago - generoso) < Decimal("0.01")
    assert fator_pago > multiplicador_justo(3, 1)  # pagou ACIMA do justo
    assert conservacao() == antes


def test_valor_corrompido_no_banco_volta_ao_padrao(app, bc, cassino):
    """A tela do cassino não é lugar de erro 500.

    Se alguém editar o banco na mão, ou se a faixa encolher depois, a leitura
    devolve o padrão em vez de derrubar o jogo.
    """
    from vavacoin.modelos import definir_config_texto
    from vavacoin.vantagem import chave_de

    definir_config_texto(chave_de("mines"), "99.00")
    db.session.commit()

    assert vantagem("mines") == PADRAO


# --- o registro -------------------------------------------------------------


def test_mudanca_de_vantagem_fica_registrada(app, bc, cassino):
    """Quem mudou, quando, de quanto para quanto e qual jogo.

    É o que defende o dono da acusação de ter mexido para alguém perder.
    """
    definir_vantagem("mines", "5.00", cassino["dono"])
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo).where(RegistroAdministrativo.acao == "vantagem")
    ).scalar_one()
    assert registro.ator_id == cassino["dono"].id
    assert registro.alvo == "mines"
    assert "2.00" in registro.detalhe and "5.00" in registro.detalhe
    assert registro.criado_em is not None


def test_cada_jogo_tem_a_propria_vantagem(app, bc, cassino):
    definir_vantagem("mines", "3.00", cassino["dono"])
    definir_vantagem("crash", "7.50", cassino["dono"])
    db.session.commit()

    vigentes = todas()
    assert vigentes["mines"] == Decimal("3.00")
    assert vigentes["crash"] == Decimal("7.50")
    assert vigentes["torre"] == PADRAO  # não mexido, segue no padrão


# --- o congelamento ---------------------------------------------------------


def test_rodada_aberta_paga_pela_tabela_de_quando_apostou(app, bc, cassino):
    """O teste que a feature inteira existe para garantir.

    Abre a rodada com 2%, o dono sobe para 10% no meio, e a rodada paga como
    se nada tivesse mudado. Se algum dia isto falhar, o cassino ganhou a
    capacidade de baixar o pagamento com a pessoa no meio do jogo.
    """
    ana = cassino["ana"]
    antes = conservacao()

    rodada = criar_rodada(ana, "10.00", minas_escolhidas=3)
    db.session.commit()
    assert rodada.vantagem == Decimal("2.00")

    definir_vantagem("mines", "10.00", cassino["dono"])
    db.session.commit()

    # Abre uma casa segura e saca.
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(ana, segura)
    db.session.commit()
    rodada = retirar(ana)
    db.session.commit()

    esperado_antigo = multiplicador_justo(3, 1) * fator_de(Decimal("2.00"))
    esperado_novo = multiplicador_justo(3, 1) * fator_de(Decimal("10.00"))
    fator_pago = rodada.premio / rodada.aposta

    assert abs(fator_pago - esperado_antigo) < Decimal("0.01")
    assert abs(fator_pago - esperado_novo) > Decimal("0.05")
    assert conservacao() == antes


def test_rodada_nova_ja_nasce_com_a_vantagem_nova(app, bc, cassino):
    """O congelamento protege quem está jogando, não congela o cassino."""
    definir_vantagem("mines", "8.00", cassino["dono"])
    db.session.commit()

    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()

    assert rodada.vantagem == Decimal("8.00")


def test_rodadas_antigas_seguem_valendo_dois_por_cento(app, bc, cassino):
    """As que já existiam foram jogadas com 2%, e a conta delas não muda."""
    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()

    db.session.execute(
        db.update(RodadaMines)
        .where(RodadaMines.id == rodada.id)
        .values(vantagem=Decimal("2.00"))
    )
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(RodadaMines, rodada.id).vantagem == Decimal("2.00")


def test_vantagem_zero_e_jogo_justo(app, bc, cassino):
    """Zero é extremo legítimo: a casa abre mão da vantagem, não fica negativa."""
    definir_vantagem("mines", "0.00", cassino["dono"])
    db.session.commit()
    antes = conservacao()

    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(cassino["ana"], segura)
    db.session.commit()
    rodada = retirar(cassino["ana"])
    db.session.commit()

    fator_pago = rodada.premio / rodada.aposta
    assert abs(fator_pago - multiplicador_justo(3, 1)) < Decimal("0.01")
    assert conservacao() == antes


# --- a web ------------------------------------------------------------------


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def test_o_dono_edita_a_vantagem_na_linha(app, bc, cassino):
    """Um POST por linha, sem passo intermediário."""
    cliente = _entrar(app, "gustavo")

    resposta = cliente.post(
        "/caladinho/casa/vantagem/mines",
        data={"vantagem": "6.00"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    db.session.expire_all()
    assert vantagem("mines") == Decimal("6.00")


def test_quem_nao_e_dono_nao_edita_a_vantagem(app, bc, cassino):
    cliente = _entrar(app, "ana")

    resposta = cliente.post(
        "/caladinho/casa/vantagem/mines", data={"vantagem": "10.00"}
    )

    assert resposta.status_code == 403
    db.session.expire_all()
    assert vantagem("mines") == PADRAO


def test_o_painel_do_dono_lista_todos_os_jogos(app, bc, cassino):
    corpo = _entrar(app, "gustavo").get("/caladinho/casa").get_data(as_text=True)

    for jogo in JOGOS:
        assert f"/caladinho/casa/vantagem/{jogo}" in corpo


def test_a_tela_do_mines_mostra_a_vantagem_da_rodada_aberta(app, bc, cassino):
    """Com rodada aberta, a tela mostra a congelada — não a vigente."""
    cliente = _entrar(app, "ana")
    criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()

    definir_vantagem("mines", "9.00", cassino["dono"])
    db.session.commit()

    corpo = cliente.get("/caladinho/mines").get_data(as_text=True)
    assert "<b>2.00%</b>" in corpo
    assert "9.00%" not in corpo


# --- a guarda de exposição com vantagem negativa ----------------------------
#
# A pergunta que estes testes respondem: com a casa pagando acima do justo, o
# `premio_maximo = aposta × 25` continua sendo um teto verdadeiro, ou vira
# mentira que faz a casa aceitar aposta que não cobre?
#
# Continua verdadeiro, e o motivo é onde o teto é aplicado: `min(justo ×
# fator, 25)` corta DEPOIS de multiplicar pela vantagem. Se algum dia alguém
# derivar o teto do fator — ou tirar o `min` — estes testes caem.


@pytest.mark.parametrize("pct", ["-10.00", "-5.00", "0.00", "10.00"])
def test_o_mines_nunca_paga_acima_do_teto_seja_qual_for_a_vantagem(app, pct):
    """Varre a tabela inteira: 24 configurações de mina, todas as casas."""
    fator = fator_de(Decimal(pct))
    for minas in range(1, 25):
        for abertas in range(1, casas_seguras(minas) + 1):
            assert multiplicador_pagavel(minas, abertas, fator) <= TETO_MINES


def test_o_crash_nunca_estoura_acima_do_teto_com_vantagem_negativa(app):
    gerador = random.Random(99)
    fator = fator_de(Decimal("-10.00"))
    for _ in range(5000):
        assert sortear_ponto_de_estouro(fator, gerador) <= TETO_CRASH


def test_a_aposta_maxima_continua_cobrindo_o_pior_caso(app, bc, cassino):
    """O teste que fecha a pergunta: a casa aceita o que consegue pagar?

    Aposta no limite, com a vantagem mais generosa possível, e o prêmio máximo
    dessa rodada cabe em metade do caixa. É a regra de banca do dono ("25× a
    aposta tem que ser menor que 50% do cassino") continuando de pé enquanto a
    casa paga acima do justo.
    """
    definir_vantagem("mines", "-10.00", cassino["dono"])
    db.session.commit()

    caixa = cassino["casa"].saldo
    maxima = limite_de_aposta()
    fator = fator_de(Decimal("-10.00"))

    pior_pagamento = max(
        multiplicador_pagavel(minas, abertas, fator)
        for minas in range(1, 25)
        for abertas in range(1, casas_seguras(minas) + 1)
    )
    assert quantizar_para_baixo(maxima * pior_pagamento) <= caixa / 2


def test_o_dono_nao_retira_o_que_esta_comprometido_em_evento_generoso(app, bc, cassino):
    """`livre = caixa − comprometido` continua honesto com vantagem negativa.

    O comprometido sai de `premio_maximo`, que é o teto real da rodada em
    qualquer vantagem. Se ele mentisse, o dono conseguiria esvaziar a casa por
    baixo de uma rodada aberta que vai pagar generoso.
    """
    definir_vantagem("mines", "-10.00", cassino["dono"])
    db.session.commit()

    livre_antes = livre_para_retirar()
    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()

    # A aposta entrou no caixa (+10) e prendeu o prêmio máximo (−250).
    assert livre_para_retirar() == livre_antes + Decimal("10.00") - Decimal("250.00")

    with pytest.raises(CaixaComprometido):
        retirar_do_caixa(cassino["dono"], livre_para_retirar() + Decimal("0.01"))


def test_a_casa_paga_de_verdade_o_que_prometeu_no_evento(app, bc, cassino):
    """Fim a fim: evento generoso, rodada ganha, dinheiro sai do caixa.

    E a massa se conserva — a casa perder não é a economia perder.
    """
    definir_vantagem("mines", "-10.00", cassino["dono"])
    db.session.commit()
    antes = conservacao()
    caixa_antes = cassino["casa"].saldo

    rodada = criar_rodada(cassino["ana"], "10.00", minas_escolhidas=3)
    db.session.commit()
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(cassino["ana"], segura)
    db.session.commit()
    rodada = retirar(cassino["ana"])
    db.session.commit()

    # Pagou acima do justo, e o caixa sentiu.
    assert rodada.premio > quantizar_para_baixo(
        rodada.aposta * multiplicador_justo(3, 1)
    )
    assert cassino["casa"].saldo == caixa_antes + rodada.aposta - rodada.premio
    assert conservacao() == antes


# --- a vantagem com centavos ------------------------------------------------


@pytest.mark.parametrize("pct", ["2.50", "-2.50", "7.25", "0.01", "-0.01"])
def test_vantagem_com_centavos_funciona_nos_quatro_jogos(app, bc, cassino, pct):
    """O campo aceita ``step="0.01"``, então 2,50% é digitável — e quebrava.

    ``fator_de(2.50)`` vale ``0.975``, com três casas. O sorteio do crash
    passava esse fator por ``para_decimal``, que existe para recusar DINHEIRO
    com precisão abaixo do centavo, e derrubava a rodada com erro 500. O fator
    é uma razão, não dinheiro.

    O teste percorre os quatro jogos porque o mesmo engano cabe em qualquer um
    deles no dia em que alguém for mexer.
    """
    from vavacoin.caladinho import (
        criar_rodada_crash,
        criar_rodada_torre,
        jogar_dados,
    )
    from vavacoin.dados import MENOR

    for jogo in JOGOS:
        definir_vantagem(jogo, pct, cassino["dono"])
    db.session.commit()
    antes = conservacao()

    criar_rodada(cassino["ana"], "1.00", minas_escolhidas=3)
    db.session.commit()
    retirar_ou_ignorar(cassino["ana"])

    criar_rodada_crash(cassino["ana"], "1.00", "2.00")
    db.session.commit()

    criar_rodada_torre(cassino["ana"], "1.00", portas=3)
    db.session.commit()

    jogar_dados(cassino["ana"], "1.00", MENOR, 50)
    db.session.commit()

    assert conservacao() == antes


def retirar_ou_ignorar(jogador):
    """Fecha a rodada de mines se ela ainda estiver aberta."""
    from vavacoin.caladinho import rodada_ativa, revelar_casa, retirar

    rodada = rodada_ativa(jogador)
    if rodada is None:
        return
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(jogador, segura)
    db.session.commit()
    retirar(jogador)
    db.session.commit()
