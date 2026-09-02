"""Edição na linha da tabela de contas.

O atrito que isto resolve é real: ajustar o saldo de alguém exigia sair da
lista, digitar o nome de novo e escrever uma frase. Agora é digitar o número e
salvar.

O que **não** mudou, e é o que estes testes protegem: o saldo continua
passando por ``ajustar_saldo``, com lançamento no ledger. A tabela mudou a
tela, não o caminho do dinheiro.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.constantes import SUPPLY_MAXIMO
from vavacoin.extensoes import db
from vavacoin.formularios import MOTIVO_PADRAO
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import TIPO_AJUSTE, mover, supply_emitido
from vavacoin.modelos import Transacao, Usuario, buscar_usuario
from vavacoin.operacoes import criar_convite

SENHA_BC = "senha-do-banco-central-123"
SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def painel(app, bc):
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "banco_central", "senha": SENHA_BC},
        follow_redirects=True,
    )
    return cliente


@pytest.fixture
def ana(app, bc, nova_pessoa):
    return nova_pessoa(nome="ana", com_convite=True, saldo="50.00")


def _salvar(painel, conta, **campos):
    """Posta a linha da conta, como o botão Salvar faz."""
    dados = {"nome_usuario": conta.nome_usuario, "senha": "", "motivo": ""}
    dados["saldo"] = str(conta.saldo)
    dados.update(campos)
    return painel.post(
        f"/painel/conta/{conta.id}", data=dados, follow_redirects=True
    )


# --- saldo ------------------------------------------------------------------


def test_salvar_saldo_passa_pelo_ledger(app, bc, painel, ana):
    """Inegociável: a tabela mudou a tela, não o caminho do dinheiro."""
    conservacao()
    linhas_antes = db.session.query(Transacao).count()

    resposta = _salvar(painel, ana, saldo="80.00")
    assert resposta.status_code == 200

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == Decimal("80.00")

    linha = (
        db.session.query(Transacao)
        .order_by(Transacao.id.desc())
        .first()
    )
    assert db.session.query(Transacao).count() == linhas_antes + 1
    assert linha.tipo == TIPO_AJUSTE
    assert linha.ator_id == bc.id
    assert linha.saldo_destino_depois == Decimal("80.00")
    conservacao()


def test_auditoria_fecha_depois_de_editar_na_linha(app, bc, painel, ana):
    from vavacoin.auditoria import auditar

    _salvar(painel, ana, saldo="123.45")
    db.session.expire_all()
    assert auditar()["ok"] is True
    conservacao()


def test_motivo_em_branco_vira_o_padrao(app, bc, painel, ana):
    _salvar(painel, ana, saldo="70.00", motivo="")

    linha = (
        db.session.query(Transacao)
        .filter_by(tipo=TIPO_AJUSTE)
        .order_by(Transacao.id.desc())
        .first()
    )
    assert linha.motivo == MOTIVO_PADRAO
    conservacao()


def test_motivo_escrito_e_respeitado(app, bc, painel, ana):
    _salvar(painel, ana, saldo="70.00", motivo="pagou o lanche por mim")

    linha = (
        db.session.query(Transacao)
        .filter_by(tipo=TIPO_AJUSTE)
        .order_by(Transacao.id.desc())
        .first()
    )
    assert linha.motivo == "pagou o lanche por mim"


def test_salvar_sem_mudar_o_saldo_nao_gera_lancamento(app, bc, painel, ana):
    linhas_antes = db.session.query(Transacao).count()
    _salvar(painel, ana, saldo=str(ana.saldo))
    assert db.session.query(Transacao).count() == linhas_antes
    conservacao()


def test_saldo_negativo_e_recusado(app, bc, painel, ana):
    resposta = _salvar(painel, ana, saldo="-5.00")
    assert resposta.status_code == 400

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == Decimal("50.00")
    conservacao()


# --- nome -------------------------------------------------------------------


def test_renomear_para_a_mesma_pessoa_com_acento(app, bc, painel, ana):
    """`ana` para `Ana` é a mesma conta: normaliza igual, então passa."""
    _salvar(painel, ana, nome_usuario="Ana")

    db.session.expire_all()
    conta = db.session.get(Usuario, ana.id)
    assert conta.nome_usuario == "Ana"
    assert conta.nome_normalizado == "ana"


def test_renomear_para_nome_de_outra_conta_e_recusado(app, bc, painel, ana, nova_pessoa):
    bia = nova_pessoa(nome="bia", com_convite=True)
    conservacao()

    resposta = _salvar(painel, ana, nome_usuario="BIA")
    assert resposta.status_code == 400
    assert "já existe" in resposta.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).nome_usuario == "ana"
    assert db.session.get(Usuario, bia.id).nome_usuario == "bia"


def test_erro_aparece_na_linha_da_conta(app, bc, painel, ana, nova_pessoa):
    nova_pessoa(nome="bia", com_convite=True)
    corpo = _salvar(painel, ana, nome_usuario="bia").get_data(as_text=True)
    assert "linha-erro" in corpo


def test_renomear_e_mudar_saldo_de_uma_vez(app, bc, painel, ana):
    conservacao()
    _salvar(painel, ana, nome_usuario="Aninha", saldo="90.00", motivo="tudo junto")

    db.session.expire_all()
    conta = db.session.get(Usuario, ana.id)
    assert conta.nome_usuario == "Aninha"
    assert conta.saldo == Decimal("90.00")
    conservacao()


# --- senha ------------------------------------------------------------------


def test_senha_em_branco_nao_mexe_na_senha(app, bc, painel, ana):
    _salvar(painel, ana, senha="")

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).verificar_senha(SENHA)


def test_senha_preenchida_troca_a_senha(app, bc, painel, ana):
    _salvar(painel, ana, senha="outra-senha")

    db.session.expire_all()
    conta = db.session.get(Usuario, ana.id)
    assert conta.verificar_senha("outra-senha")
    assert not conta.verificar_senha(SENHA)


# --- contas de sistema ------------------------------------------------------


def test_o_caixa_do_cassino_e_editavel_na_linha(app, bc, painel):
    """O atalho que ele pediu: pôr dinheiro na casa sem sair da lista."""
    from vavacoin.caladinho import criar_casa

    casa = criar_casa(autoridade=bc)
    db.session.commit()
    conservacao()

    _salvar(painel, casa, saldo="1000.00", motivo="caixa do Caladinho")

    db.session.expire_all()
    assert db.session.get(Usuario, casa.id).saldo == Decimal("1000.00")
    conservacao()


def test_o_cassino_nao_e_renomeado_nem_ganha_senha(app, bc, painel):
    from vavacoin.caladinho import criar_casa

    casa = criar_casa(autoridade=bc)
    db.session.commit()

    assert _salvar(painel, casa, nome_usuario="outro").status_code == 400
    db.session.rollback()
    assert _salvar(painel, casa, senha="entra-no-site").status_code == 400
    db.session.rollback()

    db.session.expire_all()
    conta = db.session.get(Usuario, casa.id)
    assert conta.nome_usuario == "caladinho"
    assert conta.senha_hash is None, "a casa não entra pelo site"


def test_contas_de_sistema_editam_saldo_mas_nao_senha_nem_nome(app, bc, painel):
    """O saldo das duas é editável; senha e nome, não.

    Este teste dizia que o Banco Central não tinha campo de saldo. A decisão
    mudou: ele tem, e é por ali que se emite e se queima.
    """
    from vavacoin.caladinho import criar_casa

    criar_casa(autoridade=bc)
    db.session.commit()
    corpo = painel.get("/painel/").get_data(as_text=True)

    assert 'aria-label="Saldo de banco_central"' in corpo
    assert 'aria-label="Saldo de caladinho"' in corpo
    assert 'aria-label="Nova senha de banco_central"' not in corpo
    assert 'aria-label="Nova senha de caladinho"' not in corpo
    assert 'aria-label="Usuário de banco_central"' not in corpo
    assert 'aria-label="Usuário de caladinho"' not in corpo


def test_contas_de_sistema_aparecem_para_consulta(app, bc, painel):
    """Aparecem na lista, mas sem campo do que não se edita."""
    from vavacoin.caladinho import criar_casa

    criar_casa(autoridade=bc)
    db.session.commit()
    corpo = painel.get("/painel/").get_data(as_text=True)

    assert "banco_central" in corpo
    assert "caladinho" in corpo


def test_baixar_o_saldo_do_bc_na_linha_queima(app, bc, painel):
    """Pela linha da tabela: baixar o BC destrói moeda, e o supply desce."""
    from vavacoin.moeda import TIPO_QUEIMA, supply_emitido

    conservacao()
    _salvar(painel, bc, saldo="4000.00", motivo="tirando de circulação")

    db.session.expire_all()
    assert db.session.get(Usuario, bc.id).saldo == Decimal("4000.00")
    assert supply_emitido() == Decimal("4000.00")
    assert (
        db.session.query(Transacao).order_by(Transacao.id.desc()).first().tipo
        == TIPO_QUEIMA
    )
    conservacao()


def test_subir_o_saldo_do_bc_na_linha_emite(app, bc, painel):
    from vavacoin.moeda import TIPO_EMISSAO, supply_emitido

    conservacao()
    _salvar(painel, bc, saldo="7000.00", motivo="mais moeda")

    db.session.expire_all()
    assert db.session.get(Usuario, bc.id).saldo == Decimal("7000.00")
    assert supply_emitido() == Decimal("7000.00")
    assert (
        db.session.query(Transacao).order_by(Transacao.id.desc()).first().tipo
        == TIPO_EMISSAO
    )
    conservacao()


def test_o_painel_mostra_o_supply_mudando_depois_de_queimar(app, bc, painel):
    """Se o número não se mexe na tela, alguém queima duas vezes achando que
    não pegou."""
    antes = painel.get("/painel/").get_data(as_text=True)
    assert "5000.00" in antes

    _salvar(painel, bc, saldo="3000.00", motivo="queimando")

    depois = painel.get("/painel/").get_data(as_text=True)
    assert "3000.00" in depois
    assert "7000.00" in depois, "e o espaço sob o teto volta a aparecer"
    conservacao()


def test_o_painel_nao_apaga_conta_com_historico(app, bc, painel, ana):
    """O 🗑️ cego do Benbals não veio, e não vai vir.

    Este teste já disse "não existe excluir conta". Existe agora, mas com a
    regra que falta lá: a `ana` da fixture tem saldo, então o painel oferece
    **encerrar** e o caminho de apagar de verdade nem aparece para ela.
    """
    corpo = painel.get("/painel/").get_data(as_text=True)

    assert "/painel/conta/%s/apagar" % ana.id not in corpo
    assert "/painel/conta/%s/encerrar" % ana.id in corpo


# --- teto do supply ---------------------------------------------------------


def test_a_linha_recusa_saldo_que_estouraria_o_teto(app, bc, painel, ana):
    """E diz quanto ainda cabe."""
    mover(None, bc, "5000.00", tipo="emissao", motivo="ao teto", ator=bc)
    db.session.commit()
    mover(bc, ana, bc.saldo, motivo="esvaziando o não emitido")
    db.session.commit()
    conservacao()

    saldo_antes = db.session.get(Usuario, ana.id).saldo
    resposta = _salvar(painel, ana, saldo=str(saldo_antes + Decimal("1.00")))

    assert resposta.status_code == 400
    assert "ainda cabem" in resposta.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == saldo_antes
    assert supply_emitido() == SUPPLY_MAXIMO
    conservacao()


# --- só o Banco Central -----------------------------------------------------


def test_jogador_nao_edita_conta_nenhuma(app, bc, ana):
    codigo = criar_convite(destinatario="bia", autoridade=bc).codigo
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/cadastro",
        data={
            "codigo": codigo,
            "nome_usuario": "bia",
            "nome_exibicao": "Bia",
            "senha": SENHA,
            "confirmacao": SENHA,
        },
        follow_redirects=True,
    )

    resposta = cliente.post(
        f"/painel/conta/{ana.id}", data={"saldo": "9999.00"}
    )
    assert resposta.status_code == 403

    db.session.expire_all()
    assert db.session.get(Usuario, ana.id).saldo == Decimal("50.00")
    conservacao()


def test_conta_inexistente(app, bc, painel):
    assert painel.post("/painel/conta/99999", data={"saldo": "1.00"}).status_code == 404
