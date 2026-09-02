"""A vantagem da casa — um mecanismo só, para todos os jogos do Caladinho.

O mines nasceu com 2% escritos no código. Isso vira deploy toda vez que o dono
quer experimentar outro número, e não sobrevive a ter três jogos. Aqui a
vantagem é **dado**: uma linha por jogo na tabela ``Configuracao``, editável
pelo dono no painel da casa.

## Por que percentual, e não fator

Guardado como pontos percentuais (``2.00`` = 2%), não como o fator ``0.98``.
São a mesma informação, mas o painel edita o que a pessoa pensa — "quanto a
casa fica com" — e não o complemento. O fator é derivado na hora de calcular,
por :func:`fator_de`.

O tipo é ``Dinheiro`` (inteiro de centésimos) pela mesma razão que o resto do
projeto recusa ``float``: 2,5% precisa ser exatamente 2,5%. Aqui o "centavo"
é um centésimo de ponto percentual.

## A faixa: −10% a +10%, e de onde os dois números saem

**Vantagem negativa é permitida, por decisão do dono.** Ela faz o jogo pagar,
na média, mais do que arrecada — a casa perde por aritmética, não por azar —,
e é exatamente isso que ele quer poder fazer: evento em que o Caladinho paga
acima do justo. Não existe atrito nem aviso contra isso na tela; a decisão é
dele e está tomada.

### O lado de cima: +10%

A vantagem multiplica o multiplicador justo, e o justo da primeira casa do
mines é baixo — com 1 mina, ``25/24 = 1,0416``. A partir de **5%** esse
produto cai abaixo de 1,00 e a tela passa a mostrar que abrir uma casa e sacar
devolve **menos** do que a aposta::

    vantagem | 1 mina | 2 minas | 3 minas
        0%   |  1.04  |  1.08   |  1.13
        2%   |  1.02  |  1.06   |  1.11
        4%   |  1.00  |  1.04   |  1.09
        5%   |  0.98  |  1.03   |  1.07
       10%   |  0.93  |  0.97   |  1.02

Não é defeito — é a vantagem funcionando —, mas multiplicador abaixo de 1,00
na tela parece a casa roubando. Até 4% isso nunca aparece; entre 4 e 10 o dono
vai sabendo o que a tela mostra.

### O lado de baixo: −10%

O que **não** é o motivo do limite: catástrofe numa rodada. O teto de 25× é
aplicado **depois** do fator (``min(multiplicador × fator, 25)``), então o
prêmio máximo continua sendo ``aposta × 25`` em qualquer vantagem — conferido
por teste até −300%. Como a aposta máxima é ``caixa / 50``, a rodada mais cara
possível paga ``caixa / 2``, e nenhum número digitado neste campo esvazia a
casa de uma vez. A guarda de exposição continua honesta.

O motivo do limite é a **velocidade da sangria**, que é linear em |vantagem|.
Com aposta máxima em ``caixa/50``, a perda esperada por rodada máxima é
``|vantagem| × caixa / 50``::

    vantagem | % do caixa por rodada | rodadas até perder metade
       −5%   |        0,100%         |          500
      −10%   |        0,200%         |          250
      −25%   |        0,500%         |          100
      −50%   |        1,000%         |           50
     −100%   |        2,000%         |           25

−10% dá evento de verdade — no mines com 3 minas a primeira casa sai de 1,11
para 1,24, e no crash o retorno esperado vira 110% do apostado — e ainda assim
o caixa aguenta 250 rodadas no tamanho máximo. O dono vê o caixa no painel
dele; não há como isso passar despercebido a noite inteira.

E o motivo de parar em 10 e não em 25 ou 50 é o **erro de digitação de uma
tecla**: −10 vira −100 com um dígito a mais, e −100% sangra quatro vezes mais
rápido. Com o limite em 10, esse erro bate no validador em vez de virar
evento. Simetria com o lado de cima é de brinde, não a razão.
"""

