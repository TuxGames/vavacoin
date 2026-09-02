"""O caminho único do dinheiro.

Só existe uma função que altera saldo: :func:`mover`. Não há um segundo
caminho, nem para o seed, nem para o admin, nem para o teste. A única escrita
de saldo fora dela é a gênese (:func:`criar_genese`), que existe porque antes
dela não há Banco Central para autorizar coisa alguma — e é blindada para
rodar uma vez só, com o ledger vazio.

**Dinheiro pode ser criado depois da gênese.** O administrador ajusta saldo
para consertar valor errado, e ajuste para cima cunha. Isso não acontece por
fora: acontece como um ``mover()`` sem origem, do tipo ``emissao``, exigindo
o Banco Central e um motivo escrito. O supply, por causa disso, deixou de ser
uma constante — passou a ser o que o ledger diz (:func:`supply_emitido`).

O invariante continua valendo, só que na forma certa: a soma de **todos** os
saldos é sempre igual ao total que entrou no mundo, isto é, à soma de todos os
lançamentos sem origem.
"""

from decimal import Decimal

from sqlalchemy import select, update

from .constantes import SUPPLY_INICIAL, SUPPLY_MAXIMO, USUARIO_BANCO_CENTRAL
from .dinheiro import ZERO, para_decimal
from .erros import (
    MassaViolada,
    MesmaConta,
    SaldoInsuficiente,
    SemAutoridade,
    TetoDoSupply,
    ValorInvalido,
)
from .extensoes import db
from .modelos import Transacao, Usuario

TIPO_GENESE = "genese"
TIPO_EMISSAO = "emissao"
TIPO_QUEIMA = "queima"
#: Histórico. O saque inicial acabou, mas as linhas antigas continuam no
#: ledger e precisam de nome para serem lidas.
TIPO_SAQUE_INICIAL = "saque_inicial"
TIPO_TRANSFERENCIA = "transferencia"
TIPO_AJUSTE = "ajuste"
#: Saldo voltando ao Banco Central porque a conta foi encerrada. É uma
#: devolução como a do reset, mas de uma conta só — e tem tipo próprio para
#: o extrato dizer o que aconteceu em vez de parecer um ajuste qualquer.
TIPO_ENCERRAMENTO = "encerramento"
TIPO_RESET_RECOLHIMENTO = "reset_recolhimento"
TIPO_RESET_REDISTRIBUICAO = "reset_redistribuicao"

#: Os únicos tipos que podem entrar no ledger sem origem — ou seja, os únicos
#: que criam dinheiro. Está aqui em cima, e no CHECK da tabela, porque é a
#: lista mais importante do projeto: tudo que não está nela conserva massa.
TIPOS_SEM_ORIGEM = (TIPO_GENESE, TIPO_EMISSAO)

#: E os únicos que podem entrar sem destino: os que destroem dinheiro. Mesma
#: disciplina, do outro lado. Só o Banco Central queima, baixando o próprio
#: saldo — e é o que permite o supply descer, em vez de o teto virar catraca.
TIPOS_SEM_DESTINO = (TIPO_QUEIMA,)


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


def supply_emitido(sessao=None):
    """Quanto dinheiro existe hoje, segundo o ledger: entradas menos saídas.

    **Entradas** são os lançamentos sem origem — a gênese e toda emissão. As
    **saídas** são os sem destino, isto é, as queimas. Dinheiro só entra e só
    sai do mundo por uma linha dessas, então esta conta é a definição de
    supply — não um número escrito no código, que poderia divergir da
    realidade sem ninguém notar.
    """
    sessao = sessao or db.session
    total = ZERO
    for valor in sessao.execute(
        select(Transacao.valor).where(Transacao.origem_id.is_(None))
    ).scalars():
        total += valor
    for valor in sessao.execute(
        select(Transacao.valor).where(Transacao.destino_id.is_(None))
    ).scalars():
        total -= valor
    return total


def total_cunhado_depois_da_genese(sessao=None):
    """Quanto o supply andou desde o dia zero: cunhado menos queimado.

    **Pode ser negativo**, e é informação, não erro: significa que se queimou
    mais do que se cunhou, e o supply está abaixo dos 5.000 iniciais.
    """
    return supply_emitido(sessao) - SUPPLY_INICIAL


