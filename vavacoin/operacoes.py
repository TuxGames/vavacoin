"""Operações da economia, todas construídas em cima do ``mover()``.

As operações privilegiadas (criar conta, emitir convite, resetar) exigem o
Banco Central passado explicitamente — ver :mod:`vavacoin.autoridade`. As do
jogador (resgatar o convite, transferir) não exigem nada além dele mesmo.

Nenhuma função daqui escreve em saldo: elas compõem chamadas ao
:func:`~vavacoin.moeda.mover`. Nenhuma faz ``commit`` — quem chama é dono da
transação. As operações compostas rodam dentro de um ``SAVEPOINT``, para que
uma falha no meio não deixe metade do trabalho para trás mesmo que quem
chamou esqueça de dar ``rollback``.
"""

import secrets

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .autoridade import exigir_banco_central
from .dinheiro import ZERO, para_decimal
from .erros import (
    ContaComHistorico,
    ConviteInvalido,
    ConviteJaResgatado,
    MotivoObrigatorio,
    SaldoInsuficiente,
    SupplyInsuficiente,
    UsuarioJaResgatou,
    ValorInvalido,
)
from .extensoes import db
from .moeda import (
    TIPO_AJUSTE,
    TIPO_EMISSAO,
    TIPO_ENCERRAMENTO,
    TIPO_QUEIMA,
    TIPO_RESET_RECOLHIMENTO,
    TIPO_RESET_REDISTRIBUICAO,
    TIPO_TRANSFERENCIA,
    mover,
    supply_emitido,
    verificar_conservacao,
)
from .modelos import Convite, Usuario, agora, banco_central, registrar_acao


def criar_usuario(
    nome_usuario, senha, nome_exibicao=None, autoridade=None, sessao=None
):
    """Cria uma conta com saldo zero. Poder do Banco Central.

    Cadastro é manual e por vontade da pessoa — nada de importar lista. A
    conta nasce com zero: entrar no site não é receber dinheiro, o dinheiro
    só chega pelo resgate do convite, saindo do Banco Central.
    """
    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    usuario = Usuario(nome_exibicao=nome_exibicao or nome_usuario, saldo=ZERO)
    usuario.definir_nome(nome_usuario)
    usuario.definir_senha(senha)
    sessao.add(usuario)
    sessao.flush()
    registrar_acao(bc, "conta", alvo=usuario.nome_usuario, sessao=sessao)
    return usuario


def criar_convite(codigo=None, destinatario=None, autoridade=None, sessao=None):
    """Cria um convite — o direito de uma pessoa entrar na economia.

    Poder do Banco Central. Um convite por aluno; quantos convites existem é
    o que controla quantas pessoas entram.
    """
    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    convite = Convite(
        codigo=codigo or secrets.token_urlsafe(8),
        destinatario=destinatario,
    )
    sessao.add(convite)
    sessao.flush()
    registrar_acao(
        bc, "convite", alvo=destinatario, detalhe=convite.codigo, sessao=sessao
    )
    return convite


def resgatar_convite(usuario, codigo, sessao=None):
    """Queima o convite em nome de ``usuario``. **Não move dinheiro.**

    Havia aqui um saque de 50 VVC do Banco Central. Não há mais: quem entra
    começa com saldo zero, e o dinheiro chega depois — por transferência de
    outra pessoa ou por ajuste do Banco Central. O convite continua sendo a
    única porta de entrada e continua valendo uma vez só; o que ele dá agora
    é o direito de existir na economia, não um valor.

    A queima é um ``UPDATE ... WHERE usuario_id IS NULL``: se duas
    requisições chegarem juntas com o mesmo código, só uma afeta linha. E a
    coluna ``usuario_id`` é UNIQUE, o que impede pelo banco que a mesma conta
    resgate dois códigos diferentes.
    """
    sessao = sessao or db.session

    with sessao.begin_nested():
        convite = sessao.execute(
            select(Convite).where(Convite.codigo == codigo).with_for_update()
        ).scalar_one_or_none()
        if convite is None:
            raise ConviteInvalido(f"código inexistente: {codigo!r}")
        if convite.resgatado:
            raise ConviteJaResgatado(f"código {codigo!r} já foi resgatado")

        if usuario.eh_banco_central:
            raise ValorInvalido("o Banco Central não resgata convite")

        try:
            queima = sessao.execute(
                update(Convite)
                .where(Convite.id == convite.id, Convite.usuario_id.is_(None))
                .values(usuario_id=usuario.id, resgatado_em=agora())
            )
        except IntegrityError as erro:
            # Colisão no UNIQUE de usuario_id: esta conta já tem convite.
            raise UsuarioJaResgatou(
                f"{usuario.nome_usuario} já resgatou um convite"
            ) from erro
        if queima.rowcount != 1:
            raise ConviteJaResgatado(f"código {codigo!r} já foi resgatado")
        sessao.expire(convite)

    return convite


