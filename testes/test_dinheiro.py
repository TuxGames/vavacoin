"""O tipo monetário: exatidão e recusa de float."""

from decimal import Decimal

import pytest

from vavacoin.dinheiro import centavos, para_decimal


def test_recusa_float():
    """Float é recusado, não convertido — é a origem do centavo que some."""
    with pytest.raises(TypeError):
        para_decimal(0.1)


def test_aceita_str_int_e_decimal():
    assert para_decimal("10.50") == Decimal("10.50")
    assert para_decimal(7) == Decimal("7.00")
    assert para_decimal(Decimal("0.01")) == Decimal("0.01")


def test_recusa_precisao_abaixo_do_centavo():
    """Meio centavo não existe; arredondar calado é como massa some."""
    with pytest.raises(TypeError):
        para_decimal("0.005")


def test_soma_de_decimais_e_exata():
    """A soma que o float erra."""
    assert para_decimal("0.10") + para_decimal("0.20") == para_decimal("0.30")


def test_conversao_para_centavos():
    assert centavos("5000.00") == 500_000
    assert centavos("0.01") == 1


def test_ida_e_volta_do_tipo_no_banco(app):
    """Grava e lê 5.000,00 sem perder centavo."""
    from vavacoin.extensoes import db
    from vavacoin.modelos import Usuario

    u = Usuario(nome_usuario="x", nome_exibicao="x", saldo=Decimal("5000.00"))
    db.session.add(u)
    db.session.commit()
    db.session.expire_all()
    lido = db.session.get(Usuario, u.id)
    assert lido.saldo == Decimal("5000.00")
    assert isinstance(lido.saldo, Decimal)
