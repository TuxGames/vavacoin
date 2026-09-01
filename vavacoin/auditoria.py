"""Leitura da economia: extrato e prova de que nada vazou.

Depois que o administrador ganhou o poder de ajustar saldo, "nada vazou"
deixou de significar "a soma é 5.000": significa que a soma dos saldos bate
com o que o ledger diz ter sido emitido. Cunhagem aparece como uma linha, e
uma linha é uma decisão registrada — o que a auditoria persegue é saldo que
mudou **sem** linha nenhuma.

Nada aqui escreve. O ponto destas funções é que **qualquer pessoa possa
conferir** o que o núcleo monetário afirma, sem confiar nele: a conservação
de massa e a reconstrução do ledger são checagens independentes do caminho
de escrita.

A segunda é a mais dura. Se alguém alterar um saldo por fora do ``mover()``
— um UPDATE na mão, um script de "conserto" —, a soma pode até continuar
5.000,00, mas o ledger deixa de explicar os saldos. :func:`conferir_ledger`
é o que acusa isso.
"""

from decimal import Decimal

from sqlalchemy import select

from .constantes import SUPPLY_INICIAL, SUPPLY_MAXIMO
from .dinheiro import ZERO
from .erros import MassaViolada
from .extensoes import db
from .moeda import cabe_emitir, soma_saldos, supply_emitido
from .modelos import Convite, Transacao, Usuario, banco_central


def extrato(usuario, limite=50, sessao=None):
    """Últimas transações que tocaram a conta, mais recentes primeiro."""
    sessao = sessao or db.session
    usuario_id = usuario.id if isinstance(usuario, Usuario) else usuario
    return list(
        sessao.execute(
            select(Transacao)
            .where(
                (Transacao.origem_id == usuario_id)
                | (Transacao.destino_id == usuario_id)
            )
            .order_by(Transacao.id.desc())
            .limit(limite)
        ).scalars()
    )


def linhas_extrato(usuario, limite=50, sessao=None):
    """Extrato já com sinal e contraparte resolvidos, pronto para ler.

    Cada linha diz quanto entrou ou saiu (``valor_com_sinal``), com quem foi
    e qual era o saldo depois — o suficiente para a pessoa conferir a própria
    conta sem precisar interpretar o ledger bruto.
    """
    sessao = sessao or db.session
    usuario_id = usuario.id if isinstance(usuario, Usuario) else usuario
    nomes = dict(sessao.execute(select(Usuario.id, Usuario.nome_usuario)).all())

    linhas = []
    for transacao in extrato(usuario_id, limite=limite, sessao=sessao):
        saiu = transacao.origem_id == usuario_id
        # Quem está do outro lado: se saiu, é o destino; se entrou, a origem.
        # Na gênese não há outro lado — o dinheiro não veio de ninguém.
        contraparte_id = transacao.destino_id if saiu else transacao.origem_id
        linhas.append(
            {
                "id": transacao.id,
                "quando": transacao.criado_em,
                "tipo": transacao.tipo,
                "motivo": transacao.motivo,
                "contraparte": nomes.get(contraparte_id, "—"),
                "valor_com_sinal": -transacao.valor if saiu else transacao.valor,
                "saldo_depois": (
                    transacao.saldo_origem_depois
                    if saiu
                    else transacao.saldo_destino_depois
                ),
            }
        )
    return linhas


def resumo_da_conta(usuario, sessao=None):
    """Quanto entrou, quanto saiu e quantos movimentos teve a conta.

    Reconstruído do ledger, como todo o resto: não há contador guardado em
    lugar nenhum que possa ficar dessincronizado do que aconteceu de verdade.
    """
    sessao = sessao or db.session
    usuario_id = usuario.id if isinstance(usuario, Usuario) else usuario

    recebido = ZERO
    enviado = ZERO
    total = 0
    for transacao in sessao.execute(
        select(Transacao).where(
            (Transacao.origem_id == usuario_id)
            | (Transacao.destino_id == usuario_id)
        )
    ).scalars():
        total += 1
        if transacao.origem_id == usuario_id:
            enviado += transacao.valor
        else:
            recebido += transacao.valor

    return {"recebido": recebido, "enviado": enviado, "transacoes": total}