def cadastrar_por_convite(
    nome_usuario, senha, codigo, nome_exibicao=None, sessao=None
):
    """Cria a conta e queima o convite. É a única porta de entrada.

    A conta nasce com **saldo zero**. Entrar na economia e ter dinheiro
    viraram duas coisas separadas: o convite dá a entrada, o dinheiro chega
    depois por transferência de alguém ou por ajuste do Banco Central.

    Roda inteira num ``SAVEPOINT``: convite inválido não deixa conta órfã, e
    conta que falha não queima convite.
    """
    sessao = sessao or db.session

    with sessao.begin_nested():
        # Conferido antes de criar qualquer coisa: sem convite bom, não há
        # cadastro, e nada chega a ser escrito.
        convite = sessao.execute(
            select(Convite).where(Convite.codigo == codigo).with_for_update()
        ).scalar_one_or_none()
        if convite is None:
            raise ConviteInvalido(f"código inexistente: {codigo!r}")
        if convite.resgatado:
            raise ConviteJaResgatado(f"código {codigo!r} já foi resgatado")

        usuario = Usuario(nome_exibicao=nome_exibicao or nome_usuario, saldo=ZERO)
        usuario.definir_nome(nome_usuario)
        usuario.definir_senha(senha)
        sessao.add(usuario)
        sessao.flush()
        resgatar_convite(usuario, codigo, sessao=sessao)

    return usuario


def cadastrar_sem_convite(nome_usuario, senha, nome_exibicao=None, sessao=None):
    """Cria a conta sem código, quando o cadastro está aberto.

    Decisão do dono: como quem entra começa com **saldo zero**, o convite
    deixou de ser o que segura a porta — ele segurava dinheiro numa época em
    que resgatar valia 50 VVC, e essa época acabou.

    O que **não** mudou: o convite continua existindo inteiro, continua sendo
    de uso único e continua queimando quando alguém entra por ele. Quem chega
    pelo link do Banco Central passa por :func:`cadastrar_por_convite` como
    sempre. Este caminho é o de quem chega sem código nenhum, e existir ou não
    é o que o interruptor ``cadastro_aberto`` decide.

    A conta nasce com saldo zero aqui também. Não há caminho de entrada que
    dê dinheiro a ninguém — nem com convite, nem sem.
    """
    sessao = sessao or db.session

    with sessao.begin_nested():
        usuario = Usuario(nome_exibicao=nome_exibicao or nome_usuario, saldo=ZERO)
        usuario.definir_nome(nome_usuario)
        usuario.definir_senha(senha)
        sessao.add(usuario)
        sessao.flush()

    return usuario


def transferir(origem, destino, valor, motivo=None, sessao=None):
    """Transferência entre duas pessoas — o uso normal da moeda.

    Existe porque a moeda paga coisa da vida real (explicar uma questão, o
    lugar na fila) e o site só registra. É um apelido fino de ``mover()``,
    para que nenhuma camada de cima precise escolher o ``tipo``.
    """
    return mover(
        origem, destino, valor, tipo=TIPO_TRANSFERENCIA, motivo=motivo, sessao=sessao
    )


