"""Reinos: cofre, cidadania, imposto, juros e repasse.

Três regras governam este arquivo, e nenhuma é detalhe:

1. **Cidadania é opt-in, com saída.** Ninguém entra sem pedir.
2. **Imposto nunca tira dinheiro de ninguém.** Cobrar cria dívida; pagar é
   ato do devedor.
3. **O poder é do reino, não da pessoa.** Quem opera é uma conta pessoal com
   o papel; o cofre guarda o dinheiro e não autentica.

Todo teste que mexe em dinheiro passa pelo ``conservacao()``, e os que mexem
em muita coisa passam pela auditoria.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.auditoria import auditar
from vavacoin.caladinho import criar_casa
from vavacoin.dinheiro import ZERO
from vavacoin.erros import (
    MotivoObrigatorio,
    SaldoInsuficiente,
    SemAutoridade,
    ValorInvalido,
)
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import mover, soma_saldos, supply_emitido
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    Cidadania,
    Cobranca,
    Divida,
    RegistroAdministrativo,
    Transacao,
    Usuario,
    agora,
    definir_config,
)
from vavacoin.operacoes import ajustar_saldo
from vavacoin.reinos import (
    JUROS_MAXIMO,
    JUROS_MINIMO,
    TIPO_IMPOSTO,
    TIPO_REPASSE,
    cidadaos,
    cobrar,
    cidadania_de,
    criar_reino,
    definir_juros,
    definir_operador,
    devido,
    distribuir,
    dividas_em_aberto,
    eh_cidadao,
    eh_operador,
    entrar_no_reino,
    faixa_de_negociacao,
    negociar_divida,
    pagar_divida,
    perdoar_divida,
    pode_negociar,
    restante,
    sair_do_reino,
    tirar_operador,
    total_devido,
    valor_cobrado,
)

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def alfheim(app, bc, nova_pessoa):
    """Um reino com cofre, um operador e três cidadãos.

    "Alfheim" é dado: uma linha da tabela. O código não sabe o nome dele.
    """
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    ajustar_saldo(reino.cofre, "500.00", "cofre inicial", autoridade=bc)
    db.session.commit()

    rei = nova_pessoa(nome="rei", saldo="20.00")
    definir_operador(reino, rei, autoridade=bc)
    db.session.commit()

    povo = [
        nova_pessoa(nome="ana", saldo="100.00"),
        nova_pessoa(nome="bia", saldo="50.00"),
        nova_pessoa(nome="caio", saldo="0.00"),
    ]
    for pessoa in povo:
        entrar_no_reino(reino, pessoa)
    db.session.commit()

    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    return {"reino": reino, "rei": rei, "povo": povo, "ana": povo[0], "bia": povo[1], "caio": povo[2]}


def _auditoria_fecha():
    relatorio = auditar()
    assert relatorio["ok"], relatorio
    assert relatorio["ledger"]["saldos_divergentes"] == []
    return True


# --- o cofre e o poder ------------------------------------------------------


def test_o_cofre_e_uma_conta_de_verdade_no_ledger(app, bc, alfheim):
    """Para participar da mesma conservação de massa que todo mundo."""
    cofre = alfheim["reino"].cofre
    assert cofre.saldo == Decimal("500.00")
    assert soma_saldos() == supply_emitido()


def test_o_cofre_nao_entra_pela_tela(app, bc, alfheim):
    """O bug das contas de tesouraria do Benbals não entra aqui.

    Se o jeito de mandar no reino fosse entrar na conta do cofre, quem
    soubesse a senha seria rei e o ledger diria "o cofre cobrou".
    """
    cofre = alfheim["reino"].cofre
    assert cofre.eh_conta_de_sistema
    assert cofre.senha_hash is None
    assert cofre.is_active is False


def test_o_painel_recusa_dar_senha_ao_cofre(app, bc, alfheim):
    """A guarda vale para toda conta de sistema, não só para o cassino."""
    bc.definir_senha("senha-do-painel")
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": "senha-do-painel"},
        follow_redirects=True,
    )
    cofre = alfheim["reino"].cofre

    cliente.post(
        f"/painel/conta/{cofre.id}",
        data={"saldo": str(cofre.saldo), "senha": "invadindo"},
        follow_redirects=True,
    )

    db.session.expire_all()
    assert db.session.get(Usuario, cofre.id).senha_hash is None


def test_o_poder_e_do_reino_e_some_com_o_papel(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]
    assert eh_operador(reino, rei)

    tirar_operador(reino, rei, autoridade=bc)
    db.session.commit()

    assert not eh_operador(reino, rei)
    with pytest.raises(SemAutoridade):
        cobrar(reino, rei, Cobranca.ABSOLUTA, "5.00", "imposto")


def test_quem_nao_e_operador_nao_cobra(app, bc, alfheim):
    with pytest.raises(SemAutoridade):
        cobrar(
            alfheim["reino"], alfheim["ana"], Cobranca.ABSOLUTA, "5.00", "imposto"
        )


def test_conta_de_sistema_nao_opera_reino(app, bc, alfheim):
    casa = criar_casa(autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        definir_operador(alfheim["reino"], casa, autoridade=bc)


# --- cidadania --------------------------------------------------------------


def test_entrar_e_sair_sao_atos_da_pessoa(app, bc, alfheim, nova_pessoa):
    reino = alfheim["reino"]
    novo = nova_pessoa(nome="novo", saldo="10.00")

    assert not eh_cidadao(reino, novo)
    entrar_no_reino(reino, novo)
    db.session.commit()
    assert eh_cidadao(reino, novo)

    sair_do_reino(reino, novo)
    db.session.commit()
    assert not eh_cidadao(reino, novo)


def test_a_saida_nao_apaga_a_linha(app, bc, alfheim):
    """"Esta pessoa era cidadã quando aquela cobrança aconteceu?" precisa de
    resposta depois."""
    reino, ana = alfheim["reino"], alfheim["ana"]
    sair_do_reino(reino, ana)
    db.session.commit()

    linha = db.session.execute(
        db.select(Cidadania).where(
            Cidadania.reino_id == reino.id, Cidadania.usuario_id == ana.id
        )
    ).scalar_one()
    assert linha.saiu_em is not None
    assert linha.entrou_em is not None


def test_nao_se_entra_duas_vezes_no_mesmo_reino(app, bc, alfheim):
    with pytest.raises(ValorInvalido):
        entrar_no_reino(alfheim["reino"], alfheim["ana"])


def test_a_casa_do_cassino_nao_e_cidada(app, bc, alfheim):
    """Decisão do dono, e ela não é pessoa: imposto é coisa de gente."""
    casa = criar_casa(autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        entrar_no_reino(alfheim["reino"], casa)


def test_um_reino_por_pessoa(app, bc, alfheim):
    """Cidadania é exclusiva, por decisão do dono."""
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()

    with pytest.raises(ValorInvalido):
        entrar_no_reino(outro, alfheim["ana"])
    db.session.rollback()

    assert eh_cidadao(alfheim["reino"], alfheim["ana"])
    assert not eh_cidadao(outro, alfheim["ana"])


def test_a_exclusividade_e_garantida_pelo_banco(app, bc, alfheim):
    """Não é a rota que segura: é o índice único parcial.

    O teste passa por cima da checagem em Python e insere a segunda cidadania
    direto. Se o índice não existisse, ela entraria — e a dupla tributação
    apareceria em produção sem ninguém ter escrito código para ela.
    """
    from sqlalchemy.exc import IntegrityError

    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.insert(Cidadania).values(
                reino_id=outro.id, usuario_id=alfheim["ana"].id, entrou_em=agora()
            )
        )
        db.session.flush()
    db.session.rollback()


def test_sair_de_um_reino_libera_para_entrar_em_outro(app, bc, alfheim):
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()

    sair_do_reino(alfheim["reino"], alfheim["ana"])
    db.session.commit()
    entrar_no_reino(outro, alfheim["ana"])
    db.session.commit()

    assert eh_cidadao(outro, alfheim["ana"])
    assert cidadania_de(alfheim["ana"]).reino_id == outro.id


# --- cobrar não move dinheiro -----------------------------------------------


def test_cobrar_nao_tira_dinheiro_de_ninguem(app, bc, alfheim):
    """A regra que sustenta o desenho inteiro."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    antes = conservacao()
    saldos = {p.id: p.saldo for p in alfheim["povo"]}
    cofre_antes = reino.cofre.saldo

    cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto de guerra")
    db.session.commit()
    db.session.expire_all()

    for pessoa in alfheim["povo"]:
        assert db.session.get(Usuario, pessoa.id).saldo == saldos[pessoa.id]
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre_antes
    assert conservacao() == antes


