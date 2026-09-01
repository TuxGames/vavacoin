"""O caminho único do dinheiro.

Só existe uma função que altera saldo: :func:`mover`. Não há um segundo
caminho, nem para o seed, nem para o admin, nem para o teste. A única
escrita de saldo fora dela é a gênese (:func:`criar_genese`), que existe
porque antes dela não há dinheiro para mover — e é blindada para rodar uma
vez só, quando o ledger está vazio.

O invariante que manda em tudo: a soma de **todos** os saldos, incluindo o do
Banco Central, é sempre exatamente 5.000,00.
"""

from decimal import Decimal

from sqlalchemy import select, update

from .constantes import SUPPLY_TOTAL, USUARIO_BANCO_CENTRAL
from .dinheiro import ZERO, para_decimal
from .erros import MassaViolada, MesmaConta, SaldoInsuficiente, ValorInvalido
from .extensoes import db
from .modelos import Transacao, Usuario

TIPO_GENESE = "genese"
TIPO_SAQUE_INICIAL = "saque_inicial"
TIPO_TRANSFERENCIA = "transferencia"
TIPO_RESET_RECOLHIMENTO = "reset_recolhimento"
TIPO_RESET_REDISTRIBUICAO = "reset_redistribuicao"


def _id_de(usuario):
    """Aceita um ``Usuario`` ou um id inteiro, devolve o id."""
    if isinstance(usuario, Usuario):
        if usuario.id is None:
            raise ValorInvalido("usuário ainda não persistido (sem id)")
        return usuario.id
    if isinstance(usuario, int):
        return usuario
    raise ValorInvalido(f"conta inválida: {usuario!r}")


def soma_saldos(sessao=None):
    """Soma de todos os saldos, em ``Decimal``.

    Soma em Python, não com ``SUM()`` no banco, para que o valor passe pelo
    mesmo conversor de centavos que o resto do sistema — a verificação de
    massa não pode depender de um caminho de leitura diferente do de escrita.
    """
    sessao = sessao or db.session
    total = ZERO
    for saldo in sessao.execute(select(Usuario.saldo)).scalars():
        total += saldo
    return total


def verificar_conservacao(sessao=None, esperado=SUPPLY_TOTAL):
    """Confere que a massa é a esperada; levanta :class:`MassaViolada` se não.

    Chamada nas bordas das operações compostas (resgate, reset). Se falhar,
    não existe conserto local: existe um caminho de escrita fora do
    ``mover()`` e é ele que precisa sumir.
    """
    total = soma_saldos(sessao)
    if total != esperado:
        raise MassaViolada(
            f"soma dos saldos é {total}, deveria ser {esperado} "
            f"(diferença de {total - esperado})"
        )
    return total