def resetar_economia(
    autoridade=None, sessao=None, saque=ZERO, motivo="reset da economia"
):
    """Recolhe o saldo de todos para o Banco Central.

    Poder do Banco Central. Recolhe de **todo mundo, sem exceção — inclusive
    o dono do cassino**: sem temporada, o reset é o único mecanismo contra a
    concentração, e uma conta isenta o esvaziaria de sentido.

    Existe como operação de verdade, com ledger e conservação verificada nas
    duas pontas, porque a alternativa é UPDATE na unha no dia do reset — que
    é exatamente como o Benbals ganhou o bug de saldo sumindo.

    Com o fim do saque inicial, o padrão passou a ser **só recolher**: não há
    mais um valor óbvio para devolver a cada um. ``saque`` continua existindo
    para quando o Banco Central quiser redistribuir alguma coisa — aí vai
    para quem tem convite resgatado, que é quem está na economia.

    Roda inteiro dentro de um ``SAVEPOINT``: ou a economia inteira volta ao
    estado inicial, ou nada muda.
    """
    sessao = sessao or db.session
    exigir_banco_central(autoridade, sessao)
    saque = para_decimal(saque)
    bc = banco_central(sessao)
    if bc is None:
        raise SupplyInsuficiente("gênese ainda não rodou; não há Banco Central")

    verificar_conservacao(sessao)

    participantes = list(
        sessao.execute(
            select(Usuario)
            .join(Convite, Convite.usuario_id == Usuario.id)
            .where(Usuario.eh_banco_central.is_(False))
            .order_by(Usuario.id)
        ).scalars()
    )
    disponivel = supply_emitido(sessao)
    necessario = saque * len(participantes)
    if necessario > disponivel:
        raise SupplyInsuficiente(
            f"{len(participantes)} participantes a {saque} exigem {necessario}, "
            f"acima do supply de {disponivel}; escolha um valor menor"
        )

    with sessao.begin_nested():
        contas = sessao.execute(
            select(Usuario)
            .where(Usuario.eh_banco_central.is_(False))
            .order_by(Usuario.id)
        ).scalars()
        for conta in contas:
            if conta.saldo > ZERO:
                mover(
                    conta,
                    bc,
                    conta.saldo,
                    tipo=TIPO_RESET_RECOLHIMENTO,
                    motivo=motivo,
                    sessao=sessao,
                )

        if saque > ZERO:
            for conta in participantes:
                mover(
                    bc,
                    conta,
                    saque,
                    tipo=TIPO_RESET_REDISTRIBUICAO,
                    motivo=motivo,
                    sessao=sessao,
                )

    verificar_conservacao(sessao)
    registrar_acao(
        bc,
        "reset",
        detalhe=f"{len(participantes)} participantes a {saque} VVC",
        motivo=motivo,
        sessao=sessao,
    )
    return len(participantes)