def test_cobranca_absoluta_cria_uma_divida_por_cidadao(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]

    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto")
    db.session.commit()

    assert len(criadas) == 3
    assert all(d.principal == Decimal("10.00") for d in criadas)
    assert all(d.cobrada_por_id == rei.id for d in criadas)
    assert all(d.motivo == "imposto" for d in criadas)


def test_cobranca_percentual_usa_o_saldo_e_arredonda_para_baixo(app, bc, alfheim):
    """Patrimônio líquido no VavaCoin é o saldo, e só — não há outro ativo."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    ajustar_saldo(alfheim["ana"], "33.33", "para arredondar", autoridade=bc)
    db.session.commit()

    _, criadas = cobrar(reino, rei, Cobranca.PERCENTUAL, "10.00", "dízimo")
    db.session.commit()

    por_pessoa = {d.devedor_id: d.principal for d in criadas}
    # 10% de 33,33 = 3,333 -> 3,33, para baixo.
    assert por_pessoa[alfheim["ana"].id] == Decimal("3.33")
    assert por_pessoa[alfheim["bia"].id] == Decimal("5.00")
    # caio tem saldo zero: 10% de zero é zero, e dívida de zero não é dívida.
    assert alfheim["caio"].id not in por_pessoa


def test_cobrar_so_os_marcados(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]

    _, criadas = cobrar(
        reino, rei, Cobranca.ABSOLUTA, "5.00", "só a ana", pessoas=[alfheim["ana"]]
    )
    db.session.commit()

    assert len(criadas) == 1
    assert criadas[0].devedor_id == alfheim["ana"].id


def test_quem_ja_saiu_nao_e_cobrado(app, bc, alfheim):
    """A checklist da tela pode estar velha; cobrar quem saiu seria errado."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    sair_do_reino(reino, alfheim["ana"])
    db.session.commit()

    _, criadas = cobrar(
        reino, rei, Cobranca.ABSOLUTA, "5.00", "imposto", pessoas=alfheim["povo"]
    )
    db.session.commit()

    assert alfheim["ana"].id not in {d.devedor_id for d in criadas}


