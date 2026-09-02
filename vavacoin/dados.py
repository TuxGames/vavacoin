"""Dados — a matemática do jogo, sem banco e sem dinheiro.

Como os outros três, tudo aqui é função pura. A parte que encosta no ledger
mora em :mod:`vavacoin.caladinho`.

## O jogo

O servidor rola um número de **1 a 100**. Antes disso a pessoa escolhe um
**alvo** e um **sentido**:

- ``menor``: ganha se a rolagem for **menor ou igual** ao alvo;
- ``maior``: ganha se a rolagem for **maior** que o alvo.

Com alvo 50 os dois sentidos dão 50%, que é a simetria que faz o jogo ser
lido sem explicação.

## O multiplicador sai da probabilidade

``chance = favoráveis / 100`` e ``justo = 1 / chance``. É a definição de
aposta justa: quem acerta uma em vinte recebe vinte vezes. Sobre o justo
aplica-se a vantagem da casa, que chega aqui como **fator** já pronto,
congelado na aposta.

Como o multiplicador é uma divisão exata de inteiros por 100, ele é calculado
em ``Decimal`` com uma única divisão — mesma disciplina do resto do projeto.

## A faixa do alvo tem duas bordas, e as duas se mexem com a vantagem

**Embaixo** (alvos raros): o teto de 25× é o mesmo dos outros jogos, e é o que
mantém ``premio_maximo = aposta × 25`` verdadeiro para o cassino inteiro. Um
alvo raro demais teria o multiplicador cortado pelo teto, e um número cortado
na tela é o cassino parecendo que rouba. Então o alvo raro demais é
**recusado** em vez de silenciosamente truncado.

**Em cima** (alvos quase certos): com chance de 99% e vantagem de 10% o
multiplicador justo é 1,01 e o pago vira ``0,90`` — ganhar devolveria menos do
que a aposta. Isso é indefensável, mesmo sendo "só a vantagem funcionando",
então também é recusado.

Sobra a faixa em que todo multiplicador mostrado é verdadeiro e maior que 1::

    vantagem | favoráveis mínimos | multiplicador máximo | alvo máximo (menor)
      +10%   |         4          |        22.50         |        89
        2%   |         4          |        24.50         |        97
        0%   |         4          |        25.00         |        99
      -10%   |         5          |        22.00         |        99

:func:`limites_do_alvo` devolve essa faixa para o fator vigente, e é ela que
vira o ``min``/``max`` do campo — mas quem decide é o servidor, com o fator
congelado na rodada.
"""

from decimal import Decimal

from .dinheiro import para_decimal, quantizar_para_baixo

#: A rolagem vai de 1 a 100. Cem faces porque o alvo em "por cento" é o jeito
#: como a pessoa já pensa a chance.
FACES = 100

MENOR = "menor"
MAIOR = "maior"
SENTIDOS = (MENOR, MAIOR)

#: O mesmo teto dos outros jogos. Os quatro dividem a regra de banca do dono.
TETO_DO_MULTIPLICADOR = Decimal("25.00")

#: Ganhar tem de pagar mais do que a aposta. Abaixo disto o alvo é recusado.
MULTIPLICADOR_MINIMO = Decimal("1.01")

FATOR_PADRAO = Decimal("0.98")


def validar_sentido(sentido):
    if sentido not in SENTIDOS:
        raise ValueError("escolha maior ou menor")
    return sentido


def casos_favoraveis(sentido, alvo):
    """Quantas das 100 faces fazem a pessoa ganhar."""
    validar_sentido(sentido)
    alvo = int(alvo)
    return alvo if sentido == MENOR else FACES - alvo


def chance(sentido, alvo):
    """A probabilidade de ganhar, de 0 a 1."""
    return Decimal(casos_favoraveis(sentido, alvo)) / Decimal(FACES)


def multiplicador_justo(sentido, alvo):
    """``1 / chance``, como divisão exata de inteiros."""
    favoraveis = casos_favoraveis(sentido, alvo)
    if favoraveis <= 0:
        raise ValueError("esse alvo não tem como ganhar")
    return Decimal(FACES) / Decimal(favoraveis)