def ajustar_saldo(alvo, novo_saldo, motivo, autoridade=None, sessao=None):
    """Deixa o saldo de ``alvo`` valendo ``novo_saldo``. Poder do administrador.

    Existe para consertar valor errado. **Ajustar para cima cunha moeda** —
    é uma decisão registrada, não um acidente —, e por isso o caminho é o
    ledger, não um ``UPDATE`` na conta.

    O que acontece, conforme o sinal da diferença:

    - **Para cima**: se o Banco Central não tiver o bastante em saldo não
      emitido, a diferença que falta é **emitida** (uma linha ``emissao``,
      sem origem, que é o que faz o supply crescer); em seguida o valor sai
      do Banco Central para a pessoa numa linha ``ajuste``. Só se cunha o que
      falta: dinheiro parado no BC é usado antes.
    - **Para baixo**: a diferença volta do alvo para o Banco Central, também
      como ``ajuste``. O dinheiro não é queimado — volta a ser *não emitido*,
      exatamente como no dia zero. Quem quiser ver o total cunhado tem
      ``moeda.total_cunhado_depois_da_genese()``.

    **No próprio Banco Central é diferente**: ele é o único lado do mundo, e
    não há de onde tirar nem para onde mandar. Subir emite, baixar **queima**
    — e é assim que o supply desce.

    Passando pelo ledger, a auditoria continua fechando depois de um ajuste.
    Esse é o ponto: um alarme que dispara toda vez que o administrador
    conserta algo é um alarme que se aprende a ignorar.
    """
    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)

    motivo = (motivo or "").strip()
    if not motivo:
        raise MotivoObrigatorio(
            "ajuste de saldo exige motivo: é ele que explica a mudança depois"
        )

    if isinstance(alvo, int):
        alvo = sessao.get(Usuario, alvo)
    if alvo is None:
        raise ValorInvalido("conta inexistente")


    novo_saldo = para_decimal(novo_saldo)
    if novo_saldo < ZERO:
        raise ValorInvalido("saldo não pode ficar negativo")

    verificar_conservacao(sessao)
    anterior = alvo.saldo
    diferenca = novo_saldo - anterior
    if diferenca == ZERO:
        registrar_acao(
            bc,
            "ajuste",
            alvo=alvo.nome_usuario,
            detalhe=f"sem mudança (já valia {anterior} VVC)",
            motivo=motivo,
            sessao=sessao,
        )
        return None

    with sessao.begin_nested():
        if alvo.eh_banco_central:
            # O Banco Central é o único lado do mundo: subir o saldo dele é
            # emitir, baixar é queimar. Não há de onde tirar nem para onde
            # mandar sem mentir sobre o que está em circulação.
            if diferenca > ZERO:
                transacao = mover(
                    None, bc, diferenca, tipo=TIPO_EMISSAO,
                    motivo=motivo, ator=bc, sessao=sessao,
                )
            else:
                transacao = mover(
                    bc, None, -diferenca, tipo=TIPO_QUEIMA,
                    motivo=motivo, ator=bc, sessao=sessao,
                )
        elif diferenca > ZERO:
            faltante = diferenca - bc.saldo
            if faltante > ZERO:
                # Cunhagem: o supply cresce exatamente aqui, e a linha diz
                # quanto, quando e por quê.
                mover(
                    None,
                    bc,
                    faltante,
                    tipo=TIPO_EMISSAO,
                    motivo=f"cunhado para ajustar {alvo.nome_usuario}: {motivo}",
                    ator=bc,
                    sessao=sessao,
                )
            transacao = mover(
                bc, alvo, diferenca, tipo=TIPO_AJUSTE, motivo=motivo,
                ator=bc, sessao=sessao,
            )
        else:
            transacao = mover(
                alvo, bc, -diferenca, tipo=TIPO_AJUSTE, motivo=motivo,
                ator=bc, sessao=sessao,
            )

    verificar_conservacao(sessao)
    registrar_acao(
        bc,
        "ajuste",
        alvo=alvo.nome_usuario,
        detalhe=f"de {anterior} para {novo_saldo} VVC",
        motivo=motivo,
        sessao=sessao,
    )
    return transacao


# --- apagar e encerrar conta ------------------------------------------------
#
# O `delete_user` do Benbals é o bug que este bloco existe para não repetir:
# lá, apagar uma pessoa **faz o saldo dela sumir**, o que quebra o invariante
# de supply, e só não estoura na prática porque falha antes em erro de chave
# estrangeira, em dezoito tabelas. Quem "consertar" as FKs sem olhar isso
# destrava o vazamento.
#
# Aqui a auditoria reconstrói cada saldo somando o ledger. Apagar uma conta
# que tem lançamentos deixaria linhas apontando para ninguém e a auditoria
# passaria a acusar **para sempre** — um alarme que dispara à toa é um alarme
# que se aprende a ignorar.
#
# Por isso são duas operações e não uma, e qual delas vale não é escolha de
# quem clica:
#
# - **Conta virgem** (saldo zero e nenhum rastro): apaga de verdade. Apagar
#   não mente sobre nada, porque não há nada que ela explique.
# - **Conta com história**: não apaga. Encerra — o saldo volta ao Banco
#   Central por `mover()`, com motivo, e as linhas do ledger ficam. O extrato
#   de quem transacionou com ela continua fazendo sentido.


def _colunas_de_rastro():
    """Toda ponta onde uma conta deixa rastro que precisaria explicá-la depois.

    O ledger nas três (origem, destino e ator), o diário do god mode, e as
    rodadas dos quatro jogos. **Explícita de propósito**, ao contrário de
    ``_colunas_que_apontam_para_conta``, que sai do metadata: aqui esquecer
    uma tabela faz uma conta com histórico passar por virgem e ser apagada em
    silêncio. Quando entrar o quinto jogo, esta lista tem de falhar em revisão
    de código — não em produção.
    """
    from .modelos import (
        RegistroAdministrativo,
        RodadaCrash,
        RodadaDados,
        RodadaMines,
        RodadaTorre,
        Transacao,
    )

    return [
        Transacao.origem_id,
        Transacao.destino_id,
        Transacao.ator_id,
        RegistroAdministrativo.ator_id,
        RodadaMines.jogador_id,
        RodadaCrash.jogador_id,
        RodadaTorre.jogador_id,
        RodadaDados.jogador_id,
    ]


