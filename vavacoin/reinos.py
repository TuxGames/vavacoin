"""Reinos: cofre, cidadania, imposto e repasse.

Genérico desde o primeiro dia. Alfheim é uma linha da tabela ``reino``, não
uma constante — o segundo reino não pode exigir reescrever o primeiro.

Três decisões que governam tudo aqui, e que não são deste módulo inventar:

1. **Cidadania é opt-in, com saída.** Ninguém entra sem pedir e ninguém fica
   preso. É o princípio do projeto inteiro.
2. **Imposto nunca tira dinheiro de ninguém.** Cobrar cria uma **dívida**;
   pagar é ato do devedor. Um reino não consegue debitar a conta de um
   cidadão, e isso é de propósito.
3. **O poder é do reino, não da pessoa.** Quem opera é uma conta pessoal com
   o papel de operador *daquele* reino. O cofre guarda o dinheiro e não
   autentica — se a maneira de mandar fosse entrar na conta do cofre, quem
   soubesse a senha seria rei, e o ledger diria "o cofre cobrou" em vez de
   dizer quem digitou.

Todo movimento de dinheiro passa por ``mover()``, como no resto do projeto: a
conservação de massa vale durante cobrança, pagamento e repasse.
"""

import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .dinheiro import ZERO, para_decimal, quantizar_para_baixo
from .erros import (
    MotivoObrigatorio,
    SaldoInsuficiente,
    SemAutoridade,
    ValorInvalido,
)
from .extensoes import db
from .modelos import (
    Cidadania,
    PedidoDeCidadania,
    Cobranca,
    Distribuicao,
    Divida,
    OperadorDoReino,
    Reino,
    Usuario,
    agora,
    registrar_acao,
)
from .moeda import mover
from .nomes import normalizar_nome

#: Imposto pago pelo cidadão ao cofre, e repasse do cofre ao cidadão. Dois
#: tipos próprios para o extrato dizer o que aconteceu.
TIPO_IMPOSTO = "imposto"
TIPO_REPASSE = "repasse"

#: Juros por dia, em pontos percentuais. Editável pelo operador dentro da
#: faixa, com registro — mesmo desenho da vantagem do cassino.
JUROS_PADRAO = Decimal("1.00")
JUROS_MINIMO = Decimal("0.00")
JUROS_MAXIMO = Decimal("5.00")

CEM = Decimal("100")
UM_DIA = timedelta(days=1)


# --- o reino ----------------------------------------------------------------


