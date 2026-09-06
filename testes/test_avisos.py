"""Aviso do reino: o operador escreve uma vez, os cidadãos leem.

É a primeira coisa do projeto em que uma pessoa escreve e vinte leem. Por isso
o teste que mais importa aqui não é de regra de negócio — é o de escape: uma
tag mandada no aviso tem de chegar na tela como letra.

Nada disto move dinheiro. A conservação e a auditoria são conferidas mesmo
assim, porque a feature encosta em cidadania e a garantia é barata.
"""

import pytest

from vavacoin.auditoria import conferir_ledger
from vavacoin.avisos import (
    TAMANHO_MAXIMO,
    apagar_aviso,
    avisos_do_reino,
    avisos_pendentes,
    criar_aviso,
    marcar_visto,
    pode_apagar,
)
from vavacoin.erros import SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    AvisoDoReino,
    AvisoVisto,
    definir_config,
)
from vavacoin.reinos import (
    criar_reino,
    definir_operador,
    entrar_no_reino,
    sair_do_reino,
    tirar_operador,
)

from conftest import conservacao

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cena(app, bc, nova_pessoa):
    """Alfheim com um operador (o Bento) e dois cidadãos."""
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()

    bento = nova_pessoa(nome="bento", saldo="50.00")
    ana = nova_pessoa(nome="ana", saldo="30.00")
    caio = nova_pessoa(nome="caio", saldo="20.00")
    fora = nova_pessoa(nome="fora", saldo="10.00")
    db.session.commit()

    definir_operador(reino, bento, autoridade=bc)
    db.session.commit()
    for pessoa in (bento, ana, caio):
        entrar_no_reino(reino, pessoa)
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()

    return {"reino": reino, "bento": bento, "ana": ana, "caio": caio, "fora": fora}


def _entrar(app, nome):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": SENHA}, follow_redirects=True
    )
    return cliente


# --- escapar o que a pessoa escreveu ----------------------------------------


def test_uma_tag_no_aviso_aparece_como_texto(app, bc, cena):
    """O teste que justifica os outros: vinte pessoas leem o que uma escreveu."""
    criar_aviso(cena["reino"], cena["bento"], "<script>alert('oi')</script>")
    db.session.commit()

    corpo = _entrar(app, "ana").get("/carteira").get_data(as_text=True)

    assert "<script>alert" not in corpo
    assert "&lt;script&gt;" in corpo


def test_a_tag_tambem_e_escapada_na_pagina_do_reino(app, bc, cena):
    """Duas telas mostram o aviso; as duas escapam."""
    criar_aviso(cena["reino"], cena["bento"], "<img src=x onerror=alert(1)>")
    db.session.commit()

    corpo = _entrar(app, "ana").get("/reino/alfheim").get_data(as_text=True)

    assert "<img src=x" not in corpo
    assert "&lt;img" in corpo


# --- quem escreve -----------------------------------------------------------


def test_o_operador_escreve_e_o_aviso_guarda_autor_e_data(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião amanhã")
    db.session.commit()

    assert aviso.autor_id == cena["bento"].id
    assert aviso.reino_id == cena["reino"].id
    assert aviso.criado_em is not None
    assert aviso.texto == "reunião amanhã"


def test_cidadao_comum_nao_escreve(app, bc, cena):
    with pytest.raises(SemAutoridade):
        criar_aviso(cena["reino"], cena["ana"], "eu mando aqui")


def test_aviso_vazio_e_recusado(app, bc, cena):
    for vazio in ("", "   ", "\n\t "):
        with pytest.raises(ValorInvalido):
            criar_aviso(cena["reino"], cena["bento"], vazio)


def test_aviso_grande_demais_e_recusado_no_servidor(app, bc, cena):
    """O ``maxlength`` da tela é conveniência; a regra é aqui."""
    with pytest.raises(ValorInvalido):
        criar_aviso(cena["reino"], cena["bento"], "a" * (TAMANHO_MAXIMO + 1))

    no_limite = criar_aviso(cena["reino"], cena["bento"], "a" * TAMANHO_MAXIMO)
    db.session.commit()
    assert len(no_limite.texto) == TAMANHO_MAXIMO


def test_o_texto_e_aparado(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "  amanhã  ")
    db.session.commit()

    assert aviso.texto == "amanhã"


# --- idempotência -----------------------------------------------------------


def test_o_mesmo_token_nao_cria_dois_avisos(app, bc, cena):
    """Clique duplo é um aviso só."""
    criar_aviso(cena["reino"], cena["bento"], "reunião", token="abc123")
    db.session.commit()

    with pytest.raises(ValorInvalido):
        criar_aviso(cena["reino"], cena["bento"], "reunião", token="abc123")
    db.session.rollback()

    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoDoReino)
    ).scalar_one() == 1