def multiplicador(sentido, alvo, fator=None):
    """O multiplicador da aposta, já com a vantagem da casa."""
    fator = FATOR_PADRAO if fator is None else fator
    return quantizar_para_baixo(multiplicador_justo(sentido, alvo) * fator)


def multiplicador_pagavel(sentido, alvo, fator=None):
    """O que a casa paga: ``min(multiplicador, teto)``.

    O ``min`` é aplicado **depois** do fator, como nos outros jogos — é a
    propriedade de que a guarda de exposição depende. Na prática ele nunca
    corta nada aqui, porque :func:`limites_do_alvo` já recusa o alvo que
    passaria do teto; fica como a mesma rede de segurança que os outros têm.
    """
    return min(multiplicador(sentido, alvo, fator), TETO_DO_MULTIPLICADOR)


def limites_do_alvo(sentido, fator=None):
    """A faixa de alvos em que o multiplicador é verdadeiro e maior que 1.

    Devolve ``(minimo, maximo)`` inclusivos. As duas bordas se mexem com a
    vantagem, e por isso a faixa é sempre calculada a partir do fator — nunca
    escrita como constante.
    """
    fator = FATOR_PADRAO if fator is None else fator
    validar_sentido(sentido)

    permitidos = [
        favoraveis
        for favoraveis in range(1, FACES)
        if MULTIPLICADOR_MINIMO
        <= quantizar_para_baixo(Decimal(FACES) / Decimal(favoraveis) * fator)
        <= TETO_DO_MULTIPLICADOR
    ]
    if not permitidos:
        # Vantagem absurda o bastante para não sobrar faixa. Não acontece
        # dentro dos limites da vantagem, mas o jogo não pode explodir por
        # causa de um número no banco.
        return (1, FACES - 1)

    if sentido == MENOR:
        return (min(permitidos), max(permitidos))
    # Em "maior" os favoráveis são ``100 - alvo``: a faixa espelha.
    return (FACES - max(permitidos), FACES - min(permitidos))


def validar_alvo(sentido, alvo, fator=None):
    """Normaliza e confere o alvo contra a faixa do fator vigente."""
    validar_sentido(sentido)
    try:
        alvo = int(alvo)
    except (TypeError, ValueError):
        raise ValueError("alvo inválido")

    minimo, maximo = limites_do_alvo(sentido, fator)
    if not minimo <= alvo <= maximo:
        raise ValueError(f"o alvo vai de {minimo} a {maximo}")
    return alvo


def rolar(aleatorio):
    """A rolagem, de 1 a 100. Quem chama passa o ``secrets.SystemRandom()``."""
    return aleatorio.randrange(1, FACES + 1)


def ganhou(sentido, alvo, resultado):
    """A rolagem fez a pessoa ganhar?"""
    validar_sentido(sentido)
    if sentido == MENOR:
        return int(resultado) <= int(alvo)
    return int(resultado) > int(alvo)


def premio_maximo(aposta):
    """O maior prêmio que uma aposta pode gerar: ``aposta × 25``.

    O mesmo dos outros três, e pela mesma razão: uma regra de banca só para o
    cassino inteiro.
    """
    return quantizar_para_baixo(para_decimal(aposta) * TETO_DO_MULTIPLICADOR)


def tabela_de_multiplicadores(sentido, fator=None):
    """Alguns alvos de referência, para a tela ter o que mostrar.

    Não é a faixa inteira — são 99 alvos, e uma tabela com 99 linhas não ajuda
    ninguém a decidir.
    """
    minimo, maximo = limites_do_alvo(sentido, fator)
    marcos = [minimo, 5, 10, 25, 50, 75, 90, maximo]
    vistos, linhas = set(), []
    for alvo in sorted(a for a in marcos if minimo <= a <= maximo):
        if alvo in vistos:
            continue
        vistos.add(alvo)
        linhas.append(
            (
                alvo,
                quantizar_para_baixo(chance(sentido, alvo) * 100),
                multiplicador_pagavel(sentido, alvo, fator),
            )
        )
    return linhas