def cabe_emitir(sessao=None):
    """Quanto ainda dá para cunhar antes de bater o teto do supply."""
    resta = SUPPLY_MAXIMO - supply_emitido(sessao)
    return resta if resta > ZERO else ZERO


def verificar_conservacao(sessao=None, esperado=None):
    """Confere que a soma dos saldos é o supply; levanta :class:`MassaViolada`.

    Sem ``esperado``, compara com o supply reconstruído do ledger. Antes o
    alvo era a constante 5.000; agora que o administrador pode cunhar, fixar
    o número faria a verificação acusar erro toda vez que ele consertasse um
    saldo — e alarme que dispara à toa é alarme que se aprende a ignorar.
    """
    sessao = sessao or db.session
    esperado = supply_emitido(sessao) if esperado is None else para_decimal(esperado)
    total = soma_saldos(sessao)
    if total != esperado:
        raise MassaViolada(
            f"soma dos saldos é {total}, o ledger diz que deveria ser {esperado} "
            f"(diferença de {total - esperado})"
        )
    return total


def mover(
    origem,
    destino,
    valor,
    tipo=TIPO_TRANSFERENCIA,
    motivo=None,
    ator=None,
    sessao=None,
):
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

    ``origem=None`` é **emissão**: dinheiro novo entrando no mundo. Só é
    aceita com ``tipo`` na lista :data:`TIPOS_SEM_ORIGEM` e com o Banco
    Central como ``ator`` — cunhar é poder do administrador, e fica escrito
    no ledger quem cunhou e por quê. Fora disso, nenhuma chamada cria moeda.

    ``destino=None`` é **queima**: dinheiro saindo do mundo. Mesma disciplina
    do outro lado (:data:`TIPOS_SEM_DESTINO`), e só do saldo do próprio Banco
    Central. É o que faz o supply poder descer.

    ``ator`` é quem mandou fazer, quando não é o próprio dono da origem: o
    administrador ajustando o saldo de alguém, por exemplo. É o que permite
    responder "por que meu saldo mudou?" seis meses depois.

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

    destino_id = _id_de(destino) if destino is not None else None
    ator_id = _id_de(ator) if ator is not None else None

    if origem is None and destino is None:
        raise ValorInvalido("movimento sem origem e sem destino não é movimento")

    if origem is None:
        return _emitir(sessao, destino_id, valor, tipo, motivo, ator_id)

    origem_id = _id_de(origem)

    if destino is None:
        return _queimar(sessao, origem_id, valor, tipo, motivo, ator_id)

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
        ator_id=ator_id,
        saldo_origem_depois=conta_origem.saldo,
        saldo_destino_depois=conta_destino.saldo,
    )
    sessao.add(transacao)
    sessao.flush()
    return transacao


def _emitir(sessao, destino_id, valor, tipo, motivo, ator_id):
    """Ramo de emissão do ``mover()``: dinheiro novo, sem origem.

    Fica dentro do ``mover()`` de propósito, e não numa função pública ao
    lado: a regra do projeto é que só um lugar escreve saldo, e cunhar não
    pode virar a exceção que fura essa regra.
    """
    if tipo not in TIPOS_SEM_ORIGEM:
        raise ValorInvalido(
            f"movimento sem origem só existe para {TIPOS_SEM_ORIGEM}, "
            f"não para {tipo!r}"
        )
    if not (motivo or "").strip():
        raise ValorInvalido("emissão exige motivo escrito: ela cunha moeda")

    ator = sessao.get(Usuario, ator_id) if ator_id is not None else None
    if ator is None or not ator.eh_banco_central:
        raise SemAutoridade("só o Banco Central emite moeda")

    # O teto vale aqui, e só aqui, porque este é o único ponto que cria
    # dinheiro. Conferir na operação de cima (o ajuste) deixaria de fora
    # qualquer caminho novo que emitisse — e o ponto de ter um teto é ele não
    # depender de quem lembra de checá-lo.
    #
    # A gênese não passa por aqui: ela escreve o saldo direto, quando ainda
    # não existe Banco Central para autorizar coisa alguma.
    resta = cabe_emitir(sessao)
    if valor > resta:
        raise TetoDoSupply(
            f"o supply pararia acima de {SUPPLY_MAXIMO}; ainda cabem {resta} VVC"
        )

    conta_destino = sessao.execute(
        select(Usuario).where(Usuario.id == destino_id).with_for_update()
    ).scalar_one_or_none()
    if conta_destino is None:
        raise ValorInvalido(f"conta de destino inexistente: {destino_id}")

    sessao.execute(
        update(Usuario)
        .where(Usuario.id == destino_id)
        .values(saldo=Usuario.saldo + valor)
    )
    sessao.expire(conta_destino, ["saldo"])

    transacao = Transacao(
        origem_id=None,
        destino_id=destino_id,
        valor=valor,
        tipo=tipo,
        motivo=motivo,
        ator_id=ator_id,
        saldo_origem_depois=None,
        saldo_destino_depois=conta_destino.saldo,
    )
    sessao.add(transacao)
    sessao.flush()
    return transacao