def test_cobranca_pede_motivo(app, bc, alfheim):
    with pytest.raises(MotivoObrigatorio):
        cobrar(alfheim["reino"], alfheim["rei"], Cobranca.ABSOLUTA, "5.00", "")


def test_a_mesma_cobranca_nao_roda_duas_vezes(app, bc, alfheim):
    """Idempotência do lote: o token é único no banco."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    cobrar(reino, rei, Cobranca.ABSOLUTA, "5.00", "imposto", token="token-fixo")
    db.session.commit()

    with pytest.raises(ValorInvalido):
        cobrar(reino, rei, Cobranca.ABSOLUTA, "5.00", "imposto", token="token-fixo")
    db.session.rollback()

    assert db.session.query(Divida).count() == 3


# --- juros ------------------------------------------------------------------


def _envelhecer(divida, dias):
    """Empurra o relógio da dívida para trás, sem esperar de verdade."""
    db.session.execute(
        db.update(Divida)
        .where(Divida.id == divida.id)
        .values(
            juros_desde=agora() - timedelta(days=dias),
            cobrada_em=agora() - timedelta(days=dias),
        )
    )
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Divida, divida.id)


def test_divida_nova_nao_rende_hoje(app, bc, alfheim):
    """Dias inteiros: o número não muda enquanto a pessoa olha a tela."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto")
    db.session.commit()

    assert devido(criadas[0]) == Decimal("10.00")


