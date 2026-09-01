"""Tipo monetário do VavaCoin.

Dinheiro nunca é ``float``. O motivo é o de sempre: ``0.1 + 0.2 != 0.3`` em
binário, e uma economia com supply fixo em 5.000,00 não sobrevive a um
centavo aparecendo ou sumindo por arredondamento de ponto flutuante.

A persistência é feita em **centavos inteiros** (não em ``NUMERIC``) porque o
SQLite não tem tipo decimal nativo: um ``NUMERIC`` ali vira ``REAL``, ou seja,
float com outro nome. Inteiro é exato em qualquer banco, e a conversão para
``Decimal`` acontece na borda.
"""

from decimal import ROUND_DOWN, Decimal, InvalidOperation

from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator

#: Menor unidade representável. Nada abaixo disso existe na economia.
CENTAVO = Decimal("0.01")
ZERO = Decimal("0.00")


def para_decimal(valor):
    """Converte ``valor`` para ``Decimal`` com exatamente dois dígitos.

    Recusa ``float`` de propósito, em vez de convertê-lo. Aceitar float aqui
    seria abrir a porta justamente para o erro que este módulo existe para
    impedir — e o autor da chamada quase sempre queria ``Decimal("1.10")``,
    não ``1.1`` (que na verdade é 1.100000000000000088817841970012523233890533447265625).

    Também recusa precisão abaixo do centavo em vez de arredondar: arredondar
    calado é como massa some.
    """
    if isinstance(valor, float):
        raise TypeError(
            "dinheiro não aceita float; use Decimal('...') ou uma string"
        )
    if isinstance(valor, Decimal):
        d = valor
    elif isinstance(valor, (int, str)):
        try:
            d = Decimal(valor)
        except InvalidOperation as erro:
            raise TypeError(f"valor monetário inválido: {valor!r}") from erro
    else:
        raise TypeError(f"tipo inválido para dinheiro: {type(valor).__name__}")

    if not d.is_finite():
        raise TypeError(f"valor monetário não finito: {valor!r}")

    quantizado = d.quantize(CENTAVO)
    if quantizado != d:
        raise TypeError(
            f"valor com precisão abaixo do centavo: {valor!r} "
            "(arredondar em silêncio é como massa some)"
        )
    return quantizado


def quantizar_para_baixo(valor):
    """Arredonda para dois decimais **para baixo**.

    Existe separado de :func:`para_decimal`, que recusa precisão abaixo do
    centavo em vez de arredondar. Aqui arredondar é o certo e o sentido
    importa: multiplicador e prêmio saem de divisão, e sobra dízima. Para
    baixo, sempre — a diferença de meio centavo tem que cair para a casa, não
    contra ela, senão o jogo paga um pouquinho mais do que a tabela promete.
    """
    if isinstance(valor, float):
        raise TypeError("dinheiro não aceita float; use Decimal('...') ou string")
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_DOWN)


def centavos(valor):
    """Quantos centavos inteiros ``valor`` representa."""
    return int(para_decimal(valor) * 100)


class Dinheiro(TypeDecorator):
    """Coluna monetária: ``Decimal`` em Python, centavos inteiros no banco."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return centavos(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / 100).quantize(CENTAVO)
