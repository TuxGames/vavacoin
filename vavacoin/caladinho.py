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
"""

import secrets

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .constantes import USUARIO_CASSINO
from .dinheiro import ZERO, para_decimal, quantizar_para_baixo
from .erros import (
    ApostaAlta,
    CaixaComprometido,
    CasaIndisponivel,
    RodadaEmAndamento,
    SemAutoridade,
    SemRodadaAtiva,
    ValorInvalido,
)
from .extensoes import db
from .mines import (
    CASAS,
    aposta_maxima,
    bateu_o_teto,
    multiplicador,
    multiplicador_pagavel,
    premio_maximo,
    validar_minas,
)
from .modelos import RodadaMines, Transacao, Usuario, agora
from .moeda import mover

TIPO_APOSTA = "aposta_mines"
TIPO_PREMIO = "premio_mines"

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
            Transacao.tipo.in_((TIPO_APOSTA, TIPO_PREMIO)),
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
    """O prêmio máximo somado das rodadas ainda ativas.

    Sem descontar isto, dez jogadores apostam ao mesmo tempo, cada aposta
    passa sozinha no teto, e juntas passam do que a casa tem.
    """
    sessao = sessao or db.session
    total = ZERO
    for aposta in sessao.execute(
        select(RodadaMines.aposta).where(RodadaMines.estado == RodadaMines.ATIVA)
    ).scalars():
        total += premio_maximo(aposta)
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
    fator = multiplicador(rodada.minas_escolhidas, len(reveladas))
    aplicado = sessao.execute(
        update(RodadaMines)
        .where(
            RodadaMines.id == rodada.id,
            RodadaMines.estado == RodadaMines.ATIVA,
            RodadaMines.reveladas == rodada.reveladas,
        )
        .values(
            reveladas=",".join(str(c) for c in reveladas), multiplicador=fator
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("a rodada mudou; recarregue a página")
    sessao.expire(rodada)

    # Ao bater o teto não há mais o que ganhar abrindo casa: a rodada encerra
    # e paga sozinha. Deixá-la aberta seria oferecer risco sem prêmio.
    if bateu_o_teto(rodada.minas_escolhidas, len(reveladas)):
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
            encerrada_em=agora(),
        )
    )
    if aplicado.rowcount != 1:
        raise SemRodadaAtiva("esta rodada já acabou")
    sessao.expire(rodada)
    return rodada


def retirar(jogador, sessao=None):
    """Encerra ganhando e paga o prêmio. Idempotente.

    O estado vira ``retirada`` **antes** do pagamento, por ``UPDATE``
    condicional: se duas requisições chegarem juntas, só uma passa da trava, e
    só ela paga.
    """
    sessao = sessao or db.session
    conta_da_casa = exigir_casa(sessao, travada=True)

    rodada = rodada_ativa(jogador, sessao, travada=True)
    if rodada is None:
        raise SemRodadaAtiva("nenhuma rodada em andamento para retirar")

    abertas = len(rodada.casas_reveladas)
    if abertas < 1:
        raise ValorInvalido("revele ao menos uma casa antes de retirar")

    fator = multiplicador_pagavel(rodada.minas_escolhidas, abertas)
    premio = quantizar_para_baixo(rodada.aposta * fator)

    aplicado = sessao.execute(
        update(RodadaMines)
        .where(RodadaMines.id == rodada.id, RodadaMines.estado == RodadaMines.ATIVA)
        .values(estado=RodadaMines.RETIRADA, premio=premio, encerrada_em=agora())
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
    fator = multiplicador_pagavel(rodada.minas_escolhidas, abertas) if abertas else ZERO
    return {
        "id": rodada.id,
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