def criar_reino(nome, autoridade=None, sessao=None):
    """Cria o reino e o cofre dele. Poder do Banco Central.

    O cofre nasce sem senha, como a casa do cassino: conta de sistema não
    entra pela tela.
    """
    from .autoridade import exigir_banco_central

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)

    nome = (nome or "").strip()
    normalizado = normalizar_nome(nome)
    if not normalizado:
        raise ValorInvalido("o reino precisa de um nome")

    existente = sessao.execute(
        select(Reino).where(Reino.nome_normalizado == normalizado)
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    cofre = Usuario(nome_exibicao=f"Cofre de {nome}", eh_cofre=True, saldo=ZERO)
    cofre.definir_nome(f"cofre_{normalizado}")
    sessao.add(cofre)
    sessao.flush()

    reino = Reino(
        nome=nome,
        nome_normalizado=normalizado,
        cofre_id=cofre.id,
        juros_diarios=JUROS_PADRAO,
    )
    sessao.add(reino)
    sessao.flush()
    registrar_acao(bc, "reino", alvo=nome, detalhe="reino criado", sessao=sessao)
    return reino


def reino_por_nome(nome, sessao=None):
    sessao = sessao or db.session
    return sessao.execute(
        select(Reino).where(Reino.nome_normalizado == normalizar_nome(nome or ""))
    ).scalar_one_or_none()


def reinos(sessao=None):
    sessao = sessao or db.session
    return list(sessao.execute(select(Reino).order_by(Reino.nome)).scalars())


def definir_operador(reino, pessoa, autoridade=None, sessao=None):
    """Dá o papel de operador a uma pessoa. Poder do Banco Central.

    O papel é do reino: tirá-lo tira o poder, e dá-lo a mais alguém é um
    ministro. Nenhuma conta de sistema opera — o cofre não manda em si mesmo.
    """
    from .autoridade import exigir_banco_central

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)

    if pessoa.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não opera reino")

    existente = sessao.execute(
        select(OperadorDoReino).where(
            OperadorDoReino.reino_id == reino.id,
            OperadorDoReino.usuario_id == pessoa.id,
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    papel = OperadorDoReino(reino_id=reino.id, usuario_id=pessoa.id)
    sessao.add(papel)
    sessao.flush()
    registrar_acao(
        bc,
        "reino",
        alvo=reino.nome,
        detalhe=f"{pessoa.nome_usuario} virou operador",
        sessao=sessao,
    )
    return papel


def tirar_operador(reino, pessoa, autoridade=None, sessao=None):
    """Tira o papel. Perdeu o papel, perdeu o poder."""
    from .autoridade import exigir_banco_central

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    papel = sessao.execute(
        select(OperadorDoReino).where(
            OperadorDoReino.reino_id == reino.id,
            OperadorDoReino.usuario_id == pessoa.id,
        )
    ).scalar_one_or_none()
    if papel is None:
        return None
    sessao.delete(papel)
    sessao.flush()
    registrar_acao(
        bc,
        "reino",
        alvo=reino.nome,
        detalhe=f"{pessoa.nome_usuario} deixou de ser operador",
        sessao=sessao,
    )
    return papel


def eh_operador(reino, pessoa, sessao=None):
    if reino is None or pessoa is None or not getattr(pessoa, "id", None):
        return False
    sessao = sessao or db.session
    return (
        sessao.execute(
            select(OperadorDoReino.id).where(
                OperadorDoReino.reino_id == reino.id,
                OperadorDoReino.usuario_id == pessoa.id,
            )
        ).first()
        is not None
    )


def exigir_operador(reino, pessoa, sessao=None):
    if not eh_operador(reino, pessoa, sessao):
        raise SemAutoridade("só o operador do reino faz isso")
    return pessoa


def operadores(reino, sessao=None):
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(Usuario)
            .join(OperadorDoReino, OperadorDoReino.usuario_id == Usuario.id)
            .where(OperadorDoReino.reino_id == reino.id)
            .order_by(Usuario.nome_usuario)
        ).scalars()
    )