def _contas_com_rastro(ids, sessao):
    """Quais destes ids aparecem em alguma ponta. Uma consulta por ponta.

    O painel do Banco Central pergunta isso de toda conta da tabela ao mesmo
    tempo. Perguntando uma por uma, eram tantas idas ao banco quanto contas —
    e o plano grátis tem um worker só, então cada ida é fila para a turma
    inteira. Aqui o custo não cresce com o número de contas.

    Para uma conta só a conta é a mesma: o laço para assim que ela é achada.
    """
    restantes = [conta_id for conta_id in ids if conta_id is not None]
    achados = set()
    for coluna in _colunas_de_rastro():
        if not restantes:
            break
        achados.update(
            valor
            for (valor,) in sessao.execute(
                select(coluna).where(coluna.in_(restantes)).distinct()
            )
            if valor is not None
        )
        restantes = [conta_id for conta_id in restantes if conta_id not in achados]
    return achados


def _conta_tem_rastro(alvo, sessao):
    """A conta aparece em alguma linha que precisaria explicá-la depois?"""
    return alvo.id in _contas_com_rastro([alvo.id], sessao)


def _exigir_conta_removivel(alvo, sessao):
    """As contas que nem apagar nem encerrar podem tocar.

    Banco Central e casa do Caladinho não são pessoas: são peças do sistema, e
    o ledger inteiro se apoia nelas. O dono do cassino sai da frente passando
    a posse primeiro — apagar quem responde pela casa deixaria a casa órfã por
    acidente, e trocar de dono é um comando que já existe.
    """
    from .caladinho import casa as casa_do_cassino
    from .modelos import OperadorDoReino

    if alvo.eh_banco_central:
        raise ValorInvalido("o Banco Central não se apaga")
    if alvo.eh_cassino:
        raise ValorInvalido("a casa do Caladinho não se apaga")
    if alvo.eh_cofre:
        raise ValorInvalido("o cofre de um reino não se apaga")
    if alvo.eh_removida:
        raise ValorInvalido("essa conta já foi removida")

    conta_da_casa = casa_do_cassino(sessao)
    if conta_da_casa is not None and conta_da_casa.dono_id == alvo.id:
        raise ValorInvalido(
            f"{alvo.nome_usuario} é dono do Caladinho; passe a posse antes"
        )

    # O operador é o poder do reino, e ele mora numa pessoa. Apagar quem opera
    # deixaria o reino sem quem cobre, distribua ou responda pedido — sem
    # ninguém perceber, porque nada quebra na hora. Tira o papel primeiro.
    opera = sessao.execute(
        select(OperadorDoReino.reino_id)
        .where(OperadorDoReino.usuario_id == alvo.id)
        .limit(1)
    ).first()
    if opera is not None:
        raise ValorInvalido(
            f"{alvo.nome_usuario} opera um reino; tire o papel antes"
        )


def destino_da_conta(alvo, sessao=None):
    """O que vai acontecer com esta conta: ``"apagar"`` ou ``"encerrar"``.

    Existe para a tela **dizer antes** qual dos dois é o caso, e para o botão
    de apagar de verdade nem aparecer onde ele não vale. Consultada de novo na
    hora de executar: entre desenhar a tela e clicar, a conta pode ter
    recebido dinheiro.
    """
    sessao = sessao or db.session
    return destinos_das_contas([alvo], sessao)[alvo.id]


def destinos_das_contas(contas, sessao=None):
    """O mesmo de :func:`destino_da_conta`, para uma lista inteira.

    Existe porque o painel do Banco Central desenha uma tabela e precisava do
    destino de cada linha. A **decisão** mora aqui e só aqui — a função de uma
    conta chama esta com uma lista de um. Duas implementações da mesma regra
    divergem, e aqui divergir seria oferecer "apagar" numa conta com
    histórico: o botão que o Benbals tem e que faz saldo sumir.
    """
    sessao = sessao or db.session
    contas = list(contas)
    com_rastro = _contas_com_rastro([conta.id for conta in contas], sessao)
    return {
        conta.id: (
            "encerrar"
            if conta.saldo != ZERO or conta.id in com_rastro
            else "apagar"
        )
        for conta in contas
    }


