"""Crash — a matemática do jogo, sem banco e sem dinheiro.

Como :mod:`vavacoin.mines`, tudo aqui é função pura: dá para conferir a
distribuição inteira sem subir a aplicação. A parte que encosta no ledger mora
em :mod:`vavacoin.caladinho`.

## Uma rodada para todo mundo, marcada pelo relógio

O jogo era individual: cada aposta abria a própria curva e o número subia só
na sua tela. O dono pediu o contrário — "os usuários querem ver o avião
subindo e explodindo em tempo real" —, e tempo real com todo mundo junto quer
dizer **uma curva só, a mesma em todas as telas**.

A rodada não é criada por ninguém: ela é o **relógio**. O número da rodada é
``floor(epoch / DURACAO_DO_CICLO)``, e disso sai tudo — quando abre a aposta,
quando o avião levanta, quando o resultado some. Dois celulares que nunca se
falaram concordam sobre qual rodada está correndo porque os dois olham a hora.

Sem isso seria preciso um processo criando rodada de tempos em tempos, e o
plano grátis do PythonAnywhere não tem onde rodar esse processo: a tarefa
agendada é uma por dia.

## O ponto de estouro é derivado, não sorteado

``HMAC(segredo_do_servidor, número_da_rodada)`` vira o ``u`` uniforme que a
fórmula de sempre transforma em multiplicador. Duas consequências, e as duas
importam:

- **qualquer processo chega no mesmo número** sem combinar nada com ninguém,
  então não há estado por jogador para o servidor guardar e a conta por rodada
  não cresce com o tamanho da turma;
- **ninguém de fora chega nele**, porque o segredo não sai do servidor.

O segredo é do servidor e nasce sozinho na primeira rodada — não é a
``SECRET_KEY``, de propósito: a chave de sessão tem outro dono, outro ciclo de
troca, e no repositório existe uma de desenvolvimento que é pública.

## Só alvo. Não existe botão de sacar durante o voo

Esta é a parte contraintuitiva, e ela é o que faz o desenho fechar.

Para desenhar a explosão na hora certa, **o navegador precisa saber onde a
curva para**. Se ele sabe, e ainda existisse um botão de sacar, o jogo
acabava: quem vê que estoura em 3× clica em 2,99× e nunca mais perde. A
vantagem da casa iria a zero, e não por bug — por desenho.

Tirando o clique, saber a curva deixa de valer nada. O alvo é declarado junto
com a aposta e resolvido pelo servidor; o navegador só desenha. É o mesmo
princípio do tabuleiro do mines, que também já está decidido antes do primeiro
clique: a animação é teatro sobre um resultado que já existe.

O efeito colateral é bom o bastante para virar regra: **ninguém mais perde por
lag de rede.** Era o furo da primeira versão — o saque manual era um POST
validado contra o relógio do servidor, e os 250 ms de rede podiam cair em cima
do estouro, transformando vitória em derrota sem a pessoa ter como saber que
fora a rede. Sem botão, não há clique para chegar tarde.

## O estouro não pode existir para o cliente antes da janela fechar

É o ponto de segurança da feature, e vale escrito: quem souber onde a rodada
estoura **enquanto ainda dá para apostar** aposta só quando é favorável, e
ganha sempre. Por isso nenhuma rota entrega o ponto de estouro durante a
janela de aposta, e **não existe jeito de perguntar pela rodada seguinte** — a
rota do reveal não aceita número nenhum, ela responde sobre a rodada de agora.

## A curva

``m(t) = 2^(t / SEGUNDOS_PARA_DOBRAR)``.

Ela era lenta de propósito — quanto mais lenta, menos multiplicador cabe
dentro de um atraso de rede. **Esse motivo morreu com o botão de sacar**, e a
curva acelerou: agora o que manda é o ciclo caber num tempo que dê para ficar
olhando. Com o teto de 25×, o voo mais longo possível é o que define a duração
do ciclo.

O teto é o mesmo do mines, 25×, e **precisa** ser: a regra de banca do dono
("25× a aposta tem que ser menor que 50% do cassino") é uma só para o cassino
inteiro, e é ela que vira ``aposta_maxima = caixa / 50``.
"""

import hashlib
import hmac
import math
from decimal import Decimal

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo

#: Quantos segundos para o multiplicador dobrar.
#:
#: Era 8. A lentidão existia para proteger o saque manual do atraso de rede, e
#: o saque manual não existe mais — o que manda agora é o ciclo caber numa
#: espera tolerável, porque a rodada é compartilhada e quem perdeu espera a
#: próxima.
SEGUNDOS_PARA_DOBRAR = Decimal("3")

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
    # O fator é uma RAZÃO, não dinheiro: com vantagem de 2,50% ele vale
    # 0,975, e `para_decimal` — que existe para recusar dinheiro com
    # precisão abaixo do centavo — recusava isso e derrubava a rodada.
    # Quem arredonda aqui é o `quantizar_para_baixo` do resultado.
    bruto = quantizar_para_baixo(Decimal(fator) / u)
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


# --- a rodada compartilhada -------------------------------------------------
#
# Tudo aqui é aritmética de relógio: não há estado, não há banco e não há
# jogador. É o que permite o navegador e o servidor chegarem na mesma resposta
# sem trocarem uma palavra.


#: Quanto dura a janela em que dá para apostar, em segundos.
JANELA_DE_APOSTA = 10

#: Quanto tempo o resultado fica na tela depois do voo mais longo possível.
MOSTRA_O_RESULTADO = 6