def definir_juros(reino, novos, operador, sessao=None):
    """Muda os juros diários, dentro da faixa, e registra quem mudou.

    Mesmo desenho da vantagem do cassino, e pelo mesmo motivo: é o registro
    que defende o operador da acusação de ter mexido para prejudicar alguém.
    """
    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)

    try:
        novos = para_decimal(str(novos).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("juros inválidos") from erro
    if not JUROS_MINIMO <= novos <= JUROS_MAXIMO:
        raise ValorInvalido(f"os juros vão de {JUROS_MINIMO}% a {JUROS_MAXIMO}% ao dia")

    anterior = reino.juros_diarios
    reino.juros_diarios = novos
    sessao.flush()
    registrar_acao(
        operador,
        "reino",
        alvo=reino.nome,
        detalhe=f"juros de {anterior}% para {novos}% ao dia",
        sessao=sessao,
    )
    return novos


# --- cidadania --------------------------------------------------------------


def cidadania_ativa(reino, pessoa, sessao=None):
    sessao = sessao or db.session
    return sessao.execute(
        select(Cidadania).where(
            Cidadania.reino_id == reino.id,
            Cidadania.usuario_id == pessoa.id,
            Cidadania.saiu_em.is_(None),
        )
    ).scalar_one_or_none()


def eh_cidadao(reino, pessoa, sessao=None):
    return cidadania_ativa(reino, pessoa, sessao) is not None


def cidadania_de(pessoa, sessao=None):
    """A cidadania ativa da pessoa, seja qual for o reino, ou ``None``.

    Existe porque a cidadania é exclusiva: a pergunta que importa deixou de
    ser "é cidadão daqui?" e passou a ser "é cidadão de algum lugar?".
    """
    sessao = sessao or db.session
    return sessao.execute(
        select(Cidadania).where(
            Cidadania.usuario_id == pessoa.id, Cidadania.saiu_em.is_(None)
        )
    ).scalar_one_or_none()


def cidadaos(reino, sessao=None):
    """As pessoas com cidadania ativa, em ordem de nome."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(Usuario)
            .join(Cidadania, Cidadania.usuario_id == Usuario.id)
            .where(Cidadania.reino_id == reino.id, Cidadania.saiu_em.is_(None))
            .order_by(Usuario.nome_usuario)
        ).scalars()
    )


def entrar_no_reino(reino, pessoa, sessao=None):
    """A pessoa pede para entrar. Ato dela, sempre.

    A casa do cassino não é cidadã de reino nenhum — nem ela nem qualquer
    conta de sistema: elas não são pessoas, e imposto é coisa de gente.
    """
    sessao = sessao or db.session

    if pessoa.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não é cidadã")
    if pessoa.encerrada:
        raise ValorInvalido("conta encerrada não entra em reino")

    # Um reino por pessoa. A rota confere para dar uma frase decente; quem
    # garante é o índice único parcial, que barra até duas requisições
    # simultâneas pedindo entrada em reinos diferentes.
    atual = cidadania_de(pessoa, sessao)
    if atual is not None:
        if atual.reino_id == reino.id:
            raise ValorInvalido(f"{pessoa.nome_usuario} já é cidadão de {reino.nome}")
        raise ValorInvalido(
            f"{pessoa.nome_usuario} já é cidadão de {atual.reino.nome}; "
            "saia de lá antes"
        )

    cidadania = Cidadania(reino_id=reino.id, usuario_id=pessoa.id)
    sessao.add(cidadania)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("você já é cidadão de um reino") from erro
    return cidadania


def sair_do_reino(reino, pessoa, sessao=None):
    """A pessoa pede para sair. **A dívida em aberto sobrevive intacta.**

    Decisão do dono: a dívida é uma relação entre quem cobrou e quem deve, não
    um atributo da cidadania. Sair de um reino não é motivo para o que já foi
    contraído deixar de existir nem para parar de correr — senão bastaria sair
    na véspera do vencimento e o imposto viraria piada.

    O que dá saída para quem quer negociar não é a porta do reino: é o credor,
    que pode baixar o valor até o principal ou perdoar a dívida inteira. Essa
    é a válvula, e ela é de quem cobrou.
    """
    sessao = sessao or db.session
    cidadania = cidadania_ativa(reino, pessoa, sessao)
    if cidadania is None:
        raise ValorInvalido(f"{pessoa.nome_usuario} não é cidadão de {reino.nome}")

    cidadania.saiu_em = agora()
    sessao.flush()
    return cidadania


# --- convite e pedido de cidadania ------------------------------------------
#
# Dois caminhos para a mesma coisa, e os dois exigem as DUAS partes: o reino
# convida e a pessoa aceita, ou a pessoa pede e o operador aprova. Ninguém
# entra sozinho e ninguém é colocado à força — é o princípio de sempre, agora
# com as duas ordens possíveis.
#
# `entrar_no_reino` continua existindo e é o que ambos os caminhos chamam ao
# confirmar: há um lugar só onde alguém vira cidadão, e é lá que a
# exclusividade é conferida.


def _pendencia(reino, pessoa, sessao):
    return sessao.execute(
        select(PedidoDeCidadania).where(
            PedidoDeCidadania.reino_id == reino.id,
            PedidoDeCidadania.usuario_id == pessoa.id,
            PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
        )
    ).scalar_one_or_none()


def _abrir_pendencia(reino, pessoa, origem, quem, sessao):
    """Cria a pendência, ou devolve a que já existe. Idempotente.

    Enviar duas vezes não cria duas linhas: o índice único parcial recusa, e
    antes dele esta função já devolve a pendência aberta. Sem isso a tela do
    outro lado encheria de convites idênticos.
    """
    if pessoa.eh_conta_de_sistema:
        raise ValorInvalido("conta de sistema não é cidadã")
    if pessoa.encerrada:
        raise ValorInvalido("conta encerrada não entra em reino")
    if eh_cidadao(reino, pessoa, sessao):
        raise ValorInvalido(f"{pessoa.nome_usuario} já é cidadão de {reino.nome}")

    aberta = _pendencia(reino, pessoa, sessao)
    if aberta is not None:
        return aberta

    pedido = PedidoDeCidadania(
        reino_id=reino.id,
        usuario_id=pessoa.id,
        origem=origem,
        criado_por_id=quem.id,
    )
    sessao.add(pedido)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("já existe um pedido em aberto") from erro
    return pedido


def convidar(reino, pessoa, operador, sessao=None):
    """O reino convida; quem aceita é a pessoa.

    Quem envia é o **operador**, não o cofre: o cofre guarda dinheiro e não
    autentica, e o registro precisa dizer qual pessoa convidou.

    Convidar quem já é cidadão de outro reino é legítimo — a exclusividade é
    conferida quando ela aceitar, e a decisão de sair de lá é dela.
    """
    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)
    pedido = _abrir_pendencia(
        reino, pessoa, PedidoDeCidadania.REINO, operador, sessao
    )
    registrar_acao(
        operador,
        "reino",
        alvo=pessoa.nome_usuario,
        detalhe=f"convite para {reino.nome}",
        sessao=sessao,
    )
    return pedido


def pedir_cidadania(reino, pessoa, sessao=None):
    """A pessoa pede; quem aprova é um operador.

    Pedir não dá cidadania nenhuma — dá uma linha para o reino responder.
    """
    sessao = sessao or db.session
    return _abrir_pendencia(
        reino, pessoa, PedidoDeCidadania.PESSOA, pessoa, sessao
    )


def pode_responder(pedido, pessoa, sessao=None):
    """Quem fecha esta pendência.

    O lado que **não** começou. Convite do reino é a pessoa que aceita;
    pedido da pessoa é o operador que aprova. Deixar o mesmo lado confirmar o
    que ele mesmo abriu seria entrar sozinho com passo a mais.
    """
    sessao = sessao or db.session
    if pessoa is None or not getattr(pessoa, "id", None):
        return False
    if not pedido.pendente:
        return False
    if pedido.eh_convite:
        return pedido.usuario_id == pessoa.id
    return eh_operador(pedido.reino, pessoa, sessao)


def _fechar(pedido, estado, quem, sessao):
    """Fecha a pendência por ``UPDATE`` condicional. Idempotente.

    Dois cliques, ou o reenvio do POST, encontram a linha já fora de
    ``pendente`` e não passam.
    """
    aplicado = sessao.execute(
        update(PedidoDeCidadania)
        .where(
            PedidoDeCidadania.id == pedido.id,
            PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
        )
        .values(estado=estado, respondido_em=agora(), respondido_por_id=quem.id)
    )
    if aplicado.rowcount != 1:
        raise ValorInvalido("esse pedido já foi respondido")
    sessao.expire(pedido)


def aceitar_pedido(pedido, quem, sessao=None):
    """Fecha a pendência e cria a cidadania. É aqui que alguém vira cidadão.

    A exclusividade é conferida **agora**, e não quando o convite foi enviado:
    entre uma coisa e outra podem passar dias. Quem já é cidadão de outro
    reino recebe a recusa de ``entrar_no_reino``, e a pendência continua de pé
    para depois de ela sair de lá.
    """
    sessao = sessao or db.session
    if not pode_responder(pedido, quem, sessao):
        raise SemAutoridade("este pedido não é seu para responder")

    reino = pedido.reino
    pessoa = pedido.usuario

    # Primeiro a cidadania: se ela for recusada (exclusividade, conta
    # encerrada), a pendência fica intacta para uma segunda tentativa.
    cidadania = entrar_no_reino(reino, pessoa, sessao=sessao)
    _fechar(pedido, PedidoDeCidadania.ACEITO, quem, sessao)
    registrar_acao(
        quem,
        "reino",
        alvo=pessoa.nome_usuario,
        detalhe=f"cidadania em {reino.nome} aceita",
        sessao=sessao,
    )
    return cidadania


def recusar_pedido(pedido, quem, sessao=None):
    """Fecha a pendência sem criar cidadania. Os dois lados podem recusar.

    Quem recusa é quem responderia — e também quem enviou, porque desistir do
    convite que se mandou é tão legítimo quanto recusá-lo.
    """
    sessao = sessao or db.session
    if not (
        pode_responder(pedido, quem, sessao) or pedido.criado_por_id == quem.id
    ):
        raise SemAutoridade("este pedido não é seu para responder")

    _fechar(pedido, PedidoDeCidadania.RECUSADO, quem, sessao)
    return pedido


def pendencias_da_pessoa(pessoa, sessao=None):
    """Convites esperando a pessoa, e pedidos que ela mandou."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(PedidoDeCidadania)
            .where(
                PedidoDeCidadania.usuario_id == pessoa.id,
                PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
            )
            .order_by(PedidoDeCidadania.id)
        ).scalars()
    )


