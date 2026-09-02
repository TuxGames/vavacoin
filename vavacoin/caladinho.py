"""O Caladinho: o cassino, onde as regras do mines encostam no dinheiro.

A matemática é pura e mora em :mod:`vavacoin.mines`. Aqui é o ledger — e por
isso **a aposta é um lançamento e o prêmio é outro**, os dois por ``mover()``.
O cassino não cria nem destrói VavaCoin: a conservação de massa continua
valendo durante o jogo inteiro, com rodada ganha, perdida ou abandonada no
meio.

O caixa da casa é o saldo de uma conta de verdade, lido no momento da aposta.
No cassino original ele vinha de um valor sincronizado de fora, porque a casa
morava noutro sistema; aqui não existe "fora" — a conta está no mesmo banco, e
copiar a indireção só criaria um número que pode divergir do saldo real.

Três coisas que este módulo não deixa acontecer:

1. **Cobrar sem poder pagar.** O teto de banca é conferido antes de o dinheiro
   sair do jogador, contra o caixa daquele instante e já descontando o que as
   rodadas ativas comprometeram.
2. **Resolver duas vezes.** Toda transição é ``UPDATE ... WHERE estado =
   'ativa'`` com checagem de linha afetada. Clique duplo, recarregar a página
   e conexão que cai chegam todos ao mesmo lugar.
3. **Decidir no navegador.** O cliente informa a casa clicada; as minas ficam
   no servidor e só aparecem quando a rodada encerra.
4. **Mudar a tabela com a rodada aberta.** A vantagem da casa é editável pelo
   dono, e a rodada guarda a que valia no instante da aposta. Quem começou a
   jogar com 2% termina com 2%, mesmo que o dono suba para 8% no meio — senão
   daria para baixar o pagamento de alguém que já está jogando, que é a
   acusação que este cassino não pode receber.
"""

import secrets
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .constantes import USUARIO_CASSINO
from .dinheiro import ZERO, para_decimal, quantizar_para_baixo
from .erros import (
    ApostaAlta,
    CaixaComprometido,
    CasaIndisponivel,
    RodadaEmAndamento,
    SaldoInsuficiente,
    SemAutoridade,
    SemRodadaAtiva,
    ValorInvalido,
)
from .extensoes import db
from .crash import (
    MULTIPLICADOR_INICIAL as MULTIPLICADOR_INICIAL_CRASH,
    TETO_DO_MULTIPLICADOR as TETO_CRASH,
    ganhou as crash_ganhou,
    multiplicador_no_tempo,
    premio_maximo as premio_maximo_crash,
    segundos_para_multiplicador,
    sortear_ponto_de_estouro,
    validar_alvo,
)
from .dados import (
    ganhou as ganhou_nos_dados,
    multiplicador_pagavel as multiplicador_pagavel_dados,
    rolar,
    validar_alvo as validar_alvo_dos_dados,
    validar_sentido,
)
from .torre import (
    altura as altura_da_torre,
    bateu_o_teto as bateu_o_teto_torre,
    multiplicador as multiplicador_torre,
    multiplicador_pagavel as multiplicador_pagavel_torre,
    premio_maximo as premio_maximo_torre,
    tabela_de_multiplicadores as tabela_da_torre,
    validar_portas,
)
from .mines import (
    CASAS,
    aposta_maxima,
    bateu_o_teto,
    multiplicador,
    multiplicador_pagavel,
    premio_maximo,
    validar_minas,
)
from .modelos import (
    RodadaCrash,
    RodadaDados,
    RodadaMines,
    RodadaTorre,
    Transacao,
    Usuario,
    agora,
)
from .moeda import mover
from .vantagem import fator_de, vantagem as vantagem_vigente

TIPO_APOSTA = "aposta_mines"
TIPO_PREMIO = "premio_mines"

TIPO_APOSTA_CRASH = "aposta_crash"
TIPO_PREMIO_CRASH = "premio_crash"

TIPO_APOSTA_TORRE = "aposta_torre"
TIPO_PREMIO_TORRE = "premio_torre"

TIPO_APOSTA_DADOS = "aposta_dados"
TIPO_PREMIO_DADOS = "premio_dados"

#: Tudo que é aposta e tudo que é prêmio, de qualquer jogo. O lucro do dono
#: soma por aqui — assim um jogo novo entra na conta ao ser acrescentado nesta
#: tupla, e não ao alguém lembrar de mexer no ``lucro_do_dono``.
TIPOS_DE_APOSTA = (
    TIPO_APOSTA,
    TIPO_APOSTA_CRASH,
    TIPO_APOSTA_TORRE,
    TIPO_APOSTA_DADOS,
)
TIPOS_DE_PREMIO = (
    TIPO_PREMIO,
    TIPO_PREMIO_CRASH,
    TIPO_PREMIO_TORRE,
    TIPO_PREMIO_DADOS,
)

#: Dinheiro do dono entrando e saindo da casa. É capital, não lucro — por
#: isso não entra na conta do :func:`lucro_do_dono`.
TIPO_APORTE = "aporte_caladinho"
TIPO_RETIRADA = "retirada_caladinho"


# --- a casa -----------------------------------------------------------------


def casa(sessao=None, travada=False):
    """A conta da casa, ou ``None`` se o cassino ainda não foi criado."""
    sessao = sessao or db.session
    consulta = select(Usuario).where(Usuario.eh_cassino.is_(True))
    if travada:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalar_one_or_none()


def exigir_casa(sessao=None, travada=False):
    conta = casa(sessao, travada=travada)
    if conta is None:
        raise CasaIndisponivel(
            "a casa do Caladinho ainda não existe; rode `flask criar-cassino`"
        )
    return conta


def criar_casa(autoridade=None, sessao=None):
    """Cria a conta da casa. Poder do Banco Central. Idempotente."""
    from .autoridade import exigir_banco_central
    from .modelos import registrar_acao

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)

    existente = casa(sessao)
    if existente is not None:
        return existente

    conta = Usuario(nome_exibicao="Caladinho", eh_cassino=True, saldo=ZERO)
    conta.definir_nome(USUARIO_CASSINO)
    sessao.add(conta)
    sessao.flush()
    registrar_acao(bc, "cassino", alvo=conta.nome_usuario, sessao=sessao)
    return conta


# --- posse ------------------------------------------------------------------