def test_os_juros_correm_por_dia(app, bc, alfheim):
    """Lineares e exatos: 1% ao dia sobre 100 é 1 por dia, sempre."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto")
    db.session.commit()

    divida = _envelhecer(criadas[0], 10)

    # 1% ao dia × 10 dias × 100 = 10
    assert devido(divida) == Decimal("110.00")


def test_juros_zero_nao_rende(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]
    definir_juros(reino, JUROS_MINIMO, rei)
    db.session.commit()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto")
    db.session.commit()

    divida = _envelhecer(criadas[0], 30)

    assert devido(divida) == Decimal("100.00")


@pytest.mark.parametrize("ruim", ["-0.01", "-1", "5.01", "50", "abc"])
def test_juros_fora_da_faixa_sao_recusados(app, bc, alfheim, ruim):
    with pytest.raises(ValorInvalido):
        definir_juros(alfheim["reino"], ruim, alfheim["rei"])


def test_mudanca_de_juros_fica_registrada(app, bc, alfheim):
    """Mesmo padrão da vantagem do cassino: quem mudou, de quanto para quanto."""
    definir_juros(alfheim["reino"], "3.00", alfheim["rei"])
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.acao == "reino")
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert registro.ator_id == alfheim["rei"].id
    assert "1.00" in registro.detalhe and "3.00" in registro.detalhe


def test_so_o_operador_muda_os_juros(app, bc, alfheim):
    with pytest.raises(SemAutoridade):
        definir_juros(alfheim["reino"], "3.00", alfheim["ana"])


# --- pagar ------------------------------------------------------------------


def test_pagar_move_dinheiro_do_devedor_para_o_cofre(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()
    saldo, cofre_antes = ana.saldo, reino.cofre.saldo

    pagar_divida(criadas[0])
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Usuario, ana.id).saldo == saldo - Decimal("10.00")
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre_antes + Decimal("10.00")
    assert db.session.get(Divida, criadas[0].id).quitada
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_o_pagamento_e_um_lancamento_com_tipo_proprio(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()

    pagar_divida(criadas[0])
    db.session.commit()

    lancamento = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == TIPO_IMPOSTO)
    ).scalar_one()
    assert lancamento.origem_id == ana.id
    assert lancamento.destino_id == reino.cofre_id


def test_pagamento_parcial_abate_e_os_juros_seguem_sobre_o_resto(app, bc, alfheim):
    """O ponto sutil: pagar metade não apaga o juro que já tinha corrido."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    assert devido(divida) == Decimal("110.00")

    pagar_divida(divida, "50.00")
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    # 110 devidos - 50 pagos = 60, e o juro corrido virou parte do saldo.
    assert restante(divida) == Decimal("60.00")
    assert not divida.quitada
    assert conservacao() == antes

    # Mais dez dias sobre os 60 que sobraram: 1% × 10 × 60 = 6.
    divida = _envelhecer(divida, 10)
    assert devido(divida) == Decimal("66.00")


def test_nao_se_paga_mais_do_que_se_deve(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()
    saldo = ana.saldo

    pagar_divida(criadas[0], "999.00")
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Usuario, ana.id).saldo == saldo - Decimal("10.00")
    assert conservacao() == antes


def test_pagar_sem_saldo_e_recusado(app, bc, alfheim):
    reino, rei, caio = alfheim["reino"], alfheim["rei"], alfheim["caio"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[caio])
    db.session.commit()

    with pytest.raises(SaldoInsuficiente):
        pagar_divida(criadas[0])
    db.session.rollback()

    assert conservacao() == antes


def test_pagar_duas_vezes_e_recusado(app, bc, alfheim):
    """Guarda de status: dívida quitada não paga de novo."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()

    pagar_divida(criadas[0])
    db.session.commit()
    saldo = db.session.get(Usuario, ana.id).saldo

    with pytest.raises(ValorInvalido):
        pagar_divida(db.session.get(Divida, criadas[0].id))
    db.session.rollback()

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == saldo
    assert conservacao() == antes


# --- sair com dívida --------------------------------------------------------


def test_sair_do_reino_nao_apaga_a_divida(app, bc, alfheim):
    """Decisão do dono: a dívida sobrevive à saída, inteira.

    Ela é uma relação entre quem cobrou e quem deve, não um atributo da
    cidadania — então sair não a toca. Se morresse na saída, bastaria sair na
    véspera e o imposto viraria piada.

    A válvula para quem quer negociar não é a porta do reino: é o credor, que
    pode baixar até o principal ou perdoar.
    """
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    assert devido(divida) == Decimal("110.00")

    sair_do_reino(reino, ana)
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    assert not divida.quitada
    assert devido(divida) == Decimal("110.00")

    # E continua correndo: sair não para o relógio.
    divida = _envelhecer(divida, 20)
    assert devido(divida) == Decimal("120.00")


def test_a_divida_de_quem_saiu_ainda_se_paga(app, bc, alfheim):
    """Sair não é calote nem quitação: dá para pagar depois."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()
    sair_do_reino(reino, ana)
    db.session.commit()

    pagar_divida(db.session.get(Divida, criadas[0].id))
    db.session.commit()

    assert db.session.get(Divida, criadas[0].id).quitada
    assert conservacao() == antes