def apagar_conta(alvo, autoridade=None, sessao=None):
    """Apaga de verdade uma conta que não explica nada. Poder do Banco Central.

    Recusa qualquer conta com saldo ou com rastro — e a recusa é do servidor,
    não da tela. Este é o ponto exato em que o Benbals vaza dinheiro, e a
    diferença é uma condição, não um cuidado.

    O convite que a conta resgatou, se houver, vai junto. Ele registrava que
    aquela pessoa entrou, e a entrada está sendo apagada como se não tivesse
    acontecido; deixá-lo "livre" faria o código voltar a valer para quem o
    tivesse guardado, e deixá-lo apontando para ninguém seria lixo. Some com a
    conta, e o Banco Central emite outro com um clique.
    """
    from .autoridade import exigir_banco_central
    from .modelos import registrar_acao

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    _exigir_conta_removivel(alvo, sessao)

    if alvo.saldo != ZERO:
        raise ContaComHistorico(
            f"{alvo.nome_usuario} tem {alvo.saldo} VVC; encerre em vez de apagar"
        )
    if _conta_tem_rastro(alvo, sessao):
        raise ContaComHistorico(
            f"{alvo.nome_usuario} tem histórico; encerre em vez de apagar"
        )

    nome = alvo.nome_usuario
    convite = sessao.execute(
        select(Convite).where(Convite.usuario_id == alvo.id)
    ).scalar_one_or_none()
    detalhe = "conta apagada"
    if convite is not None:
        detalhe = f"conta apagada; convite {convite.codigo} apagado junto"
        sessao.delete(convite)

    sessao.delete(alvo)
    sessao.flush()
    registrar_acao(bc, "conta", alvo=nome, detalhe=detalhe, sessao=sessao)
    return nome


def encerrar_conta(alvo, motivo, autoridade=None, sessao=None):
    """Encerra a conta e devolve o saldo ao Banco Central. Poder do BC.

    O saldo volta por ``mover()``, com motivo — não some, não é zerado por
    ``UPDATE``, e aparece no extrato dos dois lados. É a diferença inteira
    entre isto e o ``delete_user`` do Benbals.

    As linhas do ledger ficam onde estão. Quem transferiu para esta conta
    continua vendo a transferência, e a auditoria continua explicando cada
    centavo — que é o motivo de a conta encerrada continuar existindo como
    linha em vez de virar um id órfão.
    """
    from .autoridade import exigir_banco_central
    from .modelos import registrar_acao

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    _exigir_conta_removivel(alvo, sessao)

    motivo = (motivo or "").strip()
    if not motivo:
        raise MotivoObrigatorio("encerrar conta pede motivo")

    if alvo.encerrada:
        raise ValorInvalido(f"{alvo.nome_usuario} já está encerrada")

    devolvido = alvo.saldo
    if devolvido > ZERO:
        mover(
            alvo,
            bc,
            devolvido,
            tipo=TIPO_ENCERRAMENTO,
            motivo=motivo,
            ator=bc,
            sessao=sessao,
        )

    alvo.encerrada_em = agora()
    solto = _desligar_do_reino(alvo, sessao)
    sessao.flush()
    registrar_acao(
        bc,
        "conta",
        alvo=alvo.nome_usuario,
        detalhe=f"conta encerrada; {devolvido} VVC devolvidos{solto}",
        motivo=motivo,
        sessao=sessao,
    )
    return alvo