def pendencias_do_reino(reino, sessao=None):
    """Pedidos esperando o operador, e convites que o reino mandou."""
    sessao = sessao or db.session
    return list(
        sessao.execute(
            select(PedidoDeCidadania)
            .where(
                PedidoDeCidadania.reino_id == reino.id,
                PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
            )
            .order_by(PedidoDeCidadania.id)
        ).scalars()
    )


# --- juros ------------------------------------------------------------------


def dias_de_juros(divida, momento=None):
    """Dias **inteiros** desde o último marco.

    Inteiros de propósito: assim o número na tela não muda enquanto a pessoa
    olha, e dívida criada agora não rende nada hoje.

    Dívida **negociada** não conta dia nenhum: o credor fixou um número, e
    número fixado não cresce.
    """
    if divida.negociada:
        return 0
    fim = momento or agora()
    inicio = divida.juros_desde
    if inicio.tzinfo is None:  # o SQLite devolve ingênuo; o relógio é UTC
        from datetime import timezone as _tz

        inicio = inicio.replace(tzinfo=_tz.utc)
    if fim.tzinfo is None:
        from datetime import timezone as _tz

        fim = fim.replace(tzinfo=_tz.utc)
    if fim <= inicio:
        return 0
    return (fim - inicio) // UM_DIA