# --- distribuir -------------------------------------------------------------


def test_distribuir_paga_o_mesmo_a_cada_marcado(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]
    antes = conservacao()
    cofre_antes = reino.cofre.saldo
    saldos = {p.id: p.saldo for p in alfheim["povo"]}

    total, alvos = distribuir(reino, rei, "10.00", alfheim["povo"], "auxílio")
    db.session.commit()
    db.session.expire_all()

    assert total == Decimal("30.00")
    assert len(alvos) == 3
    for pessoa in alfheim["povo"]:
        assert db.session.get(Usuario, pessoa.id).saldo == saldos[pessoa.id] + Decimal("10.00")
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre_antes - Decimal("30.00")
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_distribuicao_e_recusada_inteira_se_o_cofre_nao_cobre(app, bc, alfheim):
    """Nada de pagar até acabar: metade recebendo por ordem alfabética é pior
    do que o operador saber que não dá."""
    reino, rei = alfheim["reino"], alfheim["rei"]
    antes = conservacao()
    saldos = {p.id: p.saldo for p in alfheim["povo"]}

    with pytest.raises(SaldoInsuficiente):
        distribuir(reino, rei, "200.00", alfheim["povo"], "auxílio")
    db.session.rollback()

    db.session.expire_all()
    for pessoa in alfheim["povo"]:
        assert db.session.get(Usuario, pessoa.id).saldo == saldos[pessoa.id]
    assert db.session.get(Usuario, reino.cofre_id).saldo == Decimal("500.00")
    assert conservacao() == antes


def test_o_repasse_e_um_lancamento_com_tipo_proprio(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]
    distribuir(reino, rei, "10.00", [alfheim["ana"]], "auxílio")
    db.session.commit()

    lancamento = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == TIPO_REPASSE)
    ).scalar_one()
    assert lancamento.origem_id == reino.cofre_id
    assert lancamento.destino_id == alfheim["ana"].id
    assert lancamento.ator_id == rei.id, "o ledger registra quem operou"


def test_distribuir_pede_motivo_e_valor(app, bc, alfheim):
    reino, rei = alfheim["reino"], alfheim["rei"]
    with pytest.raises(MotivoObrigatorio):
        distribuir(reino, rei, "10.00", alfheim["povo"], "")
    with pytest.raises(ValorInvalido):
        distribuir(reino, rei, "0.00", alfheim["povo"], "auxílio")


def test_so_o_operador_distribui(app, bc, alfheim):
    with pytest.raises(SemAutoridade):
        distribuir(
            alfheim["reino"], alfheim["ana"], "1.00", alfheim["povo"], "auxílio"
        )


# --- a web ------------------------------------------------------------------


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def test_a_pagina_do_reino_abre(app, bc, alfheim):
    corpo = _entrar(app, "ana").get("/reino/alfheim").get_data(as_text=True)
    assert "Alfheim" in corpo


def test_a_pagina_esconde_se_o_interruptor_estiver_desligado(app, bc, alfheim):
    definir_config(CHAVE_REINOS_VISIVEIS, False)
    db.session.commit()

    resposta = _entrar(app, "ana").get("/reino/alfheim")

    assert resposta.status_code == 404


def test_quem_nao_e_operador_nao_abre_a_mesa(app, bc, alfheim):
    resposta = _entrar(app, "ana").get("/reino/alfheim/operar")
    assert resposta.status_code == 403


