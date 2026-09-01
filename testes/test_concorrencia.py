"""Duas mãos no mesmo saldo ao mesmo tempo.

O caso que só aparece em produção: dois pedidos simultâneos gastando o mesmo
dinheiro. O que impede o estouro aqui é a combinação de ``BEGIN IMMEDIATE``
(serializa escritores no SQLite) com o ``UPDATE ... WHERE saldo >= valor``
dentro do ``mover()``.
"""

import threading
from decimal import Decimal

from conftest import conservacao

from vavacoin.erros import ConviteJaResgatado, ErroMonetario
from vavacoin.extensoes import db
from vavacoin.moeda import mover
from vavacoin.operacoes import criar_convite, criar_usuario, resgatar_convite


def _rodar_em_paralelo(app, tarefa, quantidade=2):
    """Roda ``tarefa`` em N threads, cada uma com seu contexto e sua sessão.

    Uma barreira alinha as threads no instante anterior à operação, para que
    a corrida aconteça de verdade em vez de por sorte de agendamento.
    """
    barreira = threading.Barrier(quantidade)
    resultados = []
    trava = threading.Lock()

    def alvo():
        with app.app_context():
            barreira.wait()
            try:
                tarefa()
                db.session.commit()
                resultado = ("ok", None)
            except Exception as erro:  # noqa: BLE001 — o teste classifica depois
                db.session.rollback()
                resultado = ("erro", erro)
            finally:
                db.session.remove()
            with trava:
                resultados.append(resultado)

    threads = [threading.Thread(target=alvo) for _ in range(quantidade)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return resultados


def test_dois_gastos_simultaneos_nao_estouram_o_saldo(app, bc, nova_pessoa):
    """Com 50 na conta, dois pedidos de 50 ao mesmo tempo: um só passa."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    ana_id, bia_id = ana.id, bia.id
    conservacao()
    db.session.remove()

    resultados = _rodar_em_paralelo(app, lambda: mover(ana_id, bia_id, "50.00"))

    sucessos = [r for r in resultados if r[0] == "ok"]
    assert len(sucessos) == 1, f"resultados: {resultados}"

    db.session.expire_all()
    from vavacoin.modelos import Usuario

    assert db.session.get(Usuario, ana_id).saldo == Decimal("0.00")
    assert db.session.get(Usuario, bia_id).saldo == Decimal("100.00")
    conservacao()


def test_mesmo_convite_resgatado_por_duas_contas_ao_mesmo_tempo(app, bc):
    """Só uma das contas saca; o supply não emite 100 por um código."""
    ana = criar_usuario("ana", "senha-boa-123", autoridade=bc)
    bia = criar_usuario("bia", "senha-boa-123", autoridade=bc)
    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()
    codigo = convite.codigo
    ids = [ana.id, bia.id]
    conservacao()
    db.session.remove()

    from vavacoin.modelos import Usuario

    contador = threading.Lock()
    proximo = iter(ids)

    def tarefa():
        with contador:
            usuario_id = next(proximo)
        usuario = db.session.get(Usuario, usuario_id)
        resgatar_convite(usuario, codigo)

    resultados = _rodar_em_paralelo(app, tarefa)

    sucessos = [r for r in resultados if r[0] == "ok"]
    assert len(sucessos) == 1, f"resultados: {resultados}"
    for estado, erro in resultados:
        if estado == "erro":
            assert isinstance(erro, (ConviteJaResgatado, ErroMonetario)), erro

    # O convite não move dinheiro; o que ele dá é a entrada na economia.
    # Uma conta ficou com ele, a outra não.
    db.session.expire_all()
    from vavacoin.modelos import Convite

    resgatados = db.session.query(Convite).filter(Convite.usuario_id.isnot(None)).count()
    assert resgatados == 1
    conservacao()


def test_movimentos_cruzados_nao_travam(app, bc, nova_pessoa):
    """A e B mandando um para o outro ao mesmo tempo: ordem de lock fixa."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    bia = nova_pessoa(com_convite=True, saldo="50.00")
    ana_id, bia_id = ana.id, bia.id
    conservacao()
    db.session.remove()

    lado = iter([(ana_id, bia_id), (bia_id, ana_id)])
    trava = threading.Lock()

    def tarefa():
        with trava:
            origem, destino = next(lado)
        mover(origem, destino, "10.00")

    resultados = _rodar_em_paralelo(app, tarefa)
    assert all(r[0] == "ok" for r in resultados), f"resultados: {resultados}"

    db.session.expire_all()
    from vavacoin.modelos import Usuario

    assert db.session.get(Usuario, ana_id).saldo == Decimal("50.00")
    assert db.session.get(Usuario, bia_id).saldo == Decimal("50.00")
    conservacao()