def _desligar_do_reino(alvo, sessao):
    """Tira a conta de cidadania ativa e fecha pendência aberta em nome dela.

    Chamado ao encerrar. Sem isto, uma conta encerrada continuava cidadã: ela
    aparecia na tabela do reino, no ranking do reino, e — o que importa de
    verdade — na lista para quem o reino **distribui** dinheiro. Repasse a uma
    conta que ninguém mais abre é dinheiro parado para sempre.

    A pendência fecha como ``recusado`` sem responsável: ninguém respondeu, a
    conta acabou. Deixá-la pendente prenderia a dupla pessoa/reino no índice
    de pendência única para sempre, e o reino nunca mais poderia convidar
    aquele nome — pendência sem saída é exatamente o que não pode existir.

    A **dívida não é tocada**, pelo mesmo motivo de ``sair_do_reino``: ela é
    entre quem cobrou e quem deve, e encerrar a conta não é quitação.
    """
    from .modelos import Cidadania, PedidoDeCidadania

    saiu = sessao.execute(
        update(Cidadania)
        .where(Cidadania.usuario_id == alvo.id, Cidadania.saiu_em.is_(None))
        .values(saiu_em=agora())
    ).rowcount
    fechadas = sessao.execute(
        update(PedidoDeCidadania)
        .where(
            PedidoDeCidadania.usuario_id == alvo.id,
            PedidoDeCidadania.estado == PedidoDeCidadania.PENDENTE,
        )
        .values(estado=PedidoDeCidadania.RECUSADO, respondido_em=agora())
    ).rowcount

    partes = []
    if saiu:
        partes.append("saiu do reino")
    if fechadas:
        partes.append(f"{fechadas} pendência(s) de cidadania fechada(s)")
    return ("; " + "; ".join(partes)) if partes else ""


def _nova_sombra(sessao):
    """Cria a conta-sombra que vai herdar as linhas de uma conta apagada.

    **Uma por remoção**, e não uma compartilhada por todas — a ideia da conta
    única é a primeira que ocorre e ela quebra em dois lugares:

    1. O ``CHECK`` ``origem_id <> destino_id``. Duas pessoas que transferiram
       entre si, ambas apagadas, teriam aquela linha apontando para a mesma
       sombra dos dois lados, e o banco recusaria a segunda remoção.
    2. Pior, e silencioso até rodar a auditoria: ``conferir_ledger`` confere o
       ``saldo_origem_depois`` de cada linha contra o saldo reconstruído
       naquele instante. Uma sombra compartilhada intercalaria o histórico de
       duas pessoas, e **toda** linha reatribuída passaria a divergir — a
       auditoria acusaria para sempre, sem que nada de errado tivesse
       acontecido com o dinheiro.

    Com uma sombra por remoção o replay é idêntico: ela recebe exatamente as
    linhas de uma conta só, na mesma ordem, e termina em zero como a conta
    terminou. Na tela todas se chamam "conta removida".
    """
    numero = 1
    while sessao.execute(
        select(Usuario.id).where(Usuario.nome_normalizado == f"removida-{numero}")
    ).first():
        numero += 1

    sombra = Usuario(
        # Todas se chamam igual na tela — é o que o extrato da contraparte
        # mostra no lugar do nome que sumiu. Só o nome normalizado é único,
        # e é ele que numera; guardar o nome antigo aqui seria manter à vista
        # exatamente a identidade que a remoção existe para apagar.
        nome_usuario="conta removida",
        nome_normalizado=f"removida-{numero}",
        nome_exibicao="conta removida",
        # Sem senha: não entra pela tela. Já encerrada: não recebe
        # transferência. Saldo escondido: não é gente, não ranqueia.
        senha_hash=None,
        eh_removida=True,
        saldo_publico=False,
        saldo=ZERO,
        encerrada_em=agora(),
    )
    sessao.add(sombra)
    sessao.flush()
    return sombra


def _colunas_que_apontam_para_conta():
    """Toda coluna do banco que é chave estrangeira para ``usuario.id``.

    Derivada do metadata, e não escrita à mão: a tabela nova de amanhã já
    entra sozinha, tanto na contagem quanto na reatribuição.
    """
    return [
        (tabela, coluna)
        for tabela in db.metadata.sorted_tables
        for coluna in tabela.columns
        if any(
            chave.target_fullname == "usuario.id" for chave in coluna.foreign_keys
        )
    ]


