"""Normalização de nome de usuário.

A pessoa escolhe como o nome dela aparece — com maiúscula e com acento, se
quiser. O sistema, por baixo, compara sempre a **forma normalizada**: sem
acento, minúscula, espaços colapsados.

Guardar as duas formas resolve dois problemas que só aparecem depois:

- **Unicidade.** Sem normalizar, ``João`` e ``joao`` viram duas contas
  diferentes, e a turma passa a ter dois "joão" — um deles recebendo
  transferência que era para o outro.
- **Login.** Quem se cadastrou como ``João`` tenta entrar digitando ``joao``
  no celular, sem acento, e não consegue. O nome que ele lembra é o que
  aparece na tela, não a sequência exata de bytes.

Mesma ideia do ``normalizar_nome`` do ITA-IME.
"""

import re
import unicodedata


def normalizar_nome(texto):
    """Forma canônica de um nome: sem acento, minúscula, espaços colapsados.

    ``NFKD`` separa a letra do acento (``ã`` vira ``a`` + ``~``); descartar as
    marcas combinantes deixa só a letra base. É o caminho que trata ``ç``,
    ``ñ`` e as vogais acentuadas de uma vez, sem tabela de substituição na mão.
    """
    if texto is None:
        return ""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().lower()