def test_o_clique_duplo_na_tela_nao_cria_dois(app, bc, cena):
    """Pela rota, com o token da tela: o segundo POST não passa."""
    operador = _entrar(app, "bento")
    pagina = operador.get("/reino/alfheim/operar").get_data(as_text=True)
    token = pagina.split('name="token" value="')[1].split('"')[0]

    for _ in range(2):
        operador.post(
            "/reino/alfheim/aviso",
            data={"texto": "prova na sexta", "token": token},
            follow_redirects=True,
        )
    db.session.expire_all()

    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoDoReino)
    ).scalar_one() == 1


# --- quem vê ----------------------------------------------------------------


def test_os_cidadaos_veem(app, bc, cena):
    criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    assert len(avisos_pendentes(cena["ana"])) == 1
    assert len(avisos_pendentes(cena["caio"])) == 1


def test_quem_nao_e_do_reino_nao_ve(app, bc, cena):
    criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    assert avisos_pendentes(cena["fora"]) == []
    with pytest.raises(LookupError):
        avisos_do_reino(cena["reino"], cena["fora"])


def test_quem_entra_depois_nao_recebe_aviso_antigo(app, bc, cena):
    """O aviso é do momento: chegar atrasado não é ter participado."""
    criar_aviso(cena["reino"], cena["bento"], "o de antes")
    db.session.commit()

    novato = cena["fora"]
    entrar_no_reino(cena["reino"], novato)
    db.session.commit()

    assert avisos_pendentes(novato) == []

    criar_aviso(cena["reino"], cena["bento"], "o de agora")
    db.session.commit()

    assert [a.texto for a in avisos_pendentes(novato)] == ["o de agora"]


def test_quem_sai_para_de_ver(app, bc, cena):
    criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    assert len(avisos_pendentes(cena["ana"])) == 1

    sair_do_reino(cena["reino"], cena["ana"])
    db.session.commit()

    assert avisos_pendentes(cena["ana"]) == []


def test_quem_sai_e_volta_entra_como_quem_chega(app, bc, cena):
    criar_aviso(cena["reino"], cena["bento"], "o de antes")
    db.session.commit()
    sair_do_reino(cena["reino"], cena["ana"])
    db.session.commit()

    entrar_no_reino(cena["reino"], cena["ana"])
    db.session.commit()

    assert avisos_pendentes(cena["ana"]) == []


def test_o_operador_ve_todos_do_reino(app, bc, cena):
    """Ele administra a lista; não daria para apagar o que não se enxerga."""
    criar_aviso(cena["reino"], cena["bento"], "um")
    criar_aviso(cena["reino"], cena["bento"], "dois")
    db.session.commit()

    assert len(avisos_do_reino(cena["reino"], cena["bento"])) == 2


# --- dispensar --------------------------------------------------------------


def test_marcar_visto_tira_da_carteira_de_quem_marcou(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    marcar_visto(aviso, cena["ana"])
    db.session.commit()

    assert avisos_pendentes(cena["ana"]) == []
    assert len(avisos_pendentes(cena["caio"])) == 1, "dispensa é por pessoa"


def test_dispensado_continua_na_pagina_do_reino(app, bc, cena):
    """Sumir da carteira não é sumir do reino."""
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    marcar_visto(aviso, cena["ana"])
    db.session.commit()

    assert len(avisos_do_reino(cena["reino"], cena["ana"])) == 1


def test_marcar_visto_duas_vezes_nao_quebra(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    for _ in range(3):
        marcar_visto(aviso, cena["ana"])
        db.session.commit()

    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoVisto)
    ).scalar_one() == 1


