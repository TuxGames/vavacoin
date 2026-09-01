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

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .autoridade import exigir_banco_central
from .constantes import SAQUE_INICIAL, SUPPLY_TOTAL
from .dinheiro import ZERO, para_decimal
from .erros import (
    ConviteInvalido,
    ConviteJaResgatado,
    SaldoInsuficiente,
    SupplyInsuficiente,
    UsuarioJaResgatou,
    ValorInvalido,
)
from .extensoes import db
from .moeda import (
    TIPO_RESET_RECOLHIMENTO,
    TIPO_RESET_REDISTRIBUICAO,
    TIPO_SAQUE_INICIAL,
    TIPO_TRANSFERENCIA,
    mover,
    verificar_conservacao,
)
from .modelos import Convite, Usuario, agora, banco_central


def criar_usuario(
    nome_usuario, senha, nome_exibicao=None, autoridade=None, sessao=None
):
    """Cria uma conta com saldo zero. Poder do Banco Central.

    Cadastro é manual e por vontade da pessoa — nada de importar lista. A
    conta nasce com zero: entrar no site não é receber dinheiro, o dinheiro
    só chega pelo resgate do convite, saindo do Banco Central.
    """
    sessao = sessao or db.session
    exigir_banco_central(autoridade, sessao)
    usuario = Usuario(
        nome_usuario=nome_usuario,
        nome_exibicao=nome_exibicao or nome_usuario,
        saldo=ZERO,
    )
    usuario.definir_senha(senha)
    sessao.add(usuario)
    sessao.flush()
    return usuario


def criar_convite(codigo=None, destinatario=None, autoridade=None, sessao=None):
    """Cria um convite — o direito de uma pessoa sacar os 50 iniciais.

    Poder do Banco Central. Um convite por aluno; quantos convites existem é
    o que controla quantas pessoas entram, e o supply não cresce com eles.
    """
    sessao = sessao or db.session
    exigir_banco_central(autoridade, sessao)
    convite = Convite(
        codigo=codigo or secrets.token_urlsafe(8),
        destinatario=destinatario,
    )
    sessao.add(convite)
    sessao.flush()
    return convite


def resgatar_convite(usuario, codigo, sessao=None, saque=SAQUE_INICIAL):
    """Saca os 50 iniciais do Banco Central em nome de ``usuario``.

    O dinheiro **sai do Banco Central** por ``mover()``: não é criado. Se o
    Banco Central não tiver saldo não emitido suficiente, a operação falha —
    a resposta registrada para "a turma passou de 100" é reduzir o saque, não
    cunhar moeda.

    A queima do convite é um ``UPDATE ... WHERE usuario_id IS NULL``: se duas
    requisições chegarem juntas com o mesmo código, só uma afeta linha. E a
    coluna ``usuario_id`` é UNIQUE, o que impede pelo banco que a mesma conta
    resgate dois códigos diferentes.
    """
    sessao = sessao or db.session
    saque = para_decimal(saque)
    bc = banco_central(sessao)
    if bc is None:
        raise ConviteInvalido("gênese ainda não rodou; não há Banco Central")

    verificar_conservacao(sessao)

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

        try:
            transacao = mover(
                bc,
                usuario,
                saque,
                tipo=TIPO_SAQUE_INICIAL,
                motivo=f"saque inicial pelo convite {codigo}",
                sessao=sessao,
            )
        except SaldoInsuficiente as erro:
            raise SupplyInsuficiente(
                f"o Banco Central não tem {saque} não emitidos: {erro}"
            ) from erro

    verificar_conservacao(sessao)
    return transacao


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
    autoridade=None, sessao=None, saque=SAQUE_INICIAL, motivo="reset da economia"
):
    """Recolhe tudo ao Banco Central e redistribui os 50 por pessoa.

    Poder do Banco Central. Recolhe de **todo mundo, sem exceção — inclusive
    o dono do cassino**: sem temporada, o reset é o único mecanismo contra a
    concentração, e uma conta isenta o esvaziaria de sentido.

    Existe como operação de verdade, com ledger e conservação verificada nas
    duas pontas, porque a alternativa é UPDATE na unha no dia do reset — que
    é exatamente como o Benbals ganhou o bug de saldo sumindo.

    Redistribui para quem tem convite resgatado: o direito aos 50 é da
    pessoa, e o reset não muda quem está na economia.

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
    necessario = saque * len(participantes)
    if necessario > SUPPLY_TOTAL:
        raise SupplyInsuficiente(
            f"{len(participantes)} participantes a {saque} exigem {necessario}, "
            f"acima do supply de {SUPPLY_TOTAL}; reduza o saque inicial"
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
    return len(participantes)