def test_entrar_e_sair_pela_web(app, bc, alfheim, nova_pessoa):
    novo = nova_pessoa(nome="novo", saldo="10.00")
    db.session.commit()
    cliente = _entrar(app, "novo")

    cliente.post("/reino/alfheim/entrar", follow_redirects=True)
    db.session.expire_all()
    assert eh_cidadao(alfheim["reino"], novo)

    cliente.post("/reino/alfheim/sair", follow_redirects=True)
    db.session.expire_all()
    assert not eh_cidadao(alfheim["reino"], novo)


def test_cobrar_e_pagar_pela_web(app, bc, alfheim):
    antes = conservacao()
    reino, ana = alfheim["reino"], alfheim["ana"]
    operador = _entrar(app, "rei")

    mesa = operador.get("/reino/alfheim/operar").get_data(as_text=True)
    import re

    token = re.search(r'name="token" value="([^"]+)"', mesa).group(1)
    operador.post(
        "/reino/alfheim/cobrar",
        data={
            "token": token,
            "tipo": Cobranca.ABSOLUTA,
            "parametro": "10.00",
            "motivo_cobranca": "imposto",
            "cidadao": [str(ana.id)],
        },
        follow_redirects=True,
    )

    divida = db.session.execute(db.select(Divida)).scalar_one()
    assert divida.devedor_id == ana.id

    saldo = db.session.get(Usuario, ana.id).saldo
    _entrar(app, "ana").post(
        f"/reino/divida/{divida.id}/pagar", follow_redirects=True
    )

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == saldo - Decimal("10.00")
    assert conservacao() == antes