def _voo_mais_longo():
    """Quantos segundos a curva leva para bater no teto, arredondado para cima.

    É o voo mais demorado que pode existir, porque o estouro é limitado ao
    teto. Derivado da curva em vez de escrito à mão: mexer em
    ``SEGUNDOS_PARA_DOBRAR`` sem mexer aqui deixaria o ciclo curto demais, e o
    avião seria cortado no ar.
    """
    return int(math.ceil(float(segundos_para_multiplicador(TETO_DO_MULTIPLICADOR))))


#: O voo mais longo possível, em segundos.
VOO_MAXIMO = _voo_mais_longo()

#: O ciclo inteiro: janela de aposta, voo e resultado.
#:
#: Somado, e não escolhido: assim o ciclo é sempre grande o bastante para o
#: voo mais longo caber dentro dele. Com a curva de hoje dá 30 s — dois por
#: minuto, que é o que faz as contas de relógio caírem em números redondos.
DURACAO_DO_CICLO = JANELA_DE_APOSTA + VOO_MAXIMO + MOSTRA_O_RESULTADO

#: Os três momentos de uma rodada, na ordem em que acontecem.
APOSTAS = "apostas"
VOO = "voo"
RESULTADO = "resultado"


def numero_da_rodada(epoch):
    """Qual rodada está correndo neste instante.

    ``floor(epoch / ciclo)``, e nada mais. O número é o relógio — por isso
    dois celulares que nunca se falaram concordam sobre qual rodada é esta.
    """
    return int(epoch // DURACAO_DO_CICLO)


def inicio_da_rodada(numero):
    """O epoch em que a janela de aposta desta rodada abriu."""
    return int(numero) * DURACAO_DO_CICLO


def comeco_do_voo(numero):
    """O epoch do instante zero da curva: quando a janela de aposta fecha."""
    return inicio_da_rodada(numero) + JANELA_DE_APOSTA


def fim_do_ciclo(numero):
    """O epoch em que esta rodada sai da tela e a seguinte abre."""
    return inicio_da_rodada(numero) + DURACAO_DO_CICLO


def da_para_apostar(numero, epoch):
    """A janela desta rodada ainda está aberta?

    É a mesma pergunta que decide se a aposta entra **e** se o ponto de
    estouro pode sair do servidor. Uma função só, chamada dos dois lugares: se
    fossem duas, um dia elas discordariam e a que discordasse a favor do
    cliente entregaria o jogo.
    """
    return inicio_da_rodada(numero) <= epoch < comeco_do_voo(numero)


def segundos_de_voo(numero, epoch):
    """Há quanto tempo o avião levantou. Negativo antes de levantar."""
    return Decimal(str(epoch - comeco_do_voo(numero)))


def fase_da_rodada(numero, epoch, ponto_de_estouro=None):
    """Em que momento a rodada está, do ponto de vista de quem olha.

    Com o ``ponto_de_estouro`` em mãos a resposta é exata: o voo acaba quando
    a curva alcança o estouro, que quase sempre é antes do voo máximo. Sem
    ele, o melhor que dá para dizer é que o voo ainda pode estar acontecendo.
    """
    if da_para_apostar(numero, epoch):
        return APOSTAS
    decorridos = segundos_de_voo(numero, epoch)
    limite = (
        segundos_para_multiplicador(ponto_de_estouro)
        if ponto_de_estouro is not None
        else Decimal(VOO_MAXIMO)
    )
    return VOO if decorridos < limite else RESULTADO


def voo_terminou(numero, ponto_de_estouro, epoch):
    """O avião já explodiu? É o gatilho da liquidação preguiçosa."""
    return segundos_de_voo(numero, epoch) >= segundos_para_multiplicador(
        ponto_de_estouro
    )


class _SorteioDeterministico:
    """Um ``random()`` que sempre devolve o mesmo número.

    Existe para o estouro derivado passar pela **mesma**
    :func:`sortear_ponto_de_estouro` do sorteio de verdade, em vez de uma
    segunda fórmula escrita ao lado. Duas fórmulas para a mesma distribuição é
    como a vantagem da casa deixa de ser a que está no painel.
    """

    def __init__(self, valor):
        self._valor = valor

    def random(self):
        return self._valor


#: Quantos bits do HMAC viram o ``u`` uniforme. 52 é o que um ``double``
#: representa exatamente — mais que isso não acrescenta aleatoriedade
#: perceptível e menos começaria a deixar buraco na distribuição.
_BITS = 52


def uniforme_da_rodada(segredo, numero):
    """O ``u`` uniforme em (0, 1] desta rodada, a partir do segredo.

    HMAC-SHA256 e não um hash simples: com hash puro, quem descobrisse o
    formato da entrada poderia testar segredos candidatos contra um estouro
    conhecido. O ``+1`` tira o zero do intervalo, porque a fórmula divide
    por ``u``.
    """
    if isinstance(segredo, str):
        segredo = segredo.encode("utf-8")
    digest = hmac.new(segredo, str(int(numero)).encode("ascii"), hashlib.sha256).digest()
    bruto = int.from_bytes(digest, "big") >> (256 - _BITS)
    return (Decimal(bruto) + 1) / Decimal(1 << _BITS)


def ponto_de_estouro_da_rodada(segredo, numero, fator):
    """Onde a rodada ``numero`` para. Determinístico, e igual em toda máquina.

    ``fator`` é ``(100 - vantagem) / 100``, e por isso o resultado depende da
    vantagem: a rodada **congela a sua** quando nasce, senão o dono mexendo no
    painel no meio do voo mudaria o estouro de uma rodada em andamento.
    """
    return sortear_ponto_de_estouro(
        fator, _SorteioDeterministico(uniforme_da_rodada(segredo, numero))
    )