def _reatribuir(alvo, sombra, sessao):
    """Aponta para a sombra tudo que apontava para a conta. Varre o metadata.

    Aqui a lista é **derivada**, e não escrita à mão como em
    ``_conta_tem_rastro`` — de propósito, e a diferença tem motivo. Lá,
    esquecer uma tabela faz uma conta com histórico passar por virgem e ser
    apagada em silêncio: erro caro, então a lista tem de doer em revisão de
    código. Aqui, esquecer uma tabela estoura em violação de chave
    estrangeira na hora (o SQLite roda com ``PRAGMA foreign_keys`` ligado), e
    o banco recusa a remoção inteira. Varrer o metadata é o que garante que a
    tabela nova de amanhã já entre.

    O que **não** pode acontecer é uma linha do ledger ficar sem origem:
    ``origem_id`` nulo é emissão, e apagar gente viraria cunhagem. Por isso
    não existe aqui nenhum ``SET NULL`` — só reatribuição para uma conta que
    existe.
    """
    movidas = 0
    for tabela, coluna in _colunas_que_apontam_para_conta():
        movidas += sessao.execute(
            tabela.update().where(coluna == alvo.id).values({coluna.name: sombra.id})
        ).rowcount
    return movidas


def referencias_da_conta(alvo, sessao=None):
    """Quantas linhas apontam para esta conta — o número que a tela mostra.

    Sai da mesma lista de colunas que :func:`_reatribuir` usa para mover: se
    a contagem viesse de outro lugar, o número da tela e o da remoção
    começariam a divergir no dia em que uma tabela nova entrasse, e a tela
    prometeria uma coisa enquanto o banco faz outra.
    """
    sessao = sessao or db.session
    total = 0
    for tabela, coluna in _colunas_que_apontam_para_conta():
        total += sessao.execute(
            select(func.count()).select_from(tabela).where(coluna == alvo.id)
        ).scalar_one()
    return total


def remover_conta(alvo, motivo, autoridade=None, sessao=None):
    """Apaga a conta de verdade, mantendo o ledger inteiro. Poder do BC.

    É o degrau acima de :func:`encerrar_conta`, para quando encerrar não
    basta — a conta continua listada no painel e o Banco Central quer sumir
    com ela. A linha de ``usuario`` deixa de existir: nome, apelido, senha,
    tudo.

    A ordem importa, e é esta:

    1. **Encerra**, se ainda não estava. O saldo volta ao Banco Central por
       ``mover()``, com motivo. A conta chega em zero antes de sumir — nunca
       se apaga dinheiro junto com a pessoa.
    2. **Cria a sombra** e reatribui a ela toda linha que apontava para a
       conta. O ledger continua completo: quem transferiu para essa pessoa
       continua vendo a transferência, agora como "conta removida".
    3. **Apaga a linha.**

    O convite que ela resgatou vai junto, pelo mesmo motivo de
    :func:`apagar_conta`: a entrada está sendo apagada como se não tivesse
    acontecido.

    **A armadilha deste projeto, e por que ela não acontece aqui.** O caminho
    óbvio para apagar alguém é anular as referências. Aqui isso seria o pior
    estrago possível: lançamento sem ``origem_id`` **é emissão** — é o ramo do
    ``mover()`` que cunha, e o ``CHECK`` da tabela até permite quando o tipo é
    ``emissao``. Anular a origem das linhas de quem saiu faria a auditoria ler
    o histórico da pessoa como dinheiro criado do nada, e o supply passaria a
    mentir. Nada aqui anula referência: toda linha troca de dono para uma
    conta que existe e fecha em zero.

    Conservação e auditoria continuam fechando depois — e os testes que
    provam isso são o contrato desta função.
    """
    from .modelos import registrar_acao

    sessao = sessao or db.session
    bc = exigir_banco_central(autoridade, sessao)
    _exigir_conta_removivel(alvo, sessao)

    motivo = (motivo or "").strip()
    if not motivo:
        raise MotivoObrigatorio("remover conta pede motivo")

    with sessao.begin_nested():
        if not alvo.encerrada:
            encerrar_conta(alvo, motivo, autoridade=bc, sessao=sessao)

        nome = alvo.nome_usuario
        convite = sessao.execute(
            select(Convite).where(Convite.usuario_id == alvo.id)
        ).scalar_one_or_none()
        if convite is not None:
            sessao.delete(convite)
            sessao.flush()

        sombra = _nova_sombra(sessao)
        movidas = _reatribuir(alvo, sombra, sessao)

        sessao.delete(alvo)
        sessao.flush()
        registrar_acao(
            bc,
            "conta",
            alvo=nome,
            detalhe=(
                f"conta removida; {movidas} referência(s) passaram a "
                f"{sombra.nome_usuario}"
            ),
            motivo=motivo,
            sessao=sessao,
        )

    verificar_conservacao(sessao)
    return nome