def test_a_distribuicao_nao_paga_duas_vezes_com_dois_cliques(app, bc, alfheim):
    """O token de uso único, que é o motivo de ele existir."""
    antes = conservacao()
    operador = _entrar(app, "rei")
    import re

    mesa = operador.get("/reino/alfheim/operar").get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', mesa).group(1)
    dados = {
        "token": token,
        "valor": "10.00",
        "motivo_repasse": "auxílio",
        "cidadao": [str(p.id) for p in alfheim["povo"]],
    }

    operador.post("/reino/alfheim/distribuir", data=dados, follow_redirects=True)
    db.session.expire_all()
    depois_do_primeiro = db.session.get(Usuario, alfheim["ana"].id).saldo

    # O mesmo POST de novo: o token já foi gasto.
    operador.post("/reino/alfheim/distribuir", data=dados, follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(Usuario, alfheim["ana"].id).saldo == depois_do_primeiro
    assert db.session.query(Transacao).filter_by(tipo=TIPO_REPASSE).count() == 3
    assert conservacao() == antes


def test_ninguem_paga_divida_alheia(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()
    antes = conservacao()

    _entrar(app, "bia").post(
        f"/reino/divida/{criadas[0].id}/pagar", follow_redirects=True
    )

    db.session.expire_all()
    assert not db.session.get(Divida, criadas[0].id).quitada
    assert conservacao() == antes


# --- a taxa congela na criação ----------------------------------------------


def test_a_taxa_congela_na_criacao_da_divida(app, bc, alfheim):
    """Mudar a taxa do reino não reprecifica cobrança antiga.

    Mesmo princípio da vantagem congelada na aposta do cassino, e pelo mesmo
    motivo: senão dá para encarecer retroativamente a dívida de alguém.
    """
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    assert criadas[0].juros_diarios == Decimal("1.00")

    definir_juros(reino, "5.00", rei)
    db.session.commit()

    divida = _envelhecer(criadas[0], 10)
    # 1% ao dia, a taxa de quando nasceu — não os 5% de agora.
    assert devido(divida) == Decimal("110.00")


def test_divida_nova_nasce_com_a_taxa_nova(app, bc, alfheim):
    """Congelar protege quem já deve, não congela o reino."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    definir_juros(reino, "3.00", rei)
    db.session.commit()

    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto", pessoas=[ana])
    db.session.commit()

    assert criadas[0].juros_diarios == Decimal("3.00")


# --- negociar ---------------------------------------------------------------


def test_a_faixa_vai_do_principal_ao_total_com_juros(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)

    piso, teto = faixa_de_negociacao(divida)

    assert piso == Decimal("100.00")  # juros zerados
    assert teto == Decimal("110.00")  # com os juros corridos


def test_negociar_no_meio_da_faixa(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)

    negociar_divida(divida, "105.00", rei)
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    assert devido(divida) == Decimal("105.00")


def test_o_valor_negociado_para_os_juros(app, bc, alfheim):
    """Número combinado que continua crescendo não é acordo nenhum."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    negociar_divida(divida, "105.00", rei)
    db.session.commit()

    divida = _envelhecer(db.session.get(Divida, divida.id), 60)

    assert devido(divida) == Decimal("105.00")


@pytest.mark.parametrize("fora", ["99.99", "110.01", "0", "-5"])
def test_negociar_fora_da_faixa_e_recusado(app, bc, alfheim, fora):
    """O servidor recusa; o min/max do campo é conforto, não defesa."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)

    with pytest.raises(ValorInvalido):
        negociar_divida(divida, fora, rei)


def test_o_piso_desconta_o_que_ja_foi_pago(app, bc, alfheim):
    """"O que falta para fechar o principal", nas palavras do dono."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    pagar_divida(divida, "40.00")
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    piso, _ = faixa_de_negociacao(divida)

    # Piso em total acumulado é o principal; o que FALTA pagar é 60.
    assert piso == Decimal("100.00")
    negociar_divida(divida, piso, rei)
    db.session.commit()
    db.session.expire_all()
    assert devido(db.session.get(Divida, divida.id)) == Decimal("60.00")


def test_o_piso_nunca_e_negativo(app, bc, alfheim):
    """Quem já pagou mais que o principal não recebe troco de desconto."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)  # 50 + 10% = 55
    # Paga 52 dos 55: passou do principal de 50.
    pagar_divida(divida, "52.00")
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    piso, teto = faixa_de_negociacao(divida)

    assert piso == Decimal("52.00")  # o que já foi pago, não os 50 do principal
    assert teto >= piso
    negociar_divida(divida, piso, rei)
    db.session.commit()
    db.session.expire_all()
    assert devido(db.session.get(Divida, divida.id)) == ZERO


def test_o_desconto_pode_quitar_a_divida_sozinho(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    antes = conservacao()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    pagar_divida(divida, "100.00")
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)
    assert not divida.quitada  # ainda faltam os 10 de juros

    negociar_divida(divida, "100.00", rei)
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Divida, divida.id).quitada
    assert conservacao() == antes


# --- perdoar ----------------------------------------------------------------


def test_perdoar_apaga_a_divida(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida_id = criadas[0].id

    perdoado = perdoar_divida(criadas[0], rei)
    db.session.commit()

    assert perdoado == Decimal("50.00")
    assert db.session.get(Divida, divida_id) is None
    assert total_devido(ana, reino=reino) == ZERO


# --- nem desconto nem perdão movem dinheiro ---------------------------------


def test_negociar_nao_move_um_centavo(app, bc, alfheim):
    """Dívida nunca foi dinheiro no ledger: é uma cobrança pendente."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)

    antes = conservacao()
    saldo, cofre = ana.saldo, reino.cofre.saldo
    lancamentos = db.session.query(Transacao).count()

    negociar_divida(divida, "100.00", rei)
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Usuario, ana.id).saldo == saldo
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre
    assert db.session.query(Transacao).count() == lancamentos
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_perdoar_nao_move_um_centavo_nem_abala_a_auditoria(app, bc, alfheim):
    """O teste que autoriza `perdoar` a APAGAR a linha.

    Nenhum lançamento do ledger aponta para a dívida, então apagá-la não
    deixa nada órfão. Os pagamentos parciais já feitos continuam no ledger,
    explicando os saldos que sempre explicaram.
    """
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)
    # Paga um pedaço ANTES do perdão: o pagamento é real e tem de sobreviver.
    pagar_divida(divida, "30.00")
    db.session.commit()
    db.session.expire_all()
    divida = db.session.get(Divida, divida.id)

    antes = conservacao()
    saldo, cofre = (
        db.session.get(Usuario, ana.id).saldo,
        db.session.get(Usuario, reino.cofre_id).saldo,
    )
    lancamentos = db.session.query(Transacao).count()

    perdoar_divida(divida, rei)
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Usuario, ana.id).saldo == saldo
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre
    assert db.session.query(Transacao).count() == lancamentos
    assert conservacao() == antes
    assert _auditoria_fecha()


# --- quem pode ---------------------------------------------------------------