def dono(sessao=None):
    """A conta de quem é a casa, ou ``None`` se ninguém assumiu."""
    sessao = sessao or db.session
    conta = casa(sessao)
    if conta is None or conta.dono_id is None:
        return None
    return sessao.get(Usuario, conta.dono_id)


def definir_dono(alvo, autoridade=None, sessao=None):
    """Aponta o dono da casa. Poder do Banco Central.

    Marca também **desde quando**, que é a data de onde o lucro passa a ser
    somado. Trocar de dono é só chamar de novo: a posse é fixa por decisão do
    projeto, não por o código não saber fazer outra coisa.
    """
    from .autoridade import exigir_banco_central
    from .modelos import registrar_acao

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    conta = exigir_casa(sessao)

    if alvo is not None and alvo.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não é dona do cassino")

    conta.dono_id = alvo.id if alvo is not None else None
    conta.dono_desde = agora() if alvo is not None else None
    sessao.flush()
    registrar_acao(
        bc,
        "cassino",
        alvo=alvo.nome_usuario if alvo is not None else None,
        detalhe="dono do Caladinho",
        sessao=sessao,
    )
    return conta


def lucro_do_dono(sessao=None):
    """Quanto a casa ganhou desde que o dono assumiu.

    Apostas recebidas menos prêmios pagos, somados do ledger a partir de
    ``dono_desde``. Aporte e retirada ficam de fora de propósito: mexer no
    próprio caixa não é lucro nem prejuízo.

    Somado do ledger, e não guardado: um contador à parte diverge do que de
    fato aconteceu, e ninguém percebe até alguém conferir.
    """
    sessao = sessao or db.session
    conta = casa(sessao)
    if conta is None or conta.dono_desde is None:
        return ZERO

    total = ZERO
    for transacao in sessao.execute(
        select(Transacao).where(
            Transacao.criado_em >= conta.dono_desde,
            Transacao.tipo.in_(TIPOS_DE_APOSTA + TIPOS_DE_PREMIO),
        )
    ).scalars():
        if transacao.destino_id == conta.id:
            total += transacao.valor
        elif transacao.origem_id == conta.id:
            total -= transacao.valor
    return total


def livre_para_retirar(sessao=None):
    """Quanto do caixa o dono pode tirar sem deixar rodada aberta a descoberto.

    Caixa menos o comprometido. Se há rodada aberta que pode pagar 500, esses
    500 estão presos — senão o dono esvazia a casa no meio de uma jogada e o
    jogador não recebe ao sacar.
    """
    sessao = sessao or db.session
    conta = casa(sessao)
    if conta is None:
        return ZERO
    livre = conta.saldo - exposicao_comprometida(sessao)
    return livre if livre > ZERO else ZERO


def aportar(pessoa, valor, sessao=None):
    """Põe dinheiro do dono na casa. Aparece no extrato dos dois lados."""
    sessao = sessao or db.session
    conta = exigir_casa(sessao)
    if dono(sessao) is None or pessoa.id != conta.dono_id:
        raise SemAutoridade("só o dono aporta no caixa")

    return mover(
        pessoa,
        conta,
        valor,
        tipo=TIPO_APORTE,
        motivo="aporte do dono",
        sessao=sessao,
    )


def retirar_do_caixa(pessoa, valor, sessao=None):
    """Tira dinheiro da casa para o dono, até o que estiver livre.

    O limite é conferido **antes** de mover: o comprometido pelas rodadas
    abertas fica preso, e é isso que garante que quem está jogando recebe se
    ganhar.
    """
    sessao = sessao or db.session
    conta = exigir_casa(sessao, travada=True)
    if dono(sessao) is None or pessoa.id != conta.dono_id:
        raise SemAutoridade("só o dono retira do caixa")

    try:
        valor = para_decimal(valor)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if valor <= ZERO:
        raise ValorInvalido("o valor precisa ser maior que zero")

    livre = livre_para_retirar(sessao)
    if valor > livre:
        raise CaixaComprometido(
            f"livre para retirar agora: {livre} VVC "
            f"(o resto está preso em rodada aberta)"
        )

    return mover(
        conta,
        pessoa,
        valor,
        tipo=TIPO_RETIRADA,
        motivo="retirada do dono",
        sessao=sessao,
    )


# --- teto de banca ----------------------------------------------------------


def exposicao_comprometida(sessao=None):
    """O prêmio máximo somado das rodadas ainda ativas, de TODOS os jogos.

    Sem descontar isto, dez jogadores apostam ao mesmo tempo, cada aposta
    passa sozinha no teto, e juntas passam do que a casa tem.

    Somar só um jogo teria o mesmo efeito de não somar nada: bastaria abrir a
    rodada cara no jogo que ficou de fora. Todo jogo novo entra aqui no mesmo
    dia em que passa a aceitar aposta.
    """
    sessao = sessao or db.session
    total = ZERO
    for aposta in sessao.execute(
        select(RodadaMines.aposta).where(RodadaMines.estado == RodadaMines.ATIVA)
    ).scalars():
        total += premio_maximo(aposta)
    for aposta in sessao.execute(
        select(RodadaCrash.aposta).where(RodadaCrash.estado == RodadaCrash.ATIVA)
    ).scalars():
        total += premio_maximo_crash(aposta)
    for aposta in sessao.execute(
        select(RodadaTorre.aposta).where(RodadaTorre.estado == RodadaTorre.ATIVA)
    ).scalars():
        total += premio_maximo_torre(aposta)
    return total


def limite_de_aposta(sessao=None):
    """Maior aposta aceita agora. Não depende do número de minas."""
    sessao = sessao or db.session
    conta = casa(sessao)
    if conta is None:
        return ZERO
    return aposta_maxima(conta.saldo, comprometido=exposicao_comprometida(sessao))


# --- rodada -----------------------------------------------------------------


def rodada_ativa(jogador, sessao=None, travada=False):
    """A rodada em andamento do jogador, se houver.

    É o que faz recarregar a página retomar a mesma rodada em vez de começar
    outra: a tela lê daqui, e o GET nunca cria nada.
    """
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    consulta = (
        select(RodadaMines)
        .where(
            RodadaMines.jogador_id == jogador_id,
            RodadaMines.estado == RodadaMines.ATIVA,
        )
        .order_by(RodadaMines.id.desc())
    )
    if travada:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalars().first()


