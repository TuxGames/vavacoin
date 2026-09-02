"""Torre — a matemática do jogo, sem banco e sem dinheiro.

Como :mod:`vavacoin.mines` e :mod:`vavacoin.crash`, tudo aqui é função pura: a
torre inteira, em qualquer dificuldade e qualquer vantagem, se confere sem
subir a aplicação. A parte que encosta no ledger mora em
:mod:`vavacoin.caladinho`.

## O jogo

Cada andar tem ``portas`` portas e **uma** delas é armadilha. Escolhe uma por
andar: acertou, sobe e o multiplicador acumula; pisou na armadilha, perde a
aposta. Dá para sacar a qualquer momento com o que já acumulou.

## O multiplicador

Sobreviver a um andar tem chance ``seguras / portas``. O multiplicador justo
depois de ``a`` andares é o inverso do produto dessas chances::

    justo(a) = (portas / seguras) ** a

Calculado como **fração de inteiros**, com uma única divisão ``Decimal`` no
fim — pelo mesmo motivo do mines: multiplicar Decimais em sequência acumula
erro de arredondamento a cada passo, e num jogo que paga dinheiro isso vira
centavo perdido.

Sobre o justo aplica-se a vantagem da casa, que é editável e chega aqui como
**fator** já pronto, congelado na aposta.

## A dificuldade é UM botão, como no mines

O jogador escolhe **quantas portas** cada andar tem, de 2 a 4. Menos portas é
mais arriscado e sobe mais rápido — é o mesmo eixo do número de minas, com o
mesmo formato de um inteiro só.

O caminho não seguido, e o porquê: fixar 4 portas e deixar o jogador escolher
de 1 a 3 armadilhas também daria um botão só, mas com 3 armadilhas o andar
paga 4× e o terceiro andar salta o justo para 62,72 — cortado a 25 pelo teto,
um corte de 60% bem no andar mais difícil de alcançar. Um número desses na
tabela parece a casa roubando, e essa é a acusação que este cassino não pode
receber. Com as portas como botão o pior corte é de 20%.

## Onde a torre termina

**No teto de 25×, não num número de andares.** A altura é consequência: a
torre tem exatamente os andares que cabem até o teto, e o último é saque
forçado, como no mines. Não existe um segundo limite para manter sincronizado,
e — o que importa de verdade — ``premio_maximo`` continua sendo ``aposta ×
25``, que é o que mantém honesta a guarda de exposição da casa.

Como a vantagem entra antes do teto, a altura muda com ela: em evento
generoso o multiplicador sobe mais rápido e a torre fica mais baixa. Por isso
a altura é congelada junto com o fator, na aposta.

    portas | chance por andar | andares | topo  | corte no topo
       2   |       50%        |    5    | 25.00 |     20%
       3   |       67%        |    8    | 25.00 |      0%
       4   |       75%        |   12    | 25.00 |     19%
"""

from decimal import Decimal

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo

#: Uma armadilha por andar. Fixo: a dificuldade mora no número de portas.
ARMADILHAS_POR_ANDAR = 1

MIN_PORTAS = 2
MAX_PORTAS = 4

#: O mesmo teto do mines e do crash. Os três jogos dividem a regra de banca do
#: dono ("25× a aposta tem que ser menor que 50% do caixa"), e é ela que vira
#: ``aposta_maxima = caixa / 50``.
TETO_DO_MULTIPLICADOR = Decimal("25.00")

#: Quantos andares no máximo, por segurança de tela e de laço. Nenhuma
#: dificuldade chega perto disso com o teto de 25×; existe para que uma
#: vantagem absurda não gere uma torre infinita.
LIMITE_DE_ANDARES = 40


def portas_seguras(portas):
    return portas - ARMADILHAS_POR_ANDAR


def multiplicador_justo(portas, andares):
    """``(portas / seguras) ** andares``, como fração de inteiros."""
    if andares <= 0:
        return Decimal(1)
    seguras = portas_seguras(portas)
    return Decimal(portas**andares) / Decimal(seguras**andares)


def multiplicador(portas, andares, fator=None):
    """O multiplicador acumulado, sem o teto.

    ``fator`` é ``(100 - vantagem) / 100``, congelado na aposta. Sem ele vale
    o padrão histórico de 2%, para que uma conta feita fora de rodada não
    invente vantagem nenhuma.
    """
    if andares <= 0:
        return Decimal("1.00")
    fator = Decimal("0.98") if fator is None else fator
    return quantizar_para_baixo(multiplicador_justo(portas, andares) * fator)


def multiplicador_pagavel(portas, andares, fator=None):
    """O que a casa paga de fato: ``min(multiplicador, teto)``.

    O ``min`` é aplicado **depois** do fator, e é isso que mantém o prêmio
    máximo em ``aposta × 25`` mesmo com vantagem negativa — a propriedade de
    que a guarda de exposição depende.
    """
    return min(multiplicador(portas, andares, fator), TETO_DO_MULTIPLICADOR)


def bateu_o_teto(portas, andares, fator=None):
    """Chegou ao máximo? Daqui em diante subir não paga mais nada."""
    return multiplicador(portas, andares, fator) >= TETO_DO_MULTIPLICADOR


def altura(portas, fator=None):
    """Quantos andares a torre tem: os que cabem até o teto, inclusive o topo.

    Consequência do teto, não um número escolhido à parte.
    """
    for andar in range(1, LIMITE_DE_ANDARES + 1):
        if bateu_o_teto(portas, andar, fator):
            return andar
    return LIMITE_DE_ANDARES


def tabela_de_multiplicadores(portas, fator=None):
    """A progressão andar a andar até o topo, para desenhar na tela."""
    return [
        (andar, multiplicador_pagavel(portas, andar, fator))
        for andar in range(1, altura(portas, fator) + 1)
    ]


def premio_maximo(aposta):
    """O maior prêmio que uma aposta pode gerar: ``aposta × 25``.

    Não depende da dificuldade nem da vantagem, porque o teto é o mesmo para
    todas — é o que faz a aposta máxima ser um número só para o cassino
    inteiro.
    """
    return quantizar_para_baixo(para_decimal(aposta) * TETO_DO_MULTIPLICADOR)


def validar_portas(portas):
    """Normaliza e valida a escolha de dificuldade."""
    try:
        portas = int(portas)
    except (TypeError, ValueError):
        raise ValueError("número de portas inválido")
    if not MIN_PORTAS <= portas <= MAX_PORTAS:
        raise ValueError(f"escolha entre {MIN_PORTAS} e {MAX_PORTAS} portas")
    return portas
