"""Crash — a matemática do jogo, sem banco e sem dinheiro.

Como :mod:`vavacoin.mines`, tudo aqui é função pura: dá para conferir a
distribuição inteira sem subir a aplicação. A parte que encosta no ledger mora
em :mod:`vavacoin.caladinho`.

## O ponto de estouro

Sorteado **no instante da aposta**, no servidor, e guardado na rodada. A
distribuição é a clássica do crash::

    M = (1 - vantagem) / u,    u ~ uniforme em (0, 1]

Ela tem uma propriedade que nenhuma outra dá de graça: para **qualquer** alvo
``t``, o retorno esperado é o mesmo::

    P(M >= t) = (1 - vantagem) / t
    esperado  = P(M >= t) × t = 1 - vantagem

Ou seja: a vantagem da casa é exatamente a vantagem configurada, sair em 1,50×
ou em 20× dá no mesmo, e não existe alvo "esperto". Isso importa porque o
alvo é escolhido pelo jogador — se algum alvo fosse melhor que os outros, o
jogo viraria uma charada de otimização em vez de aposta.

## Por que existe alvo

Não há websocket aqui, e não vai haver. O saque manual é um POST validado
contra o relógio do servidor, e entre o clique e a chegada do POST passam uns
250 ms de rede — tempo em que o multiplicador andou. Se o estouro cair nesse
vão, a pessoa clicou antes e perde assim mesmo, sem ter como saber que foi a
rede. É a mesma classe de problema do tabuleiro em branco: a tela acusa o
cassino de roubar.

O alvo fecha esse buraco. Declarado **junto com a aposta**, ele é resolvido no
servidor sem depender de clique nenhum: quem põe alvo e não toca no botão tem
risco de rede **zero**. O botão continua existindo e só serve para sair
**antes** do alvo. Assim o pior que a rede faz é entregar o alvo no lugar do
número onde a pessoa clicou — nunca transformar vitória em derrota.

Consequência de desenho, e é boa: o resultado da rodada fica inteiramente
decidido no instante da aposta (``alvo <= estouro`` ganha, senão perde). A
animação é teatro sobre um resultado que já existe, exatamente como o
tabuleiro do mines já é sorteado antes do primeiro clique. Resolver a rodada
mais tarde não é re-sortear nada.

## A curva

``m(t) = 2^(t / 8)``: dobra a cada oito segundos. Escolhida devagar de
propósito — quanto mais lenta a curva, menos multiplicador cabe dentro de um
atraso de rede, e menos a rede importa para quem saca no braço.

O teto é o mesmo do mines, 25×, e **precisa** ser: a regra de banca do dono
("25× a aposta tem que ser menor que 50% do caixa") é uma só para o cassino
inteiro, e é ela que vira ``aposta_maxima = caixa / 50``.
"""

from decimal import Decimal

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo

#: Quantos segundos para o multiplicador dobrar.
SEGUNDOS_PARA_DOBRAR = Decimal("8")

#: Onde todo crash começa.
MULTIPLICADOR_INICIAL = Decimal("1.00")

#: O mesmo teto do mines, e pelo mesmo motivo: é o número que torna o prêmio
#: máximo previsível (``aposta × 25``) e, com isso, torna possível limitar a
#: aposta ao que a casa aguenta. Os dois jogos dividem a regra de banca.
TETO_DO_MULTIPLICADOR = Decimal("25.00")

#: Menor alvo aceitável. Abaixo de 1,01× não há aposta: sair em 1,00× é
#: devolver a aposta, o que não é jogo nenhum.
ALVO_MINIMO = Decimal("1.01")


def multiplicador_no_tempo(segundos):
    """Onde a curva está depois de ``segundos``. Nunca abaixo de 1,00×.

    ``2^(t/8)`` calculado em ``Decimal``: o projeto inteiro recusa ``float``, e
    um multiplicador que paga dinheiro não é lugar para começar.
    """
    segundos = Decimal(str(segundos)) if not isinstance(segundos, Decimal) else segundos
    if segundos <= 0:
        return MULTIPLICADOR_INICIAL
    expoente = (segundos / SEGUNDOS_PARA_DOBRAR) * Decimal(2).ln()
    bruto = expoente.exp()
    # Arredonda o transcendental antes de truncar em centavos. Sem isto, o
    # instante em que a curva vale exatamente 2,00× devolve 1,99: `ln` e `exp`
    # erram na vigésima casa, e truncar transforma esse erro num centavo a
    # menos para quem está jogando. Dez casas é muito mais precisão do que o
    # jogo usa e muito menos do que o erro que se quer descartar.
    return quantizar_para_baixo(bruto.quantize(Decimal("0.0000000001")))


def segundos_para_multiplicador(multiplicador):
    """O inverso da curva: quando ela chega em ``multiplicador``.

    Serve para saber se o alvo já passou sem depender de varrer o tempo.
    """
    multiplicador = para_decimal(multiplicador)
    if multiplicador <= MULTIPLICADOR_INICIAL:
        return ZERO
    return (multiplicador.ln() / Decimal(2).ln()) * SEGUNDOS_PARA_DOBRAR


def sortear_ponto_de_estouro(fator, aleatorio):
    """``M = fator / u``, com ``u`` uniforme em (0, 1].

    ``fator`` é ``(100 - vantagem) / 100``; ``aleatorio`` é o gerador (recebe
    o ``secrets.SystemRandom()`` de quem chama, e um gerador fixo no teste).

    Quantizado para baixo e nunca abaixo de 1,00×: com ``u`` perto de 1 o
    resultado é o próprio fator, que é menor que 1 — e isso é o estouro
    imediato, que é justamente por onde a vantagem da casa entra.
    """
    u = Decimal(str(aleatorio.random()))
    if u <= 0:  # praticamente impossível, mas dividir por zero é certeza
        u = Decimal("0.0000000001")
    bruto = quantizar_para_baixo(para_decimal(fator) / u)
    if bruto < MULTIPLICADOR_INICIAL:
        return MULTIPLICADOR_INICIAL
    return min(bruto, TETO_DO_MULTIPLICADOR)


def validar_alvo(alvo):
    """Normaliza e confere o alvo declarado pelo jogador."""
    try:
        alvo = para_decimal(str(alvo).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValueError("alvo inválido") from erro
    if alvo < ALVO_MINIMO:
        raise ValueError(f"o alvo mínimo é {ALVO_MINIMO}×")
    if alvo > TETO_DO_MULTIPLICADOR:
        raise ValueError(f"o alvo máximo é {TETO_DO_MULTIPLICADOR}×")
    return alvo


def ganhou(alvo, ponto_de_estouro):
    """A rodada foi ganha? Decidido no instante da aposta.

    Sair exatamente no ponto de estouro conta como ganho: o estouro é onde a
    curva **passa** do valor, e o alvo é atingido antes disso.
    """
    return para_decimal(alvo) <= para_decimal(ponto_de_estouro)


def premio_maximo(aposta):
    """O maior prêmio que uma aposta pode gerar: ``aposta × 25``.

    Deliberadamente o teto, e não ``aposta × alvo``, mesmo que o alvo declarado
    seja menor: é a regra de banca do dono, escrita uma vez só e igual para os
    dois jogos. Reservar mais do que a rodada pode pagar erra para o lado de
    quem está jogando.
    """
    return quantizar_para_baixo(para_decimal(aposta) * TETO_DO_MULTIPLICADOR)