def test_a_rota_de_dispensar_e_so_de_quem_ve(app, bc, cena):
    """O número do endereço não pode virar sonda de reino alheio."""
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    resposta = _entrar(app, "fora").post(f"/reino/aviso/{aviso.id}/visto")

    assert resposta.status_code == 404
    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoVisto)
    ).scalar_one() == 0


def test_a_carteira_deixa_de_mostrar_depois_do_ok(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião de sexta")
    db.session.commit()
    cliente = _entrar(app, "ana")
    assert "reunião de sexta" in cliente.get("/carteira").get_data(as_text=True)

    cliente.post(f"/reino/aviso/{aviso.id}/visto", follow_redirects=True)
    db.session.expire_all()

    assert "reunião de sexta" not in cliente.get("/carteira").get_data(as_text=True)


# --- apagar -----------------------------------------------------------------


def test_o_autor_apaga(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    apagar_aviso(aviso, cena["bento"])
    db.session.commit()

    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoDoReino)
    ).scalar_one() == 0


def test_apagar_leva_junto_as_marcas_de_dispensa(app, bc, cena):
    """Marca sem aviso é lixo apontando para nada."""
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    marcar_visto(aviso, cena["ana"])
    db.session.commit()

    apagar_aviso(aviso, cena["bento"])
    db.session.commit()

    assert db.session.execute(
        db.select(db.func.count()).select_from(AvisoVisto)
    ).scalar_one() == 0


def test_cidadao_nao_apaga(app, bc, cena):
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    assert not pode_apagar(aviso, cena["ana"])
    with pytest.raises(ValorInvalido):
        apagar_aviso(aviso, cena["ana"])


def test_ex_operador_nao_apaga_o_que_escreveu(app, bc, cena):
    """Perdeu o papel, perdeu o poder — como na dívida."""
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    tirar_operador(cena["reino"], cena["bento"], autoridade=bc)
    db.session.commit()

    assert not pode_apagar(aviso, cena["bento"])


def test_o_aviso_do_ex_operador_nao_fica_orfao(app, bc, cena):
    """Quando o rei troca, o novo assume o que o antigo deixou."""
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    definir_operador(cena["reino"], cena["ana"], autoridade=bc)
    tirar_operador(cena["reino"], cena["bento"], autoridade=bc)
    db.session.commit()

    assert pode_apagar(aviso, cena["ana"])


def test_operador_de_outro_reino_nao_apaga(app, bc, cena, nova_pessoa):
    outro = criar_reino("Vanaheim", autoridade=bc)
    db.session.commit()
    rei_de_la = nova_pessoa(nome="rei_vana")
    definir_operador(outro, rei_de_la, autoridade=bc)
    db.session.commit()
    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()

    assert not pode_apagar(aviso, rei_de_la)


# --- o resto do site continua de pé -----------------------------------------


def test_com_os_reinos_desligados_a_carteira_nao_mostra_aviso(app, bc, cena):
    criar_aviso(cena["reino"], cena["bento"], "reunião de sexta")
    definir_config(CHAVE_REINOS_VISIVEIS, False)
    db.session.commit()

    corpo = _entrar(app, "ana").get("/carteira").get_data(as_text=True)

    assert "reunião de sexta" not in corpo


def test_a_carteira_de_quem_nao_tem_reino_abre(app, bc, cena):
    resposta = _entrar(app, "fora").get("/carteira")

    assert resposta.status_code == 200


def test_o_aviso_nao_mexe_em_dinheiro(app, bc, cena):
    antes = conservacao()

    aviso = criar_aviso(cena["reino"], cena["bento"], "reunião")
    db.session.commit()
    marcar_visto(aviso, cena["ana"])
    db.session.commit()
    apagar_aviso(aviso, cena["bento"])
    db.session.commit()

    assert conservacao() == antes
    assert conferir_ledger()["ok"]
