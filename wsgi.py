"""Ponto de entrada da aplicação — é para cá que o PythonAnywhere aponta.

Localmente, ``flask run`` usa a config de desenvolvimento. Com
``VAVACOIN_ENV=producao`` a aplicação sobe com cookie só por HTTPS e exige
que ``VAVACOIN_SECRET_KEY`` esteja definida — com a chave padrão, que está no
repositório, qualquer um forjaria o cookie de sessão de qualquer conta.
"""

import os

from vavacoin import criar_app
from vavacoin.config import Config, ConfigProducao

configuracao = (
    ConfigProducao if os.environ.get("VAVACOIN_ENV") == "producao" else Config
)

app = criar_app(configuracao)

# O PythonAnywhere procura `application` no arquivo WSGI.
application = app