def mover(origem, destino, valor, tipo=TIPO_TRANSFERENCIA, motivo=None, sessao=None):
    """Move ``valor`` de ``origem`` para ``destino`` e registra no ledger.

    É a única função que altera saldo. Débito e crédito acontecem na mesma
    transação do banco: ou os dois valem, ou nenhum vale.

    As duas linhas são travadas antes de qualquer leitura de saldo, sempre na
    ordem crescente de id. A ordem importa: dois movimentos cruzados (A para B
    e B para A ao mesmo tempo) travando cada um na sua ordem natural fazem
    deadlock; travando ambos na mesma ordem, um espera o outro.

    O débito é feito por um ``UPDATE ... WHERE saldo >= valor`` e a operação
    só continua se ele afetou uma linha. Isso não é redundância do lock: no
    SQLite o ``SELECT ... FOR UPDATE`` é ignorado pelo dialeto, e é esse
    ``WHERE`` que impede duas retiradas simultâneas de estourarem o saldo.

    Não faz ``commit``: quem chama decide o limite da transação, e é isso que
    permite compor várias movimentações atomicamente (o reset faz isso).

    :returns: a :class:`~vavacoin.modelos.Transacao` gravada.
    """
    sessao = sessao or db.session

    try:
        valor = para_decimal(valor)
    except TypeError as erro:
        raise ValorInvalido(str(erro)) from erro
    if valor <= ZERO:
        raise ValorInvalido(f"valor precisa ser positivo, recebido {valor}")

    origem_id = _id_de(origem)
    destino_id = _id_de(destino)
    if origem_id == destino_id:
        raise MesmaConta("origem e destino são a mesma conta")

    # Trava as duas linhas, sempre na mesma ordem.
    ids_ordenados = sorted((origem_id, destino_id))
    contas = {
        u.id: u
        for u in sessao.execute(
            select(Usuario)
            .where(Usuario.id.in_(ids_ordenados))
            .order_by(Usuario.id)
            .with_for_update()
        ).scalars()
    }
    if origem_id not in contas:
        raise ValorInvalido(f"conta de origem inexistente: {origem_id}")
    if destino_id not in contas:
        raise ValorInvalido(f"conta de destino inexistente: {destino_id}")

    conta_origem = contas[origem_id]
    conta_destino = contas[destino_id]
    if conta_origem.saldo < valor:
        raise SaldoInsuficiente(
            f"{conta_origem.nome_usuario} tem {conta_origem.saldo}, "
            f"precisa de {valor}"
        )

    debito = sessao.execute(
        update(Usuario)
        .where(Usuario.id == origem_id, Usuario.saldo >= valor)
        .values(saldo=Usuario.saldo - valor)
    )
    if debito.rowcount != 1:
        # Alguém debitou a mesma conta entre o lock e o UPDATE. Nada foi
        # movido; a exceção aborta a transação de quem chamou.
        raise SaldoInsuficiente(
            f"saldo de {conta_origem.nome_usuario} mudou durante a operação"
        )

    sessao.execute(
        update(Usuario)
        .where(Usuario.id == destino_id)
        .values(saldo=Usuario.saldo + valor)
    )

    # Os UPDATEs foram por SQL; os objetos em memória ainda têm o saldo velho.
    sessao.expire(conta_origem, ["saldo"])
    sessao.expire(conta_destino, ["saldo"])

    transacao = Transacao(
        origem_id=origem_id,
        destino_id=destino_id,
        valor=valor,
        tipo=tipo,
        motivo=motivo,
        saldo_origem_depois=conta_origem.saldo,
        saldo_destino_depois=conta_destino.saldo,
    )
    sessao.add(transacao)
    sessao.flush()
    return transacao


def criar_genese(sessao=None, supply=SUPPLY_TOTAL):
    """Cria o Banco Central com todo o supply. Idempotente.

    Este é o **único** ponto do projeto que faz dinheiro existir, e existe
    porque no dia zero não há de onde mover. É blindado de três formas: só
    roda se não houver Banco Central, só roda se o ledger estiver vazio, e o
    ``UPDATE`` exige que o saldo ainda seja zero. Rodar duas vezes devolve o
    mesmo Banco Central com os mesmos 5.000,00 — nunca 10.000.

    Os 5.000 ficam no Banco Central como **saldo não emitido**: não é
    "dinheiro do BC", é dinheiro que ainda não entrou em circulação.
    """
    sessao = sessao or db.session

    existente = sessao.execute(
        select(Usuario).where(Usuario.nome_usuario == USUARIO_BANCO_CENTRAL)
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    if sessao.execute(select(Transacao.id).limit(1)).first() is not None:
        raise MassaViolada(
            "há transações no ledger sem Banco Central: banco inconsistente"
        )

    supply = para_decimal(supply)
    bc = Usuario(
        nome_usuario=USUARIO_BANCO_CENTRAL,
        nome_exibicao="Banco Central do VaVaCoin",
        eh_banco_central=True,
        saldo=ZERO,
    )
    sessao.add(bc)
    sessao.flush()

    emissao = sessao.execute(
        update(Usuario)
        .where(Usuario.id == bc.id, Usuario.saldo == Decimal("0.00"))
        .values(saldo=supply)
    )
    if emissao.rowcount != 1:
        raise MassaViolada("gênese concorrente detectada; nada foi emitido")
    sessao.expire(bc, ["saldo"])

    sessao.add(
        Transacao(
            origem_id=None,
            destino_id=bc.id,
            valor=supply,
            tipo=TIPO_GENESE,
            motivo="emissão única do supply",
            saldo_origem_depois=None,
            saldo_destino_depois=bc.saldo,
        )
    )
    sessao.flush()
    verificar_conservacao(sessao, esperado=supply)
    return bc