def ultima_rodada(jogador, sessao=None):
    """A rodada encerrada mais recente do jogador, se houver.

    Existe porque o resultado não pode viver só no ``?rodada=`` que o redirect
    do clique carrega: reenvio do POST, recarregar a página e voltar ao jogo
    chegam sem esse parâmetro, e sem um lugar de onde reler o resultado a tela
    mostrava tabuleiro fechado como se nada tivesse acontecido.
    """
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    return (
        sessao.execute(
            select(RodadaMines)
            .where(
                RodadaMines.jogador_id == jogador_id,
                RodadaMines.estado != RodadaMines.ATIVA,
            )
            .order_by(RodadaMines.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def criar_rodada(jogador, aposta, minas_escolhidas, sessao=None):
    """Começa a rodada e cobra a aposta.

    A ordem importa: tudo que pode recusar é conferido **antes** de o dinheiro
    sair. As minas são sorteadas com ``secrets`` no servidor e ficam guardadas
    na rodada; não vão para o cliente enquanto ela viver.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao)

    if jogador.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não joga")

    try:
        minas_escolhidas = validar_minas(minas_escolhidas)
    except ValueError as erro:
        raise ValorInvalido(str(erro)) from erro

    try:
        aposta = para_decimal(aposta)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if aposta <= ZERO:
        raise ValorInvalido("a aposta precisa ser maior que zero")

    # Varre as abandonadas ANTES de medir a exposição: é aqui que o caixa
    # preso por rodada esquecida faria diferença, recusando aposta boa.
    expirar_mines_abandonadas(sessao)

    # Trava a linha do jogador: serializa a criação de rodada e impede que
    # duas requisições simultâneas criem duas.
    travado = sessao.execute(
        select(Usuario).where(Usuario.id == jogador.id).with_for_update()
    ).scalar_one()

    if rodada_ativa(jogador, sessao) is not None:
        raise RodadaEmAndamento("você já tem uma rodada em andamento")

    if travado.saldo < aposta:
        raise ValorInvalido(f"você tem {travado.saldo} VVC")

    # Teto de banca, com o caixa real e descontando as rodadas ativas.
    maximo = limite_de_aposta(sessao)
    if aposta > maximo:
        raise ApostaAlta(f"aposta máxima para o caixa de agora: {maximo} VVC")

    minas = sorted(secrets.SystemRandom().sample(range(CASAS), minas_escolhidas))
    rodada = RodadaMines(
        jogador_id=jogador.id,
        aposta=aposta,
        # A vantagem vigente AGORA, congelada na rodada. A partir daqui o dono
        # pode mudá-la à vontade que esta rodada não sente.
        vantagem=vantagem_vigente("mines", sessao),
        minas_escolhidas=minas_escolhidas,
        minas=",".join(str(m) for m in minas),
        reveladas="",
        estado=RodadaMines.ATIVA,
        multiplicador=para_decimal("1.00"),
        premio=ZERO,
    )
    sessao.add(rodada)
    try:
        sessao.flush()
    except IntegrityError as erro:
        # O índice parcial barrou uma segunda rodada ativa. Nada foi cobrado.
        sessao.rollback()
        raise RodadaEmAndamento("você já tem uma rodada em andamento") from erro

    transacao = mover(
        jogador,
        conta_da_casa,
        aposta,
        tipo=TIPO_APOSTA,
        motivo=f"mines #{rodada.id}",
        sessao=sessao,
    )
    rodada.transacao_aposta_id = transacao.id
    sessao.flush()
    return rodada


def revelar_casa(jogador, posicao, sessao=None):
    """Abre uma casa. O servidor decide o que havia nela.

    Casa já aberta é no-op, como no original: recarregar a página depois de um
    clique não muda nada.
    """
    sessao = sessao or db.session

    try:
        posicao = int(posicao)
    except (TypeError, ValueError):
        raise ValorInvalido("casa inválida")
    if not 0 <= posicao < CASAS:
        raise ValorInvalido("casa fora do tabuleiro")

    rodada = rodada_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento")

    reveladas = rodada.casas_reveladas
    if posicao in reveladas:
        return rodada

    if posicao in rodada.casas_com_mina:
        return _estourar(rodada, posicao, sessao)

    reveladas.append(posicao)
    fator_da_rodada = fator_de(rodada.vantagem)
    fator = multiplicador(rodada.minas_escolhidas, len(reveladas), fator_da_rodada)
    aplicado = sessao.execute(
        update(RodadaMines)
        .where(
            RodadaMines.id == rodada.id,
            RodadaMines.estado == RodadaMines.ATIVA,
            RodadaMines.reveladas == rodada.reveladas,
        )
        .values(
            reveladas=",".join(str(c) for c in reveladas),
            multiplicador=fator,
            mexida_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("a rodada mudou; recarregue a página")
    sessao.expire(rodada)

    # Ao bater o teto não há mais o que ganhar abrindo casa: a rodada encerra
    # e paga sozinha. Deixá-la aberta seria oferecer risco sem prêmio.
    if bateu_o_teto(rodada.minas_escolhidas, len(reveladas), fator_da_rodada):
        return retirar(jogador, sessao=sessao)

    return rodada


def _estourar(rodada, posicao, sessao):
    """Achou mina. A aposta já está com a casa; perder é fechar a rodada."""
    aplicado = sessao.execute(
        update(RodadaMines)
        .where(RodadaMines.id == rodada.id, RodadaMines.estado == RodadaMines.ATIVA)
        .values(
            estado=RodadaMines.ESTOURADA,
            casa_estourada=posicao,
            premio=ZERO,
            mexida_em=agora(),
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)
    return rodada


def retirar(jogador, sessao=None, rodada=None, exigir_casa_aberta=True):
    """Encerra ganhando e paga o prêmio. Idempotente.

    O estado vira ``retirada`` **antes** do pagamento, por ``UPDATE``
    condicional: se duas requisições chegarem juntas, só uma passa da trava, e
    só ela paga.

    ``exigir_casa_aberta`` é a regra do jogo: ninguém saca sem ter arriscado
    nada. A expiração passa ``False`` porque ali não há saque — há devolução,
    e devolver a aposta de quem não chegou a jogar é o resultado certo.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao, travada=True)

    if rodada is None:
        rodada = rodada_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento para retirar")

    abertas = len(rodada.casas_reveladas)
    if exigir_casa_aberta and abertas < 1:
        raise ValorInvalido("revele ao menos uma casa antes de retirar")

    fator = multiplicador_pagavel(
        rodada.minas_escolhidas, abertas, fator_de(rodada.vantagem)
    )
    premio = quantizar_para_baixo(rodada.aposta * fator)

    aplicado = sessao.execute(
        update(RodadaMines)
        .where(RodadaMines.id == rodada.id, RodadaMines.estado == RodadaMines.ATIVA)
        .values(
            estado=RodadaMines.RETIRADA,
            premio=premio,
            mexida_em=agora(),
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)

    if premio > ZERO:
        transacao = mover(
            conta_da_casa,
            rodada.jogador_id,
            premio,
            tipo=TIPO_PREMIO,
            motivo=f"mines #{rodada.id} · {fator}x",
            sessao=sessao,
        )
        rodada.transacao_premio_id = transacao.id
        sessao.flush()
    return rodada


#: Quanto tempo uma rodada de mines sobrevive sem ninguém mexer. O mesmo
#: prazo da torre: são o mesmo problema.
VALIDADE_DO_MINES = timedelta(minutes=30)


def expirar_mines_abandonadas(sessao=None, momento=None):
    """Fecha as rodadas de mines esquecidas, pagando o já conquistado.

    O mesmo conserto que a torre já nasceu tendo, agora aplicado ao jogo que
    está no ar. Rodada aberta segura ``premio_maximo`` em
    :func:`exposicao_comprometida`; quem fecha a aba congela esse pedaço do
    caixa para sempre, e um punhado de abas fechadas passa a recusar aposta de
    quem quer jogar — sem que ninguém esteja jogando.

    Paga o multiplicador já conquistado. Com zero casas abertas isso é 1,00×,
    ou seja, a aposta de volta: quem não chegou a arriscar nada não perde
    nada. A rodada aparece como ``retirada``, porque foi o que aconteceu.
    """
    sessao = sessao or db.session
    limite = (momento or agora()) - VALIDADE_DO_MINES

    abandonadas = list(
        sessao.execute(
            select(RodadaMines)
            .where(
                RodadaMines.estado == RodadaMines.ATIVA,
                RodadaMines.mexida_em < limite,
            )
            .with_for_update()
        ).scalars()
    )
    for rodada in abandonadas:
        try:
            retirar(
                rodada.jogador_id,
                sessao=sessao,
                rodada=rodada,
                exigir_casa_aberta=False,
            )
        except (SemRodadaAtiva, CaixaComprometido, SaldoInsuficiente):
            # Outra requisição chegou primeiro, ou a casa não cobre agora.
            # Nenhum dos dois é motivo para derrubar quem só passava por aqui.
            continue
    return abandonadas


def historico(jogador, limite=15, sessao=None):
    """Rodadas encerradas do jogador, da mais recente para a mais antiga."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(RodadaMines)
            .where(
                RodadaMines.jogador_id == jogador.id,
                RodadaMines.estado != RodadaMines.ATIVA,
            )
            .order_by(RodadaMines.id.desc())
            .limit(limite)
        ).scalars()
    )


def visao_da_rodada(rodada):
    """O que a tela pode mostrar.

    Enquanto a rodada vive, ``minas`` é ``None``: o tabuleiro só aparece
    quando ela encerra. É a projeção segura do original, e é o ponto em que um
    descuido entregaria o jogo.
    """
    if rodada is None:
        return None
    abertas = len(rodada.casas_reveladas)
    fator = (
        multiplicador_pagavel(
            rodada.minas_escolhidas, abertas, fator_de(rodada.vantagem)
        )
        if abertas
        else ZERO
    )
    return {
        "id": rodada.id,
        # A vantagem desta rodada, não a de agora: é o que a tela deve mostrar
        # para quem está no meio do jogo.
        "vantagem": rodada.vantagem,
        "estado": rodada.estado,
        "encerrada": rodada.encerrada,
        "aposta": rodada.aposta,
        "minas_escolhidas": rodada.minas_escolhidas,
        "reveladas": rodada.casas_reveladas,
        "multiplicador": fator,
        "premio_atual": quantizar_para_baixo(rodada.aposta * fator),
        "premio": rodada.premio,
        "minas": rodada.casas_com_mina if rodada.encerrada else None,
        "casa_estourada": rodada.casa_estourada,
    }


# --- crash ------------------------------------------------------------------
#
# O crash não tem tabuleiro: o que ele guarda de segredo é o ponto de estouro,
# sorteado na aposta. E não tem "clique que revela": o resultado já está
# decidido quando a rodada nasce, e o que o tempo faz é só chegar nele.
#
# Por isso a liquidação aqui é **preguiçosa**: quem lê a página aplica o
# desfecho de uma rodada cujo prazo venceu. Não é sortear no GET — é aplicar
# uma decisão de antes, e aplicar tarde dá o mesmo resultado que aplicar na
# hora. Sem isso, fechar a aba deixaria a rodada aberta para sempre, prendendo
# o caixa da casa em `exposicao_comprometida`.


def rodada_crash_ativa(jogador, sessao=None, travada=False):
    """A rodada de crash em andamento do jogador, se houver."""
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    consulta = (
        select(RodadaCrash)
        .where(
            RodadaCrash.jogador_id == jogador_id,
            RodadaCrash.estado == RodadaCrash.ATIVA,
        )
        .order_by(RodadaCrash.id.desc())
    )
    if travada:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalars().first()


def ultima_rodada_crash(jogador, sessao=None):
    """A rodada de crash encerrada mais recente, para a tela ter o que mostrar.

    Mesma lição do mines: resultado que só existe no parâmetro do redirect não
    sobrevive a rede ruim.
    """
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    return (
        sessao.execute(
            select(RodadaCrash)
            .where(
                RodadaCrash.jogador_id == jogador_id,
                RodadaCrash.estado != RodadaCrash.ATIVA,
            )
            .order_by(RodadaCrash.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def segundos_decorridos(rodada, momento=None):
    """Há quanto tempo a rodada começou, pelo relógio do servidor."""
    momento = momento or agora()
    inicio = rodada.iniciada_em
    if inicio.tzinfo is None:
        # O SQLite devolve datetime ingênuo; o relógio do projeto é UTC.
        inicio = inicio.replace(tzinfo=timezone.utc)
    return Decimal(str((momento - inicio).total_seconds()))


def criar_rodada_crash(jogador, aposta, alvo, sessao=None):
    """Começa a rodada, sorteia o estouro e cobra a aposta.

    Mesma ordem do mines: tudo que pode recusar é conferido **antes** de o
    dinheiro sair. O ponto de estouro é sorteado aqui, com ``secrets``, e não
    vai para o cliente enquanto a rodada viver.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao)

    if jogador.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não joga")

    try:
        alvo = validar_alvo(alvo)
    except ValueError as erro:
        raise ValorInvalido(str(erro)) from erro

    try:
        aposta = para_decimal(aposta)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if aposta <= ZERO:
        raise ValorInvalido("a aposta precisa ser maior que zero")

    travado = sessao.execute(
        select(Usuario).where(Usuario.id == jogador.id).with_for_update()
    ).scalar_one()

    if rodada_crash_ativa(jogador, sessao) is not None:
        raise RodadaEmAndamento("você já tem uma rodada em andamento")

    if travado.saldo < aposta:
        raise ValorInvalido(f"você tem {travado.saldo} VVC")

    maximo = limite_de_aposta(sessao)
    if aposta > maximo:
        raise ApostaAlta(f"aposta máxima para o caixa de agora: {maximo} VVC")

    vantagem_da_rodada = vantagem_vigente("crash", sessao)
    rodada = RodadaCrash(
        jogador_id=jogador.id,
        aposta=aposta,
        vantagem=vantagem_da_rodada,
        alvo=alvo,
        ponto_de_estouro=sortear_ponto_de_estouro(
            fator_de(vantagem_da_rodada), secrets.SystemRandom()
        ),
        estado=RodadaCrash.ATIVA,
        multiplicador=ZERO,
        premio=ZERO,
        iniciada_em=agora(),
    )
    sessao.add(rodada)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise RodadaEmAndamento("você já tem uma rodada em andamento") from erro

    transacao = mover(
        jogador,
        conta_da_casa,
        aposta,
        tipo=TIPO_APOSTA_CRASH,
        motivo=f"crash #{rodada.id}",
        sessao=sessao,
    )
    rodada.transacao_aposta_id = transacao.id
    sessao.flush()
    return rodada


def _encerrar_crash(rodada, estado, multiplicador, premio, sessao):
    """Fecha a rodada e paga, se houver o que pagar. Idempotente.

    O estado vira o final **antes** do pagamento, por ``UPDATE`` condicional:
    duas requisições juntas, só uma passa da trava, e só ela paga.

    ``multiplicador`` e ``premio`` vêm SEPARADOS de propósito. Na rodada
    perdida o multiplicador guardado é o ponto de estouro — é o número que a
    tela mostra e que responde "onde foi que estourou?" —, mas o prêmio é
    zero. Enquanto o prêmio era derivado do multiplicador, a rodada estourada
    pagava como se tivesse ganhado.
    """

    aplicado = sessao.execute(
        update(RodadaCrash)
        .where(RodadaCrash.id == rodada.id, RodadaCrash.estado == RodadaCrash.ATIVA)
        .values(
            estado=estado,
            multiplicador=multiplicador,
            premio=premio,
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)

    if premio > ZERO:
        # O teto de banca de novo, agora contra o caixa DESTE instante: entre a
        # aposta e o pagamento pode ter entrado outra rodada.
        conta_da_casa = exigir_casa(sessao, travada=True)
        if conta_da_casa.saldo < premio:
            raise CaixaComprometido(
                f"a casa não tem {premio} VVC agora; procure o dono do cassino"
            )
        transacao = mover(
            conta_da_casa,
            rodada.jogador_id,
            premio,
            tipo=TIPO_PREMIO_CRASH,
            motivo=f"crash #{rodada.id} · {multiplicador}x",
            sessao=sessao,
        )
        rodada.transacao_premio_id = transacao.id
        sessao.flush()
    return rodada


def resolver_crash(jogador, sessao=None, momento=None):
    """Aplica o desfecho de uma rodada cujo prazo já venceu. Não sorteia nada.

    Chamada na leitura da página. Se a rodada ainda não chegou nem ao alvo nem
    ao estouro, não faz nada e devolve a rodada como está.
    """
    sessao = sessao or db.session
    rodada = rodada_crash_ativa(jogador, sessao, travada=True)
    if rodada is None:
        return None

    decisivo = min(rodada.alvo, rodada.ponto_de_estouro)
    if segundos_decorridos(rodada, momento) < segundos_para_multiplicador(decisivo):
        return rodada

    if crash_ganhou(rodada.alvo, rodada.ponto_de_estouro):
        return _encerrar_crash(
            rodada,
            RodadaCrash.RETIRADA,
            rodada.alvo,
            quantizar_para_baixo(rodada.aposta * rodada.alvo),
            sessao,
        )
    # Perdeu: o multiplicador guardado é onde estourou, e o prêmio é zero.
    return _encerrar_crash(
        rodada, RodadaCrash.ESTOURADA, rodada.ponto_de_estouro, ZERO, sessao
    )


def sacar_crash(jogador, sessao=None, momento=None):
    """Saque manual: sair ANTES do alvo, pelo número de agora.

    Validado contra o relógio do servidor, nunca contra o do navegador. O
    cliente só anima; quem diz onde a curva está é este método.

    Três desfechos, e nenhum depende de o POST chegar rápido:

    - a curva já passou do alvo (ou do estouro) → a rodada é resolvida pelo
      que estava decidido desde a aposta, e o clique não muda nada;
    - a curva está no meio → paga pelo multiplicador de agora, que é menor que
      o alvo.

    É este desenho que tira a rede do caminho: quem declarou alvo e não clica
    tem risco zero, e quem clica no braço só pode ganhar menos do que o alvo,
    nunca perder a rodada por causa de 250 ms de atraso.
    """
    sessao = sessao or db.session
    rodada = rodada_crash_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento")

    decorridos = segundos_decorridos(rodada, momento)
    decisivo = min(rodada.alvo, rodada.ponto_de_estouro)

    # O prazo da rodada venceu: o desfecho é o de sempre, e o clique chegou
    # tarde demais para mudar alguma coisa. Não é punição — é o alvo (ou o
    # estouro) tendo acontecido primeiro.
    if decorridos >= segundos_para_multiplicador(decisivo):
        return resolver_crash(jogador, sessao=sessao, momento=momento)

    agora_na_curva = multiplicador_no_tempo(decorridos)
    if agora_na_curva < MULTIPLICADOR_INICIAL_CRASH:
        agora_na_curva = MULTIPLICADOR_INICIAL_CRASH
    return _encerrar_crash(
        rodada,
        RodadaCrash.RETIRADA,
        agora_na_curva,
        quantizar_para_baixo(rodada.aposta * agora_na_curva),
        sessao,
    )


def historico_crash(jogador, limite=15, sessao=None):
    """Rodadas de crash encerradas, da mais recente para a mais antiga."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(RodadaCrash)
            .where(
                RodadaCrash.jogador_id == jogador.id,
                RodadaCrash.estado != RodadaCrash.ATIVA,
            )
            .order_by(RodadaCrash.id.desc())
            .limit(limite)
        ).scalars()
    )


def visao_da_rodada_crash(rodada, momento=None):
    """O que a tela pode mostrar.

    Enquanto a rodada vive, ``ponto_de_estouro`` é ``None``: é o segredo do
    servidor, e entregá-lo seria entregar o jogo. O que a tela recebe para
    animar é há quanto tempo a rodada começou e qual o alvo — com isso o
    cliente desenha a curva sem saber onde ela para.
    """
    if rodada is None:
        return None
    return {
        "id": rodada.id,
        "estado": rodada.estado,
        "encerrada": rodada.encerrada,
        "aposta": rodada.aposta,
        "vantagem": rodada.vantagem,
        "alvo": rodada.alvo,
        "multiplicador": rodada.multiplicador,
        "premio": rodada.premio,
        "decorridos": segundos_decorridos(rodada, momento),
        "ponto_de_estouro": rodada.ponto_de_estouro if rodada.encerrada else None,
    }


# --- torre ------------------------------------------------------------------
#
# A torre é o mines com a forma trocada: em vez de um tabuleiro de 25 casas,
# uma pilha de andares com uma armadilha cada. A disciplina é a mesma, e de
# propósito — sorteio no servidor na hora da aposta, segredo enquanto a rodada
# vive, transição por UPDATE condicional, aposta e prêmio como dois
# lançamentos.
#
# O que ela ganhou e o mines ainda não tem: **expiração**. Rodada de torre
# esquecida não fica presa para sempre segurando o caixa da casa na exposição
# comprometida. Depois de `VALIDADE_DA_TORRE` sem ninguém mexer, ela é sacada
# pelo multiplicador já conquistado — que em zero andares é 1,00×, ou seja, a
# aposta de volta. Nem punição nem presente: devolve exatamente o que a pessoa
# tinha na mão quando parou.


#: Quanto tempo uma rodada de torre sobrevive sem ninguém mexer.
VALIDADE_DA_TORRE = timedelta(minutes=30)


def rodada_torre_ativa(jogador, sessao=None, travada=False):
    """A rodada de torre em andamento do jogador, se houver."""
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    consulta = (
        select(RodadaTorre)
        .where(
            RodadaTorre.jogador_id == jogador_id,
            RodadaTorre.estado == RodadaTorre.ATIVA,
        )
        .order_by(RodadaTorre.id.desc())
    )
    if travada:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalars().first()


def ultima_rodada_torre(jogador, sessao=None):
    """A rodada de torre encerrada mais recente.

    Existe pela lição que o mines já pagou: resultado que só vive no
    ``?rodada=`` do redirect some quando o POST é reenviado ou a página é
    recarregada, e a tela volta a parecer rodada nova.
    """
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    return (
        sessao.execute(
            select(RodadaTorre)
            .where(
                RodadaTorre.jogador_id == jogador_id,
                RodadaTorre.estado != RodadaTorre.ATIVA,
            )
            .order_by(RodadaTorre.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def criar_rodada_torre(jogador, aposta, portas, sessao=None):
    """Começa a rodada, sorteia a torre inteira e cobra a aposta.

    A ordem importa: tudo que pode recusar é conferido **antes** de o dinheiro
    sair. As armadilhas são sorteadas aqui, com ``secrets``, uma por andar até
    o topo, e não vão para o cliente enquanto a rodada viver.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao)

    if jogador.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não joga")

    try:
        portas = validar_portas(portas)
    except ValueError as erro:
        raise ValorInvalido(str(erro)) from erro

    try:
        aposta = para_decimal(aposta)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if aposta <= ZERO:
        raise ValorInvalido("a aposta precisa ser maior que zero")

    # Varre as abandonadas ANTES de medir a exposição: é aqui que o caixa
    # preso por rodada esquecida faria diferença, recusando aposta boa.
    expirar_torres_abandonadas(sessao)

    travado = sessao.execute(
        select(Usuario).where(Usuario.id == jogador.id).with_for_update()
    ).scalar_one()

    if rodada_torre_ativa(jogador, sessao) is not None:
        raise RodadaEmAndamento("você já tem uma rodada em andamento")

    if travado.saldo < aposta:
        raise ValorInvalido(f"você tem {travado.saldo} VVC")

    maximo = limite_de_aposta(sessao)
    if aposta > maximo:
        raise ApostaAlta(f"aposta máxima para o caixa de agora: {maximo} VVC")

    vantagem_da_rodada = vantagem_vigente("torre", sessao)
    sorteio = secrets.SystemRandom()
    andares = altura_da_torre(portas, fator_de(vantagem_da_rodada))
    armadilhas = [sorteio.randrange(portas) for _ in range(andares)]

    rodada = RodadaTorre(
        jogador_id=jogador.id,
        aposta=aposta,
        vantagem=vantagem_da_rodada,
        portas=portas,
        armadilhas=",".join(str(a) for a in armadilhas),
        escolhas="",
        estado=RodadaTorre.ATIVA,
        multiplicador=para_decimal("1.00"),
        premio=ZERO,
        mexida_em=agora(),
    )
    sessao.add(rodada)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise RodadaEmAndamento("você já tem uma rodada em andamento") from erro

    transacao = mover(
        jogador,
        conta_da_casa,
        aposta,
        tipo=TIPO_APOSTA_TORRE,
        motivo=f"torre #{rodada.id}",
        sessao=sessao,
    )
    rodada.transacao_aposta_id = transacao.id
    sessao.flush()
    return rodada


def abrir_porta(jogador, porta, sessao=None):
    """Abre uma porta do andar atual. O servidor decide o que havia atrás.

    Reenviar o mesmo POST não abre duas portas: o ``UPDATE`` condicional exige
    que ``escolhas`` ainda seja o que era quando a decisão foi tomada.
    """
    sessao = sessao or db.session

    rodada = rodada_torre_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento")

    try:
        porta = int(porta)
    except (TypeError, ValueError):
        raise ValorInvalido("porta inválida")
    if not 0 <= porta < rodada.portas:
        raise ValorInvalido("porta fora do andar")

    escolhas = rodada.portas_abertas
    andar = len(escolhas)
    armadilhas = rodada.armadilha_por_andar
    if andar >= len(armadilhas):
        # Já está no topo: não há andar para abrir. Encerra pagando.
        return sacar_torre(jogador, sessao=sessao)

    escolhas.append(porta)
    novo_texto = ",".join(str(p) for p in escolhas)

    if porta == armadilhas[andar]:
        return _estourar_torre(rodada, novo_texto, andar, sessao)

    fator_da_rodada = fator_de(rodada.vantagem)
    subidos = len(escolhas)
    novo_multiplicador = multiplicador_torre(rodada.portas, subidos, fator_da_rodada)

    aplicado = sessao.execute(
        update(RodadaTorre)
        .where(
            RodadaTorre.id == rodada.id,
            RodadaTorre.estado == RodadaTorre.ATIVA,
            RodadaTorre.escolhas == rodada.escolhas,
        )
        .values(
            escolhas=novo_texto,
            multiplicador=novo_multiplicador,
            mexida_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("a rodada mudou; recarregue a página")
    sessao.expire(rodada)

    # No topo não há mais o que ganhar subindo: a rodada encerra e paga
    # sozinha, como o mines faz ao bater o teto.
    if bateu_o_teto_torre(rodada.portas, subidos, fator_da_rodada):
        return sacar_torre(jogador, sessao=sessao)

    return rodada


def _estourar_torre(rodada, escolhas, andar, sessao):
    """Pisou na armadilha. A aposta já está com a casa; perder é fechar."""
    aplicado = sessao.execute(
        update(RodadaTorre)
        .where(RodadaTorre.id == rodada.id, RodadaTorre.estado == RodadaTorre.ATIVA)
        .values(
            estado=RodadaTorre.ESTOURADA,
            escolhas=escolhas,
            andar_estourado=andar,
            premio=ZERO,
            mexida_em=agora(),
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)
    return rodada


def sacar_torre(jogador, sessao=None, rodada=None):
    """Encerra ganhando e paga o acumulado. Idempotente.

    O estado vira ``retirada`` **antes** do pagamento, por ``UPDATE``
    condicional: se duas requisições chegarem juntas, só uma passa da trava, e
    só ela paga.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao, travada=True)

    if rodada is None:
        rodada = rodada_torre_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento para retirar")

    subidos = rodada.andares_subidos
    fator = multiplicador_pagavel_torre(
        rodada.portas, subidos, fator_de(rodada.vantagem)
    )
    premio = quantizar_para_baixo(rodada.aposta * fator)

    aplicado = sessao.execute(
        update(RodadaTorre)
        .where(RodadaTorre.id == rodada.id, RodadaTorre.estado == RodadaTorre.ATIVA)
        .values(
            estado=RodadaTorre.RETIRADA,
            multiplicador=fator,
            premio=premio,
            mexida_em=agora(),
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)

    if premio > ZERO:
        # O teto de banca de novo, contra o caixa DESTE instante: entre a
        # aposta e o pagamento pode ter entrado outra rodada.
        if conta_da_casa.saldo < premio:
            raise CaixaComprometido(
                f"a casa não tem {premio} VVC agora; procure o dono do cassino"
            )
        transacao = mover(
            conta_da_casa,
            rodada.jogador_id,
            premio,
            tipo=TIPO_PREMIO_TORRE,
            motivo=f"torre #{rodada.id} · {fator}x",
            sessao=sessao,
        )
        rodada.transacao_premio_id = transacao.id
        sessao.flush()
    return rodada


def expirar_torres_abandonadas(sessao=None, momento=None):
    """Fecha as rodadas de torre esquecidas, pagando o já conquistado.

    Rodada aberta segura ``premio_maximo`` na exposição comprometida. Sem
    isto, quem fecha a aba com rodada aberta congela um pedaço do caixa da
    casa para sempre — e dez pessoas fazendo isso travam o cassino inteiro
    sem que ninguém esteja jogando.

    Paga o multiplicador já conquistado, que em zero andares é 1,00×: a
    aposta de volta. Não é presente nem punição, é devolver o que a pessoa
    tinha na mão. Rodada expirada aparece como ``retirada``, porque foi
    exatamente isso que aconteceu.
    """
    sessao = sessao or db.session
    limite = (momento or agora()) - VALIDADE_DA_TORRE

    abandonadas = list(
        sessao.execute(
            select(RodadaTorre)
            .where(
                RodadaTorre.estado == RodadaTorre.ATIVA,
                RodadaTorre.mexida_em < limite,
            )
            .with_for_update()
        ).scalars()
    )
    for rodada in abandonadas:
        try:
            sacar_torre(rodada.jogador_id, sessao=sessao, rodada=rodada)
        except (SemRodadaAtiva, CaixaComprometido):
            # Outra requisição chegou primeiro, ou a casa não cobre agora.
            # Nenhum dos dois é motivo para derrubar quem só passava por aqui.
            continue
    return abandonadas


def historico_torre(jogador, limite=15, sessao=None):
    """Rodadas de torre encerradas, da mais recente para a mais antiga."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(RodadaTorre)
            .where(
                RodadaTorre.jogador_id == jogador.id,
                RodadaTorre.estado != RodadaTorre.ATIVA,
            )
            .order_by(RodadaTorre.id.desc())
            .limit(limite)
        ).scalars()
    )


def visao_da_rodada_torre(rodada):
    """O que a tela pode mostrar.

    Enquanto a rodada vive, ``armadilhas`` é ``None``: a torre só aparece
    quando ela encerra. É a projeção segura, e é o ponto em que um descuido
    entregaria o jogo inteiro.
    """
    if rodada is None:
        return None
    fator_da_rodada = fator_de(rodada.vantagem)
    subidos = rodada.andares_subidos
    andares = len(rodada.armadilha_por_andar)
    atual = multiplicador_pagavel_torre(rodada.portas, subidos, fator_da_rodada)
    return {
        "id": rodada.id,
        "estado": rodada.estado,
        "encerrada": rodada.encerrada,
        "aposta": rodada.aposta,
        "vantagem": rodada.vantagem,
        "portas": rodada.portas,
        "andares": andares,
        "andar_atual": len(rodada.portas_abertas),
        "andares_subidos": subidos,
        "escolhas": rodada.portas_abertas,
        "multiplicador": atual if subidos else ZERO,
        "premio_atual": quantizar_para_baixo(rodada.aposta * atual) if subidos else ZERO,
        "premio": rodada.premio,
        "andar_estourado": rodada.andar_estourado,
        "armadilhas": rodada.armadilha_por_andar if rodada.encerrada else None,
        "tabela": tabela_da_torre(rodada.portas, fator_da_rodada),
    }


# --- dados ------------------------------------------------------------------
#
# O jogo mais simples dos quatro, e o único que resolve inteiro dentro de uma
# transação: cobra, rola, paga. Não existe rodada ativa, então não existem as
# duas dores que os outros têm — nem liquidação para acontecer depois, nem
# rodada abandonada segurando o caixa da casa.
#
# O que continua igual: a rolagem é do servidor, o teto de banca é conferido
# antes de cobrar E o caixa é conferido antes de pagar, e aposta e prêmio são
# dois lançamentos por `mover()`.


def jogar_dados(jogador, aposta, sentido, alvo, sessao=None, aleatorio=None):
    """Cobra, rola e paga, tudo de uma vez. Devolve a rodada já resolvida.

    ``aleatorio`` existe para o teste poder fixar a rolagem; em produção é o
    ``secrets.SystemRandom()``, e o padrão é ele justamente para que esquecer
    o argumento não vire um dado previsível.
    """
    sessao = sessao or db.session
    aleatorio = aleatorio or secrets.SystemRandom()
    conta_da_casa = exigir_casa(sessao, travada=True)

    if jogador.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não joga")

    vantagem_da_rodada = vantagem_vigente("dados", sessao)
    fator = fator_de(vantagem_da_rodada)

    try:
        sentido = validar_sentido(sentido)
        alvo = validar_alvo_dos_dados(sentido, alvo, fator)
    except ValueError as erro:
        raise ValorInvalido(str(erro)) from erro

    try:
        aposta = para_decimal(aposta)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if aposta <= ZERO:
        raise ValorInvalido("a aposta precisa ser maior que zero")

    travado = sessao.execute(
        select(Usuario).where(Usuario.id == jogador.id).with_for_update()
    ).scalar_one()
    if travado.saldo < aposta:
        raise ValorInvalido(f"você tem {travado.saldo} VVC")

    maximo = limite_de_aposta(sessao)
    if aposta > maximo:
        raise ApostaAlta(f"aposta máxima para o caixa de agora: {maximo} VVC")

    fator_pago = multiplicador_pagavel_dados(sentido, alvo, fator)
    premio_possivel = quantizar_para_baixo(aposta * fator_pago)

    # O caixa é conferido ANTES de cobrar, e não só antes de pagar: aqui as
    # duas coisas acontecem no mesmo instante, então cobrar sem poder pagar
    # seria cobrar e devolver — barulho no extrato por nada.
    if conta_da_casa.saldo + aposta < premio_possivel:
        raise CaixaComprometido(
            f"a casa não tem {premio_possivel} VVC agora; procure o dono do cassino"
        )

    resultado = rolar(aleatorio)
    venceu = ganhou_nos_dados(sentido, alvo, resultado)

    rodada = RodadaDados(
        jogador_id=jogador.id,
        aposta=aposta,
        vantagem=vantagem_da_rodada,
        sentido=sentido,
        alvo=alvo,
        resultado=resultado,
        estado=RodadaDados.GANHA if venceu else RodadaDados.PERDIDA,
        multiplicador=fator_pago if venceu else ZERO,
        premio=premio_possivel if venceu else ZERO,
    )
    sessao.add(rodada)
    sessao.flush()

    transacao = mover(
        jogador,
        conta_da_casa,
        aposta,
        tipo=TIPO_APOSTA_DADOS,
        motivo=f"dados #{rodada.id}",
        sessao=sessao,
    )
    rodada.transacao_aposta_id = transacao.id

    if venceu and premio_possivel > ZERO:
        premio = mover(
            conta_da_casa,
            jogador.id,
            premio_possivel,
            tipo=TIPO_PREMIO_DADOS,
            motivo=f"dados #{rodada.id} · {fator_pago}x",
            sessao=sessao,
        )
        rodada.transacao_premio_id = premio.id
    sessao.flush()
    return rodada


def ultima_rodada_dados(jogador, sessao=None):
    """A rodada de dados mais recente, para a tela ter o que mostrar.

    Mesma lição dos outros: o resultado não pode viver só no ``?rodada=`` que
    o redirect carrega.
    """
    sessao = sessao or db.session
    jogador_id = jogador.id if isinstance(jogador, Usuario) else jogador
    return (
        sessao.execute(
            select(RodadaDados)
            .where(RodadaDados.jogador_id == jogador_id)
            .order_by(RodadaDados.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def historico_dados(jogador, limite=15, sessao=None):
    """Rodadas de dados, da mais recente para a mais antiga."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(RodadaDados)
            .where(RodadaDados.jogador_id == jogador.id)
            .order_by(RodadaDados.id.desc())
            .limit(limite)
        ).scalars()
    )


def visao_da_rodada_dados(rodada):
    """O que a tela mostra. Aqui não há segredo a esconder: a rodada já acabou."""
    if rodada is None:
        return None
    return {
        "id": rodada.id,
        "estado": rodada.estado,
        "ganhou": rodada.ganhou,
        "aposta": rodada.aposta,
        "vantagem": rodada.vantagem,
        "sentido": rodada.sentido,
        "alvo": rodada.alvo,
        "resultado": rodada.resultado,
        "multiplicador": rodada.multiplicador,
        "premio": rodada.premio,
    }
