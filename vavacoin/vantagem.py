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

## A faixa, e de onde ela sai

Vantagem **negativa** faz o jogo pagar, na média, mais do que arrecada: a casa
quebra por aritmética, não por azar. Por isso o piso é zero — e zero é jogo
justo, que é um extremo legítimo (o dono pode querer uma noite sem vantagem).

O teto é 10%, e o número tem motivo. Com a tabela do mines, a vantagem entra
multiplicando o multiplicador justo, e o justo da primeira casa é baixo: com
1 mina, ``25/24 = 1,0416``. A partir de **5%** esse produto cai abaixo de
1,00 e a tela passa a mostrar que abrir uma casa e sacar devolve **menos** do
que a aposta::

    vantagem | 1 mina | 2 minas | 3 minas
        0%   |  1.04  |  1.08   |  1.13
        2%   |  1.02  |  1.06   |  1.11
        4%   |  1.00  |  1.04   |  1.09
        5%   |  0.98  |  1.03   |  1.07
       10%   |  0.93  |  0.97   |  1.02

Não é defeito — é a vantagem funcionando —, mas um multiplicador abaixo de
1,00 na tela parece a casa roubando, e essa é a acusação que este cassino não
pode receber. Daí a recomendação registrada: **até 4% a tabela nunca mostra
número menor que 1,00**; entre 4% e 10% o dono pode ir, sabendo o que aparece.
O código permite até 10% e recusa o resto; o bom senso entre 4 e 10 é dele.
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

MINIMA = Decimal("0.00")
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
        raise ValorInvalido(
            f"a vantagem vai de {MINIMA}% a {MAXIMA}%"
        )
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