def test_quem_nao_criou_a_divida_nao_negocia(app, bc, alfheim, nova_pessoa):
    """Nem outro operador: quem cobrou é quem negocia."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    ministro = nova_pessoa(nome="ministro", saldo="10.00")
    definir_operador(reino, ministro, autoridade=bc)
    db.session.commit()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()

    assert not pode_negociar(criadas[0], ministro)
    with pytest.raises(SemAutoridade):
        negociar_divida(criadas[0], "50.00", ministro)
    with pytest.raises(SemAutoridade):
        perdoar_divida(criadas[0], ministro)


def test_o_devedor_nao_perdoa_a_propria_divida(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()

    with pytest.raises(SemAutoridade):
        perdoar_divida(criadas[0], ana)


def test_ex_operador_nao_perdoa_o_que_criou(app, bc, alfheim):
    """Perdeu o papel, perdeu o poder — inclusive sobre o que ele mesmo criou.

    Sem isso, remover um operador seria convite para ele voltar e perdoar
    tudo que cobrou, que é sabotagem com cara de bondade.
    """
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()

    tirar_operador(reino, rei, autoridade=bc)
    db.session.commit()

    assert not pode_negociar(criadas[0], rei)
    with pytest.raises(SemAutoridade):
        perdoar_divida(criadas[0], rei)


def test_a_divida_do_ex_operador_nao_fica_orfa(app, bc, alfheim, nova_pessoa):
    """Quando o autor sai, qualquer operador atual assume a cobrança dele.

    Uma dívida que ninguém pode perdoar seria uma dívida que ninguém pode
    corrigir — e o poder é do reino, não da pessoa.
    """
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    ministro = nova_pessoa(nome="ministro", saldo="10.00")
    definir_operador(reino, ministro, autoridade=bc)
    db.session.commit()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()
    assert not pode_negociar(criadas[0], ministro)

    tirar_operador(reino, rei, autoridade=bc)
    db.session.commit()

    assert pode_negociar(criadas[0], ministro)
    perdoar_divida(criadas[0], ministro)
    db.session.commit()


# --- o registro -------------------------------------------------------------


def test_o_desconto_fica_registrado(app, bc, alfheim):
    """Quem, quando, de quanto para quanto."""
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana])
    db.session.commit()
    divida = _envelhecer(criadas[0], 10)

    negociar_divida(divida, "100.00", rei)
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.acao == "reino")
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert registro.ator_id == rei.id
    assert registro.alvo == ana.nome_usuario
    assert "110.00" in registro.detalhe and "100.00" in registro.detalhe
    assert registro.criado_em is not None


def test_o_perdao_fica_registrado(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()

    perdoar_divida(criadas[0], rei)
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo)
        .where(RegistroAdministrativo.acao == "reino")
        .order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert registro.ator_id == rei.id
    assert registro.alvo == ana.nome_usuario
    assert "perdoada" in registro.detalhe and "50.00" in registro.detalhe


# --- pela web ---------------------------------------------------------------


def test_negociar_e_perdoar_pela_web(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(
        reino, rei, Cobranca.ABSOLUTA, "100.00", "imposto", pessoas=[ana, alfheim["bia"]]
    )
    db.session.commit()
    da_ana, da_bia = criadas[0], criadas[1]
    antes = conservacao()
    operador = _entrar(app, "rei")

    operador.post(
        f"/reino/divida/{da_ana.id}/negociar",
        data={"valor": "100.00"},
        follow_redirects=True,
    )
    operador.post(f"/reino/divida/{da_bia.id}/perdoar", follow_redirects=True)

    db.session.expire_all()
    assert devido(db.session.get(Divida, da_ana.id)) == Decimal("100.00")
    assert db.session.get(Divida, da_bia.id) is None
    assert conservacao() == antes


def test_o_devedor_nao_perdoa_pela_web(app, bc, alfheim):
    reino, rei, ana = alfheim["reino"], alfheim["rei"], alfheim["ana"]
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "50.00", "imposto", pessoas=[ana])
    db.session.commit()

    _entrar(app, "ana").post(
        f"/reino/divida/{criadas[0].id}/perdoar", follow_redirects=True
    )

    db.session.expire_all()
    assert db.session.get(Divida, criadas[0].id) is not None