def estado_da_economia(sessao=None):
    """Retrato da economia: quanto existe, quanto circula, quanto sobrou no BC.

    "Não emitido" é o saldo do Banco Central: dinheiro que existe mas ainda
    não entrou em circulação. Separar isso de "em circulação" é o que deixa
    ver se a economia está viva ou se travou em alguém.
    """
    sessao = sessao or db.session
    bc = banco_central(sessao)
    total = soma_saldos(sessao)
    nao_emitido = bc.saldo if bc is not None else ZERO

    contas = list(
        sessao.execute(
            select(Usuario).where(Usuario.eh_banco_central.is_(False))
        ).scalars()
    )
    participantes = sessao.execute(
        select(db.func.count(Convite.id)).where(Convite.usuario_id.is_not(None))
    ).scalar_one()

    emitido = supply_emitido(sessao)
    return {
        "supply_inicial": SUPPLY_INICIAL,
        "supply_atual": emitido,
        "supply_maximo": SUPPLY_MAXIMO,
        "cabe_emitir": cabe_emitir(sessao),
        "cunhado_depois": emitido - SUPPLY_INICIAL,
        "supply_esperado": emitido,
        "soma_dos_saldos": total,
        "diferenca": total - emitido,
        "conservado": total == emitido,
        "nao_emitido": nao_emitido,
        "em_circulacao": total - nao_emitido,
        "contas": len(contas),
        "participantes": participantes,
        "transacoes": sessao.execute(
            select(db.func.count(Transacao.id))
        ).scalar_one(),
    }


def conferir_ledger(sessao=None):
    """Reconstrói todo saldo a partir do ledger e compara com o gravado.

    Percorre as transações em ordem, aplica débito e crédito num saldo
    paralelo começando do zero, e no fim compara com o que está na tabela de
    usuários. De quebra, confere o ``saldo_*_depois`` gravado em cada linha
    contra o saldo reconstruído naquele instante — é isso que denuncia uma
    linha adulterada, e não só um saldo adulterado.

    :returns: dicionário com ``ok`` e as listas de divergências encontradas.
    """
    sessao = sessao or db.session
    reconstruido = {}
    linhas_inconsistentes = []

    for transacao in sessao.execute(
        select(Transacao).order_by(Transacao.id)
    ).scalars():
        if transacao.origem_id is not None:
            reconstruido[transacao.origem_id] = (
                reconstruido.get(transacao.origem_id, ZERO) - transacao.valor
            )
            if transacao.saldo_origem_depois != reconstruido[transacao.origem_id]:
                linhas_inconsistentes.append(
                    {
                        "transacao": transacao.id,
                        "lado": "origem",
                        "gravado": transacao.saldo_origem_depois,
                        "reconstruido": reconstruido[transacao.origem_id],
                    }
                )
        reconstruido[transacao.destino_id] = (
            reconstruido.get(transacao.destino_id, ZERO) + transacao.valor
        )
        if transacao.saldo_destino_depois != reconstruido[transacao.destino_id]:
            linhas_inconsistentes.append(
                {
                    "transacao": transacao.id,
                    "lado": "destino",
                    "gravado": transacao.saldo_destino_depois,
                    "reconstruido": reconstruido[transacao.destino_id],
                }
            )

    saldos_divergentes = []
    for usuario in sessao.execute(select(Usuario)).scalars():
        esperado = reconstruido.get(usuario.id, ZERO)
        if usuario.saldo != esperado:
            saldos_divergentes.append(
                {
                    "usuario": usuario.nome_usuario,
                    "saldo": usuario.saldo,
                    "pelo_ledger": esperado,
                    "diferenca": usuario.saldo - esperado,
                }
            )

    return {
        "ok": not saldos_divergentes and not linhas_inconsistentes,
        "saldos_divergentes": saldos_divergentes,
        "linhas_inconsistentes": linhas_inconsistentes,
        "soma_pelo_ledger": sum(reconstruido.values(), Decimal("0.00")),
    }


def auditar(sessao=None):
    """Auditoria completa: conservação de massa + ledger explica os saldos."""
    economia = estado_da_economia(sessao)
    ledger = conferir_ledger(sessao)
    return {
        "ok": economia["conservado"] and ledger["ok"],
        "economia": economia,
        "ledger": ledger,
    }


def auditar_ou_falhar(sessao=None):
    """Como :func:`auditar`, mas estoura se algo estiver errado.

    Serve para script e para CI: um comando que sai com erro é mais difícil
    de ignorar do que um relatório que ninguém lê.
    """
    relatorio = auditar(sessao)
    if not relatorio["ok"]:
        raise MassaViolada(
            "auditoria falhou: "
            f"soma {relatorio['economia']['soma_dos_saldos']} "
            f"(o ledger diz {relatorio['economia']['supply_atual']}); "
            f"{len(relatorio['ledger']['saldos_divergentes'])} saldo(s) que o "
            f"ledger não explica; "
            f"{len(relatorio['ledger']['linhas_inconsistentes'])} linha(s) "
            "inconsistente(s)"
        )
    return relatorio
