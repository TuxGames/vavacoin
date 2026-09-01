"""O Banco Central entra pela tela, e o que ele faz fica todo registrado.

Este arquivo era ``test_banco_central_fechado.py`` e afirmava o contrário:
que o BC não tinha porta. A decisão mudou — o BC loga e tem god mode —, e os
testes mudaram junto, **afirmando o novo comportamento com a mesma dureza**.
Não é o mesmo que deixar de testar: cada porta que se abriu tem aqui um teste
dizendo exatamente até onde ela abre.

O que continua travado, e por quê:

- só o Banco Central tem god mode; jogador comum não vira administrador;
- conta sem senha não entra — é o estado do BC entre a gênese e o
  ``flask senha-bc``;
- o BC não resgata convite e não recebe transferência: ele não é jogador;
- todo ajuste passa pelo ledger, com ator e motivo, e a auditoria fecha depois.
"""

from decimal import Decimal

import pytest
from conftest import conservacao
from flask_login import login_user

from vavacoin.autoridade import exigir_banco_central
from vavacoin.constantes import SUPPLY_INICIAL
from vavacoin.erros import MotivoObrigatorio, SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.moeda import (
    TIPO_AJUSTE,
    TIPO_EMISSAO,
    mover,
    supply_emitido,
    total_cunhado_depois_da_genese,
)
from vavacoin.modelos import RegistroAdministrativo, Transacao, Usuario
from vavacoin.operacoes import (
    ajustar_saldo,
    criar_convite,
    criar_usuario,
    resetar_economia,
)

SENHA_BC = "senha-do-banco-central-123"


@pytest.fixture
def bc_com_senha(app, bc):
    """O Banco Central depois do `flask senha-bc`."""
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    return bc


# --- o Banco Central agora autentica ----------------------------------------


def test_banco_central_aceita_senha(app, bc):
    """O CHECK que proibia isto caiu na migration 353a30f6e6f5."""
    bc.definir_senha(SENHA_BC)
    db.session.commit()

    assert bc.senha_hash is not None
    assert bc.senha_hash.startswith("$2")
    assert bc.verificar_senha(SENHA_BC)
    assert not bc.verificar_senha("chute")


def test_banco_central_sem_senha_ainda_nao_entra(app, bc):
    """Entre a gênese e o `flask senha-bc`, a conta não abre sessão.

    Não é sobra da regra antiga: é a mesma regra de qualquer conta sem senha.
    """
    assert bc.senha_hash is None
    assert bc.is_active is False
    with app.test_request_context():
        assert login_user(bc) is False


def test_banco_central_com_senha_e_is_active_e_tem_id(app, bc_com_senha):
    assert bc_com_senha.is_active is True
    assert bc_com_senha.get_id() == str(bc_com_senha.id)


def test_login_user_aceita_o_banco_central(app, bc_com_senha):
    with app.test_request_context():
        assert login_user(bc_com_senha) is True


def test_user_loader_devolve_o_banco_central(app, bc_com_senha):
    carregar = app.login_manager._user_callback
    with app.test_request_context():
        assert carregar(str(bc_com_senha.id)).id == bc_com_senha.id


def test_saldo_negativo_continua_barrado_pelo_banco(app, bc):
    """A migration trocou a tabela; esta é a rede que não podia ir junto."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.session.execute(
            db.update(Usuario).where(Usuario.id == bc.id).values(saldo=Decimal("-1.00"))
        )
    db.session.rollback()
    conservacao()


# --- o god mode é só do Banco Central ---------------------------------------


def test_jogador_comum_nao_e_admin(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    assert ana.eh_admin is False
    assert bc.eh_admin is True


def test_jogador_nao_exerce_poder_do_banco_central(app, bc, nova_pessoa):
    """Continua valendo: poder se pede, e só o BC tem."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    for chamada in (
        lambda: criar_convite(destinatario="pra mim", autoridade=ana),
        lambda: criar_usuario("laranja", "senha-boa-123", autoridade=ana),
        lambda: resetar_economia(autoridade=ana),
        lambda: ajustar_saldo(ana, "999.00", "quero mais", autoridade=ana),
    ):
        with pytest.raises(SemAutoridade):
            chamada()
        db.session.rollback()

    assert ana.saldo == Decimal("50.00")
    conservacao()


