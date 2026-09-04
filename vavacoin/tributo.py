"""O imposto que o cassino paga a um reino sobre o lucro dos cidadãos dele.

O acordo é o Caladinho pagar uma fatia do lucro que tira **dos cidadãos
daquele reino** — não do lucro total. Por isso cada rodada carrega
``reino_id``, congelado no instante da aposta, e nulo é o "não cidadão".

## Por que congelado, e não calculado depois

Se a atribuição saísse da cidadania atual, alguém entrando ou saindo do reino
reescreveria imposto de rodada passada e a conta do mês mudaria sozinha. É o
mesmo motivo da vantagem congelada na aposta, e o mesmo risco: dois amigos
discutindo um número que se move quando ninguém está olhando.

## A conta

Num período ``[início, fim)``, para um reino::

    lucro       = Σ apostas − Σ prêmios   (rodadas encerradas atribuídas a ele)
    tributável  = max(0, lucro − abatimento acumulado)
    imposto     = tributável × alíquota

Somado das rodadas, nunca de um contador guardado — um contador à parte diverge
do que aconteceu e ninguém percebe até alguém conferir. É a mesma disciplina do
``lucro_do_dono``.

## Prejuízo: abatimento, não imposto negativo

Período em que o cassino perde para os cidadãos de um reino não gera imposto
negativo — o reino não devolve dinheiro. Mas também não pode ser simplesmente
zero e esquecido: se fosse zero nos períodos ruins e a alíquota cheia nos bons,
na média o cassino pagaria **mais** que a alíquota combinada.

Então o prejuízo vira **saldo a abater**, guardado por reino, e o próximo
período com lucro é tributado sobre ``lucro − saldo``. O saldo é consumido
conforme é usado, e o consumo fica na linha da liquidação — a pergunta "por que
o imposto desse período foi menor?" se responde olhando o registro.

Duas sutilezas que os testes guardam:

- o abatimento reduz o **lucro tributável**, não o imposto. Abater 100 com
  alíquota de 10% tira 10 do imposto, não 100;
- o saldo só é consumido **junto com a liquidação**, sob a mesma guarda de
  status que impede pagar duas vezes. Não existe estado em que o saldo foi
  consumido e o imposto não foi pago, nem o contrário.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo
from .erros import SemAutoridade, ValorInvalido
from .extensoes import db
from .modelos import (
    agora,
    com_fuso,
    LiquidacaoDeImposto,
    Reino,
    RodadaCrash,
    RodadaDados,
    RodadaMines,
    RodadaTorre,
    Usuario,
    registrar_acao,
)
from .moeda import mover

#: Imposto do cassino para o cofre de um reino. Tipo próprio para o extrato
#: dizer o que aconteceu, e para o lucro do dono não confundi-lo com aposta.
TIPO_IMPOSTO_CASSINO = "imposto_cassino"

#: O combinado de hoje é 10%. Vive aqui só como valor inicial da coluna de
#: cada reino — cada um negocia o seu, e mudar fica registrado.
ALIQUOTA_PADRAO = Decimal("10.00")
ALIQUOTA_MINIMA = Decimal("0.00")
#: Metade do lucro é o teto. Acima disso o cassino trabalharia para o reino, e
#: um acordo desses não é imposto, é sociedade — que seria outra feature.
ALIQUOTA_MAXIMA = Decimal("50.00")

CEM = Decimal("100")

#: As quatro rodadas e o nome da coluna que diz **quando a aposta foi feita**.
#: O crash chama a dela de ``iniciada_em`` porque é o instante zero da curva;
#: as outras, de ``criada_em``. O par explícito existe para essa diferença não
#: virar um ``AttributeError`` em produção.
#:
#: A lista é deliberadamente burra: quando entrar o quinto jogo, isto tem de
#: falhar em revisão de código, não depois.
RODADAS = (
    (RodadaMines, "criada_em"),
    (RodadaCrash, "iniciada_em"),
    (RodadaTorre, "criada_em"),
    (RodadaDados, "criada_em"),
)


def definir_aliquota(reino, nova, operador, sessao=None):
    """Muda a alíquota do reino, dentro da faixa, e registra quem mudou."""
    from .reinos import exigir_operador

    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)

    try:
        nova = para_decimal(str(nova).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("alíquota inválida") from erro
    if not ALIQUOTA_MINIMA <= nova <= ALIQUOTA_MAXIMA:
        raise ValorInvalido(
            f"a alíquota vai de {ALIQUOTA_MINIMA}% a {ALIQUOTA_MAXIMA}%"
        )

    anterior = reino.aliquota_cassino
    reino.aliquota_cassino = nova
    sessao.flush()
    registrar_acao(
        operador,
        "reino",
        alvo=reino.nome,
        detalhe=f"alíquota do cassino de {anterior}% para {nova}%",
        sessao=sessao,
    )
    return nova


def lucro_do_periodo(reino_id, inicio, fim, sessao=None):
    """Apostas menos prêmios das rodadas atribuídas, no período ``[início, fim)``.

    Só rodadas **encerradas**: uma rodada aberta ainda não tem resultado, e
    contá-la seria tributar dinheiro que talvez volte para o jogador.

    ``reino_id`` nulo devolve o lucro tirado de quem não é cidadão de reino
    nenhum — o outro lado da separação que o dono pediu.
    """
    sessao = sessao or db.session
    total = ZERO
    for modelo, coluna in RODADAS:
        quando = getattr(modelo, coluna)
        consulta = select(modelo).where(quando >= inicio, quando < fim)
        consulta = (
            consulta.where(modelo.reino_id.is_(None))
            if reino_id is None
            else consulta.where(modelo.reino_id == reino_id)
        )
        if hasattr(modelo, "ATIVA"):
            consulta = consulta.where(modelo.estado != modelo.ATIVA)
        for rodada in sessao.execute(consulta).scalars():
            total += rodada.aposta - rodada.premio
    return total


def previsao(reino, inicio, fim, sessao=None):
    """A conta do período, **sem** liquidar nada. É o que a tela mostra.

    Devolve os mesmos números que a liquidação vai gravar, para o dono ver
    antes de apertar o botão — e para conferir depois que o que foi pago é o
    que estava na tela.
    """
    sessao = sessao or db.session
    lucro = lucro_do_periodo(reino.id, inicio, fim, sessao)
    disponivel = reino.abatimento

    if lucro > ZERO:
        usado = disponivel if disponivel < lucro else lucro
        tributavel = lucro - usado
        novo_saldo = disponivel - usado
    else:
        # Prejuízo não gera imposto negativo: engorda o saldo a abater.
        usado = ZERO
        tributavel = ZERO
        novo_saldo = disponivel - lucro  # lucro <= 0, então soma o prejuízo

    imposto = quantizar_para_baixo(tributavel * reino.aliquota_cassino / CEM)
    return {
        # O período vai junto porque agora ele é de cada reino, e não da
        # tela: quem desenha a linha precisa dizer de quando ela fala.
        "inicio": inicio,
        "fim": fim,
        "lucro": lucro,
        "abatimento_disponivel": disponivel,
        "abatimento_usado": usado,
        "lucro_tributavel": tributavel,
        "aliquota": reino.aliquota_cassino,
        "imposto": imposto,
        "abatimento_depois": novo_saldo,
        "ja_liquidado": liquidacao_do_periodo(reino, inicio, fim, sessao) is not None,
    }


def ultima_liquidacao(reino, sessao=None):
    """A liquidação mais recente deste reino, pelo fim do período."""
    sessao = sessao or db.session
    return sessao.execute(
        select(LiquidacaoDeImposto)
        .where(LiquidacaoDeImposto.reino_id == reino.id)
        .order_by(LiquidacaoDeImposto.fim.desc())
        .limit(1)
    ).scalar_one_or_none()


def inicio_do_periodo(reino, sessao=None):
    """Onde o próximo período **tem de** começar. Não é sugestão.

    É o fim da última liquidação deste reino — e, se ele nunca foi liquidado,
    a criação do reino, porque antes disso não existe rodada atribuída a ele
    e não há nada para deixar de fora.

    Existe uma implementação só, e a tela chama esta: se a data viesse
    preenchida por um lado e conferida por outro, o dia em que as duas
    discordassem seria o dia em que o cassino paga duas vezes ou deixa de
    pagar. A tela sugere; :func:`liquidar` garante.
    """
    ultima = ultima_liquidacao(reino, sessao)
    if ultima is not None:
        return com_fuso(ultima.fim)
    return com_fuso(reino.criado_em)


def fim_efetivo(fim, momento=None):
    """O fim do período, nunca além de agora.

    **Aparar, e não recusar.** Um período que termina no futuro taxaria zero
    naquele trecho e, como o próximo começa onde este acaba, engoliria de
    véspera todo o lucro até lá — é o jeito de pular pedaço de propósito, e
    fecha o buraco espelhado do da sobreposição.

    Recusar também fecharia, mas "até agora" é o que a pessoa quis dizer em
    todo caso realista: a tela manda ``agora()`` e o relógio anda um pouco
    entre montar a página e apertar o botão. Aparar acerta a intenção sem
    transformar em erro o que é só latência. O valor aparado é o que fica
    gravado na linha e o que a tela mostra depois — não há divergência entre
    o que foi pedido e o que ficou registrado.
    """
    momento = momento or agora()
    fim = com_fuso(fim)
    return fim if fim < momento else momento


def _exigir_periodo_encaixado(reino, inicio, fim, sessao):
    """Os períodos de um reino são um mosaico: sem sobra e sem sobreposição.

    **Sobrepor cobra duas vezes.** Foi o bug: o padrão da tela era "últimos 30
    dias terminando agora", e o ``fim`` mudava a cada visita — então a segunda
    liquidação, feita do jeito natural, cobria de novo quase todo o período da
    primeira. Um lucro de 100,00 rendeu 20,00 de imposto numa alíquota de 10%,
    saindo do caixa do cassino. O ``UNIQUE (reino, início, fim)`` não pegava
    isso: ele só barra o intervalo **idêntico**.

    **Deixar vão nunca cobra.** É o erro espelhado, e é mais silencioso — o
    lucro do meio simplesmente não é de ninguém. Por isso o começo não é
    escolhido, e sim derivado de :func:`inicio_do_periodo`.

    As duas recusas juntas obrigam ``inicio == inicio_do_periodo(reino)``.
    Estão escritas separadas porque são diagnósticos diferentes, e quem
    recebe a mensagem precisa saber qual dos dois erros cometeu.

    O ``fim`` no futuro é aparado antes de chegar aqui, por
    :func:`fim_efetivo` — ver lá por que aparar e não recusar.
    """
    cruzada = sessao.execute(
        select(LiquidacaoDeImposto)
        .where(
            LiquidacaoDeImposto.reino_id == reino.id,
            LiquidacaoDeImposto.inicio < fim,
            LiquidacaoDeImposto.fim > inicio,
        )
        .limit(1)
    ).scalar_one_or_none()
    if cruzada is not None:
        raise ValorInvalido(
            "esse período cruza um já liquidado "
            f"({cruzada.inicio:%d/%m} a {cruzada.fim:%d/%m})"
        )

    esperado = inicio_do_periodo(reino, sessao)
    if inicio > esperado:
        raise ValorInvalido(
            f"o período tem de começar em {esperado:%d/%m %H:%M}; "
            "senão o lucro do meio não é cobrado de ninguém"
        )


def liquidacao_do_periodo(reino, inicio, fim, sessao=None):
    sessao = sessao or db.session
    return sessao.execute(
        select(LiquidacaoDeImposto).where(
            LiquidacaoDeImposto.reino_id == reino.id,
            LiquidacaoDeImposto.inicio == inicio,
            LiquidacaoDeImposto.fim == fim,
        )
    ).scalar_one_or_none()


def liquidar(reino, inicio, fim, quem, sessao=None):
    """Fecha o período: paga o imposto e consome o abatimento. **Uma vez só.**

    A linha da liquidação entra **antes** de o dinheiro sair, e
    ``(reino, início, fim)`` é UNIQUE: o segundo clique bate no índice e nada
    se move. É o que impede o mesmo lucro de ser cobrado de novo.

    O consumo do abatimento acontece na mesma transação do pagamento, sob a
    mesma guarda. Não existe estado em que o saldo foi consumido e o imposto
    não foi pago, nem o contrário.

    Quem aperta é o **dono do cassino** — é o caixa dele que paga. O reino
    recebe; não é ele quem cobra à força.
    """
    from .caladinho import dono as dono_do_cassino
    from .caladinho import exigir_casa

    sessao = sessao or db.session

    inicio = com_fuso(inicio)
    fim = fim_efetivo(fim)
    if fim <= inicio:
        raise ValorInvalido("o período precisa terminar depois de começar")
    # A tela preenche as datas; quem garante é aqui. Um `inicio` digitado na
    # barra de endereço encontra a mesma recusa que a tela evita.
    _exigir_periodo_encaixado(reino, inicio, fim, sessao)

    conta_da_casa = exigir_casa(sessao, travada=True)
    dono = dono_do_cassino(sessao)
    if dono is None or quem is None or quem.id != dono.id:
        raise SemAutoridade("só o dono do cassino liquida o imposto")

    conta = previsao(reino, inicio, fim, sessao)

    linha = LiquidacaoDeImposto(
        reino_id=reino.id,
        inicio=inicio,
        fim=fim,
        lucro_bruto=conta["lucro"],
        abatimento_usado=conta["abatimento_usado"],
        lucro_tributavel=conta["lucro_tributavel"],
        aliquota=conta["aliquota"],
        imposto=conta["imposto"],
        liquidado_por_id=quem.id,
    )
    sessao.add(linha)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("esse período já foi liquidado") from erro

    imposto = conta["imposto"]
    if imposto > ZERO:
        if conta_da_casa.saldo < imposto:
            raise ValorInvalido(
                f"o caixa tem {conta_da_casa.saldo} VVC e o imposto é {imposto} VVC"
            )
        cofre = sessao.get(Usuario, reino.cofre_id)
        transacao = mover(
            conta_da_casa,
            cofre,
            imposto,
            tipo=TIPO_IMPOSTO_CASSINO,
            motivo=f"{reino.nome}: imposto do cassino",
            ator=quem,
            sessao=sessao,
        )
        linha.transacao_id = transacao.id

    # O saldo a abater anda junto com o pagamento, sob a mesma guarda.
    reino.abatimento = conta["abatimento_depois"]
    sessao.flush()

    registrar_acao(
        quem,
        "reino",
        alvo=reino.nome,
        detalhe=(
            f"imposto do cassino: lucro {conta['lucro']}, "
            f"abatimento usado {conta['abatimento_usado']}, "
            f"tributável {conta['lucro_tributavel']} × {conta['aliquota']}% "
            f"= {imposto}; saldo a abater agora {conta['abatimento_depois']}"
        ),
        sessao=sessao,
    )
    return linha


def panorama(inicio, fim, sessao=None):
    """Lucro e imposto de cada reino, mais o de fora de reino.

    É o que a tela do dono desenha: de quem veio cada centavo, e quanto disso
    vira imposto de quem.

    O ``inicio`` recebido vale **só** para o "fora de reino", que é número de
    conferência e não é cobrado de ninguém. O período de cada reino sai de
    :func:`inicio_do_periodo` — é do reino, não da tela.
    """
    sessao = sessao or db.session
    linhas = []
    for reino in sessao.execute(select(Reino).order_by(Reino.nome)).scalars():
        # Cada reino começa onde a própria última liquidação terminou. Uma
        # janela comum a todos não existe: dois reinos liquidados em dias
        # diferentes não têm o mesmo ponto de partida, e forçar um só faria o
        # segundo cobrar duas vezes ou pular pedaço.
        linhas.append(
            (reino, previsao(reino, inicio_do_periodo(reino, sessao), fim, sessao))
        )
    return {
        "reinos": linhas,
        # Nulo é o "não cidadão": lucro que não deve imposto a ninguém.
        "fora_de_reino": lucro_do_periodo(None, inicio, fim, sessao),
    }