def restante(divida):
    """O que falta, sem contar os juros que estão correndo agora.

    Com a dívida negociada, quem manda é o valor de quitação: ele já é o
    total acumulado, então o que falta é ele menos o que foi pago.
    """
    if divida.negociada:
        valor = divida.quitacao - divida.pago
    else:
        valor = divida.principal + divida.juros_cristalizados - divida.pago
    return valor if valor > ZERO else ZERO


def juros_correntes(divida, taxa_diaria=None, momento=None):
    """``restante × taxa × dias inteiros``. Linear, e exato em ``Decimal``.

    Linear e não composto por engenharia, não por gosto: composto exigiria
    potência fracionária, que é a mesma classe de erro que já custou um
    centavo na curva do crash. Aqui tudo é multiplicação exata.
    """
    dias = dias_de_juros(divida, momento)
    if dias <= 0:
        return ZERO
    # A taxa é a CONGELADA na dívida, não a vigente do reino: mudar a taxa
    # depois não reprecifica cobrança antiga.
    taxa = divida.juros_diarios if taxa_diaria is None else taxa_diaria
    return quantizar_para_baixo(
        restante(divida) * (para_decimal(taxa) / CEM) * Decimal(dias)
    )


def devido(divida, taxa_diaria=None, momento=None):
    """Quanto a pessoa deve **agora**, juros correntes incluídos."""
    if divida.quitada:
        return ZERO
    return restante(divida) + juros_correntes(divida, taxa_diaria, momento)


# --- cobrança ---------------------------------------------------------------


def base_da_cobranca(pessoa):
    """O patrimônio líquido de alguém no VavaCoin: o **saldo**, e só.

    Não há outro ativo — nem cota, nem empresa, nem item. Confirmado pelo
    dono. Se um dia houver, é esta função que muda, e só ela.
    """
    return pessoa.saldo


def valor_cobrado(tipo, parametro, pessoa):
    """Quanto esta pessoa deve nesta cobrança.

    Percentual arredonda **para baixo**, como todo dinheiro do projeto: o que
    não dá para representar em centavos não é cobrado.
    """
    parametro = para_decimal(parametro)
    if tipo == Cobranca.ABSOLUTA:
        return parametro
    return quantizar_para_baixo(base_da_cobranca(pessoa) * parametro / CEM)