def test_operacoes_privilegiadas_recusam_ausencia_de_autoridade(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    for chamada in (
        lambda: criar_usuario("ninguem", "senha-boa-123"),
        lambda: criar_convite(destinatario="Ninguém"),
        lambda: resetar_economia(),
        lambda: ajustar_saldo(ana, "10.00", "sem autoridade"),
    ):
        with pytest.raises(SemAutoridade):
            chamada()
        db.session.rollback()
    conservacao()


def test_exigir_banco_central_aceita_id_e_objeto(app, bc):
    assert exigir_banco_central(bc).id == bc.id
    assert exigir_banco_central(bc.id).id == bc.id
    with pytest.raises(SemAutoridade):
        exigir_banco_central("banco_central")


# --- o BC continua não sendo jogador ----------------------------------------


def test_banco_central_nao_resgata_convite(app, bc):
    conservacao()
    convite = criar_convite(destinatario="tentativa", autoridade=bc)
    db.session.commit()

    from vavacoin.operacoes import resgatar_convite

    with pytest.raises(ValorInvalido):
        resgatar_convite(bc, convite.codigo)
    db.session.rollback()
    conservacao()


def test_ajustar_o_saldo_do_banco_central_emite_ou_queima(app, bc):
    """Este teste afirmava que o saldo do BC não era ajustável.

    A decisão mudou: ele é o único lado do mundo, então subir o saldo dele
    **emite** e baixar **queima**. Não há de onde tirar nem para onde mandar
    sem mentir sobre o que está em circulação.
    """
    from vavacoin.moeda import TIPO_EMISSAO, TIPO_QUEIMA, supply_emitido

    conservacao()

    ajustar_saldo(bc, "6000.00", "mais moeda", autoridade=bc)
    db.session.commit()
    assert bc.saldo == Decimal("6000.00")
    assert supply_emitido() == Decimal("6000.00")
    assert (
        db.session.query(Transacao).order_by(Transacao.id.desc()).first().tipo
        == TIPO_EMISSAO
    )
    conservacao()

    ajustar_saldo(bc, "4500.00", "tirando de circulação", autoridade=bc)
    db.session.commit()
    assert bc.saldo == Decimal("4500.00")
    assert supply_emitido() == Decimal("4500.00")
    assert (
        db.session.query(Transacao).order_by(Transacao.id.desc()).first().tipo
        == TIPO_QUEIMA
    )
    conservacao()


def test_reset_recolhe_do_dono_do_cassino_tambem(app, bc, nova_pessoa):
    """Sem exceção: o reset é o que desfaz a concentração."""
    dono = nova_pessoa(com_convite=True, saldo="50.00")
    otario = nova_pessoa(com_convite=True, saldo="50.00")
    mover(otario, dono, "50.00", motivo="mines do Caladinho")
    db.session.commit()
    assert dono.saldo == Decimal("100.00")
    conservacao()

    resetar_economia(autoridade=bc)
    db.session.commit()

    # Sem saque inicial, o reset passou a só recolher: todo mundo zera e o
    # dinheiro volta inteiro para o Banco Central.
    assert dono.saldo == Decimal("0.00")
    assert otario.saldo == Decimal("0.00")
    assert bc.saldo == SUPPLY_INICIAL
    conservacao()


# --- ajuste de saldo: cunha, mas pelo ledger --------------------------------


def test_ajuste_para_cima_usa_o_nao_emitido_antes_de_cunhar(app, bc, nova_pessoa):
    """Dinheiro parado no Banco Central é gasto antes de criar dinheiro novo."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    supply_antes = supply_emitido()

    ajustar_saldo(ana, "80.00", "corrigindo aposta paga errado", autoridade=bc)
    db.session.commit()

    assert ana.saldo == Decimal("80.00")
    assert supply_emitido() == supply_antes, "não devia ter cunhado nada"
    assert total_cunhado_depois_da_genese() == Decimal("0.00")
    conservacao()


def test_ajuste_cunha_so_o_que_falta(app, bc, nova_pessoa):
    """Com o BC vazio, o ajuste emite exatamente a diferença que faltava."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    # Esvazia o Banco Central: tudo vai para a ana.
    mover(bc, ana, bc.saldo, motivo="esvaziando para o teste")
    db.session.commit()
    assert bc.saldo == Decimal("0.00")
    conservacao()

    antes = ana.saldo
    ajustar_saldo(ana, antes + Decimal("30.00"), "corrigindo", autoridade=bc)
    db.session.commit()

    assert ana.saldo == antes + Decimal("30.00")
    assert total_cunhado_depois_da_genese() == Decimal("30.00")
    assert supply_emitido() == SUPPLY_INICIAL + Decimal("30.00")
    conservacao()


def test_cunhagem_aparece_como_linha_de_emissao(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    mover(bc, ana, bc.saldo, motivo="esvaziando")
    db.session.commit()

    ajustar_saldo(ana, ana.saldo + Decimal("10.00"), "erro meu", autoridade=bc)
    db.session.commit()

    emissoes = db.session.query(Transacao).filter_by(tipo=TIPO_EMISSAO).all()
    assert len(emissoes) == 1
    assert emissoes[0].origem_id is None
    assert emissoes[0].destino_id == bc.id
    assert emissoes[0].valor == Decimal("10.00")
    assert emissoes[0].ator_id == bc.id
    assert "erro meu" in emissoes[0].motivo
    conservacao()


def test_ajuste_para_baixo_devolve_ao_banco_central(app, bc, nova_pessoa):
    """Não queima: volta a ser não emitido, como no dia zero."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    bc_antes = bc.saldo

    ajustar_saldo(ana, "20.00", "tinha contado errado", autoridade=bc)
    db.session.commit()

    assert ana.saldo == Decimal("20.00")
    assert bc.saldo == bc_antes + Decimal("30.00")
    assert total_cunhado_depois_da_genese() == Decimal("0.00")
    conservacao()


def test_auditoria_fecha_depois_de_um_ajuste(app, bc, nova_pessoa):
    """O teste que prova que ficou certo.

    Se um ajuste fizesse a auditoria acusar divergência, o administrador
    aprenderia a ignorar o alarme — e aí o alarme não serve para mais nada.
    """
    from vavacoin.auditoria import auditar

    ana = nova_pessoa(com_convite=True, saldo="50.00")
    mover(bc, ana, bc.saldo, motivo="esvaziando")
    db.session.commit()

    ajustar_saldo(ana, ana.saldo + Decimal("123.45"), "conserto", autoridade=bc)
    ajustar_saldo(ana, "7.00", "conserto do conserto", autoridade=bc)
    db.session.commit()

    relatorio = auditar()
    assert relatorio["ok"] is True, relatorio["ledger"]
    assert relatorio["ledger"]["saldos_divergentes"] == []
    assert relatorio["ledger"]["linhas_inconsistentes"] == []
    conservacao()


def test_ajuste_registra_ator_e_motivo_no_ledger(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    ajustar_saldo(ana, "77.00", "pagou o lanche por mim", autoridade=bc)
    db.session.commit()

    linha = (
        db.session.query(Transacao)
        .filter_by(tipo=TIPO_AJUSTE)
        .order_by(Transacao.id.desc())
        .first()
    )
    assert linha.ator_id == bc.id
    assert linha.motivo == "pagou o lanche por mim"
    conservacao()


def test_ajuste_sem_motivo_e_recusado(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    for motivo in (None, "", "   "):
        with pytest.raises(MotivoObrigatorio):
            ajustar_saldo(ana, "80.00", motivo, autoridade=bc)
        db.session.rollback()
    assert ana.saldo == Decimal("50.00")
    conservacao()


def test_ajuste_para_saldo_negativo_e_recusado(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()
    with pytest.raises(ValorInvalido):
        ajustar_saldo(ana, "-1.00", "vingança", autoridade=bc)
    db.session.rollback()
    assert ana.saldo == Decimal("50.00")
    conservacao()


def test_ajuste_sem_mudanca_nao_gera_transacao(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    linhas_antes = db.session.query(Transacao).count()

    assert ajustar_saldo(ana, "50.00", "conferindo", autoridade=bc) is None
    db.session.commit()

    assert db.session.query(Transacao).count() == linhas_antes
    conservacao()


def test_emissao_exige_o_banco_central_como_ator(app, bc, nova_pessoa):
    """Nem chamando o mover() na mão dá para cunhar sem ser o BC."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(SemAutoridade):
        mover(None, ana, "100.00", tipo=TIPO_EMISSAO, motivo="quero", ator=ana)
    db.session.rollback()

    with pytest.raises(SemAutoridade):
        mover(None, ana, "100.00", tipo=TIPO_EMISSAO, motivo="quero")
    db.session.rollback()
    conservacao()


def test_movimento_sem_origem_so_vale_para_emissao(app, bc, nova_pessoa):
    """Uma transferência sem origem seria moeda aparecendo do nada."""
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    conservacao()

    with pytest.raises(ValorInvalido):
        mover(None, ana, "100.00", tipo="transferencia", motivo="disfarce", ator=bc)
    db.session.rollback()
    conservacao()


def test_emissao_exige_motivo(app, bc):
    conservacao()
    with pytest.raises(ValorInvalido):
        mover(None, bc, "100.00", tipo=TIPO_EMISSAO, ator=bc)
    db.session.rollback()
    conservacao()


# --- diário do god mode -----------------------------------------------------


def test_diario_registra_ajuste_com_valores_e_motivo(app, bc, nova_pessoa):
    ana = nova_pessoa(com_convite=True, saldo="50.00")
    ajustar_saldo(ana, "80.00", "corrigindo aposta", autoridade=bc)
    db.session.commit()

    registro = (
        db.session.query(RegistroAdministrativo)
        .filter_by(acao="ajuste")
        .order_by(RegistroAdministrativo.id.desc())
        .first()
    )
    assert registro.ator_id == bc.id
    assert registro.alvo == ana.nome_usuario
    assert "50.00" in registro.detalhe and "80.00" in registro.detalhe
    assert registro.motivo == "corrigindo aposta"
    assert registro.criado_em is not None


def test_diario_registra_convite_conta_e_reset(app, bc):
    criar_convite(destinatario="Ana", autoridade=bc)
    criar_usuario("ana", "senha-boa-123", autoridade=bc)
    resetar_economia(autoridade=bc, motivo="começar de novo")
    db.session.commit()

    acoes = [
        r.acao
        for r in db.session.query(RegistroAdministrativo)
        .order_by(RegistroAdministrativo.id)
        .all()
    ]
    assert acoes == ["convite", "conta", "reset"]


def test_cadastro_pela_web_nao_polui_o_diario(app, bc, nova_pessoa):
    """O diário é do administrador. Aluno se cadastrando não é ação dele."""
    from vavacoin.operacoes import cadastrar_por_convite

    convite = criar_convite(destinatario="Ana", autoridade=bc)
    db.session.commit()
    antes = db.session.query(RegistroAdministrativo).count()

    cadastrar_por_convite("ana", "senha-boa-123", convite.codigo)
    db.session.commit()

    assert db.session.query(RegistroAdministrativo).count() == antes
    conservacao()