from decimal import Decimal

from .dinheiro import para_decimal
from .erros import ValorInvalido
from .modelos import config_texto, definir_config_texto, registrar_acao

#: Os jogos do Caladinho. O mines existe; os outros entram um a um, e cada um
#: já nasce com a vantagem editável porque o mecanismo é este, não um por jogo.
JOGOS = ("mines", "crash", "torre", "dados")

#: Onde o mines começou. Continua sendo o padrão de todo jogo que ainda não
#: teve a vantagem mexida.
PADRAO = Decimal("2.00")

#: Negativa é permitida, e é o evento generoso. O limite não existe para
#: impedir a casa de perder — existe para que um dígito a mais não multiplique
#: por quatro a velocidade com que ela perde.
MINIMA = Decimal("-10.00")
MAXIMA = Decimal("10.00")

#: Acima disto a tabela do mines mostra multiplicador abaixo de 1,00 na
#: primeira casa. Não é limite do código — é o aviso que o painel dá.
SEM_MULTIPLICADOR_ABAIXO_DE_UM = Decimal("4.00")

CEM = Decimal("100")


def chave_de(jogo):
    return f"caladinho_vantagem_{validar_jogo(jogo)}"


def validar_jogo(jogo):
    if jogo not in JOGOS:
        raise ValorInvalido(f"jogo desconhecido: {jogo!r}")
    return jogo


def validar_vantagem(valor):
    """Normaliza e confere a faixa. É a única porta de entrada de um número."""
    try:
        valor = para_decimal(str(valor).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("vantagem inválida") from erro
    if not MINIMA <= valor <= MAXIMA:
        raise ValorInvalido(f"a vantagem vai de {MINIMA}% a {MAXIMA}%")
    return valor


def vantagem(jogo, sessao=None):
    """A vantagem vigente do jogo, em pontos percentuais.

    Um valor gravado fora da faixa (edição direta no banco, ou uma faixa que
    encolheu depois) volta para o padrão em vez de derrubar o jogo: a tela do
    cassino não é lugar de erro 500, e a vantagem errada seria silenciosa.
    """
    cru = config_texto(chave_de(jogo), sessao=sessao)
    if cru is None:
        return PADRAO
    try:
        return validar_vantagem(cru)
    except ValorInvalido:
        return PADRAO


def fator_de(pontos_percentuais):
    """O que multiplica o multiplicador justo: ``(100 - vantagem) / 100``."""
    return (CEM - para_decimal(pontos_percentuais)) / CEM


def fator(jogo, sessao=None):
    """O fator vigente do jogo. Atalho de ``fator_de(vantagem(jogo))``."""
    return fator_de(vantagem(jogo, sessao=sessao))


def definir_vantagem(jogo, nova, ator, sessao=None):
    """Grava a vantagem e registra quem mudou, quando e de quanto para quanto.

    O registro não é enfeite: é o que defende o dono da acusação de ter mexido
    na vantagem para alguém perder. Sem ele, "o cassino mudou o pagamento no
    meio" é indefensável — com ele, a resposta é uma linha com hora e autor.

    Usa o mesmo diário do god mode (``RegistroAdministrativo``), que é onde já
    se olha quando alguém pergunta "quem mexeu nisto?".
    """
    jogo = validar_jogo(jogo)
    nova = validar_vantagem(nova)
    anterior = vantagem(jogo, sessao=sessao)

    definir_config_texto(chave_de(jogo), str(nova), sessao=sessao)
    registrar_acao(
        ator,
        "vantagem",
        alvo=jogo,
        detalhe=f"de {anterior}% para {nova}%",
        sessao=sessao,
    )
    return nova


def todas(sessao=None):
    """A vantagem vigente de cada jogo, para desenhar o painel do dono."""
    return {jogo: vantagem(jogo, sessao=sessao) for jogo in JOGOS}