def _queimar(sessao, origem_id, valor, tipo, motivo, ator_id):
    """Ramo de queima do ``mover()``: dinheiro saindo do mundo, sem destino.

    Fica dentro do ``mover()`` pelo mesmo motivo da emissão: só um lugar
    escreve saldo, e destruir moeda não pode virar a exceção que fura a regra.
    """
    if tipo not in TIPOS_SEM_DESTINO:
        raise ValorInvalido(
            f"movimento sem destino só existe para {TIPOS_SEM_DESTINO}, "
            f"não para {tipo!r}"
        )
    if not (motivo or "").strip():
        raise ValorInvalido("queima exige motivo escrito: ela destrói moeda")

    ator = sessao.get(Usuario, ator_id) if ator_id is not None else None
    if ator is None or not ator.eh_banco_central:
        raise SemAutoridade("só o Banco Central queima moeda")
    if origem_id != ator.id:
        raise SemAutoridade("a queima sai do saldo do próprio Banco Central")

    conta_origem = sessao.execute(
        select(Usuario).where(Usuario.id == origem_id).with_for_update()
    ).scalar_one_or_none()
    if conta_origem is None:
        raise ValorInvalido(f"conta de origem inexistente: {origem_id}")
    if conta_origem.saldo < valor:
        raise SaldoInsuficiente(
            f"o Banco Central tem {conta_origem.saldo}, queimaria {valor}"
        )

    debito = sessao.execute(
        update(Usuario)
        .where(Usuario.id == origem_id, Usuario.saldo >= valor)
        .values(saldo=Usuario.saldo - valor)
    )
    if debito.rowcount != 1:
        raise SaldoInsuficiente("o saldo do Banco Central mudou durante a queima")
    sessao.expire(conta_origem, ["saldo"])

    transacao = Transacao(
        origem_id=origem_id,
        destino_id=None,
        valor=valor,
        tipo=tipo,
        motivo=motivo,
        ator_id=ator_id,
        saldo_origem_depois=conta_origem.saldo,
        saldo_destino_depois=None,
    )
    sessao.add(transacao)
    sessao.flush()
    return transacao


def criar_genese(sessao=None, supply=SUPPLY_INICIAL):
    """Cria o Banco Central com o supply inicial. Idempotente.

    Escreve saldo fora do ``mover()`` porque é o único momento em que não há
    Banco Central para autorizar a emissão — a autoridade ainda não existe.
    É blindada de três formas: só roda se não houver Banco Central, só roda se
    o ledger estiver vazio, e o ``UPDATE`` exige que o saldo ainda seja zero.
    Rodar duas vezes devolve o mesmo Banco Central com o mesmo saldo.

    Os 5.000 ficam no Banco Central como **saldo não emitido**: não é
    "dinheiro do BC", é dinheiro que ainda não entrou em circulação.
    """
    sessao = sessao or db.session

    existente = sessao.execute(
        select(Usuario).where(Usuario.nome_normalizado == USUARIO_BANCO_CENTRAL)
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    if sessao.execute(select(Transacao.id).limit(1)).first() is not None:
        raise MassaViolada(
            "há transações no ledger sem Banco Central: banco inconsistente"
        )

    supply = para_decimal(supply)
    bc = Usuario(
        nome_exibicao="Banco Central do VavaCoin",
        eh_banco_central=True,
        saldo=ZERO,
    )
    bc.definir_nome(USUARIO_BANCO_CENTRAL)
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
            motivo="emissão única do supply inicial",
            ator_id=None,
            saldo_origem_depois=None,
            saldo_destino_depois=bc.saldo,
        )
    )
    sessao.flush()
    verificar_conservacao(sessao, esperado=supply)
    return bc
