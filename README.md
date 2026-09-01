# VaVáCoin — núcleo monetário

Primeira fatia: só o motor de dinheiro e os testes que provam que ele não
vaza. Sem interface, sem ranking, sem cassino. As decisões do projeto estão
no `CLAUDE.md` e não se relitigam aqui.

## Ambiente

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
```

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

Toda operação é cercada pelo helper `conservacao()` (em `testes/conftest.py`):
a soma de **todos** os saldos, incluindo o do Banco Central, é sempre
exatamente 5.000,00.

## Banco e operação

```bash
export FLASK_APP=wsgi.py
flask db upgrade                      # cria as tabelas
flask genese                          # 5.000,00 no Banco Central (idempotente)
flask convite --destinatario "Fulano" # emite um convite; imprime o código
flask conservacao                     # confere a massa
flask resetar                         # recolhe tudo e redistribui os 50
```

## Onde está o quê

| arquivo | o que guarda |
| --- | --- |
| `vavacoin/dinheiro.py` | tipo monetário: `Decimal` em Python, centavos inteiros no banco |
| `vavacoin/moeda.py` | `mover()` — o caminho único —, a gênese e a verificação de massa |
| `vavacoin/operacoes.py` | resgate do convite, transferência, reset, extrato |
| `vavacoin/modelos.py` | `Usuario`, `Convite`, `Transacao` (ledger) |
| `vavacoin/constantes.py` | supply, saque inicial, capacidade |

## Regras que o código impõe

- Nenhuma função fora de `mover()` escreve em saldo. A exceção é a gênese,
  que só roda com o ledger vazio e sem Banco Central.
- Dinheiro nunca é `float` — `para_decimal()` recusa, não converte.
- Nada de `commit` dentro da biblioteca: quem chama é dono da transação. As
  operações compostas rodam em `SAVEPOINT` para não deixar meio trabalho.
- Senha com hash bcrypt. O Banco Central não tem senha e não autentica.
