"""Mines — a matemática do jogo, sem banco e sem dinheiro.

Trazido do `cassino_benbal`, que já rodou com gente jogando. Tudo aqui é
função pura: dá para conferir a tabela de pagamento inteira sem subir a
aplicação. A parte que encosta no ledger mora em :mod:`vavacoin.caladinho`.

## Multiplicador

Grid de 25 casas. O jogador escolhe quantas minas (1 a 24); as seguras são
``S = 25 - minas``. Ao abrir a k-ésima casa segura, o multiplicador justo
acumulado é::

    justo(k) = produto_{i=0}^{k-1} (25 - i) / (S - i)

A cada acerto a chance de a próxima ser segura é ``(S-i)/(25-i)``; o
multiplicador justo é o inverso do produto dessas chances.

O produto é calculado como **fração de inteiros**, com uma única divisão
``Decimal`` no fim. Multiplicar Decimais em sequência acumularia erro de
arredondamento a cada passo, e num jogo que paga dinheiro isso vira centavo
perdido — o mesmo motivo que faz o resto do projeto recusar ``float``.

Sobre o justo aplica-se a vantagem da casa (2%), quantizando **para baixo**.

## Teto

O multiplicador pagável para em 25×. Não é enfeite: é o que torna o prêmio
máximo de uma rodada previsível (``aposta × 25``) e, com isso, torna possível
limitar a aposta ao que a casa aguenta. Sem teto, o multiplicador do tabuleiro
limpo é astronômico e nenhum limite de aposta protegeria a casa.
"""

from decimal import Decimal

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo

#: Grid 5x5.
CASAS = 25
MIN_MINAS = 1
MAX_MINAS = 24

#: Vantagem da casa: 2%. Fica na tela, em número — o que é escondido vira
#: suspeita que o dono não consegue desprovar depois.
VANTAGEM_DA_CASA = Decimal("0.98")

#: Teto do multiplicador pagável.
TETO_DO_MULTIPLICADOR = Decimal("25.00")

#: Fração do caixa que o prêmio máximo de uma rodada pode alcançar.
FRACAO_MAXIMA_DA_CASA = Decimal("0.50")


def casas_seguras(minas):
    return CASAS - minas


def multiplicador_justo(minas, abertas):
    """``produto (25-i)/(S-i)``, como fração de inteiros com uma só divisão."""
    if abertas <= 0:
        return Decimal(1)
    seguras = casas_seguras(minas)
    numerador = 1
    denominador = 1
    for i in range(abertas):
        numerador *= CASAS - i
        denominador *= seguras - i
    return Decimal(numerador) / Decimal(denominador)


def multiplicador(minas, abertas):
    """O multiplicador acumulado do jogador, sem o teto.

    Guardar o valor sem teto é de propósito: é o número "verdadeiro" da
    rodada. O teto entra no que se paga (:func:`multiplicador_pagavel`).
    """
    if abertas <= 0:
        return Decimal("1.00")
    return quantizar_para_baixo(multiplicador_justo(minas, abertas) * VANTAGEM_DA_CASA)


def multiplicador_pagavel(minas, abertas):
    """O que a casa paga de fato: ``min(multiplicador, teto)``."""
    return min(multiplicador(minas, abertas), TETO_DO_MULTIPLICADOR)


def bateu_o_teto(minas, abertas):
    """Chegou ao máximo? Daqui em diante abrir casa não paga mais nada."""
    return multiplicador(minas, abertas) >= TETO_DO_MULTIPLICADOR


def tabela_de_multiplicadores(minas):
    """A progressão até o teto, para desenhar na tela."""
    linhas = []
    for k in range(1, casas_seguras(minas) + 1):
        linhas.append((k, multiplicador_pagavel(minas, k)))
        if bateu_o_teto(minas, k):
            break
    return linhas


def premio_maximo(aposta):
    """O maior prêmio que uma aposta pode gerar. Com o teto, é ``aposta × 25``.

    Não depende do número de minas: o teto é o mesmo para todas as
    configurações. É isso que faz a aposta máxima ser um número só.
    """
    return quantizar_para_baixo(para_decimal(aposta) * TETO_DO_MULTIPLICADOR)


def aposta_maxima(caixa, comprometido=ZERO):
    """Maior aposta que a casa aguenta agora::

        aposta_maxima = (0,50 × caixa − comprometido) / 25

    ``comprometido`` é o prêmio máximo das rodadas ainda ativas. O original
    não desconta isso porque lá só existe uma rodada ativa por jogador — mas
    nada impede dez jogadores ao mesmo tempo, e aí cada aposta passa sozinha
    no teto e juntas estouram a casa. Quem sacaria por último é quem não
    receberia.
    """
    disponivel = (FRACAO_MAXIMA_DA_CASA * para_decimal(caixa)) - para_decimal(
        comprometido
    )
    if disponivel <= ZERO:
        return ZERO
    return quantizar_para_baixo(disponivel / TETO_DO_MULTIPLICADOR)


def validar_minas(minas):
    """Normaliza e valida a escolha de minas."""
    try:
        minas = int(minas)
    except (TypeError, ValueError):
        raise ValueError("número de minas inválido")
    if not MIN_MINAS <= minas <= MAX_MINAS:
        raise ValueError(f"escolha entre {MIN_MINAS} e {MAX_MINAS} minas")
    return minas