def cobrar(
    reino,
    operador,
    tipo,
    parametro,
    motivo,
    pessoas=None,
    token=None,
    sessao=None,
):
    """Gera uma dívida por pessoa. **Não move um centavo.**

    Cobrar é registrar que alguém deve; quem paga é o devedor, quando quiser.
    É a decisão do dono, e é o que mantém o imposto compatível com "nada
    acontece com uma pessoa sem que ela tenha pedido".

    O lote é idempotente pelo ``token``, que é único no banco: o segundo POST
    do mesmo botão bate no índice e não cobra ninguém de novo.

    Quem não é cidadão no momento da cobrança é ignorado em silêncio — a
    checklist da tela pode estar velha, e cobrar quem saiu seria cobrar quem
    não deve.
    """
    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)

    if tipo not in (Cobranca.ABSOLUTA, Cobranca.PERCENTUAL):
        raise ValorInvalido("tipo de cobrança desconhecido")

    motivo = (motivo or "").strip()
    if not motivo:
        raise MotivoObrigatorio("cobrança pede motivo")

    try:
        parametro = para_decimal(str(parametro).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("valor inválido") from erro
    if parametro <= ZERO:
        raise ValorInvalido("o valor precisa ser maior que zero")
    if tipo == Cobranca.PERCENTUAL and parametro > CEM:
        raise ValorInvalido("a alíquota não passa de 100%")

    alvos = list(pessoas) if pessoas is not None else cidadaos(reino, sessao)
    alvos = [p for p in alvos if eh_cidadao(reino, p, sessao)]

    lote = Cobranca(
        reino_id=reino.id,
        operador_id=operador.id,
        tipo=tipo,
        parametro=parametro,
        motivo=motivo,
        token=token or secrets.token_urlsafe(16),
    )
    sessao.add(lote)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("essa cobrança já foi feita") from erro

    momento = agora()
    criadas = []
    for pessoa in alvos:
        valor = valor_cobrado(tipo, parametro, pessoa)
        if valor <= ZERO:
            # Percentual sobre saldo zero dá zero. Dívida de zero não é
            # dívida — seria uma linha para a pessoa olhar e não entender.
            continue
        divida = Divida(
            reino_id=reino.id,
            devedor_id=pessoa.id,
            cobranca_id=lote.id,
            cobrada_por_id=operador.id,
            principal=valor,
            # A taxa de HOJE, congelada nesta dívida.
            juros_diarios=reino.juros_diarios,
            motivo=motivo,
            cobrada_em=momento,
            juros_desde=momento,
        )
        sessao.add(divida)
        criadas.append(divida)
    sessao.flush()

    registrar_acao(
        operador,
        "reino",
        alvo=reino.nome,
        detalhe=f"cobrança {tipo} de {parametro} em {len(criadas)} cidadão(s)",
        motivo=motivo,
        sessao=sessao,
    )
    return lote, criadas


# --- dívida -----------------------------------------------------------------


def dividas_em_aberto(pessoa, reino=None, sessao=None):
    sessao = sessao or db.session
    consulta = select(Divida).where(
        Divida.devedor_id == pessoa.id, Divida.quitada_em.is_(None)
    )
    if reino is not None:
        consulta = consulta.where(Divida.reino_id == reino.id)
    return list(sessao.execute(consulta.order_by(Divida.id)).scalars())


def total_devido(pessoa, reino=None, sessao=None, momento=None):
    return sum(
        (devido(d, momento=momento) for d in dividas_em_aberto(pessoa, reino, sessao)),
        ZERO,
    )


def pagar_divida(divida, quanto=None, sessao=None, momento=None):
    """O devedor paga, no todo ou em parte. Move dinheiro por ``mover()``.

    Pagamento parcial é permitido, e crava os juros que já correram antes de
    abater — assim pagar metade não apaga retroativamente juro que já existia.

    Idempotente por status: dívida já quitada recusa, e o ``UPDATE``
    condicional de quitação impede que dois cliques quitem duas vezes.
    """
    sessao = sessao or db.session

    if divida.quitada:
        raise ValorInvalido("essa dívida já está quitada")

    devedor = sessao.execute(
        select(Usuario).where(Usuario.id == divida.devedor_id).with_for_update()
    ).scalar_one()
    cofre = sessao.get(Usuario, divida.reino.cofre_id)

    momento = momento or agora()

    # Crava o que já correu ANTES de abater: é o que faz o pagamento parcial
    # não apagar juro passado.
    corridos = juros_correntes(divida, momento=momento)
    if corridos > ZERO:
        divida.juros_cristalizados += corridos
    divida.juros_desde = momento
    sessao.flush()

    aberto = restante(divida)
    if aberto <= ZERO:
        divida.quitada_em = momento
        sessao.flush()
        return divida

    if quanto is None:
        valor = aberto
    else:
        try:
            valor = para_decimal(str(quanto).strip().replace(",", "."))
        except (TypeError, AttributeError) as erro:
            raise ValorInvalido("valor inválido") from erro
    if valor <= ZERO:
        raise ValorInvalido("o valor precisa ser maior que zero")
    if valor > aberto:
        valor = aberto
    if devedor.saldo < valor:
        raise SaldoInsuficiente(f"você tem {devedor.saldo} VVC")

    mover(
        devedor,
        cofre,
        valor,
        tipo=TIPO_IMPOSTO,
        motivo=f"{divida.reino.nome}: {divida.motivo}",
        sessao=sessao,
    )
    divida.pago += valor
    if restante(divida) <= ZERO:
        aplicado = sessao.execute(
            update(Divida)
            .where(Divida.id == divida.id, Divida.quitada_em.is_(None))
            .values(quitada_em=momento)
        )
        if aplicado.rowcount != 1:
            raise ValorInvalido("essa dívida já está quitada")
    sessao.flush()
    return divida


# --- negociar e perdoar -----------------------------------------------------
#
# Quem criou a dívida pode baixar o valor de quitação, até o principal, ou
# perdoar a dívida inteira.
#
# **Nada aqui move dinheiro.** Dívida nunca foi dinheiro no ledger: é uma
# cobrança pendente, uma linha noutra tabela. Só o pagamento move, por
# `mover()`. É por isso que perdoar pode APAGAR a linha sem a auditoria
# acusar nada — não existe lançamento apontando para ela, e os pagamentos
# parciais que já aconteceram continuam no ledger onde sempre estiveram.


def pode_negociar(divida, pessoa, sessao=None):
    """Quem mexe nesta dívida: o autor da cobrança, se ainda for operador.

    Duas condições, e cada uma tapa um buraco:

    - **ser o autor** é o pedido do dono: quem cobrou é quem negocia. Sem
      isso, qualquer operador poderia perdoar dívida dos outros.
    - **ser operador hoje** é o princípio que o projeto já tem: perdeu o
      papel, perdeu o poder. Sem isso, um ex-operador removido poderia
      voltar e perdoar tudo que criou, que é sabotagem com cara de bondade.

    E, para a dívida não ficar órfã quando o autor sai: **se o autor não é
    mais operador, qualquer operador atual do reino assume**. É o reino que
    manda, não a pessoa — e uma dívida que ninguém pode perdoar seria uma
    dívida que ninguém pode corrigir.
    """
    sessao = sessao or db.session
    if pessoa is None or not getattr(pessoa, "id", None):
        return False
    reino = divida.reino
    if not eh_operador(reino, pessoa, sessao):
        return False
    if divida.cobrada_por_id == pessoa.id:
        return True
    autor = sessao.get(Usuario, divida.cobrada_por_id)
    return autor is None or not eh_operador(reino, autor, sessao)


def exigir_credor(divida, pessoa, sessao=None):
    if not pode_negociar(divida, pessoa, sessao):
        raise SemAutoridade("só quem criou a dívida negocia ou perdoa")
    return pessoa


def faixa_de_negociacao(divida, momento=None):
    """``(piso, teto)`` do valor de quitação, em total acumulado.

    - **piso**: o principal com os juros zerados. Se já houve pagamento
      parcial, o piso é o que falta para fechar o principal — e nunca
      negativo, porque quem já pagou mais que o principal não pode receber
      troco de um desconto.
    - **teto**: o total com os juros corridos até agora, que é o que a pessoa
      deveria se ninguém negociasse.

    Os dois em total acumulado (incluindo o já pago) porque é assim que
    ``quitacao`` é guardado: com ``pago`` sendo o único contador que anda,
    ``restante`` vira uma subtração e não há dois números para divergir.
    """
    piso = divida.principal if divida.principal > divida.pago else divida.pago
    teto = divida.pago + devido(divida, momento=momento)
    if teto < piso:
        teto = piso
    return piso, teto


def negociar_divida(divida, valor, credor, sessao=None, momento=None):
    """Fixa o valor de quitação. **Não move um centavo.**

    O servidor recusa fora da faixa: a tela mostra o piso e o teto, mas quem
    decide se o número vale é aqui.

    Fixado o valor, os juros param. O credor combinou um número, e número
    combinado que continua crescendo não é acordo nenhum.
    """
    sessao = sessao or db.session
    exigir_credor(divida, credor, sessao)

    if divida.quitada:
        raise ValorInvalido("essa dívida já está quitada")

    try:
        valor = para_decimal(str(valor).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("valor inválido") from erro

    piso, teto = faixa_de_negociacao(divida, momento)
    if not piso <= valor <= teto:
        raise ValorInvalido(f"o valor de quitação vai de {piso} a {teto} VVC")

    antes = divida.pago + devido(divida, momento=momento)
    divida.quitacao = valor
    sessao.flush()

    # O desconto pode fechar a dívida sozinho, quando o que já foi pago
    # alcança o valor combinado.
    if restante(divida) <= ZERO:
        aplicado = sessao.execute(
            update(Divida)
            .where(Divida.id == divida.id, Divida.quitada_em.is_(None))
            .values(quitada_em=momento or agora())
        )
        if aplicado.rowcount == 1:
            sessao.expire(divida)

    registrar_acao(
        credor,
        "reino",
        alvo=divida.devedor.nome_usuario,
        detalhe=f"dívida #{divida.id}: de {antes} para {valor} VVC",
        motivo=divida.motivo,
        sessao=sessao,
    )
    return divida


def perdoar_divida(divida, credor, sessao=None):
    """Apaga a dívida. **Não move um centavo.**

    Apagar é seguro justamente porque a dívida nunca foi dinheiro: nenhum
    lançamento do ledger aponta para esta linha, e os pagamentos parciais já
    feitos continuam onde estavam, explicando os saldos que explicavam antes.
    A conservação de massa e a auditoria não sentem nada — e há teste dizendo
    isso.

    O registro fica: o dia em que alguém disser que foi perdoado por
    favorecimento, a resposta é uma linha com hora, autor e valor.
    """
    sessao = sessao or db.session
    exigir_credor(divida, credor, sessao)

    perdoado = devido(divida)
    devedor = divida.devedor.nome_usuario
    identificador = divida.id
    motivo = divida.motivo

    sessao.delete(divida)
    sessao.flush()
    registrar_acao(
        credor,
        "reino",
        alvo=devedor,
        detalhe=f"dívida #{identificador} perdoada, {perdoado} VVC",
        motivo=motivo,
        sessao=sessao,
    )
    return perdoado


# --- distribuição -----------------------------------------------------------


def distribuir(
    reino, operador, valor_por_pessoa, pessoas, motivo, token=None, sessao=None
):
    """Paga o mesmo valor a cada pessoa marcada. **Tudo ou nada.**

    Recusada inteira se o cofre não cobre todo mundo: pagar até acabar
    deixaria metade da lista recebendo e a outra metade não, decidido pela
    ordem alfabética. O operador prefere saber que não dá.

    **Idempotente no banco**, como :func:`cobrar`: o lote vira uma linha em
    ``distribuicao`` com ``token`` UNIQUE, e o segundo lote com o mesmo token
    bate no índice antes de qualquer dinheiro sair. Aqui isso pesa mais que na
    cobrança — cobrar duas vezes cria dívida repetida, que o operador apaga
    perdoando; distribuir duas vezes esvazia o cofre, e não há desfazer.

    O token da sessão do navegador continua existindo e barra o clique duplo
    sequencial; este índice barra o que a sessão não vê, que são dois POSTs
    simultâneos lendo o mesmo cookie antes de qualquer um gastá-lo.
    """
    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)

    motivo = (motivo or "").strip()
    if not motivo:
        raise MotivoObrigatorio("distribuição pede motivo")

    try:
        valor = para_decimal(str(valor_por_pessoa).strip().replace(",", "."))
    except (TypeError, AttributeError) as erro:
        raise ValorInvalido("valor inválido") from erro
    if valor <= ZERO:
        raise ValorInvalido("o valor precisa ser maior que zero")

    alvos = [p for p in pessoas if eh_cidadao(reino, p, sessao)]
    if not alvos:
        raise ValorInvalido("marque ao menos um cidadão")

    cofre = sessao.execute(
        select(Usuario).where(Usuario.id == reino.cofre_id).with_for_update()
    ).scalar_one()

    total = valor * len(alvos)
    if cofre.saldo < total:
        raise SaldoInsuficiente(
            f"o cofre tem {cofre.saldo} VVC e a distribuição pede {total} VVC"
        )

    # O lote entra ANTES dos repasses: se o token repetir, o índice recusa
    # aqui e nenhum centavo chegou a sair.
    lote = Distribuicao(
        reino_id=reino.id,
        operador_id=operador.id,
        valor_por_pessoa=valor,
        total=total,
        quantos=len(alvos),
        motivo=motivo,
        token=token or secrets.token_urlsafe(16),
    )
    sessao.add(lote)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("essa distribuição já foi feita") from erro

    for pessoa in alvos:
        mover(
            cofre,
            pessoa,
            valor,
            tipo=TIPO_REPASSE,
            motivo=f"{reino.nome}: {motivo}",
            ator=operador,
            sessao=sessao,
        )
    sessao.flush()

    registrar_acao(
        operador,
        "reino",
        alvo=reino.nome,
        detalhe=f"{total} VVC para {len(alvos)} cidadão(s)",
        motivo=motivo,
        sessao=sessao,
    )
    return total, alvos
